"""
输入模块 - 基于多进程架构的手柄监听

架构说明：
- 手柄监听运行在独立子进程中（pygame 需要独立进程环境）
- 键盘监听使用 keyboard 库的全局钩子（主进程）
- 通过 multiprocessing.Queue 进行进程间通信
- 通过 multiprocessing.Event 控制子进程生命周期
"""

import threading
import os
import multiprocessing
import keyboard

from utils import log

TAG = "INPUTMODULE"

# SDL_GameControllerButton 按钮索引映射
# 参考: https://wiki.libsdl.org/SDL_GameControllerButton
BUTTON_MAP = {
    # SDL_CONTROLLER_BUTTON_A = 0
    0: 'A',
    # SDL_CONTROLLER_BUTTON_B = 1
    1: 'B',
    # SDL_CONTROLLER_BUTTON_X = 2
    2: 'X',
    # SDL_CONTROLLER_BUTTON_Y = 3
    3: 'Y',
    # SDL_CONTROLLER_BUTTON_BACK = 4
    4: 'BACK',
    # SDL_CONTROLLER_BUTTON_GUIDE = 5 (Home/Xbox按钮)
    5: 'GUIDE',
    # SDL_CONTROLLER_BUTTON_START = 6
    6: 'START',
    # SDL_CONTROLLER_BUTTON_LEFTSTICK = 7 (左摇杆按下)
    7: 'LEFTSTICK',
    # SDL_CONTROLLER_BUTTON_RIGHTSTICK = 8 (右摇杆按下)
    8: 'RIGHTSTICK',
    # SDL_CONTROLLER_BUTTON_LEFTSHOULDER = 9 (LB/L1)
    9: 'LEFTSHOULDER',
    # SDL_CONTROLLER_BUTTON_RIGHTSHOULDER = 10 (RB/R1)
    10: 'RIGHTSHOULDER',
    # SDL_CONTROLLER_BUTTON_DPAD_UP = 11
    11: 'DPAD_UP',
    # SDL_CONTROLLER_BUTTON_DPAD_DOWN = 12
    12: 'DPAD_DOWN',
    # SDL_CONTROLLER_BUTTON_DPAD_LEFT = 13
    13: 'DPAD_LEFT',
    # SDL_CONTROLLER_BUTTON_DPAD_RIGHT = 14
    14: 'DPAD_RIGHT',
}
BUTTON_IDX_MAP = {v: k for k, v in BUTTON_MAP.items()}

class InputModule:
    """
    输入监听模块，支持手柄（pygame 库，子进程）和键盘（keyboard 库，主进程）。

    架构：
    - 主进程：keyboard 全局钩子 + Queue 读取线程
    - 子进程：pygame 初始化 + joystick 轮询 + Queue 写入

    两者共用同一套 on_trigger / on_any_press 回调，互不冲突。

    注意：
    - 所有 pygame 相关操作都在子进程中执行，避免 SDL 线程安全问题
    - Ctrl+C 可正常退出，通过 Event 通知子进程优雅关闭
    """

    def __init__(self, trigger_button: str, trigger_key: str = 'i', poll_interval: float = 0.016):
        """
        :param trigger_button: 手柄触发识别流程的按键名称
            可选值参考 BUTTON_MAP 中的键值对。
        :param trigger_key: 键盘触发识别流程的按键（默认 'i'，不区分大小写）
        :param poll_interval: 轮询间隔（秒），用于降低 CPU 占用
        """
        self.trigger_button = trigger_button
        self.trigger_key = trigger_key.lower()
        self.poll_interval = poll_interval

        # 回调函数
        self.on_trigger = None
        self.on_any_press = None

        # 进程间通信对象（必须在 start() 之前创建）
        self._event_queue = multiprocessing.Queue()      # Queue: 子→主，传递事件
        self._stop_event = multiprocessing.Event()       # Event: 主→子，停止信号
        self._ready_event = multiprocessing.Event()      # Event: 子进程就绪信号
        self._process = None          # Process: 子进程对象
        self._reader_thread = None    # Thread: 主进程中读取 Queue 的线程

        self._running = False         # 控制读取循环的标志
        self._SENTINEL = object()     # 哨兵对象，用于唤醒阻塞的读取线程

    def init_keyboard_listener(self) -> bool:
        """初始化键盘监听 """
        # 初始化键盘全局监听（主进程）
        keyboard.hook(self._on_keyboard_event)
        log(TAG, f"键盘监听已注册，触发键: '{self.trigger_key}'")

        return True

    def start_joystick_listener(self):
        """启动手柄监听子进程和事件读取线程。"""
        # 启动子进程（pygame 在其中初始化和运行）
        self._process = multiprocessing.Process(
            target=_gamepad_worker,
            args=(
                self._event_queue,
                self._stop_event,
                self._ready_event,
                self.trigger_button,
                self.poll_interval
            ),
            daemon=True  # 守护进程，主进程退出时自动终止
        )
        self._process.start()
        log(TAG, f"手柄监听子进程已启动 (pid={self._process.pid})")
        self._ready_event.wait()
        log(TAG, f"手柄监听子进程已就绪 (pid={self._process.pid})")
        # 启动守护线程读取 Queue（在主进程中运行）
        self._running = True
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        log(TAG, f"事件读取线程已启动 (tid={self._reader_thread.ident})")

    def stop(self):
        """停止所有监听，包括子进程和读取线程。"""
        log(TAG, "正在停止监听...")

        # 1. 停止读取循环
        self._running = False

        # 2. 向 Queue 放入哨兵值，唤醒阻塞的读取线程
        if self._event_queue:
            try:
                self._event_queue.put(self._SENTINEL)
            except Exception:
                pass

        # 3. 通知子进程停止
        if self._stop_event:
            self._stop_event.set()

        # 4. 等待子进程结束（带超时，防止卡死）
        if self._process and self._process.is_alive():
            log(TAG, "等待子进程退出...")
            self._process.join(timeout=2.0)

            if self._process.is_alive():
                log(TAG, "子进程未响应，强制终止...")
                self._process.terminate()
                self._process.join(timeout=1.0)

        # 5. 等待读取线程结束（已通过哨兵值唤醒）
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)

        # 6. 清理 keyboard
        if keyboard:
            try:
                keyboard.unhook_all()
            except Exception:
                pass

        log(TAG, "监听已完全停止")

    def _read_loop(self):
        """
        事件驱动的读取循环（阻塞模式，0% CPU 占用）。

        使用 queue.get() 阻塞等待：
        - 无事件时：线程完全休眠（操作系统挂起线程）
        - 有事件时：操作系统自动唤醒线程
        - 停止时：通过哨兵值唤醒并退出

        这是真正的事件驱动模式，不会消耗 CPU。
        """
        while self._running:
            try:
                # 阻塞等待消息（无 timeout，线程会完全休眠直到有数据）
                msg = self._event_queue.get()

                # 检查是否为停止哨兵
                if msg is self._SENTINEL:
                    break

                # 解析消息并触发回调
                event_type = msg[0]

                if event_type == 'trigger':
                    # 触发按钮被按下
                    if self.on_trigger:
                        self.on_trigger()

                elif event_type == 'press':
                    # 其他按钮被按下
                    button_name = msg[1]
                    if self.on_any_press:
                        self.on_any_press(button_name)

                elif event_type == 'ready':
                    # 子进程初始化完成
                    device_name = msg[1]
                    log(TAG, f"子进程就绪，设备: {device_name}")

                elif event_type == 'error':
                    # 子进程发生错误
                    error_msg = msg[1]
                    log(TAG, f"子进程错误: {error_msg}")

            except Exception as e:
                log(TAG, f"读取队列异常: {e}")
                break

        log(TAG, "事件读取线程已退出")

    def _on_keyboard_event(self, event):
        """keyboard 库全局钩子回调，在独立线程中执行。"""
        if event.event_type == 'down':
            key_name = event.name.lower() if event.name else ''
            if key_name == self.trigger_key and self.on_trigger:
                self.on_trigger()
            elif self.on_any_press:
                self.on_any_press(key_name)


def _gamepad_worker(event_queue, stop_event, ready_event, trigger_button, poll_interval):
    """
    子进程工作函数：初始化 pygame 并轮询手柄状态。

    通过 event_queue 向主进程发送事件。
    通过 stop_event 接收主进程的停止信号。
    通过 ready_event 通知主进程初始化完成。

    Args:
        event_queue: multiprocessing.Queue，用于向主进程发送事件
        stop_event: multiprocessing.Event，用于接收停止信号
        ready_event: multiprocessing.Event，用于通知主进程初始化完成
        trigger_button: 触发按钮名称
        poll_interval: 轮询间隔（秒）
    """
    import pygame

    # 设置 SDL 允许后台事件（窗口未聚焦时也能接收输入）
    os.environ["SDL2_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"

    # 按钮索引到名称的映射（反向）
    index_to_name = {v: k for k, v in BUTTON_IDX_MAP.items()}

    try:
        # 初始化 pygame
        pygame.init()
        pygame.joystick.init()

        # 检测手柄
        joystick_count = pygame.joystick.get_count()
        if joystick_count == 0:
            event_queue.put(('error', '未检测到手柄'))
            ready_event.set()  # 设置就绪事件，让主进程可以继续
            pygame.quit()
            return

        # 打开第一个手柄
        joystick = pygame.joystick.Joystick(0)
        joystick.init()
        device_name = joystick.get_name()

        # 通知主进程初始化完成
        event_queue.put(('ready', device_name))
        ready_event.set()  # 设置就绪事件，让主进程可以继续

        # 获取触发按钮索引
        trigger_index = BUTTON_IDX_MAP.get(trigger_button, -1)
        if trigger_index < 0 or trigger_index >= joystick.get_numbuttons():
            event_queue.put(('error', f'无效的触发按钮: {trigger_button}'))
            pygame.quit()
            return

        # 记录上一帧的按钮状态
        prev_state = {i: 0 for i in range(joystick.get_numbuttons())}

        # 主循环
        while not stop_event.is_set():
            try:
                # 更新 SDL 内部状态
                pygame.event.pump()

                # 检查所有按钮的状态变化
                num_buttons = joystick.get_numbuttons()
                for btn_idx in range(num_buttons):
                    btn_name = index_to_name.get(btn_idx)
                    if btn_idx != trigger_index:
                        # log(TAG, f"非目标按键: {btn_idx} {btn_name}")
                        continue
                    current_state = joystick.get_button(btn_idx)

                    # 检测按下（从 0 变为 1）
                    if current_state == 1 and prev_state.get(btn_idx, 0) == 0:
                        if btn_idx == trigger_index:
                            # 触发按钮
                            event_queue.put(('trigger', btn_name))
                        else:
                            # 其他按钮
                            event_queue.put(('press', btn_name))

                    # 更新状态
                    prev_state[btn_idx] = current_state

            except Exception as e:
                event_queue.put(('error', f'手柄读取异常: {str(e)}'))
                break

            # 降低 CPU 占用
            pygame.time.wait(int(poll_interval * 1000))

        # 清理
        pygame.quit()

    except Exception as e:
        event_queue.put(('error', f'Pygame 初始化异常: {str(e)}'))
