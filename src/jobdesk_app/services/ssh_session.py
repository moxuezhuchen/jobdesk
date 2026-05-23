"""SSH/SFTP session helpers — thin factory functions for creating connections.

Used by both CLI and GUI. Does not depend on PySide6.
"""

from ..remote.sftp import SFTPClientWrapper
from ..remote.ssh import SSHClientWrapper


def create_ssh_client(server_config) -> SSHClientWrapper:
    return SSHClientWrapper(server_config, timeout=15)


def create_sftp_client(ssh_client) -> SFTPClientWrapper:
    return SFTPClientWrapper.from_ssh(ssh_client)
