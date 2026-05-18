"""远程操作异常类型定义。"""


class RemoteError(Exception):
    """所有远程操作异常的基类。"""


class SSHConnectionError(RemoteError):
    """SSH 连接失败。"""

    def __init__(self, message: str, host: str = "", port: int = 22):
        self.host = host
        self.port = port
        super().__init__(message)


class SSHCommandError(RemoteError):
    """远程命令执行失败（非零退出码或执行异常）。

    不包含密码或私钥信息。
    """

    def __init__(
        self,
        message: str,
        command: str = "",
        exit_code: int | None = None,
        stderr: str = "",
        stdout: str = "",
        host: str = "",
    ):
        self.command = command
        self.exit_code = exit_code
        self.stderr = stderr
        self.stdout = stdout
        self.host = host
        summary = f"{message} [command={command!r}, exit_code={exit_code}]"
        if stderr:
            tail = stderr[-200:] if len(stderr) > 200 else stderr
            summary += f" stderr_tail={tail!r}"
        super().__init__(summary)


class RemotePathError(RemoteError):
    """远程路径相关错误。"""


class RemoteStatusError(RemoteError):
    """远程状态读取失败。"""
