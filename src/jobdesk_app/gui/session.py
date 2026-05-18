"""远程会话辅助 — GUI 通过此模块创建 SSH/SFTP 连接，页面不直接操作 paramiko。"""

from ..remote.ssh import SSHClientWrapper
from ..remote.sftp import SFTPClientWrapper


def create_ssh_client(server_config) -> SSHClientWrapper:
    """从 ServerConfig 创建 SSH 连接。

    也可以通过 ProjectContext 调用:
        create_ssh_client(ctx.server_config)
    """
    return SSHClientWrapper(server_config, timeout=15)


def create_sftp_client(ssh_client) -> SFTPClientWrapper:
    """从已有 SSHClientWrapper 打开 SFTP channel。"""
    return SFTPClientWrapper.from_ssh(ssh_client)
