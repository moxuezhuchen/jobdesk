"""SSH/SFTP session helpers — thin factory functions for creating connections.

Used by both CLI and GUI. Does not depend on PySide6.
"""

from ..remote.sftp import SFTPClientWrapper
from ..remote.ssh import SSHClientWrapper


def create_ssh_client(server_config) -> SSHClientWrapper:
    return SSHClientWrapper(server_config, timeout=15)


def create_sftp_client(ssh_client) -> SFTPClientWrapper:
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
