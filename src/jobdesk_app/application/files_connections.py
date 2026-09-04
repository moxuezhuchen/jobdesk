"""Pure application contracts for the Files connection boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from ..core.configuration import ServerConfig
from .facades import FilesApplication
from .file_transfer_ports import FacadeFileTransferPort, FileTransferPort

ServerLoader = Callable[[], object]


@dataclass(frozen=True, slots=True)
class FileTransferConnectionSnapshot:
    """Immutable connection state crossing into the presentation layer."""

    server_id: str | None
    server: ServerConfig | None
    connected: bool
    remote_dir: str
    generation: int = 0
    ready: bool = False

    def __post_init__(self) -> None:
        connected = bool(self.connected)
        object.__setattr__(self, "connected", connected)
        object.__setattr__(self, "ready", bool(self.ready and connected))


@runtime_checkable
class FilesConnectionController(Protocol):
    """Connection lifecycle port consumed by the Files presentation."""

    @property
    def servers(self) -> Mapping[str, ServerConfig]: ...

    @property
    def service(self) -> FileTransferPort | None: ...

    @property
    def connected_server_id(self) -> str | None: ...

    @property
    def connected_server(self) -> ServerConfig | None: ...

    @property
    def ready(self) -> bool: ...

    def set_servers(self, servers: Mapping[str, ServerConfig]) -> None: ...

    def set_service(self, service: FileTransferPort | None) -> None: ...

    def set_server_id(self, server_id: str | None) -> None: ...

    def set_server_config(self, server: ServerConfig | None) -> None: ...

    def load_servers(self) -> Mapping[str, ServerConfig]: ...

    def connect(
        self,
        server_id: str,
        *,
        allowed_delete_roots: list[str] | None = None,
    ) -> tuple[FileTransferPort | None, FileTransferPort]: ...

    def mark_ready(self, ready: bool) -> None: ...

    def disconnect(self) -> FileTransferPort | None: ...

    def close_service(self, service: FileTransferPort) -> None: ...

    def teardown(self) -> None: ...

    def set_server(
        self,
        server_id: str | None,
        server: ServerConfig | None,
        service: FileTransferPort | None,
    ) -> None: ...

    def snapshot(self, remote_dir: str, *, ready: bool | None = None) -> FileTransferConnectionSnapshot: ...


class ApplicationFilesConnectionController:
    """Presentation connection state backed by the shared Files facade.

    Selecting a server creates only a small application-port adapter.  No SSH,
    SFTP, or transfer service is constructed or owned by the page.
    """

    def __init__(
        self,
        application: FilesApplication,
        *,
        status_cb: Callable[[str], None],
        log_cb: Callable[[str], None],
        server_loader: ServerLoader,
    ) -> None:
        self._application = application
        self._status_cb = status_cb
        self._log_cb = log_cb
        self._server_loader = server_loader
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
            self._servers = dict(getattr(config, "servers"))
        except Exception as exc:  # noqa: BLE001 - user-facing config boundary
            self._servers = {}
            self._status_cb(f"No servers configured: {exc}")
        return self._servers

    def connect(
        self,
        server_id: str,
        *,
        allowed_delete_roots: list[str] | None = None,
    ) -> tuple[FileTransferPort | None, FileTransferPort]:
        del allowed_delete_roots
        server = self._servers[server_id]
        old_service = self._service
        service = FacadeFileTransferPort(self._application, server_id)
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
        self._connected_server_id = None
        self._connected_server = None
        self._ready = False
        self._generation += 1
        return service

    def close_service(self, service: FileTransferPort) -> None:
        try:
            service.close()
        except Exception as exc:  # noqa: BLE001 - teardown is best effort
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


__all__ = [
    "ApplicationFilesConnectionController",
    "FileTransferConnectionSnapshot",
    "FilesConnectionController",
]
