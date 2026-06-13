"""
公共工具函数模块

提供统一的日志输出、配置加载等工具功能。
"""

from datetime import datetime


def log(module_name: str, message: str):
    """
    统一日志输出函数。

    格式：[HH:MM:SS.mmm] [ModuleName] message

    Args:
        module_name: 模块名称（如 'Controller', 'InputModule' 等）
        message: 日志消息内容
    """
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] [{module_name}] {message}")
