"""Application-facing Files controller compatibility contracts."""

from __future__ import annotations

from unittest.mock import MagicMock

from jobdesk_app.application.files_connections import FileTransferConnectionSnapshot
from jobdesk_app.gui.pages.file_transfer_connections import ConnectionsCoordinator


def test_connection_snapshot_carries_status_without_service_reference():
    service = MagicMock()
    snapshot = FileTransferConnectionSnapshot(None, None, service, "/", ready=False)
    assert snapshot.connected is True
    assert snapshot.ready is False
    assert not hasattr(snapshot, "service")


def test_connection_controller_separates_connected_from_ready():
    from jobdesk_app.application.files_connections import FilesConnectionController

    coordinator = FilesConnectionController(
        status_cb=MagicMock(),
        log_cb=MagicMock(),
        create_ssh=MagicMock(),
        create_sftp=MagicMock(),
    )
    coordinator.set_servers({"server": MagicMock()})
    coordinator.connect("server")

    pending = coordinator.snapshot("/")
    assert pending.connected is True
    assert pending.ready is False
    assert not hasattr(pending, "service")

    coordinator.mark_ready(True)
    ready = coordinator.snapshot("/")
    assert ready.connected is True
    assert ready.ready is True


def test_legacy_connections_coordinator_accepts_run_tasks_provider():
    provider = MagicMock(return_value=[])
    coordinator = ConnectionsCoordinator(
        status_cb=MagicMock(),
        log_cb=MagicMock(),
        create_ssh=MagicMock(),
        create_sftp=MagicMock(),
        run_tasks_provider=provider,
    )
    assert coordinator is not None
    # The provider is retained as the delete-root policy source and is called
    # when a connection is created, matching the old safety boundary.
    coordinator.set_servers({"s": MagicMock()})
    coordinator.connect("s")
    provider.assert_called_once_with()
