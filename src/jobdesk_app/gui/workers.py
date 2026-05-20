"""后台 Worker 基础设施 — 长操作放在 QThread 中，不阻塞 UI。"""

from PySide6.QtCore import QThread, Signal, QObject


class BackgroundWorker(QThread):
    """在 QThread 中执行函数，通过信号返回结果/错误。"""

    started = Signal()
    result = Signal(object)
    error = Signal(str)
    log = Signal(str)
    progress = Signal(int, int)  # bytes_done, bytes_total

    def __init__(self, target_fn, *args, **kwargs):
        super().__init__()
        self._target_fn = target_fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        self.started.emit()
        try:
            value = self._target_fn(*self._args, **self._kwargs)
            self.result.emit(value)
        except Exception as e:
            import traceback
            msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self.error.emit(msg)

    def stop_safely(self, timeout_ms: int = 3000):
        """Request stop and wait for thread to finish."""
        self.quit()
        self.wait(timeout_ms)
