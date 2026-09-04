"""SSH/SFTP session helpers (re-export + context managers)."""

from contextlib import contextmanager

from .dependencies import create_sftp as create_sftp_client
from .dependencies import create_ssh as create_ssh_client


@contextmanager
def ssh_session(server):
    """Yield a connected SSH client, guaranteed closed on exit."""
    ssh = create_ssh_client(server)
    ssh.connect()
    try:
        yield ssh
    finally:
        ssh.close()


@contextmanager
def sftp_session(server):
    """Yield a connected ``(ssh, sftp)`` pair, both guaranteed closed on exit."""
    with ssh_session(server) as ssh:
        sftp = create_sftp_client(ssh)
        try:
            yield ssh, sftp
        finally:
            sftp.close()
