"""Application-facing connection orchestration for the Files page.

The Qt Files page is a composition root.  It may provide callbacks and
consume snapshots, but it must not know how an SSH/SFTP pair becomes a
``FileTransferService``.  This module owns that wiring and deliberately has
no Qt dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping

from ..config.schema import ServerConfig
from ..config.servers import load_servers
from ..services.file_transfer_service import FileTransferService
from ..services.ssh_session import ConnectedSFTP, create_sftp_client, create_ssh_client

if TYPE_CHECKING:
    from ..services.session_pool import SessionPool


@dataclass(frozen=True, slots=True)
class FileTransferConnectionSnapshot:
    """Immutable connection state crossing the Qt/application boundary."""

    server_id: str | None
    server: ServerConfig | None
    connected: bool
    remote_dir: str
    generation: int = 0
    ready: bool = False

    def __post_init__(self) -> None:
        """Normalize status values without retaining the mutable service.

        Older positional callers passed a service in the third slot.  Treat
        that value only as the legacy connected hint so those patch seams
        remain usable while the boundary carries booleans.
        """
        connected = bool(self.connected)
        object.__setattr__(self, "connected", connected)
        object.__setattr__(self, "ready", bool(self.ready and connected))


class FilesConnectionController:
    """Own server configuration and ``FileTransferService`` lifecycle.

    Transport factories are constructor dependencies so the application
    layer remains deterministic in tests and the page never imports concrete
    SSH/SFTP construction helpers.  ``set_*`` methods are intentionally kept
    as a small compatibility seam for older GUI fixtures; new callers should
    use :meth:`connect`, :meth:`disconnect`, and :meth:`snapshot`.
    """

    def __init__(
        self,
        *,
        status_cb: Callable[[str], None],
        log_cb: Callable[[str], None],
        create_ssh: Callable[..., Any] = create_ssh_client,
        create_sftp: Callable[..., Any] = create_sftp_client,
        session_pool: SessionPool | None = None,
        allowed_delete_roots_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        self._status_cb = status_cb
        self._log_cb = log_cb
        self._create_ssh = create_ssh
        self._create_sftp = create_sftp
        self._session_pool = session_pool
        self._allowed_delete_roots_provider = allowed_delete_roots_provider or (lambda: [])
        self._servers: dict[str, ServerConfig] = {}
        self._service: FileTransferService | None = None
        self._connected_server_id: str | None = None
        self._connected_server: ServerConfig | None = None
        self._generation = 0
        self._ready = False

    @property
    def servers(self) -> dict[str, ServerConfig]:
        """Return the loaded server mapping for compatibility with the page."""
        return self._servers

    def set_servers(self, servers: Mapping[str, ServerConfig]) -> None:
        self._servers = dict(servers)

    @property
    def service(self) -> FileTransferService | None:
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

    def set_service(self, service: FileTransferService | None) -> None:
        """Compatibility injection hook used by existing GUI tests."""
        self._service = service
        if service is None:
            self._ready = False

    def set_server_id(self, server_id: str | None) -> None:
        self._connected_server_id = server_id

    def set_server_config(self, server: ServerConfig | None) -> None:
        self._connected_server = server

    def load_servers(self) -> dict[str, ServerConfig]:
        """Load ``servers.yaml`` and keep the last good result isolated."""
        try:
            config = load_servers()
        except Exception as exc:  # noqa: BLE001 -- user-facing config boundary
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
    ) -> tuple[FileTransferService | None, FileTransferService]:
        """Replace the active service and return ``(old, new)``.

        Closing the old service can block on a remote transport, so the
        caller may schedule :meth:`close_service` on its existing worker
        infrastructure.  Ownership of the actual service construction and
        close operation remains here.
        """
        server = self._servers[server_id]
        old_service = self._service
        pooled = self._session_pool is not None
        service = FileTransferService(
            self._build_service_factory(server, server_id if pooled else None),
            allowed_delete_roots=(
                list(allowed_delete_roots)
                if allowed_delete_roots is not None
                else list(self._allowed_delete_roots_provider())
            ),
            persistent_session=not pooled,
        )
        self._service = service
        self._connected_server_id = server_id
        self._connected_server = server
        self._ready = False
        self._generation += 1
        return old_service, service

    def mark_ready(self, ready: bool) -> None:
        self._ready = bool(ready and self._service is not None)

    def disconnect(self) -> FileTransferService | None:
        """Detach and return the current service without blocking."""
        service = self._service
        self._service = None
        self._ready = False
        self._connected_server_id = None
        self._connected_server = None
        self._generation += 1
        return service

    def close_service(self, service: FileTransferService) -> None:
        """Close a detached service; intended for a background worker."""
        try:
            service.close()
        except Exception as exc:  # noqa: BLE001 -- teardown is best effort
            self._log_cb(f"Error closing service: {exc}")

    def teardown(self) -> None:
        """Detach and synchronously close the active service."""
        service = self.disconnect()
        if service is not None:
            self.close_service(service)

    def set_server(
        self,
        server_id: str | None,
        server: ServerConfig | None,
        service: FileTransferService | None,
    ) -> None:
        """Compatibility state injection used by the legacy page facade."""
        self._connected_server_id = server_id
        self._connected_server = server
        self._service = service
        self._ready = bool(service is not None and self._ready)

    def snapshot(self, remote_dir: str, *, ready: bool | None = None) -> FileTransferConnectionSnapshot:
        """Return an immutable application-facing connection snapshot."""
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

    def _build_service_factory(self, server: ServerConfig, server_id: str | None) -> Callable[[], Any]:
        if self._session_pool is not None and server_id:
            from ..services.session_pool import pooled_sftp_factory

            return pooled_sftp_factory(self._session_pool, server_id, server)

        def factory() -> ConnectedSFTP:
            ssh = self._create_ssh(server)
            ssh.connect()
            sftp = self._create_sftp(ssh)
            return ConnectedSFTP(ssh, sftp)

        return factory


__all__ = ["FileTransferConnectionSnapshot", "FilesConnectionController"]
