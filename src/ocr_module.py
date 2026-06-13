import numpy as np
from PIL import Image

from paddleocr import PaddleOCR
from utils import log

CONFIDENCE_THRESHOLD = 0.8
TAG = "OCRMODULE"

class OCRModule:
    """
    基于 PaddleOCR 的文字识别模块。
    默认使用 PP-OCRv5 模型（首次运行自动下载），
    可通过配置指定本地 PP-OCRv5 模型路径。
    """

    def __init__(self, config: dict = None):

        config = config or {}
        log(TAG, "正在初始化 PaddleOCR...")

        self.ocr = PaddleOCR(
            lang='ch',
            text_detection_model_name="PP-OCRv5_mobile_det",
            text_recognition_model_name="PP-OCRv5_mobile_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            # 支持自定义 PP-OCRv5 模型路径
            det_model_dir=config.get("det_model_dir", None),
            rec_model_dir=config.get("rec_model_dir", None),
        ) # Switch to PP-OCRv5_mobile models
        log(TAG, "PaddleOCR 初始化完成")

    def recognize(self, img: Image.Image) -> str:
        """
        对输入图像进行 OCR 识别。
        :param img: PIL Image (RGB)
        :return: 拼接后的文本字符串
        """

        if img is None:
            return ""

        arr = np.array(img)
        ocr_res = self.ocr.ocr(arr)

        if not ocr_res or not ocr_res[0]:
            log(TAG, "未识别到文本")
            return ""

        result = ""
        # 过滤低置信度
        for res in ocr_res:
            filtered = [
                {
                    "text": res["rec_texts"][i],
                    "confidence": round(res["rec_scores"][i], 4),
                } for i in range(len(res["rec_texts"])) if res["rec_scores"][i] >= CONFIDENCE_THRESHOLD
            ]
            result += " " + " ".join([item["text"] for item in filtered])
        log(TAG, f"ocr 识别结果: {result}")
        return result
