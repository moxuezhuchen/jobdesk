"""SSH 客户端封装。

基于 paramiko 的 SSH 连接管理，支持连接、命令执行、上下文管理器。
不包含业务逻辑，仅提供基础 SSH 通道。
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paramiko

from ..config.schema import ServerConfig
from .errors import SSHCommandError, SSHConnectionError


@dataclass
class SSHResult:
    """单次远程命令执行结果。"""

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class _AutoAddAndSavePolicy(paramiko.MissingHostKeyPolicy):
    """Trust-on-first-use: accept unknown keys and append to known_hosts."""

    def __init__(self, known_hosts_path: Path):
        self._path = known_hosts_path

    def missing_host_key(self, client, hostname, key):
        # Accept the key and persist it
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            host_keys = client.get_host_keys()
            host_keys.add(hostname, key.get_name(), key)
            host_keys.save(str(self._path))
        except OSError:
            pass  # Non-fatal: connection still proceeds


class SSHClientWrapper:
    """基于 paramiko 的 SSH 客户端封装。

    用法：
        with SSHClientWrapper(server) as ssh:
            result = ssh.run("echo hello")
    """

    def __init__(self, server: ServerConfig, timeout: int = 15):
        self._server = server
        self._timeout = timeout
        self._client: paramiko.SSHClient | None = None

    # -- 连接 ---------------------------------------------------------

    def connect(self) -> None:
        """建立 SSH 连接。失败时抛出 SSHConnectionError。"""
        self._client = paramiko.SSHClient()

        # Load known_hosts; accept on first use and persist
        known_hosts = Path(os.path.expanduser("~/.ssh/known_hosts"))
        try:
            if known_hosts.is_file():
                self._client.load_host_keys(str(known_hosts))
        except OSError:
            pass
        self._client.set_missing_host_key_policy(_AutoAddAndSavePolicy(known_hosts))

        connect_kwargs: dict[str, Any] = {
            "hostname": self._server.host,
            "port": self._server.port,
            "username": self._server.username,
            "timeout": self._timeout,
            "banner_timeout": self._timeout,
        }

        if self._server.auth_method == "key":
            key_path = self._server.key_path
            if key_path:
                expanded = os.path.expanduser(key_path)
                pkey = self._resolve_key(expanded)
                connect_kwargs["pkey"] = pkey
            else:
                # 无 key_path 时尝试默认 SSH agent / 默认密钥
                pass
        elif self._server.auth_method == "password":
            raise SSHConnectionError(
                f"password 认证在代码中不支持直接传入密码（服务器 {self._server.display_name!r}）。"
                f"请使用 key 认证。",
                host=self._server.host,
                port=self._server.port,
            )

        try:
            self._client.connect(**connect_kwargs)
        except paramiko.SSHException as e:
            raise SSHConnectionError(
                f"SSH 连接失败: {e}",
                host=self._server.host,
                port=self._server.port,
            ) from e
        except OSError as e:
            raise SSHConnectionError(
                f"网络错误: {e}",
                host=self._server.host,
                port=self._server.port,
            ) from e
        except Exception as e:
            raise SSHConnectionError(
                f"连接失败: {type(e).__name__}: {e}",
                host=self._server.host,
                port=self._server.port,
            ) from e

    def close(self) -> None:
        """关闭 SSH 连接。"""
        if self._client:
            self._client.close()
            self._client = None

    # -- 命令执行 -------------------------------------------------------

    def run(self, command: str, timeout: int | None = None, check: bool = False) -> SSHResult:
        """在远程服务器执行命令。

        Args:
            command: 要执行的 shell 命令。
            timeout: 命令超时（秒），默认使用连接 timeout。
            check: 若为 True，exit_code != 0 时抛 SSHCommandError。

        Returns:
            SSHResult 实例。
        """
        if self._client is None:
            raise SSHConnectionError("未连接，请先调用 connect()")

        t0 = time.monotonic()
        _timeout = timeout or self._timeout
        try:
            stdin, stdout, stderr = self._client.exec_command(
                command, timeout=_timeout
            )
            # Drain stdout and stderr concurrently to prevent deadlock
            channel = stdout.channel
            channel.settimeout(_timeout)
            out_chunks = []
            err_chunks = []
            deadline = t0 + _timeout
            while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Command timed out after {_timeout}s")
                if channel.recv_ready():
                    out_chunks.append(channel.recv(65536))
                if channel.recv_stderr_ready():
                    err_chunks.append(channel.recv_stderr(65536))
                if not channel.recv_ready() and not channel.recv_stderr_ready() and not channel.exit_status_ready():
                    time.sleep(0.05)
            # Drain remaining
            while channel.recv_ready():
                out_chunks.append(channel.recv(65536))
            while channel.recv_stderr_ready():
                err_chunks.append(channel.recv_stderr(65536))
            exit_code = channel.recv_exit_status()
            stdout_str = b"".join(out_chunks).decode("utf-8", errors="replace").rstrip("\n")
            stderr_str = b"".join(err_chunks).decode("utf-8", errors="replace").rstrip("\n")
        except Exception as e:
            dt = time.monotonic() - t0
            raise SSHCommandError(
                f"命令执行异常: {e}",
                command=command,
                host=self._server.host,
                stdout="",
                stderr=str(e),
            ) from e

        dt = time.monotonic() - t0
        result = SSHResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout_str,
            stderr=stderr_str,
            duration_seconds=round(dt, 3),
        )

        if check and exit_code != 0:
            raise SSHCommandError(
                "命令返回非零退出码",
                command=command,
                exit_code=exit_code,
                stdout=stdout_str,
                stderr=stderr_str,
                host=self._server.host,
            )

        return result

    def test_connection(self) -> bool:
        """测试连接是否可用（执行一个简单命令）。"""
        try:
            self.run("echo jobdesk-alive", timeout=10)
            return True
        except Exception:
            return False

    # -- 上下文管理器 ---------------------------------------------------

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # -- 内部 ---------------------------------------------------------

    @staticmethod
    def _resolve_key(key_path: str) -> paramiko.PKey:
        p = Path(key_path)
        if not p.exists():
            raise SSHConnectionError(f"SSH 私钥不存在: {key_path}")
        for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                return key_class.from_private_key_file(str(p))
            except paramiko.SSHException:
                continue
        raise SSHConnectionError(f"无法识别或加载私钥: {key_path}")
