"""SSH/SFTP session helpers — thin factory functions for creating connections.

Used by both CLI and GUI. Does not depend on PySide6.
"""

from __future__ import annotations

import paramiko

from ..config.schema import ServerConfig
from ..remote.sftp import SFTPClientWrapper
from ..remote.ssh import SSHClientWrapper


def create_ssh_client(server_config: ServerConfig, timeout: float | None = None) -> SSHClientWrapper:
    return SSHClientWrapper(server_config, timeout=15)


def create_sftp_client(ssh: paramiko.SSHClient) -> SFTPClientWrapper:
    return SFTPClientWrapper.from_ssh(ssh)


class ConnectedSFTP:
    """Wraps an SFTP client and its owning SSH client so both close together.

    Delegates attribute access to the SFTP wrapper; closing also closes the SSH
    transport so callers (CLI and GUI alike) don't leak SSH connections.
    """

    ssh: paramiko.SSHClient
    sftp: SFTPClientWrapper

    def __init__(self, ssh: paramiko.SSHClient, sftp: SFTPClientWrapper) -> None:
        self._ssh = ssh
        self._sftp = sftp

    def __getattr__(self, name: str) -> object:
        return getattr(self._sftp, name)

    def close(self) -> None:
        self._sftp.close()
        self._ssh.close()

    def __enter__(self) -> ConnectedSFTP:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
