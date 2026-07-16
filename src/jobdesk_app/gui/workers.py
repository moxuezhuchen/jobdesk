"""后台 Worker 基础设施 — 长操作放在 QThread 中，不阻塞 UI。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QThread, Signal

DEFAULT_WORKER_STOP_TIMEOUT_MS = 3000

# Type alias for the target function signature
WorkerTarget = Callable[..., Any]


class BackgroundWorker(QThread):
    """在 QThread 中执行函数，通过信号返回结果/错误。"""

    # Signals - PySide6 requires these to be class-level attributes
    started = Signal()  # type: ignore[misc]
    result = Signal(object)  # type: ignore[misc]
    error = Signal(str)  # type: ignore[misc]
    log = Signal(str)  # type: ignore[misc]
    progress = Signal(int, int)  # type: ignore[misc]  # bytes_done, bytes_total

    # Keep-alive registry: a running QThread whose Python wrapper is garbage
    # collected triggers "QThread: Destroyed while thread is still running" and
    # aborts the process. Rapid resubmission overwrites caller-held references,
    # so every started worker holds a strong reference here until it finishes.
    _active: set[BackgroundWorker] = set()

    def __init__(self, target_fn: WorkerTarget, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._target_fn = target_fn
        self._args: tuple[Any, ...] = args
        self._kwargs: dict[str, Any] = kwargs

    def start(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        BackgroundWorker._active.add(self)
        self.finished.connect(self._unregister)
        super().start(*args, **kwargs)

    def _unregister(self) -> None:
        BackgroundWorker._active.discard(self)

    @classmethod
    def wait_all(cls, timeout_ms: int | None = DEFAULT_WORKER_STOP_TIMEOUT_MS) -> None:
        """Block until all running workers finish (use on app shutdown)."""
        for worker in list(cls._active):
            try:
                finished = worker.wait() if timeout_ms is None else worker.wait(timeout_ms)
            except RuntimeError:
                # Underlying C++ QThread already deleted (e.g. test teardown).
                cls._active.discard(worker)
                continue
            if finished:
                cls._active.discard(worker)

    def run(self) -> None:
        self.started.emit()
        try:
            value = self._target_fn(*self._args, **self._kwargs)
            if not self.isInterruptionRequested():
                self.result.emit(value)
        except Exception as e:
            import traceback
            msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            if not self.isInterruptionRequested():
                self.error.emit(msg)

    def stop_safely(self, timeout_ms: int | None = DEFAULT_WORKER_STOP_TIMEOUT_MS) -> None:
        """Request stop and wait for thread completion before destruction."""
        try:
            self.requestInterruption()
            self.quit()
            if timeout_ms is None:
                self.wait()
            else:
                self.wait(timeout_ms)
        except RuntimeError:
            # Underlying C++ QThread already deleted (e.g. finished + deleteLater
            # during teardown). Nothing left to stop.
            BackgroundWorker._active.discard(self)
