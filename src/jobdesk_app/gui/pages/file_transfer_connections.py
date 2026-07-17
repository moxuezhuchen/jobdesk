"""Connection coordinator for the Files page.

Owns server list, active SSH/SFTP connection state, and FileTransferService
lifecycle. Independent of Qt widgets except via callback hooks. The page
creates this object in ``__init__`` and forwards every user action to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Protocol

from ...config.schema import ServerConfig
from ...config.servers import load_servers
from ...services.file_transfer_service import FileTransferService

if TYPE_CHECKING:
    from ..session import SFTPClientWrapper, SSHClientWrapper


class _ConnectionFactory(Protocol):
    """Callable that produces a connected (ssh, sftp) pair."""

    def __call__(self) -> object: ...


class ConnectionsCoordinator:
    """Server list + active SSH/SFTP connection state, Qt-free."""

    def __init__(
        self,
        *,
        status_cb: Callable[[str], None],
        log_cb: Callable[[str], None],
        create_ssh: Callable[..., SSHClientWrapper],
        create_sftp: Callable[..., SFTPClientWrapper],
        run_tasks_provider: Callable[[], list[Any]],
    ) -> None:
        self._status_cb = status_cb
        self._log_cb = log_cb
        self._create_ssh = create_ssh
        self._create_sftp = create_sftp
        self._run_tasks_provider = run_tasks_provider
        self._servers: dict[str, ServerConfig] = {}
        self._service: FileTransferService | None = None
        self._connected_server_id: str | None = None
        self._connected_server: ServerConfig | None = None

    # -- Properties mirroring the page's old attributes ----------------------

    @property
    def servers(self) -> dict[str, ServerConfig]:
        return self._servers

    @property
    def service(self) -> FileTransferService | None:
        return self._service

    @property
    def connected_server_id(self) -> str | None:
        return self._connected_server_id

    @property
    def connected_server(self) -> ServerConfig | None:
        return self._connected_server

    # -- Server list ----------------------------------------------------------

    def load_servers(self) -> dict[str, ServerConfig]:
        """Re-read ``servers.yaml`` and return the parsed dict.

        On failure the internal server list is cleared and the page should
        surface a status message via :attr:`status_cb`.
        """
        try:
            cfg = load_servers()
        except Exception as exc:
            self._servers = {}
            self._status_cb(f"No servers configured: {exc}")
            return self._servers
        self._servers = cfg.servers
        return self._servers

    # -- Lifecycle ------------------------------------------------------------

    def teardown(self) -> None:
        """Close any active service. Idempotent."""
        if self._service is not None:
            try:
                self._service.close()
            except Exception as exc:  # noqa: BLE001 -- teardown best-effort
                self._log_cb(f"Error closing service: {exc}")
            self._service = None

    def set_server(
        self,
        server_id: str | None,
        server: ServerConfig | None,
        service: FileTransferService | None,
    ) -> None:
        """Set connection state without triggering connect/teardown."""
        self._connected_server_id = server_id
        self._connected_server = server
        self._service = service
