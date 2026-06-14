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
    # OCR O/0 混淆修复：仅将被字母包围的 0 替换为 o（如 FI0RANO → FIORANO），
    # 不替换数字中的 0（如 2020 不应变成 2o2o）
    text = re.sub(r"(?<=[a-z])0|0(?=[a-z])", "o", text)
    # 将括号、连字符等替换为空格，便于分词匹配
    for ch in "()[]{}<>":
        text = text.replace(ch, " ")
    for ch in "-_/\\":
        text = text.replace(ch, " ")
    # 字母-数字边界插入空格：E63S → E 63 S, 4RUNNER → 4 RUNNER
    text = re.sub(r"([a-z])(\d)", r"\1 \2", text)
    text = re.sub(r"(\d)([a-z])", r"\1 \2", text)
    # 去除多余空格
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# OCR 常见截断修正：缺失首字母或前缀
_OCR_PREFIX_FIXES = {
    "jhn": "john",
    "iulia": "giulia",
    "ontinental": "continental",
    "ventador": "aventador",
    "ancer": "lancer",
    "mited": "limited",
    "enterpe": "enterprises",
    "erra": "sierra",
}

# OCR 常见截断修正：缺失后缀
_OCR_SUFFIX_FIXES = {
    "ultin": "ultimae",
}

# 车型后缀中文→英文映射（非厂商名，而是车型版本标识）
_MODEL_SUFFIX_MAP = {
    "极限竞速特別版": "forza edition",
    "极限竞速特别版": "forza edition",
}


def _apply_ocr_fixes(text: str) -> list[str]:
    """对 OCR 文本应用常见截断修正，返回原始文本 + 所有可能的修正版本。"""
    variants = [text]
    words = text.split()
    for i, word in enumerate(words):
        # 前缀修正
        if word in _OCR_PREFIX_FIXES:
            fixed = _OCR_PREFIX_FIXES[word]
            new_words = words[:i] + [fixed] + words[i+1:]
            variants.append(" ".join(new_words))
        # 后缀修正
        if word in _OCR_SUFFIX_FIXES:
            fixed = _OCR_SUFFIX_FIXES[word]
            new_words = words[:i] + [fixed] + words[i+1:]
            variants.append(" ".join(new_words))
    return variants


def extract_core_tokens(text: str) -> set[str]:
    """提取文本中的核心词元（长度>=2的字母数字或中文字串），用于关键词索引。"""
    text = normalize(text)
    # 保留字母、数字、中文、空格，其余替换成空格（避免中英文粘连）
    text = re.sub(r"[^\u4e00-\u9fa5a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
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
        self.cn_to_en_map: dict[str, str] = {}       # 中文厂商/特例 -> 英文

        self._load_data()
        self._load_cn_map()

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

    def _load_cn_map(self):
        """加载中文厂商/车型特例映射表。"""
        map_path = self.jsonl_path.parent / "car_marker_map.json"
        if map_path.exists():
            with open(map_path, "r", encoding="utf-8") as f:
                self.cn_to_en_map = json.load(f)
            log(TAG, f"已加载 {len(self.cn_to_en_map)} 条中文映射")

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

        # 归一化 + 中文厂商替换
        q = normalize(query)
        for cn, en in sorted(self.cn_to_en_map.items(), key=lambda x: len(x[0]), reverse=True):
            q = q.replace(cn, f" {en} ")
        q = q.lower()
        # 车型后缀替换（在去中文之前，保留英文版本标识）
        for cn_suffix, en_suffix in _MODEL_SUFFIX_MAP.items():
            q = q.replace(cn_suffix, f" {en_suffix} ")
        q = re.sub(r"[\u4e00-\u9fa5]", "", q)
        q = re.sub(r"\s+", " ", q).strip()

        # 生成 OCR 修正变体（含原始）
        variants = _apply_ocr_fixes(q)

        # 对所有变体尝试匹配，取最佳结果
        best_result = None
        best_score = -1
        for variant in variants:
            result = self._match_normalized(variant)
            if result is not None and result["score"] > best_score:
                best_score = result["score"]
                best_result = result

        return best_result

    def _match_normalized(self, q: str) -> dict | None:
        """对已归一化的查询文本执行三级匹配。"""
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
            # 注意：rivals 的分数计算必须与 best_score 一致（含数字加分）
            rivals = []
            for i in candidate_set:
                if i == best_idx:
                    continue
                entry_tokens = extract_core_tokens(self.entries[i]["model"])
                if not entry_tokens:
                    continue
                intersection = q_tokens & entry_tokens
                score = int(len(intersection) / max(len(q_tokens), 1) * 100)
                q_numbers = {t for t in q_tokens if t.isdigit()}
                e_numbers = {t for t in entry_tokens if t.isdigit()}
                if q_numbers and q_numbers <= e_numbers:
                    score += 10
                if score >= best_score - self.ambiguity_margin:
                    rivals.append(i)
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

        # 引入额外 token 惩罚：候选比查询多出的有效 token 每个扣 2 分，
        # 避免子集关系导致标准版与扩展版（如 Forza Edition）同分进入歧义。
        # Forza Edition / TM Edition 等后缀额外惩罚，优先匹配基础版。
        _EDITION_PENALTY_TOKENS = {"forza", "edition", "tm"}
        q_tokens = set(q.split())
        adjusted = []
        for idx, score, ctype in candidates:
            text = (
                self.full_names_en[idx] if ctype == "full_en"
                else self.full_names_cn[idx] if ctype == "full_cn"
                else self.models_only[idx]
            )
            extra = {t for t in text.split() if t not in q_tokens and len(t) >= 2}
            penalty = len(extra) * 2
            # Edition 后缀额外惩罚
            edition_words = extra & _EDITION_PENALTY_TOKENS
            penalty += len(edition_words) * 5
            adjusted.append((idx, score - penalty, ctype))

        adjusted.sort(key=lambda x: x[1], reverse=True)
        best_idx, best_score, best_type = adjusted[0]

        # --- 歧义检测 1：top1 vs top2 分差（不同索引） ---
        rivals = [
            c for c in adjusted[1:]
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
