import os
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageDraw
import win32gui
import win32api
import win32ui
import win32con


class CaptureModule:
    """
    基于 win32gui 的全屏捕获模块。
    采用方案 B：通过高亮边框颜色特征定位当前选中的车辆卡片，
    并裁剪卡片顶部区域供 OCR 使用。
    """

    def __init__(self, config: dict):
        self._debug_dir = os.path.dirname(os.path.abspath(__file__))
        self.debug_enabled = config.get("debug", {}).get("enabled", False)

        config = config.get("capture")
        self.highlight_ranges = config.get("highlight_color_range")
        self.min_width = config.get("min_box_width")
        self.min_height = config.get("min_box_height")
        self.crop_top_ratio = config.get("crop_top_ratio")

    def _debug_save(self, img, label: str):
        """调试用：将图像以 foo.yy_mm_dd_hh_mm_ss.png 命名保存到脚本目录。"""
        if not self.debug_enabled:
            return
        ts = datetime.now().strftime("%y_%m_%d_%H_%M_%S")
        path = os.path.join(self._debug_dir, f"foo.{ts}.{label}.png")
        img.save(path)

    def capture_screen(self) -> Image.Image:
        """截取整个屏幕并返回 PIL RGB 图像。"""
        hwnd = win32gui.GetDesktopWindow()
        width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
        height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
        left = win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN)
        top = win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN)

        hdesktop = win32gui.GetWindowDC(hwnd)
        desktop_dc = win32ui.CreateDCFromHandle(hdesktop)
        mem_dc = desktop_dc.CreateCompatibleDC()

        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(desktop_dc, width, height)
        mem_dc.SelectObject(bmp)
        mem_dc.BitBlt((0, 0), (width, height), desktop_dc, (left, top), win32con.SRCCOPY)

        bmp_info = bmp.GetInfo()
        bmp_str = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            'RGB',
            (bmp_info['bmWidth'], bmp_info['bmHeight']),
            bmp_str, 'raw', 'BGRX', 0, 1
        )

        mem_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdesktop)
        win32gui.DeleteObject(bmp.GetHandle())
        self._debug_save(img, "screen")
        return img

    def locate_card(self, img: Image.Image) -> tuple:
        """
        在图像中通过高亮边框颜色定位选中卡片。
        采用连通域分析 + 轮廓形状验证，排除车身同色区域、
        左侧面板及零散噪点的干扰。
        :return: (cropped_image, bbox) 或 (None, None)
                 bbox 为 (left, top, right, bottom)
        """
        arr = np.array(img)
        mask = np.zeros(arr.shape[:2], dtype=bool)

        # 1. 颜色掩码：保留现有阈值逻辑
        for name, rng in self.highlight_ranges.items():
            r_min = rng.get("r_min", 0)
            r_max = rng.get("r_max", 255)
            g_min = rng.get("g_min", 0)
            g_max = rng.get("g_max", 255)
            b_min = rng.get("b_min", 0)
            b_max = rng.get("b_max", 255)
            m = (
                (arr[:, :, 0] >= r_min) & (arr[:, :, 0] <= r_max) &
                (arr[:, :, 1] >= g_min) & (arr[:, :, 1] <= g_max) &
                (arr[:, :, 2] >= b_min) & (arr[:, :, 2] <= b_max)
            )
            mask |= m

        # 2. 形态学闭运算：修复断裂的细边框
        binary = (mask * 255).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

        # 3. 查找外轮廓（只取最外层）
        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        candidates = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            aspect = w / h if h > 0 else 0

            # 4.1 面积过滤：排除零散噪点
            if area < self.min_width * self.min_height:
                continue

            # 4.2 宽高比过滤：排除左侧面板、菜单栏、弹幕等
            if not (0.7 <= aspect <= 1.4):
                continue

            # 4.3 多边形逼近：验证是否为四边形（矩形边框）
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.05 * peri, True)
            if len(approx) < 4:
                continue

            candidates.append((x, y, x + w, y + h))

        if not candidates:
            return None, None

        # 5. 取面积最大的候选框作为选中卡片
        best = max(candidates, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        left, top, right, bottom = best

        crop_bottom = top + int((bottom - top) * self.crop_top_ratio)
        cropped = img.crop((left, top, right, crop_bottom))
        self._debug_save(cropped, "card")
        return cropped, best

    def capture_card_top(self) -> tuple:
        """
        截屏并提取选中卡片顶部文字区域。
        :return: (cropped_image, bbox) 或 (None, None)
        """
        screen = self.capture_screen()
        return self.locate_card(screen)
