import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.pages import file_transfer_remote_edit
from jobdesk_app.gui.pages.file_transfer_remote_edit import RemoteEditSessionManager


def _manager(service_provider, start_worker) -> RemoteEditSessionManager:
    return RemoteEditSessionManager(
        service_provider=service_provider,
        settings_provider=MagicMock(),
        server_id_provider=lambda: "wsl",
        on_status=MagicMock(),
        on_error=MagicMock(),
        on_refresh_remote=MagicMock(),
        start_worker=start_worker,
        process_launcher=MagicMock(),
    )


def test_open_remote_file_reads_service_provider_once(monkeypatch, tmp_path: Path):
    service = MagicMock()
    provider = MagicMock(side_effect=[service, None])
    start_worker = MagicMock()
    local_path = tmp_path / "result.log"
    monkeypatch.setattr(
        file_transfer_remote_edit,
        "_remote_edit_temp_path",
        lambda _remote_path, _server_id: local_path,
    )
    manager = _manager(provider, start_worker)

    assert manager.open_remote_file(
        object(),
        "/remote/result.log",
        on_opened=MagicMock(),
        open_in_editor=MagicMock(),
    )

    provider.assert_called_once_with()
    result = start_worker.call_args.kwargs["target"](MagicMock())
    assert result == local_path
    service.download_path.assert_called_once()


def test_upload_session_reads_service_provider_once(tmp_path: Path):
    service = MagicMock()
    service.upload_path.return_value = []
    provider = MagicMock(side_effect=[service, None])
    start_worker = MagicMock()
    manager = _manager(provider, start_worker)
    local_path = tmp_path / "result.gjf"
    local_path.write_text("before\n", encoding="utf-8")
    manager.register_session("/remote/result.gjf", local_path)
    local_path.write_text("after\n", encoding="utf-8")

    manager.tick(object())

    provider.assert_called_once_with()
    start_worker.call_args.kwargs["target"](MagicMock())
    service.upload_path.assert_called_once()


def test_dirty_and_teardown_sessions_are_frozen_public_snapshots(tmp_path: Path):
    manager = _manager(lambda: MagicMock(), MagicMock())
    local_path = tmp_path / "result.gjf"
    local_path.write_text("before\n", encoding="utf-8")
    manager.register_session("/remote/result.gjf", local_path)
    local_path.write_text("after\n", encoding="utf-8")

    dirty = manager.dirty_sessions
    assert len(dirty) == 1
    assert dirty[0].remote_path == "/remote/result.gjf"
    assert dirty[0].dirty is True
    with pytest.raises(FrozenInstanceError):
        dirty[0].remote_path = "/other"  # type: ignore[misc]
    assert manager.teardown() == dirty


def test_remote_edit_preserves_uploading_cleanup_after_failure(tmp_path: Path):
    service = MagicMock()
    service.upload_path.side_effect = RuntimeError("upload failed")
    errors: list[tuple[str, str]] = []
    started: dict[str, Any] = {}

    def start_worker(_owner, **kwargs):
        started.update(kwargs)

    manager = RemoteEditSessionManager(
        service_provider=lambda: service,
        settings_provider=MagicMock(),
        server_id_provider=lambda: "wsl",
        on_status=lambda _message: None,
        on_error=lambda title, message: errors.append((title, message)),
        on_refresh_remote=lambda: None,
        start_worker=start_worker,
        process_launcher=MagicMock(),
    )
    local_path = tmp_path / "result.gjf"
    local_path.write_text("before\n", encoding="utf-8")
    manager.register_session("/remote/result.gjf", local_path)
    local_path.write_text("after\n", encoding="utf-8")

    manager.tick(object())
    snapshot = manager.session_snapshots[0]
    assert snapshot.uploading_signature is not None
    with pytest.raises(RuntimeError, match="upload failed"):
        started["target"](MagicMock())
    started["on_error"]("upload failed")

    assert manager.session_snapshots[0].uploading_signature is None
    assert manager.session_snapshots[0].dirty is True
    assert errors == [("Upload Remote Edit Error", "upload failed")]


def test_remote_edit_does_not_import_concrete_file_transfer_service():
    path = Path(__file__).parents[1] / "src" / "jobdesk_app" / "gui" / "pages" / "file_transfer_remote_edit.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    imported_modules = {node.module or "" for node in tree.body if isinstance(node, ast.ImportFrom)}
    imported_names = {
        alias.name for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }
    assert "services.file_transfer_service" not in imported_modules
    assert "FileTransferService" not in imported_names
    assert "FileTransferPort" in imported_names
