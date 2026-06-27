"""Qt signal adapter for the framework-neutral run monitor."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..services.run_monitor import DoneEvent
from ..services.run_monitor import RunMonitor as ServiceRunMonitor
from ..services.ssh_session import create_ssh_client


class RunMonitor(QObject):
    """Expose service monitor events through a Qt signal."""

    task_done = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = ServiceRunMonitor(create_ssh_client, self._emit_task_done)

    def watch(self, run_id: str, server_id: str, remote_batch_dir: str, server_config) -> None:
        self._service.watch(run_id, server_id, remote_batch_dir, server_config)

    def unwatch(self, run_id: str, server_id: str) -> None:
        self._service.unwatch(run_id, server_id)

    def stop_all(self) -> None:
        self._service.stop_all()

    def _emit_task_done(self, event: DoneEvent) -> None:
        self.task_done.emit(event)
