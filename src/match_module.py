import json
import re
from pathlib import Path

from rapidfuzz import fuzz, process
from utils import log

TAG = "MATCHMODULE"

def normalize(text: str) -> str:
    """预处理文本：统一大小写、替换特殊字符、去除多余空格。"""
    if not text:
        return ""
    text = text.lower()
    # 字符归一化映射表：特殊符号、重音字母、OCR 易混淆字符 → ASCII/统一形式
    _NORMALIZE_MAP = str.maketrans({
        "·": " ", "'": "'", "'": "'", '"': "'",
        "—": "-", "–": "-",
        "0": "o",  # OCR O/0 混淆修复
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "ç": "c",
        "ü": "u",
        "ö": "o", "ä": "a",
        "ñ": "n",
        "ã": "a", "õ": "o", "â": "a", "ô": "o",
        "î": "i", "û": "u", "ï": "i", "ÿ": "y",
        "á": "a", "ó": "o", "ú": "u", "í": "i",
        "à": "a", "ò": "o", "ù": "u",
    })
    text = text.translate(_NORMALIZE_MAP)
    # 将括号、连字符等替换为空格，便于分词匹配
    for ch in "()[]{}<>":
        text = text.replace(ch, " ")
    for ch in "-_/\\":
        text = text.replace(ch, " ")
    # 去除多余空格
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_core_tokens(text: str) -> set[str]:
    """提取文本中的核心词元（长度>=2的字母数字串），用于关键词索引。"""
    text = normalize(text)
    # 保留字母、数字、空格，其余丢弃
    text = re.sub(r"[^a-z0-9\s]", "", text)
    tokens = set()
    for token in text.split():
        token = token.strip()
        if len(token) >= 2:
            tokens.add(token)
    return tokens


class MatchModule:
    """
    本地车型三级匹配模块。

    匹配策略：
    1. 精确匹配（归一化后完全相等）
    2. 关键词索引匹配（基于品牌+核心型号词元）
    3. 模糊匹配（rapidfuzz token_set_ratio 兜底）

    歧义控制：
    - 若模糊匹配 top1 与 top2 分差 < ambiguity_margin，视为歧义，返回 None。
    - 若仅通过 model_only 命中且分数 < model_only_safe_threshold，检查是否存在多
      个相似型号（如 M3 的多代际），存在则视为歧义。
    """

    def __init__(
        self,
        jsonl_path: str,
        match_threshold: int,
        ambiguity_margin: int,
        model_only_safe_threshold: int,
    ):
        self.jsonl_path = Path(jsonl_path)
        self.match_threshold = match_threshold
        self.ambiguity_margin = ambiguity_margin
        self.model_only_safe_threshold = model_only_safe_threshold

        self.entries: list[dict] = []
        # 归一化后的匹配文本
        self.full_names_cn: list[str] = []   # 中文品牌 + 型号
        self.full_names_en: list[str] = []   # 英文品牌 + 型号
        self.models_only: list[str] = []     # 仅型号
        # 关键词索引
        self.brand_cn_map: dict[str, list[int]] = {}
        self.brand_en_map: dict[str, list[int]] = {}
        self.token_index: dict[str, list[int]] = {}  # 核心词元 -> 索引列表

        self._load_data()

    def _load_data(self):
        """加载 cars_info.jsonl 并构建索引。"""
        seen = set()
        idx = 0
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                model = data.get("c", "")
                manu_cn = data.get("m_cn", "")
                manu_en = data.get("m", "")

                if not model:
                    continue

                key = (manu_en, model)
                if key in seen:
                    continue
                seen.add(key)

                entry = {
                    "manufacturer_cn": manu_cn,
                    "manufacturer_en": manu_en,
                    "model": model,
                    "info": data.get("i", ""),
                }
                self.entries.append(entry)

                fcn = normalize(f"{manu_cn} {model}")
                fen = normalize(f"{manu_en} {model}")
                mo = normalize(model)

                self.full_names_cn.append(fcn)
                self.full_names_en.append(fen)
                self.models_only.append(mo)

                # 品牌索引
                if manu_cn:
                    ncn = normalize(manu_cn)
                    self.brand_cn_map.setdefault(ncn, []).append(idx)
                if manu_en:
                    nen = normalize(manu_en)
                    self.brand_en_map.setdefault(nen, []).append(idx)

                # 核心词元索引（仅针对 model 字段）
                tokens = extract_core_tokens(model)
                for token in tokens:
                    self.token_index.setdefault(token, []).append(idx)

                idx += 1

        log(TAG, f"已加载 {len(self.entries)} 条车型数据")

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def match(self, query: str) -> dict | None:
        """
        对 OCR 识别出的 query 进行本地匹配。
        返回 dict 表示成功匹配，返回 None 表示未命中或存在歧义（应走 API）。
        """
        if not query or not query.strip():
            return None

        q = normalize(query)

        # ---------- Level 1: 精确匹配 ----------
        exact = self._level1_exact(q)
        if exact is not None:
            return exact

        # ---------- Level 2: 关键词索引匹配 ----------
        keyword = self._level2_keyword(q)
        if keyword is not None:
            return keyword

        # ---------- Level 3: 模糊匹配 ----------
        fuzzy = self._level3_fuzzy(q)
        if fuzzy is not None:
            return fuzzy

        return None

    # ------------------------------------------------------------------
    # 内部匹配层级
    # ------------------------------------------------------------------
    def _level1_exact(self, q: str) -> dict | None:
        """归一化后完全相等。"""
        for i, (fcn, fen, mo) in enumerate(
            zip(self.full_names_cn, self.full_names_en, self.models_only)
        ):
            if q == fcn or q == fen or q == mo:
                return self._make_result(i, 100, "exact")
        return None

    def _level2_keyword(self, q: str) -> dict | None:
        """
        基于关键词索引的快速筛选。
        思路：从 query 中提取品牌词，缩小候选范围；再在候选范围内检查
        model 核心词元重叠度。
        """
        q_tokens = extract_core_tokens(q)
        if not q_tokens:
            return None

        # 1) 尝试识别品牌，缩小候选集
        candidate_set: set[int] | None = None

        for brand_cn, indices in self.brand_cn_map.items():
            if brand_cn in q or any(t in brand_cn.split() for t in q_tokens):
                candidate_set = set(indices) if candidate_set is None else candidate_set & set(indices)
                # 如果候选集已经很小，直接跳出
                if candidate_set and len(candidate_set) <= 3:
                    break

        for brand_en, indices in self.brand_en_map.items():
            if brand_en in q or any(t in brand_en.split() for t in q_tokens):
                candidate_set = set(indices) if candidate_set is None else candidate_set | set(indices)

        # 2) 若品牌未识别，则退而使用 model 核心词元索引
        if candidate_set is None:
            for token in q_tokens:
                if token in self.token_index:
                    if candidate_set is None:
                        candidate_set = set(self.token_index[token])
                    else:
                        candidate_set &= set(self.token_index[token])
                        if not candidate_set:
                            break

        if not candidate_set:
            return None

        # 3) 在候选集中计算重叠度，选出最佳匹配
        best_idx = -1
        best_score = 0
        for i in candidate_set:
            entry_tokens = extract_core_tokens(self.entries[i]["model"])
            if not entry_tokens:
                continue
            # 重叠比例 = 交集 / query_tokens
            intersection = q_tokens & entry_tokens
            score = int(len(intersection) / max(len(q_tokens), 1) * 100)
            # 额外加分：如果所有 query 中的数字串都能在候选 model 中找到
            q_numbers = {t for t in q_tokens if t.isdigit()}
            e_numbers = {t for t in entry_tokens if t.isdigit()}
            if q_numbers and q_numbers <= e_numbers:
                score += 10
            if score > best_score:
                best_score = score
                best_idx = i

        # 关键词匹配要求较高（90+），避免误配
        if best_idx >= 0 and best_score >= 90:
            # 检查歧义：候选集中是否有同样高分的条目
            rivals = [
                i for i in candidate_set
                if i != best_idx
                and int(len(q_tokens & extract_core_tokens(self.entries[i]["model"]))
                        / max(len(q_tokens), 1) * 100)
                >= best_score - self.ambiguity_margin
            ]
            if rivals:
                log(TAG, f"关键词匹配存在歧义: top1_score={best_score}")
                return None
            return self._make_result(best_idx, best_score, "keyword")

        return None

    def _level3_fuzzy(self, q: str) -> dict | None:
        """
        rapidfuzz 模糊匹配兜底。
        分别匹配 full_name_en、full_name_cn、model_only，取最佳结果。
        使用 extract(limit=3) 以捕获同类型歧义（如 "BMW M3" 同时匹配多条 M3）。
        """
        candidates: list[tuple[int, int, str]] = []  # (idx, score, type)

        if self.full_names_en:
            results = process.extract(
                q, self.full_names_en,
                scorer=fuzz.token_set_ratio,
                score_cutoff=self.match_threshold,
                limit=3,
            )
            for _, score, idx in results:
                candidates.append((idx, score, "full_en"))

        if self.full_names_cn:
            results = process.extract(
                q, self.full_names_cn,
                scorer=fuzz.token_set_ratio,
                score_cutoff=self.match_threshold,
                limit=3,
            )
            for _, score, idx in results:
                candidates.append((idx, score, "full_cn"))

        if self.models_only:
            results = process.extract(
                q, self.models_only,
                scorer=fuzz.token_set_ratio,
                score_cutoff=self.match_threshold,
                limit=3,
            )
            for _, score, idx in results:
                candidates.append((idx, score, "model"))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[1], reverse=True)
        best_idx, best_score, best_type = candidates[0]

        # --- 歧义检测 1：top1 vs top2 分差（不同索引） ---
        rivals = [
            c for c in candidates[1:]
            if c[0] != best_idx and best_score - c[1] < self.ambiguity_margin
        ]
        if rivals:
            log(TAG, f"模糊匹配歧义: top1={best_score}({best_type}), rivals={[(r[1], r[2]) for r in rivals]}")
            return None

        # --- 歧义检测 2：model_only 低分时的同型号多代际问题 ---
        if best_type == "model" and best_score < self.model_only_safe_threshold:
            matched_model = self.models_only[best_idx]
            similar_count = sum(
                1 for m in self.models_only
                if m == matched_model or fuzz.ratio(m, matched_model) > 90
            )
            if similar_count > 1:
                log(TAG, f"model_only 低分歧义: score={best_score}, 相似型号共 {similar_count} 条")
                return None

        return self._make_result(best_idx, best_score, best_type)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _make_result(self, idx: int, score: int, match_type: str) -> dict:
        entry = self.entries[idx]
        return {
            "matched": True,
            "score": score,
            "match_type": match_type,
            "model": entry["model"],
            "manufacturer_en": entry["manufacturer_en"],
            "manufacturer_cn": entry["manufacturer_cn"],
            "info": entry["info"],
        }
