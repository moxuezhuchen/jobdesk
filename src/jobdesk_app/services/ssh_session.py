"""SSH/SFTP session helpers — thin factory functions for creating connections.

Used by both CLI and GUI. Does not depend on PySide6.

The concrete remote wrappers are imported inside their factories.  Importing
this module is part of the GUI composition path, while Paramiko is only
needed once a connection is actually requested.  Keeping the imports local
preserves the public factory functions (and their monkeypatch seams) without
making every GUI import pay the SSH transport startup cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..remote.sftp import SFTPClientWrapper
    from ..remote.ssh import SSHClientWrapper


def is_authentication_error(exc: BaseException) -> bool:
    """Return whether *exc* is Paramiko's authentication failure.

    Keep the transport dependency behind the same connection-time boundary as
    the concrete client factories.  Error classification is only exercised
    after an SSH operation has been attempted, so importing Paramiko here does
    not add it back to the GUI cold-import path.
    """
    from paramiko.ssh_exception import AuthenticationException

    return isinstance(exc, AuthenticationException)


def is_bad_host_key_error(exc: BaseException) -> bool:
    """Return whether *exc* is Paramiko's host-key mismatch failure."""
    from paramiko.ssh_exception import BadHostKeyException

    return isinstance(exc, BadHostKeyException)


def create_ssh_client(server_config) -> SSHClientWrapper:
    from ..remote.ssh import SSHClientWrapper

    return SSHClientWrapper(server_config, timeout=15)


def create_sftp_client(ssh_client) -> SFTPClientWrapper:
    from ..remote.sftp import SFTPClientWrapper

    return SFTPClientWrapper.from_ssh(ssh_client)


class ConnectedSFTP:
    """Wraps an SFTP client and its owning SSH client so both close together.

    Delegates attribute access to the SFTP wrapper; closing also closes the SSH
    transport so callers (CLI and GUI alike) don't leak SSH connections.
    """

    def __init__(self, ssh, sftp):
        self._ssh = ssh
        self._sftp = sftp

    def __getattr__(self, name):
        return getattr(self._sftp, name)

    def close(self):
        self._sftp.close()
        self._ssh.close()
