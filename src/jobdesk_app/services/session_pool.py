"""Thread-safe ownership and reuse of SSH/SFTP client pairs."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Generator

from .protocols import SFTPClient, SSHClient

_MAX_CREATE_ATTEMPTS = 2


@dataclass
class _Entry:
    mutex: threading.Lock = field(default_factory=threading.Lock)
    config: Any = None
    ssh: SSHClient | None = None
    sftp: SFTPClient | None = None
    active_leases: int = 0
    closing: bool = False


class SessionLease:
    """Exclusive lease for one server's SSH session and optional SFTP channel."""

    def __init__(
        self,
        pool: SessionPool,
        server_id: str,
        server_config: Any,
        *,
        need_sftp: bool,
    ):
        self._pool = pool
        self._server_id = server_id
        self._server_config = server_config
        self._need_sftp = need_sftp
        self._entry: _Entry | None = None
        self._released = False
        self.ssh: SSHClient
        self.sftp: SFTPClient | None

    def __enter__(self) -> SessionLease:
        if self._entry is not None:
            raise RuntimeError("session lease has already been entered")
        entry, ssh, sftp = self._pool._acquire(
            self._server_id,
            self._server_config,
            need_sftp=self._need_sftp,
        )
        self._entry = entry
        self.ssh = ssh
        self.sftp = sftp
        return self

    def release(self) -> None:
        if self._entry is None or self._released:
            return
        self._released = True
        self._pool._release(self._entry)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


class SessionPool:
    """Own one reusable, serialized SSH session per server with optional SFTP."""

    def __init__(
        self,
        ssh_factory: Callable[..., Any],
        sftp_factory: Callable[..., Any],
    ):
        # ssh_factory/sftp_factory are typed loosely because the public Protocol
        # aliases (SSHClient / SFTPClient) describe structural shape, not the
        # concrete wrapper classes (SSHClientWrapper / SFTPClientWrapper) that
        # callers actually pass in. Treating the factories as Callable[..., Any]
        # lets us accept any conforming factory without per-call-site ignores,
        # and matches the pattern already used in RunCoordinator.
        self._ssh_factory = ssh_factory
        self._sftp_factory = sftp_factory
        self._metadata_lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._closing = False

    def lease(
        self, server_id: str, server_config: Any, *, need_sftp: bool = True
    ) -> SessionLease:
        return SessionLease(
            self,
            server_id,
            server_config,
            need_sftp=need_sftp,
        )

    @contextmanager
    def acquire(
        self, server_id: str, server_config: Any, *, need_sftp: bool = True
    ) -> Generator[SessionLease, None, None]:
        """Context manager for acquiring a session lease with automatic release.

        This is a convenience wrapper around :meth:`lease` that automatically
        enters and exits the lease context.

        Example::

            with pool.acquire("wsl", config) as lease:
                ssh = lease.ssh
                # ... operations ...
            # Session is automatically released

        Args:
            server_id: Unique identifier for the server.
            server_config: Configuration object passed to the SSH factory.
            need_sftp: Whether to create an SFTP channel (default True).

        Yields:
            SessionLease: The acquired session lease with ssh/sftp populated.
        """
        lease = self.lease(server_id, server_config, need_sftp=need_sftp)
        lease.__enter__()
        try:
            yield lease
        finally:
            lease.__exit__(None, None, None)

    def close(self) -> None:
        clients_to_close: list[tuple[SFTPClient | None, SSHClient | None]] = []
        with self._metadata_lock:
            if self._closing:
                return
            self._closing = True
            for entry in self._entries.values():
                entry.closing = True
                if entry.active_leases == 0:
                    clients_to_close.append(self._detach_clients(entry))
        for clients in clients_to_close:
            self._close_clients(*clients)

    def _acquire(
        self, server_id: str, server_config: Any, *, need_sftp: bool
    ) -> tuple[_Entry, SSHClient, SFTPClient | None]:
        with self._metadata_lock:
            if self._closing:
                raise RuntimeError("session pool is closing")
            entry = self._entries.setdefault(server_id, _Entry())

        entry.mutex.acquire()
        with self._metadata_lock:
            if self._closing or entry.closing:
                entry.mutex.release()
                raise RuntimeError("session pool is closing")
            entry.active_leases += 1

        old_clients: tuple[SFTPClient | None, SSHClient | None] = (None, None)
        try:
            if entry.ssh is not None and (
                entry.config != server_config or not self._is_alive(entry.ssh)
            ):
                old_clients = self._detach_clients(entry)
            elif need_sftp and entry.sftp is not None and not self._is_alive(entry.sftp):
                old_clients = self._detach_clients(entry)
            self._close_clients(*old_clients)
            if entry.ssh is None:
                config_snapshot = deepcopy(server_config)
                for _attempt in range(_MAX_CREATE_ATTEMPTS):
                    ssh, sftp = self._create_clients(server_config, need_sftp=need_sftp)
                    if self._is_alive(ssh) and (
                        not need_sftp or (sftp is not None and self._is_alive(sftp))
                    ):
                        entry.ssh, entry.sftp = ssh, sftp
                        entry.config = config_snapshot
                        break
                    self._close_clients(sftp, ssh)
                else:
                    raise RuntimeError(
                        f"failed to create a live session after {_MAX_CREATE_ATTEMPTS} attempts"
                    )
            elif need_sftp and entry.sftp is None:
                try:
                    sftp = self._sftp_factory(entry.ssh)
                    if not self._is_alive(sftp):
                        self._close_clients(sftp, None)
                        raise RuntimeError("failed to create a live SFTP session")
                    entry.sftp = sftp
                except BaseException:
                    clients = self._detach_clients(entry)
                    self._close_clients(*clients)
                    raise
            return entry, entry.ssh, entry.sftp
        except BaseException:
            self._release(entry)
            raise

    def _release(self, entry: _Entry) -> None:
        clients: tuple[SFTPClient | None, SSHClient | None] = (None, None)
        with self._metadata_lock:
            entry.active_leases -= 1
            if entry.closing and entry.active_leases == 0:
                clients = self._detach_clients(entry)
        entry.mutex.release()
        self._close_clients(*clients)

    def _create_clients(
        self, server_config: Any, *, need_sftp: bool
    ) -> tuple[SSHClient, SFTPClient | None]:
        ssh = self._ssh_factory(server_config)
        try:
            ssh.connect()
            sftp = self._sftp_factory(ssh) if need_sftp else None
        except BaseException:
            self._close_clients(None, ssh)
            raise
        return ssh, sftp

    @staticmethod
    def _is_alive(client: SSHClient | SFTPClient) -> bool:
        try:
            return bool(client.is_alive())
        except Exception:
            return False

    @staticmethod
    def _detach_clients(
        entry: _Entry,
    ) -> tuple[SFTPClient | None, SSHClient | None]:
        clients = entry.sftp, entry.ssh
        entry.sftp = None
        entry.ssh = None
        entry.config = None
        return clients

    @staticmethod
    def _close_clients(
        sftp: SFTPClient | None, ssh: SSHClient | None
    ) -> None:
        for client in (sftp, ssh):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
