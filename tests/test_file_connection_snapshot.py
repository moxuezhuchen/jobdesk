from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from jobdesk_app.config.schema import ServerConfig
from jobdesk_app.gui.pages.file_transfer_connections import ConnectionsCoordinator, FileConnectionSnapshot


def test_connection_snapshot_is_immutable_and_tracks_generation() -> None:
    server = ServerConfig(server_id="wsl", display_name="WSL", host="localhost", username="tester")
    coordinator = ConnectionsCoordinator(
        status_cb=lambda _message: None,
        log_cb=lambda _message: None,
        create_ssh=lambda: object(),
        create_sftp=lambda: object(),
        run_tasks_provider=lambda: [],
    )

    disconnected = coordinator.snapshot("/")
    assert disconnected == FileConnectionSnapshot(False, None, "", "/", 0)

    coordinator.set_server("wsl", server, object())
    connected = coordinator.snapshot("/remote/jobs")
    assert connected.connected is True
    assert connected.server_id == "wsl"
    assert connected.server_label == "WSL"
    assert connected.remote_directory == "/remote/jobs"
    assert connected.generation == 1
    with pytest.raises(FrozenInstanceError):
        connected.server_id = "other"  # type: ignore[misc]

    coordinator.set_server(None, None, None)
    assert coordinator.snapshot().generation == 2
