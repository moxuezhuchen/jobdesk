"""Qt signal adapter for the framework-neutral run monitor."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import QObject, Signal

from ..application.runs_monitor import MonitorEvent
from ..services.run_monitor import DoneEvent
from ..services.run_monitor import RunMonitor as ServiceRunMonitor
from ..services.ssh_session import create_ssh_client

# A Runs page can display many active records at once, and each legacy
# watcher owns a long-lived SSH transport.  Keep the GUI adapter bounded by
# default while leaving the limits injectable for deployments/tests that need
# a different budget (or an explicit ``None`` to preserve the service's
# unbounded mode).
DEFAULT_MAX_WATCHERS = 16
DEFAULT_MAX_WATCHERS_PER_SERVER = 4
DEFAULT_QUEUE_CAPACITY = 32


class RunMonitor(QObject):
    """Expose service monitor events through a Qt signal."""

    task_done = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        max_watchers: int | None = DEFAULT_MAX_WATCHERS,
        max_watchers_per_server: int | None = DEFAULT_MAX_WATCHERS_PER_SERVER,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
    ) -> None:
        super().__init__(parent)
        self._open = True
        self._service = ServiceRunMonitor(
            create_ssh_client,
            self._emit_task_done,
            max_watchers=max_watchers,
            max_watchers_per_server=max_watchers_per_server,
            queue_capacity=queue_capacity,
        )

    def watch(
        self,
        run_id: str,
        server_id: str,
        remote_batch_dir: str,
        server_config: object,
        progress_paths: Iterable[str] = (),
        watch_id: str | None = None,
    ) -> None:
        self._service.watch(run_id, server_id, remote_batch_dir, server_config, progress_paths, watch_id)

    def unwatch(self, run_id: str, server_id: str, watch_id: str | None = None) -> None:
        self._service.unwatch(run_id, server_id, watch_id)

    def stop_all(self) -> None:
        self._service.stop_all()

    @property
    def closed(self) -> bool:
        """Whether this adapter has been permanently closed by the owner."""

        return not self._open

    def close(self) -> None:
        """Stop watchers and reject callbacks emitted after page teardown."""

        if not self._open:
            return
        self._open = False
        self._service.stop_all()

    def _emit_task_done(self, event: DoneEvent) -> None:
        # Freeze the service event before it crosses the QObject signal.  No
        # service watcher, server configuration, or Qt object is retained by
        # the payload, and a callback racing with shutdown is discarded.
        if not self._open:
            return
        self.task_done.emit(MonitorEvent.from_event(event))
