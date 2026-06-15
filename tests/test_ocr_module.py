import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from PIL import Image
from rapidfuzz import fuzz

from ocr_module import OCRModule

TEST_CASE_PATH = Path(__file__).parent / "testcases" / "ocr_case.jsonl"
TEST_CASE_DIR = Path(__file__).parent / "testcases"
SIMILARITY_THRESHOLD = 70

ocr_engine = OCRModule()

def load_test_cases():
    """从 JSONL 文件加载测试例。"""
    cases = []
    with open(TEST_CASE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            cases.append(json.loads(line))
    return cases


def pytest_generate_tests(metafunc):
    """参数化测试：为每条测试例生成独立的测试用例。"""
    if "test_case" in metafunc.fixturenames:
        cases = load_test_cases()
        ids = [f"L{i+1}:{c['t'][:30]}" for i, c in enumerate(cases)]
        metafunc.parametrize("test_case", cases, ids=ids)


class TestOcrFromJsonl:
    """从 ocr_case.jsonl 读取测试例，验证 OCR 识别结果。"""

    def test_ocr_result(self, test_case):
        """验证 OCR 对每张图片的识别结果与预期文本的相似度。"""
        img_path = TEST_CASE_DIR / test_case["t"]
        expected = test_case["r"]

        assert img_path.exists(), f"测试图片不存在: {img_path}"

        img = Image.open(img_path).convert("RGB")
        import time
        start_time = time.time()
        result = ocr_engine.recognize(img)
        end_time = time.time()
        ocr_time_ms = (end_time - start_time) * 1000
        print(f"OCR 识别耗时: {ocr_time_ms:.2f} ms")

        # 移除空格后进行模糊匹配，避免 OCR 空格差异导致失败
        result_no_space = result.replace(" ", "").replace("\n", "")
        expected_no_space = expected.replace(" ", "").replace("\n", "")

        score = fuzz.partial_ratio(expected_no_space, result_no_space)

        assert score >= SIMILARITY_THRESHOLD, (
            f"OCR 结果与预期不匹配: "
            f"expected={expected!r}, got={result!r}, "
            f"similarity={score}"
        )
