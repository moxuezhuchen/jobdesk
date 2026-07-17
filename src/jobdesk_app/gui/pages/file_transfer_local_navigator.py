"""Local filesystem navigator for the Files page.

Owns the local-side navigation state (``current_project_root``,
hide-dotfiles, polling snapshot) independent of any Qt widget. The
page wires its ``QTimer`` to :meth:`LocalNavigator.check_local_changes`
and forwards the rows back through :attr:`on_rows_loaded`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...services.gui_settings import GuiSettingsStore
from ..worker_utils import WorkerContext, start_context_worker
from .file_transfer_helpers import build_local_rows


class LocalNavigator:
    """Local-side filesystem navigation state, Qt-free."""

    def __init__(
        self,
        *,
        root_provider: Callable[[], Path],
        hide_dot_provider: Callable[[], bool],
        log_provider: Callable[[], Callable[[str], None]],
        on_rows_loaded: Callable[[list[list[str]]], None],
        worker_registry_attr: str = "_background_workers",
    ) -> None:
        self._root_provider = root_provider
        self._hide_dot_provider = hide_dot_provider
        self._log_provider = log_provider
        self._on_rows_loaded = on_rows_loaded
        self._worker_registry_attr = worker_registry_attr
        self._snapshot: dict[str, float] = {}
        self._poll_running = False
        self._refresh_request_id = 0
        self._on_root_changed = lambda path: None

    @property
    def last_poll_snapshot(self) -> dict:
        return self._snapshot

    @property
    def poll_running(self) -> bool:
        return self._poll_running

    def set_root(self, path: Path) -> None:
        """Update ``state.current_project_root`` and persist the new folder."""
        self._on_root_changed(path)
        self.save_last_local_folder(path)

    def apply_default_local_folder(self, settings: GuiSettingsStore) -> Path | None:
        """Set ``state.current_project_root`` to the user's saved folder.

        Returns the chosen path (or ``None`` if no usable folder is set).
        """
        folder = settings.last_local_folder or settings.default_local_folder
        if folder and Path(folder).exists():
            chosen = Path(folder)
            self._on_root_changed(chosen)
            return chosen
        return None

    def save_last_local_folder(self, path: Path) -> None:
        GuiSettingsStore().update(last_local_folder=str(path))

    def set_root_provider(self, callback: Callable[[Path], None]) -> None:
        """Replace the root-changed notifier (used by the page to update UI)."""
        self._on_root_changed = callback

    def scan(self) -> tuple[dict[str, float], list[list[str]], str | None]:
        """Synchronously scan the local directory.

        Returns ``(snapshot, rows, error)``. This is a pure helper — callers
        decide what to do with the result (log it, feed it to the table,
        etc.).
        """
        base = Path(self._root_provider() or Path.cwd())
        return build_local_rows(base, self._hide_dot_provider())

    def check_local_changes(self, owner) -> None:
        """Poll local directory for changes (handles WSL /mnt/c writes)."""
        if self._poll_running:
            return
        base = Path(self._root_provider() or Path.cwd())
        hide_dot = self._hide_dot_provider()
        self._poll_running = True

        def _run(_ctx: WorkerContext):
            return build_local_rows(base, hide_dot)

        def _done(result):
            self._poll_running = False
            snapshot, rows, error = result
            if error:
                self._log_provider()(error)
            if snapshot != self._snapshot:
                self._snapshot = snapshot
                self._on_rows_loaded(rows)

        def _error(_message: str):
            self._poll_running = False

        start_context_worker(
            owner,
            target=_run,
            registry_attr=self._worker_registry_attr,
            on_result=_done,
            on_error=_error,
        )

    def refresh_now(self) -> None:
        """Synchronously re-scan the local directory and emit rows."""
        snapshot, rows, error = self.scan()
        if error:
            self._log_provider()(error)
        self._snapshot = snapshot
        self._on_rows_loaded(rows)

    def refresh_async(self, owner) -> None:
        """Asynchronously re-scan the local directory and emit rows."""
        base = Path(self._root_provider() or Path.cwd())
        hide_dot = self._hide_dot_provider()
        self._refresh_request_id += 1
        request_id = self._refresh_request_id

        def _run(_ctx: WorkerContext):
            return build_local_rows(base, hide_dot)

        def _done(result):
            if request_id != self._refresh_request_id:
                return
            snapshot, rows, error = result
            if error:
                self._log_provider()(error)
            self._snapshot = snapshot
            self._on_rows_loaded(rows)

        start_context_worker(
            owner,
            target=_run,
            registry_attr=self._worker_registry_attr,
            on_result=_done,
            on_error=lambda error: self._log_provider()(f"Local refresh failed: {error.splitlines()[0]}"),
        )
