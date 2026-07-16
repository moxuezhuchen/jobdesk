"""Remote-edit session manager for the Files page.

Tracks remote files opened in the local editor and uploads them back
when the temp file changes. Independent of Qt widgets except via the
service/remote-path callbacks and the page-level ``_remote_edit_timer``
``QTimer``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ...core.file_transfer import OverwritePolicy
from ...services.file_transfer_service import FileTransferService
from ..worker_utils import WorkerContext
from .file_transfer_helpers import _file_signature, _raise_if_upload_failed, _remote_edit_temp_path
from .file_transfer_tables import _RemoteEditSession


@dataclass
class RemoteEditOutcome:
    """Result of opening a remote file in the editor."""

    success: bool
    error: str | None = None


class RemoteEditSessionManager:
    """Tracks open remote files being edited locally + auto-uploads on save."""

    def __init__(
        self,
        *,
        service_provider: Callable[[], FileTransferService | None],
        settings_provider: Callable,
        server_id_provider: Callable[[], str | None],
        on_status: Callable[[str], None],
        on_error: Callable[[str, str], None],
        on_refresh_remote: Callable[[], None],
        start_worker: Callable,
        process_launcher: Callable[[list[str]], object],
    ) -> None:
        self._service_provider = service_provider
        self._settings_provider = settings_provider
        self._server_id_provider = server_id_provider
        self._on_status = on_status
        self._on_error = on_error
        self._on_refresh_remote = on_refresh_remote
        self._start_worker = start_worker
        self._process_launcher = process_launcher
        self._sessions: dict[str, _RemoteEditSession] = {}

    @property
    def dirty_sessions(self) -> list[_RemoteEditSession]:
        dirty = []
        for session in self._sessions.values():
            if session.local_path.exists() and _file_signature(session.local_path) != session.uploaded_signature:
                dirty.append(session)
        return dirty

    def has_dirty(self) -> bool:
        return bool(self.dirty_sessions)

    def register_session(self, remote_path: str, local_path: Path) -> None:
        local_path = Path(local_path)
        self._sessions[str(local_path)] = _RemoteEditSession(
            remote_path=remote_path,
            local_path=local_path,
            uploaded_signature=_file_signature(local_path),
        )

    def teardown(self) -> list[_RemoteEditSession]:
        """Return list of dirty sessions so the page can warn the user."""
        return self.dirty_sessions

    def open_remote_file(
        self,
        owner,
        remote_path: str,
        on_opened: Callable[[Path], None],
        open_in_editor: Callable[[Path], bool],
    ) -> bool:
        """Download ``remote_path`` to a temp dir and launch the editor.

        ``on_opened`` is invoked with the local temp path when the editor
        was launched successfully. The caller should call
        :meth:`register_session` once the editor is open.
        Returns ``True`` if the download worker was started.
        """
        service = self._service_provider()
        if service is None:
            self._on_status("Connect to a server first")
            return False
        server_id = self._server_id_provider()
        tmp_file = _remote_edit_temp_path(remote_path, server_id)
        tmp_file.parent.mkdir(parents=True, exist_ok=True)

        def _download(_ctx: WorkerContext):
            service.download_path(remote_path, str(tmp_file), OverwritePolicy.overwrite)
            return tmp_file

        def _on_done(path):
            if open_in_editor(Path(path)):
                on_opened(Path(path))
                self._on_status(f"Opened: {Path(remote_path).name}")

        self._start_worker(
            owner,
            target=_download,
            registry_attr="_background_workers",
            on_result=_on_done,
            on_error=lambda error: self._on_status(f"Download failed: {error.splitlines()[0]}"),
        )
        self._on_status(f"Downloading {Path(remote_path).name}...")
        return True

    def tick(self, owner) -> None:
        """Iterate sessions and upload any whose local file has changed."""
        if not self._sessions:
            return
        for key, session in list(self._sessions.items()):
            if not session.local_path.exists():
                self._sessions.pop(key, None)
                continue
            signature = _file_signature(session.local_path)
            if signature == session.uploaded_signature:
                continue
            if signature == session.uploading_signature:
                continue
            self.upload_session(owner, session, signature)

    def upload_session(
        self,
        owner,
        session: _RemoteEditSession,
        signature: str | None = None,
    ) -> None:
        service = self._service_provider()
        if service is None:
            self._on_error("Upload Remote Edit Error", "Connect to a server first")
            return
        upload_signature = signature or _file_signature(session.local_path)
        session.uploading_signature = upload_signature
        local_path = session.local_path
        remote_path = session.remote_path
        session_key = str(local_path)

        def _run(_ctx: WorkerContext):
            records = service.upload_path(local_path, remote_path, OverwritePolicy.overwrite)
            _raise_if_upload_failed(records, remote_path)
            return session_key, upload_signature, remote_path

        def _done(result):
            key, completed_signature, completed_remote_path = result
            current = self._sessions.get(key)
            if current is None:
                return
            if current.uploading_signature == completed_signature:
                current.uploaded_signature = completed_signature
                current.uploading_signature = None
            self._on_status(f"Uploaded remote edit: {completed_remote_path}")
            self._on_refresh_remote()

        def _error(error: str):
            current = self._sessions.get(session_key)
            if current is not None and current.uploading_signature == upload_signature:
                current.uploading_signature = None
            self._on_error("Upload Remote Edit Error", error.splitlines()[0])

        self._start_worker(
            owner,
            target=_run,
            registry_attr="_background_workers",
            on_result=_done,
            on_error=_error,
        )

    def open_in_text_editor(self, path: Path) -> bool:
        """Open ``path`` in the configured text editor. Used by the page shim."""
        editor = self._settings_provider().text_editor_path or "notepad.exe"
        try:
            self._process_launcher([editor, str(path)])
        except Exception as exc:
            self._on_error("Open File Error", str(exc))
            return False
        return True
