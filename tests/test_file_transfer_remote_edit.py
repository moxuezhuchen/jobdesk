from pathlib import Path
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
