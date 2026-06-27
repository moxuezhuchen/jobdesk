"""Structural interfaces used by application services."""

from __future__ import annotations

from typing import Any, Protocol


class SSHResultProtocol(Protocol):
    exit_code: int
    stdout: str
    stderr: str


class SSHChannelProtocol(Protocol):
    def exec_command(self, command: str) -> Any: ...

    def settimeout(self, timeout: float | None) -> None: ...

    def recv(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class SSHClientProtocol(Protocol):
    def connect(self) -> None: ...

    def close(self) -> None: ...

    def run(
        self,
        command: str,
        timeout: int | None = None,
        check: bool = False,
    ) -> SSHResultProtocol: ...

    def open_session(self) -> SSHChannelProtocol: ...


class SFTPClientProtocol(Protocol):
    def close(self) -> None: ...
