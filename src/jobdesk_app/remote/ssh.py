"""SSH 客户端封装。

基于 paramiko 的 SSH 连接管理，支持连接、命令执行、上下文管理器。
不包含业务逻辑，仅提供基础 SSH 通道。
"""

import os
import select
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paramiko

from ..config.schema import ServerConfig
from .errors import SSHCommandError, SSHConnectionError

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# Global lock + cooldown to prevent spawning many concurrent wsl.exe processes.
_wsl_boot_lock = threading.Lock()
_wsl_boot_last_attempt: float | None = None
_WSL_BOOT_COOLDOWN = 10.0  # seconds


def _is_local_port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    """Check if a local TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


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
        # Trust only when the accepted key can be persisted for later verification.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        host_keys = client.get_host_keys()
        host_keys.add(hostname, key.get_name(), key)
        host_keys.save(str(self._path))


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
        self._start_wsl_if_configured()
        self._client = paramiko.SSHClient()

        # Load known_hosts; unknown keys require explicit opt-in to trust-on-first-use.
        known_hosts = Path(os.path.expanduser("~/.ssh/known_hosts"))
        try:
            if known_hosts.is_file():
                self._client.load_host_keys(str(known_hosts))
        except OSError:
            pass
        if getattr(self._server, "trust_on_first_use", False):
            self._client.set_missing_host_key_policy(_AutoAddAndSavePolicy(known_hosts))
        else:
            self._client.set_missing_host_key_policy(paramiko.RejectPolicy())

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

    def _start_wsl_if_configured(self) -> None:
        global _wsl_boot_last_attempt
        distro = self._server.wsl_distro
        if not distro or sys.platform != "win32":
            return
        if self._server.host not in _LOCAL_HOSTS:
            return
        if _is_local_port_open(self._server.host, self._server.port):
            return

        with _wsl_boot_lock:
            # Re-check after acquiring lock (another thread may have booted WSL)
            if _is_local_port_open(self._server.host, self._server.port):
                return
            # Cooldown based on last attempt (success or failure)
            now = time.monotonic()
            if (
                _wsl_boot_last_attempt is not None
                and now - _wsl_boot_last_attempt < _WSL_BOOT_COOLDOWN
            ):
                return
            _wsl_boot_last_attempt = now
            try:
                creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                subprocess.run(
                    ["wsl.exe", "-d", distro, "--", "true"],
                    check=True,
                    capture_output=True,
                    timeout=self._timeout,
                    creationflags=creationflags,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise SSHConnectionError(
                    f"无法启动 WSL 发行版 {distro!r}: {exc}",
                    host=self._server.host,
                    port=self._server.port,
                ) from exc

    def close(self) -> None:
        """关闭 SSH 连接。"""
        if self._client:
            self._client.close()
            self._client = None

    # -- 命令执行 -------------------------------------------------------

    def open_session(self):
        """Open a raw SSH channel session. Raises SSHConnectionError if not connected."""
        if self._client is None:
            raise SSHConnectionError("未连接，请先调用 connect()")
        return self._client.get_transport().open_session()

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
            # Drain stdout and stderr concurrently to prevent deadlock.
            channel = stdout.channel
            channel.settimeout(_timeout)
            out_chunks = []
            err_chunks = []
            deadline = t0 + _timeout
            # Wait efficiently with select on the channel's fileno instead of
            # busy-polling with time.sleep — that 50ms sleep used to dominate
            # the latency of every short SSH command (status checks etc.).
            # When the channel does not support select (e.g. a MagicMock in
            # unit tests, or a closing channel), fall back to a short sleep
            # so we never busy-loop.
            while not channel.exit_status_ready() or channel.recv_ready() or channel.recv_stderr_ready():
                now = time.monotonic()
                if now > deadline:
                    raise TimeoutError(f"Command timed out after {_timeout}s")
                drained = False
                if channel.recv_ready():
                    out_chunks.append(channel.recv(65536))
                    drained = True
                if channel.recv_stderr_ready():
                    err_chunks.append(channel.recv_stderr(65536))
                    drained = True
                if drained:
                    continue
                remaining = deadline - now
                if remaining <= 0:
                    raise TimeoutError(f"Command timed out after {_timeout}s")
                # Cap select wait at 0.01s so exit-status arrivals that don't
                # also wake the channel pipe still get noticed promptly.
                wait = remaining if remaining < 0.01 else 0.01
                try:
                    select.select([channel], [], [], wait)
                except (OSError, ValueError, TypeError):
                    time.sleep(0.005)
            # Drain any data that arrived alongside the exit-status packet.
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
