"""Transfer execution and progress reporting for the Files page."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ...core.file_transfer import OverwritePolicy
from ...services.file_transfer_service import FileTransferService
from ..worker_utils import WorkerContext

if TYPE_CHECKING:
    from PySide6.QtWidgets import QProgressBar, QWidget

from .file_transfer_helpers import format_queue_summary, format_transfer_speed, remote_child_path


class TransferRunner:
    """Manage upload, download, preview, progress, and worker lifetimes."""

    def __init__(
        self,
        *,
        owner: object,
        progress_bar: QProgressBar,
        service_provider: Callable[[], FileTransferService | None],
        language_provider: Callable[[], str],
        worker_registry: list[Any],
        on_status: Callable[[str], None],
        on_error: Callable[[str, str], None],
        on_refresh_local: Callable[[], None],
        on_refresh_remote: Callable[[], None],
        run_transfer: Callable[..., Any],
        start_context: Callable[..., Any],
        start_tracked: Callable[..., Any],
        clock: Callable[[], float],
        show_preview: Callable[[QWidget, str, str], None],
    ) -> None:
        self._owner = owner
        self._progress_bar = progress_bar
        self._service_provider = service_provider
        self._language_provider = language_provider
        self._worker_registry = worker_registry
        self._on_status = on_status
        self._on_error = on_error
        self._on_refresh_local = on_refresh_local
        self._on_refresh_remote = on_refresh_remote
        self._run_transfer = run_transfer
        self._start_context = start_context
        self._start_tracked = start_tracked
        self._clock = clock
        self._show_preview = show_preview

    def download_selected(self, remote_path: str, local_base: Path) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return
        target = Path(local_base) / Path(remote_path).name

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            result = service.download_path(
                remote_path,
                target,
                OverwritePolicy.overwrite,
                progress_callback=_progress,
            )
            return result if isinstance(result, list) else [result]

        self._run_transfer(_run, "Download", self._on_refresh_local)

    def upload_selected(self, local_path: Path, remote_target: str) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            result = service.upload_path(
                local_path,
                remote_target,
                OverwritePolicy.overwrite,
                progress_callback=_progress,
            )
            return result if isinstance(result, list) else [result]

        self._run_transfer(_run, "Upload", self._on_refresh_remote)

    def upload_dropped_local_paths(
        self,
        paths: list[str],
        remote_dir: str,
        on_done_refresh: Callable[[], None] | None = None,
    ) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            records = []
            for path_text in paths:
                local_path = Path(path_text)
                if not local_path.exists():
                    continue
                target = remote_child_path(remote_dir, local_path.name)
                result = service.upload_path(
                    local_path,
                    target,
                    OverwritePolicy.skip_same_size,
                    progress_callback=_progress,
                )
                records.extend(result if isinstance(result, list) else [result])
            return records

        self._run_transfer(
            _run,
            "Upload",
            on_done_refresh or self._on_refresh_remote,
        )

    def download_dropped_remote_paths(
        self,
        paths: list[str],
        local_base: Path,
        on_done_refresh: Callable[[], None] | None = None,
    ) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            records = []
            for remote_path in paths:
                result = service.download_path(
                    remote_path,
                    Path(local_base) / Path(remote_path).name,
                    OverwritePolicy.overwrite,
                    progress_callback=_progress,
                )
                records.extend(result if isinstance(result, list) else [result])
            return records

        self._run_transfer(
            _run,
            "Download",
            on_done_refresh or self._on_refresh_local,
        )

    def preview_remote(self, remote_path: str, parent: object) -> None:
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return

        def _run(_ctx: WorkerContext):
            return service.preview_remote_text(remote_path)

        self._start_context(
            self._owner,
            target=_run,
            registry_attr="_background_workers",
            on_result=lambda text: self._show_preview(parent, remote_path, text[:4000]),
            on_error=lambda error: self._on_error("Preview Error", error),
        )

    def start_worker(
        self,
        run_fn_or_worker: Any,
        label: str,
        on_done_refresh: Callable[[], None],
    ) -> None:
        started_at = self._clock()
        self._progress_bar.setValue(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setFormat(f"{label}: %p%")
        self._progress_bar.setVisible(True)

        def _on_progress(done: int, total: int) -> None:
            elapsed = max(self._clock() - started_at, 0.001)
            speed = format_transfer_speed(done / elapsed)
            if total > 0:
                self._progress_bar.setValue(int(done * 100 / total))
                self._progress_bar.setFormat(f"{label}: {done // 1024}K / {total // 1024}K @ {speed}")
            else:
                self._progress_bar.setMaximum(0)
                self._progress_bar.setFormat(f"{label}: {done // 1024}K @ {speed}")

        def _on_done(records: Any) -> None:
            self._reset_progress()
            if not isinstance(records, list):
                records = [records]
            self._on_status(
                format_queue_summary(
                    [record.status for record in records],
                    self._language_provider(),
                )
            )
            on_done_refresh()

        def _on_error(message: str) -> None:
            self._reset_progress()
            self._on_error(f"{label} Error", message)

        kwargs = {
            "registry_attr": "_background_workers",
            "on_progress": _on_progress,
            "on_result": _on_done,
            "on_error": _on_error,
        }
        if hasattr(run_fn_or_worker, "start"):
            self._start_tracked(self._owner, run_fn_or_worker, **kwargs)
        else:
            self._start_context(self._owner, target=run_fn_or_worker, **kwargs)
        self._on_status(f"{label} started")

    def keep_worker(self, worker) -> None:
        self._worker_registry.append(worker)
        worker.finished.connect(
            lambda: self._worker_registry.remove(worker) if worker in self._worker_registry else None
        )
        if hasattr(worker, "deleteLater"):
            worker.finished.connect(worker.deleteLater)

    def teardown(self) -> None:
        self._reset_progress()

    def _reset_progress(self) -> None:
        self._progress_bar.setVisible(False)
        self._progress_bar.setMaximum(100)
