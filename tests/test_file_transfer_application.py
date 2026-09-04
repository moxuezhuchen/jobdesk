"""Application-facing Files controller compatibility contracts."""

from __future__ import annotations

from unittest.mock import MagicMock

from jobdesk_app.application.files_connections import (
    ApplicationFilesConnectionController,
    FileTransferConnectionSnapshot,
)
from jobdesk_app.core.configuration import ServersConfig
from jobdesk_app.gui.pages.file_transfer_connections import ConnectionsCoordinator


def test_connection_snapshot_carries_status_without_service_reference():
    service = MagicMock()
    snapshot = FileTransferConnectionSnapshot(None, None, service, "/", ready=False)
    assert snapshot.connected is True
    assert snapshot.ready is False
    assert not hasattr(snapshot, "service")


def test_connection_controller_separates_connected_from_ready():
    from jobdesk_app.bootstrap import FilesConnectionController

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


def test_application_connection_controller_creates_facade_port_only():
    server_document = {"host": "example.invalid", "username": "user"}
    facade = MagicMock()
    coordinator = ApplicationFilesConnectionController(
        facade,
        status_cb=MagicMock(),
        log_cb=MagicMock(),
        server_loader=lambda: ServersConfig(servers={"server": server_document}),
    )
    coordinator.load_servers()

    old, port = coordinator.connect("server")

    assert old is None
    assert coordinator.service is port
    assert coordinator.connected_server is not None
    assert coordinator.connected_server.server_id == "server"
    assert coordinator.snapshot("/").connected is True
    facade.assert_not_called()


def test_gui_connections_name_is_application_controller_alias():
    facade = MagicMock()
    coordinator = ConnectionsCoordinator(
        facade,
        status_cb=MagicMock(),
        log_cb=MagicMock(),
        server_loader=lambda: ServersConfig(servers={}),
    )
    assert isinstance(coordinator, ApplicationFilesConnectionController)
