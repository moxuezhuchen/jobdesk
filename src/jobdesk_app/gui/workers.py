"""后台 Worker 基础设施 — 长操作放在 QThread 中，不阻塞 UI。"""

from PySide6.QtCore import QThread, Signal


class BackgroundWorker(QThread):
    """在 QThread 中执行函数，通过信号返回结果/错误。"""

    started = Signal()
    result = Signal(object)
    error = Signal(str)
    log = Signal(str)
    progress = Signal(int, int)  # bytes_done, bytes_total

    # Keep-alive registry: a running QThread whose Python wrapper is garbage
    # collected triggers "QThread: Destroyed while thread is still running" and
    # aborts the process. Rapid resubmission overwrites caller-held references,
    # so every started worker holds a strong reference here until it finishes.
    _active: set["BackgroundWorker"] = set()

    def __init__(self, target_fn, *args, **kwargs):
        super().__init__()
        self._target_fn = target_fn
        self._args = args
        self._kwargs = kwargs

    def start(self, *args, **kwargs):
        BackgroundWorker._active.add(self)
        self.finished.connect(self._unregister)
        super().start(*args, **kwargs)

    def _unregister(self):
        BackgroundWorker._active.discard(self)

    @classmethod
    def wait_all(cls, timeout_ms: int | None = None):
        """Block until all running workers finish (use on app shutdown)."""
        for worker in list(cls._active):
            if timeout_ms is None:
                worker.wait()
            else:
                worker.wait(timeout_ms)

    def run(self):
        self.started.emit()
        try:
            value = self._target_fn(*self._args, **self._kwargs)
            self.result.emit(value)
        except Exception as e:
            import traceback
            msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            self.error.emit(msg)

    def stop_safely(self, timeout_ms: int | None = None):
        """Request stop and wait for thread completion before destruction."""
        self.requestInterruption()
        self.quit()
        if timeout_ms is None:
            self.wait()
        else:
            self.wait(timeout_ms)
