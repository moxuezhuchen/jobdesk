"""Explicit queued dispatch from worker callbacks to a Files-page GUI thread."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal, Slot


class GuiThreadDispatcher(QObject):
    """Deliver callbacks on the thread owning this dispatcher.

    A bare Python lambda connected to a ``QThread`` signal has ambiguous
    affinity under PySide.  This QObject is parented to the page, and the
    explicit queued connection makes the callback boundary testable and
    robust during rapid refresh/reconnect cycles.
    """

    _queued = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queued.connect(self._deliver, Qt.QueuedConnection)

    def post(self, callback: Callable[[], None]) -> None:
        """Queue ``callback`` for execution on the dispatcher's GUI thread."""
        self._queued.emit(callback)

    @Slot(object)
    def _deliver(self, callback: Callable[[], None]) -> None:
        callback()


__all__ = ["GuiThreadDispatcher"]
