from PyQt5.QtWidgets import QWidget, QVBoxLayout, QApplication
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QFont, QFontMetrics
import sys
import ctypes

from utils import log
TAG = "OVERLAYMODULE"

class ScrollTextWidget(QWidget):
    """
    自定义绘制控件，使用 QPainter 实现无限垂直滚动文本。
    """

    def __init__(self, text_color="#EEEEEE", font_size=14, parent=None):
        super().__init__(parent)
        self._text = ""
        self._text_color = QColor(text_color)
        self._font = QFont("Microsoft YaHei", font_size)
        self._scroll_offset = 0.0
        self._content_height = 0
        self._line_spacing = 8
        self._padding = 20
        self._wrapped_lines = []

    def set_text(self, text: str, target_width=None):
        self._text = text
        self._scroll_offset = 0.0
        self._wrap_text(target_width=target_width)
        self.update()

    def _wrap_text(self, target_width=None):
        """将文本按控件宽度自动换行，生成行列表。"""
        fm = QFontMetrics(self._font)
        w = target_width if target_width is not None else self.width()
        max_width = w - self._padding * 2
        if max_width <= 0:
            max_width = 400

        self._wrapped_lines = []
        for paragraph in self._text.split("\n"):
            if not paragraph:
                self._wrapped_lines.append("")
                continue
            line = ""
            for ch in paragraph:
                test_line = line + ch
                if fm.horizontalAdvance(test_line) > max_width:
                    if line:
                        self._wrapped_lines.append(line)
                    line = ch
                else:
                    line = test_line
            if line:
                self._wrapped_lines.append(line)

        line_height = fm.height() + self._line_spacing
        self._content_height = len(self._wrapped_lines) * line_height

    def scroll_step(self, step: int):
        """滚动指定步长（像素）。"""
        if self._content_height <= 0:
            return
        self._scroll_offset += step
        cycle = self._content_height + (QFontMetrics(self._font).height() + self._line_spacing) * 2
        if cycle > 0:
            self._scroll_offset %= cycle
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._text:
            self._wrap_text(target_width=self.width())

    def paintEvent(self, event):
        if not self._wrapped_lines:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setFont(self._font)
        painter.setPen(self._text_color)

        fm = QFontMetrics(self._font)
        line_height = fm.height() + self._line_spacing
        visible_height = self.height()

        gap = line_height * 2
        cycle = self._content_height + gap

        if cycle <= 0:
            return

        need_scroll = self._content_height > visible_height
        copies = 1 if not need_scroll else (visible_height // cycle) + 2
        for copy in range(copies):
            y_base = -self._scroll_offset + copy * cycle + self._padding
            for i, line in enumerate(self._wrapped_lines):
                y = y_base + i * line_height + fm.ascent()
                if y < -line_height:
                    continue
                if y > visible_height + line_height:
                    break
                painter.drawText(self._padding, int(y), line)


class OverlayModule(QWidget):
    """
    基于 PyQt5 的悬浮窗展示模块。
    无边框、置顶、毛玻璃背景，显示车辆介绍文本，支持无限垂直滚动。
    """

    def __init__(self, config: dict):
        super().__init__()
        self.win_width = config.get("width")
        self.win_max_height = config.get("max_height")
        self.opacity = config.get("opacity")
        self.font_size = config.get("font_size")
        self.scroll_speed = config.get("scroll_speed")
        self.scroll_interval = config.get("scroll_interval")
        self.scroll_delay = config.get("scroll_delay")
        self.bg_color = config.get("bg_color")
        self.text_color = config.get("text_color")
        self.err_win_pos = config.get("err_win_pos")

        self._last_bbox = None

        self._init_ui()

    def _init_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.win_width, self.win_max_height)

        self.scroll_widget = ScrollTextWidget(
            text_color=self.text_color,
            font_size=self.font_size,
            parent=self
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scroll_widget)

        self._scroll_timer = None
        self._scroll_delay_timer = None

    def _cleanup_timers(self):
        """停止并销毁现有滚动定时器。"""
        for attr in ("_scroll_timer", "_scroll_delay_timer"):
            timer = getattr(self, attr, None)
            if timer is not None:
                timer.stop()
                timer.deleteLater()
                setattr(self, attr, None)

    def _recalc_height(self):
        """根据内容自适应窗口高度。返回 (actual_height, need_scroll)。"""
        padding = self.scroll_widget._padding * 2
        content_height = self.scroll_widget._content_height
        actual_height = min(content_height + padding, self.win_max_height)
        self.setFixedSize(self.win_width, actual_height)
        self.scroll_widget.resize(self.width(), self.height())

        need_scroll = actual_height >= self.win_max_height
        return actual_height, need_scroll

    def _ensure_scrolling(self, need_scroll):
        """若需要滚动，启动延迟滚动定时器。"""
        if not need_scroll:
            return

        def _start_scrolling():
            self._scroll_timer = QTimer(self)
            self._scroll_timer.timeout.connect(
                lambda: self.scroll_widget.scroll_step(self.scroll_speed)
            )
            self._scroll_timer.start(self.scroll_interval)

        self._scroll_delay_timer = QTimer(self)
        self._scroll_delay_timer.setSingleShot(True)
        self._scroll_delay_timer.timeout.connect(_start_scrolling)
        self._scroll_delay_timer.start(self.scroll_delay)

    def show_text(self, text: str, bbox):
        """更新文本并显示悬浮窗。若提供 bbox，则悬浮窗显示在卡片旁边。"""
        self._cleanup_timers()

        self.scroll_widget.resize(self.width(), self.height())
        self.scroll_widget.set_text(text, target_width=self.width())

        _, need_scroll = self._recalc_height()
        self._ensure_scrolling(need_scroll)

        if bbox:
            self._last_bbox = bbox
        self._move_to_position(bbox)
        self.show()
        self.raise_()
        self.activateWindow()
        self._enable_blur()

    def update_text(self, text: str):
        """
        更新悬浮窗文本内容。
        用于在已显示的悬浮窗上更新内容（如从"加载中..."更新为实际信息）。
        """
        self._cleanup_timers()

        self.scroll_widget.set_text(text, target_width=self.width())

        _, need_scroll = self._recalc_height()
        self._ensure_scrolling(need_scroll)

        if self._last_bbox:
            # 更新位置
            self._move_to_position(self._last_bbox)
        self.raise_()
        self.activateWindow()

    def hide_overlay(self):
        """隐藏悬浮窗。"""
        self.hide()
        if self._scroll_timer is not None:
            self._scroll_timer.stop()
        if self._scroll_delay_timer is not None:
            self._scroll_delay_timer.stop()

    def _enable_blur(self):
        """
        为窗口启用 Acrylic 背景模糊效果（仅 Windows 10+）。
        若启用失败，回退到 Qt 半透明背景。
        """
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32

            class ACCENT_POLICY(ctypes.Structure):
                _fields_ = [
                    ("AccentState", ctypes.c_uint),
                    ("AccentFlags", ctypes.c_uint),
                    ("GradientColor", ctypes.c_uint),
                    ("AnimationId", ctypes.c_uint),
                ]

            class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
                _fields_ = [
                    ("Attribute", ctypes.c_int),
                    ("Data", ctypes.c_void_p),
                    ("SizeOfData", ctypes.c_size_t),
                ]

            WCA_ACCENT_POLICY = 19
            ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

            SetWindowCompositionAttribute = user32.SetWindowCompositionAttribute
            SetWindowCompositionAttribute.argtypes = [
                ctypes.c_int,
                ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA),
            ]
            SetWindowCompositionAttribute.restype = ctypes.c_bool

            accent = ACCENT_POLICY()
            accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
            accent.AccentFlags = 0
            alpha = int(self.opacity * 255)
            accent.GradientColor = (alpha << 24) | 0x000000

            data = WINDOWCOMPOSITIONATTRIBDATA()
            data.Attribute = WCA_ACCENT_POLICY
            data.Data = ctypes.cast(ctypes.byref(accent), ctypes.c_void_p)
            data.SizeOfData = ctypes.sizeof(accent)

            ok = SetWindowCompositionAttribute(hwnd, ctypes.byref(data))
            if not ok:
                self.setStyleSheet(
                    f"background-color: {self.bg_color}; border-radius: 8px;"
                )
        except Exception as e:
            log(TAG, f"启用背景模糊失败: {e}")

    def _move_to_position(self, bbox=None):
        screen = QApplication.primaryScreen()
        geo = screen.geometry()
        dpr = screen.devicePixelRatio()

        if bbox:
            card_left, card_top, card_right, card_bottom = bbox
            card_left = int(card_left / dpr)
            card_top = int(card_top / dpr)
            card_right = int(card_right / dpr)
            card_bottom = int(card_bottom / dpr)

            x = card_right + 5
            # 默认顶部对齐；若底部超出屏幕则 fallback 到底部对齐
            y = card_top
            if y + self.height() > geo.height():
                y = card_bottom - self.height()
                if y < 0:
                    y = max(0, geo.height() - self.height())
        else:
            if self.err_win_pos == "right-center":
                x = geo.width() - self.width() - 40
                y = (geo.height() - self.height()) // 2
            else:
                x = (geo.width() - self.width()) // 2
                y = (geo.height() - self.height()) // 2

        self.move(x, y)


def get_qapp():
    """获取全局 QApplication 实例，若不存在则创建。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app
