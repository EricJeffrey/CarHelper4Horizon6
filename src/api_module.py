import requests
import json

from utils import log
TAG = "APIMODULE"


class APIModule:
    """
    大模型 API 通信模块。
    Prompt 固定为：精简介绍一下{vehicle_name}
    """

    def __init__(self, config: dict):
        self.base_url = config.get("base_url", "")
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4o-mini")
        self.timeout = config.get("timeout", 10)
        self.prompt_template = config.get("prompt_template")

    def query(self, vehicle_name: str) -> str:
        """
        调用大模型 API 获取车辆简介。
        :param vehicle_name: 识别出的车辆名称
        :return: 介绍文本
        """
        prompt = self.prompt_template.format(vehicle_name=vehicle_name)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 8192
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()
            message = data["choices"][0]["message"]
            content = message.get("content", "") or message.get("reasoning_content", "")
            log(TAG, f"获取介绍成功，长度: {len(content)}")
            return content.strip()
        except requests.exceptions.Timeout:
            log(TAG, "请求超时")
            return "请求超时，请检查网络或 API 配置。"
        except Exception as e:
            log(TAG, f"请求异常: {e}")
            return f"获取介绍失败: {e}"
