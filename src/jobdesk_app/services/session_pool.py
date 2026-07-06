"""Thread-safe ownership and reuse of SSH/SFTP client pairs."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from .protocols import SFTPClientProtocol, SSHClientProtocol

_MAX_CREATE_ATTEMPTS = 2


@dataclass
class _Entry:
    mutex: threading.Lock = field(default_factory=threading.Lock)
    config: Any = None
    ssh: SSHClientProtocol | None = None
    sftp: SFTPClientProtocol | None = None
    active_leases: int = 0
    closing: bool = False
    last_used: float = 0.0


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
        self.ssh: SSHClientProtocol
        self.sftp: SFTPClientProtocol | None

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

    _ssh_factory: Callable[[Any], SSHClientProtocol]
    _sftp_factory: Callable[[SSHClientProtocol], SFTPClientProtocol]
    _metadata_lock: threading.Lock
    _entries: dict[str, _Entry]
    _closing: bool
    _max_idle_entries: int
    _idle_ttl_seconds: float

    def __init__(
        self,
        ssh_factory: Callable[[Any], SSHClientProtocol],
        sftp_factory: Callable[[SSHClientProtocol], SFTPClientProtocol],
        max_idle_entries: int = 5,
        idle_ttl_seconds: float = 300.0,
    ) -> None:
        self._ssh_factory = ssh_factory
        self._sftp_factory = sftp_factory
        self._metadata_lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._closing = False
        self._max_idle_entries = max_idle_entries
        self._idle_ttl_seconds = idle_ttl_seconds

    def lease(
        self, server_id: str, server_config: Any, *, need_sftp: bool = True
    ) -> SessionLease:
        return SessionLease(
            self,
            server_id,
            server_config,
            need_sftp=need_sftp,
        )

    def close(self) -> None:
        clients_to_close: list[tuple[SFTPClientProtocol | None, SSHClientProtocol | None]] = []
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
    ) -> tuple[_Entry, SSHClientProtocol, SFTPClientProtocol | None]:
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
            entry.last_used = time.monotonic()

        old_clients: tuple[SFTPClientProtocol | None, SSHClientProtocol | None] = (None, None)
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
        clients: tuple[SFTPClientProtocol | None, SSHClientProtocol | None] = (None, None)
        with self._metadata_lock:
            entry.active_leases -= 1
            if entry.closing and entry.active_leases == 0:
                clients = self._detach_clients(entry)
        entry.mutex.release()
        self._close_clients(*clients)
        self._evict_idle_entries()

    def _evict_idle_entries(self) -> None:
        if len(self._entries) <= self._max_idle_entries:
            return
        now = time.monotonic()
        to_close: list[tuple[SFTPClientProtocol | None, SSHClientProtocol | None]] = []
        with self._metadata_lock:
            for entry in list(self._entries.values()):
                if entry.active_leases == 0 and (now - entry.last_used) > self._idle_ttl_seconds:
                    entry.closing = True
                    clients = self._detach_clients(entry)
                    to_close.append(clients)
        for clients in to_close:
            self._close_clients(*clients)

    def _create_clients(
        self, server_config: Any, *, need_sftp: bool
    ) -> tuple[SSHClientProtocol, SFTPClientProtocol | None]:
        ssh = self._ssh_factory(server_config)
        try:
            ssh.connect()
            sftp = self._sftp_factory(ssh) if need_sftp else None
        except BaseException:
            self._close_clients(None, ssh)
            raise
        return ssh, sftp

    @staticmethod
    def _is_alive(client: SSHClientProtocol | SFTPClientProtocol) -> bool:
        try:
            return bool(client.is_alive())
        except Exception:
            return False

    @staticmethod
    def _detach_clients(
        entry: _Entry,
    ) -> tuple[SFTPClientProtocol | None, SSHClientProtocol | None]:
        clients = entry.sftp, entry.ssh
        entry.sftp = None
        entry.ssh = None
        entry.config = None
        return clients

    @staticmethod
    def _close_clients(
        sftp: SFTPClientProtocol | None, ssh: SSHClientProtocol | None
    ) -> None:
        for client in (sftp, ssh):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
