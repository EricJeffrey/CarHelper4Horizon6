from PyQt5.QtCore import QTimer, QObject, pyqtSignal

class GuiBridge(QObject):
    """
    跨线程 GUI 桥接器。
    后台线程通过 emit 信号发送数据，Qt 自动将槽函数投递到主线程执行。
    """
    show_overlay = pyqtSignal(str, object)   # (text, bbox)
    update_overlay = pyqtSignal(str)         # (text) - 更新已显示的悬浮窗内容
    hide_overlay = pyqtSignal()
    quit_app = pyqtSignal()