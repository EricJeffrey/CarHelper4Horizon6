from utils import log
import json
import sys
import threading

TAG = "CONTROLLER"

class Controller:
    """
    主控制模块。
    状态机：IDLE -> CAPTURING -> FETCHING -> DISPLAYING -> IDLE
    耗时操作（截屏/OCR/API）均在后台线程执行，UI 更新通过 _GuiBridge 信号切回主线程。
    """

    def __init__(self, config: dict):
        log(TAG, "程序初始化中...")
        self.config = config

        from overlay_module import OverlayModule, get_qapp
        self.app = get_qapp()
        self.lock = threading.Lock()
        self.state = "IDLE"

        log(TAG, "初始化输入模块...")
        from input_module import InputModule
        self.input_module = InputModule(
            trigger_button=config.get("input").get("trigger_button"),
            trigger_key=config.get("input").get("trigger_key"),
            poll_interval=config.get("input").get("poll_interval")
        )

        log(TAG, "初始化截屏模块...")
        from capture_module import CaptureModule
        self.capture_module = CaptureModule(config)

        log(TAG, "初始化OCR模块...")
        from ocr_module import OCRModule
        self.ocr_module = OCRModule(config.get("ocr", {}))

        log(TAG, "初始化API模块...")
        from api_module import APIModule
        self.api_module = APIModule(config["api"])

        log(TAG, "初始化悬浮窗模块...")
        from overlay_module import OverlayModule
        self.overlay_module = OverlayModule(config.get("overlay", {}))

        log(TAG, "初始化匹配模块...")
        match_cfg = config.get("match", {})
        from match_module import MatchModule
        self.match_module = MatchModule(
            jsonl_path=match_cfg.get("data_path"),
            match_threshold=match_cfg.get("match_threshold"),

            ambiguity_margin=match_cfg.get("ambiguity_margin"),
            model_only_safe_threshold=match_cfg.get("model_only_safe_threshold"),
        )

        # 跨线程 GUI 桥接——所有 GUI 操作必须通过信号在主线程执行（必须在各 module 初始化之后）
        log(TAG, "初始化 GUI 桥接...")
        from gui_bridge import GuiBridge
        self._bridge = GuiBridge()
        self._bridge.show_overlay.connect(self._on_show_overlay)
        self._bridge.update_overlay.connect(self._on_update_overlay)
        self._bridge.hide_overlay.connect(self.overlay_module.hide_overlay)
        self._bridge.quit_app.connect(self.app.quit)

        self.input_module.on_trigger = self._on_trigger
        self.input_module.on_any_press = self._on_any_press

    def init(self) -> bool:
        """初始化所有模块和子进程。"""
        self.input_module.init_keyboard_listener()

    # ---- 主线程槽函数 ----

    def _on_show_overlay(self, text: str, bbox):
        """由 _GuiBridge.show_overlay 信号触发，保证在主线程执行。"""
        log(TAG, f"显示悬浮窗(主线程), 文本预览: {text[:10]}..., bbox={bbox}")
        self.overlay_module.show_text(text, bbox)

    def _on_update_overlay(self, text: str):
        """由 _GuiBridge.update_overlay 信号触发，保证在主线程执行。"""
        log(TAG, f"更新悬浮窗内容(主线程), 文本预览: {text[:10]}...")
        self.overlay_module.update_text(text)

    # ---- 输入回调（主线程）----

    def _on_trigger(self):
        with self.lock:
            if self.state == "DISPLAYING":
                # 再次按 i 关闭悬浮窗
                self.state = "IDLE"
                log(TAG, "状态: IDLE (再次按 i 关闭)")
                log(TAG, "再次按 i 关闭悬浮窗")
                self._bridge.hide_overlay.emit()
                return
            if self.state != "IDLE":
                return
            self.state = "CAPTURING"
        log(TAG, "状态: CAPTURING")
        t = threading.Thread(target=self._do_capture_ocr, daemon=True, name="capture-ocr")
        t.start()

    def _on_any_press(self, btn):
        with self.lock:
            if self.state == "DISPLAYING":
                self.state = "IDLE"
                log(TAG, "状态: IDLE (按键关闭)")
                log(TAG, f"按键关闭悬浮窗, btn={btn}")
                self._bridge.hide_overlay.emit()

    # ---- 后台线程 ----

    def _do_capture_ocr(self):
        try:
            cropped, bbox = self.capture_module.capture_card_top()
            if cropped is None:
                self._show_error("未能识别车辆，请确认画面中有高亮选中的卡片")
                return

            # 先显示"加载中..."悬浮窗
            self._bridge.show_overlay.emit("加载中...", bbox)

            vehicle_name = self.ocr_module.recognize(cropped)
            if not vehicle_name or not vehicle_name.strip():
                self._show_error("OCR 未识别到车辆名称")
                return

            with self.lock:
                self.state = "FETCHING"
            log(TAG, f"状态: FETCHING, 车型: {vehicle_name}")

            t = threading.Thread(target=self._do_fetch, args=(vehicle_name, bbox), daemon=True, name="fetch")
            t.start()
        except Exception as e:
            self._show_error(f"识别异常: {e}")

    def _do_fetch(self, vehicle_name: str, bbox):
        try:
            match_result = self.match_module.match(vehicle_name)
            if match_result:
                text = match_result["info"]
                title = f"{match_result['manufacturer_en']} {match_result['model']}"
                # 标题与正文拼接，整体一起滚动
                display_text = f"{title}\n\n{text}"
                log(TAG, f"本地匹配成功: {match_result['manufacturer_en']} {match_result['model']} (score={match_result['score']}, type={match_result['match_type']})")
            else:
                if self.config.get("api").get("enable", False):
                    log(TAG, f"本地未匹配到车型，调用 API: {vehicle_name}")
                    api_result = self.api_module.query(vehicle_name)
                    display_text = f"From AI: {vehicle_name}\n{api_result}"
                else:
                    log(TAG, f"本地未匹配到车型，API 已禁用: {vehicle_name}")
                    display_text = f"未找到匹配: {vehicle_name}"

            with self.lock:
                if self.state == "FETCHING":
                    self.state = "DISPLAYING"
                    log(TAG, "状态: DISPLAYING")

            # 更新悬浮窗内容（而不是重新显示）
            self._bridge.update_overlay.emit(display_text)
            if self.config.get("debug").get("enabled"):
                import time
                time.sleep(3)
                self.capture_module.capture_screen()
        except Exception as e:
            self._show_error(f"API 异常: {e}")

    def _show_error(self, msg: str):
        from PyQt5.QtCore import QTimer
        with self.lock:
            self.state = "DISPLAYING"
        log(TAG, f"发生错误: {msg[:50]}...")
        self._bridge.show_overlay.emit(msg, None)
        QTimer.singleShot(3000, self._reset_to_idle)

    def _reset_to_idle(self):
        with self.lock:
            self.state = "IDLE"
        log(TAG, "定时器关闭悬浮窗")
        self._bridge.hide_overlay.emit()

    # ---- 运行 ----

    def run(self):
        log(TAG, "开启键盘输入监听")
        self.input_module.init_keyboard_listener()

        log(TAG, "开启手柄输入监听")
        self.input_module.start_joystick_listener()
        log(TAG, "车辆介绍助手已启动，按手柄触发键开始识别，Ctrl+C 退出")

        # Windows 上使用 SetConsoleCtrlHandler 捕获 Ctrl+C
        try:
            import ctypes
            import ctypes.wintypes

            handler_routine = ctypes.WINFUNCTYPE(
                ctypes.wintypes.BOOL,
                ctypes.wintypes.DWORD,
            )
            _handler_ref = [None]

            def _ctrl_handler(ctrl_type):
                if ctrl_type == 0:  # CTRL_C_EVENT
                    log(TAG, "收到 Ctrl+C，正在退出...")
                    self._bridge.quit_app.emit()
                    return True
                return False

            _handler_ref[0] = handler_routine(_ctrl_handler)
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleCtrlHandler(_handler_ref[0], True)
        except Exception:
            pass  # 非 Windows 环境忽略

        sys.exit(self.app.exec_())


def main():
    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
    controller = Controller(config)
    controller.run()


if __name__ == "__main__":
    main()
