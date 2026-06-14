import json
import sys
from pathlib import Path

# 将 src 加入路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from match_module import MatchModule


DATA_PATH = Path(__file__).parent.parent / "resources" / "cars_info.jsonl"
TEST_CASE_PATH = Path(__file__).parent / "test_case_match.jsonl"


def load_test_cases():
    """从 JSONL 文件加载测试例。"""
    cases = []
    with open(TEST_CASE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            if data.get("m"):  # 跳过未匹配的空条目
                cases.append(data)
    return cases


@pytest.fixture(scope="module")
def matcher():
    return MatchModule(
        jsonl_path=str(DATA_PATH),
        match_threshold=85,
        ambiguity_margin=1,
        model_only_safe_threshold=95,
    )


@pytest.fixture(scope="module")
def test_cases():
    return load_test_cases()


def pytest_generate_tests(metafunc):
    """参数化测试：为每条测试例生成独立的测试用例。"""
    if "test_case" in metafunc.fixturenames:
        cases = load_test_cases()
        ids = [f"L{i+1}:{c['t'][:30]}" for i, c in enumerate(cases)]
        metafunc.parametrize("test_case", cases, ids=ids)


class TestMatchFromJsonl:
    """从 test_case_match.jsonl 读取测试例，验证 match_module 匹配结果。"""

    def test_match_result(self, matcher, test_case):
        """验证 match_module 对每条 OCR 文本的匹配结果与预期一致。"""
        query = test_case["t"]
        expected_m = test_case["m"]
        expected_m_cn = test_case["m_cn"]
        expected_c = test_case["c"]

        result = matcher.match(query)

        # 匹配不能返回 None
        assert result is not None, f"匹配失败（返回None）: query={query!r}"

        # 验证英文厂商名
        assert result["manufacturer_en"] == expected_m, (
            f"英文厂商不匹配: query={query!r}, "
            f"expected={expected_m!r}, got={result['manufacturer_en']!r}"
        )

        # 验证中文厂商名
        assert result["manufacturer_cn"] == expected_m_cn, (
            f"中文厂商不匹配: query={query!r}, "
            f"expected={expected_m_cn!r}, got={result['manufacturer_cn']!r}"
        )

        # 验证车型名
        assert result["model"] == expected_c, (
            f"车型不匹配: query={query!r}, "
            f"expected={expected_c!r}, got={result['model']!r}"
        )
