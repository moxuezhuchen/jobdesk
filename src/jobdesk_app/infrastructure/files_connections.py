"""Concrete Files connection and transfer-service lifecycle ownership."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..application.file_transfer_ports import FileTransferPort
from ..application.files_connections import FileTransferConnectionSnapshot
from ..core.configuration import ServerConfig, ServersConfig

ServerLoader = Callable[[], ServersConfig]
TransferServiceFactory = Callable[[ServerConfig, str, list[str]], FileTransferPort]


class InfrastructureFilesConnectionController:
    """Own active transfer services without exposing transport construction."""

    def __init__(
        self,
        *,
        status_cb: Callable[[str], None],
        log_cb: Callable[[str], None],
        server_loader: ServerLoader,
        service_factory: TransferServiceFactory,
        allowed_delete_roots_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._status_cb = status_cb
        self._log_cb = log_cb
        self._server_loader = server_loader
        self._service_factory = service_factory
        self._allowed_delete_roots_provider = allowed_delete_roots_provider or (lambda: [])
        self._servers: dict[str, ServerConfig] = {}
        self._service: FileTransferPort | None = None
        self._connected_server_id: str | None = None
        self._connected_server: ServerConfig | None = None
        self._generation = 0
        self._ready = False

    @property
    def servers(self) -> Mapping[str, ServerConfig]:
        return self._servers

    @property
    def service(self) -> FileTransferPort | None:
        return self._service

    @property
    def connected_server_id(self) -> str | None:
        return self._connected_server_id

    @property
    def connected_server(self) -> ServerConfig | None:
        return self._connected_server

    @property
    def ready(self) -> bool:
        return self._ready

    def set_servers(self, servers: Mapping[str, ServerConfig]) -> None:
        self._servers = dict(servers)

    def set_service(self, service: FileTransferPort | None) -> None:
        self._service = service
        if service is None:
            self._ready = False

    def set_server_id(self, server_id: str | None) -> None:
        self._connected_server_id = server_id

    def set_server_config(self, server: ServerConfig | None) -> None:
        self._connected_server = server

    def load_servers(self) -> Mapping[str, ServerConfig]:
        try:
            config = self._server_loader()
        except Exception as exc:  # noqa: BLE001 -- user-facing configuration boundary
            self._servers = {}
            self._status_cb(f"No servers configured: {exc}")
            return self._servers
        self._servers = dict(config.servers)
        return self._servers

    def connect(
        self,
        server_id: str,
        *,
        allowed_delete_roots: list[str] | None = None,
    ) -> tuple[FileTransferPort | None, FileTransferPort]:
        server = self._servers[server_id]
        roots = (
            list(allowed_delete_roots)
            if allowed_delete_roots is not None
            else list(self._allowed_delete_roots_provider())
        )
        old_service = self._service
        service = self._service_factory(server, server_id, roots)
        self._service = service
        self._connected_server_id = server_id
        self._connected_server = server
        self._ready = False
        self._generation += 1
        return old_service, service

    def mark_ready(self, ready: bool) -> None:
        self._ready = bool(ready and self._service is not None)

    def disconnect(self) -> FileTransferPort | None:
        service = self._service
        self._service = None
        self._ready = False
        self._connected_server_id = None
        self._connected_server = None
        self._generation += 1
        return service

    def close_service(self, service: FileTransferPort) -> None:
        try:
            service.close()
        except Exception as exc:  # noqa: BLE001 -- teardown is best effort
            self._log_cb(f"Error closing service: {exc}")

    def teardown(self) -> None:
        service = self.disconnect()
        if service is not None:
            self.close_service(service)

    def set_server(
        self,
        server_id: str | None,
        server: ServerConfig | None,
        service: FileTransferPort | None,
    ) -> None:
        self._connected_server_id = server_id
        self._connected_server = server
        self._service = service
        self._ready = bool(service is not None and self._ready)

    def snapshot(self, remote_dir: str, *, ready: bool | None = None) -> FileTransferConnectionSnapshot:
        connected = self._service is not None
        effective_ready = self._ready if ready is None else bool(ready and connected)
        return FileTransferConnectionSnapshot(
            server_id=self._connected_server_id,
            server=self._connected_server,
            connected=connected,
            remote_dir=remote_dir,
            generation=self._generation,
            ready=effective_ready,
        )


__all__ = ["InfrastructureFilesConnectionController"]
