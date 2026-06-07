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


def _ssh_config_path() -> Path:
    return Path(os.path.expanduser("~/.ssh/config"))


def _load_ssh_config_lookup(alias: str) -> dict[str, object]:
    path = _ssh_config_path()
    try:
        if not alias or not path.is_file():
            return {}
    except OSError:
        return {}
    config = paramiko.SSHConfig()
    try:
        with path.open("r", encoding="utf-8") as handle:
            config.parse(handle)
    except OSError:
        return {}
    return config.lookup(alias)


def _first_identity_file(lookup: dict[str, object]) -> str:
    value = lookup.get("identityfile")
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def _proxy_command_text(template: str, host: str, port: int) -> str:
    return template.replace("%h", host).replace("%p", str(port))


def _proxy_jump_specs(proxy_jump: str) -> list[str]:
    proxy_jump = proxy_jump.strip()
    if not proxy_jump or proxy_jump.lower() == "none":
        return []
    return [jump.strip() for jump in proxy_jump.split(",") if jump.strip()]


def _split_jump_spec(jump: str) -> tuple[str | None, str, int | None]:
    user: str | None = None
    host_port = jump.strip()
    if "@" in host_port:
        user, host_port = host_port.split("@", 1)
    if host_port.startswith("[") and "]" in host_port:
        host, rest = host_port[1:].split("]", 1)
        if rest.startswith(":") and rest[1:].isdigit():
            return user, host, int(rest[1:])
        return user, host, None
    if ":" in host_port:
        host, maybe_port = host_port.rsplit(":", 1)
        if maybe_port.isdigit():
            return user, host, int(maybe_port)
    return user, host_port, None


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
        # Persist to the app-scoped file alone so the user's global known_hosts
        # entries are never copied into it.
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            client.get_host_keys().add(hostname, key.get_name(), key)
            app_keys = paramiko.HostKeys()
            if self._path.is_file():
                app_keys.load(str(self._path))
            app_keys.add(hostname, key.get_name(), key)
            app_keys.save(str(self._path))
        except OSError as exc:
            raise SSHConnectionError(
                f"Host key accepted but known_hosts persistence failed: {exc}",
                host=hostname,
            ) from exc


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
        self._jump_clients: list[paramiko.SSHClient] = []

    # -- 连接 ---------------------------------------------------------

    def connect(self) -> None:
        """建立 SSH 连接。失败时抛出 SSHConnectionError。"""
        self._start_wsl_if_configured()
        self._client = paramiko.SSHClient()

        # Load the user's global known_hosts for verification, plus an
        # app-scoped file. Trust-on-first-use only ever writes the app-scoped
        # file, so the user's ~/.ssh/known_hosts is never rewritten.
        from ..app_paths import get_app_data_dir

        app_known_hosts = get_app_data_dir() / "known_hosts"
        self._configure_host_keys(self._client, app_known_hosts)

        ssh_lookup = _load_ssh_config_lookup(self._server.ssh_access.config_alias)
        hostname = str(ssh_lookup.get("hostname") or self._server.host)
        username = str(ssh_lookup.get("user") or self._server.username)
        lookup_port = ssh_lookup.get("port")
        port = int(str(lookup_port)) if lookup_port is not None else self._server.port
        key_path = self._server.key_path or _first_identity_file(ssh_lookup)
        proxy_command = (
            self._server.ssh_access.proxy_command.strip()
            or str(ssh_lookup.get("proxycommand") or "").strip()
        )
        proxy_jump = (
            self._server.ssh_access.proxy_jump.strip()
            or str(ssh_lookup.get("proxyjump") or "").strip()
        )
        if self._server.auth_method == "password":
            raise SSHConnectionError(
                f"password 认证在代码中不支持直接传入密码（服务器 {self._server.display_name!r}）。"
                f"请使用 key 认证。",
                host=hostname,
                port=port,
            )

        connect_kwargs: dict[str, Any] = {
            "hostname": hostname,
            "port": port,
            "username": username,
            "timeout": self._timeout,
            "banner_timeout": self._timeout,
        }
        if proxy_command:
            connect_kwargs["sock"] = paramiko.ProxyCommand(
                _proxy_command_text(proxy_command, hostname, port)
            )
        elif proxy_jump:
            connect_kwargs["sock"] = self._open_proxy_jump_channel(
                proxy_jump,
                hostname,
                port,
                username,
                key_path,
                app_known_hosts,
            )

        if self._server.auth_method == "key":
            if key_path:
                expanded = os.path.expanduser(key_path)
                pkey = self._resolve_key(expanded)
                connect_kwargs["pkey"] = pkey
            else:
                # 无 key_path 时尝试默认 SSH agent / 默认密钥
                pass

        try:
            self._client.connect(**connect_kwargs)
        except paramiko.SSHException as e:
            self.close()
            raise SSHConnectionError(
                f"SSH 连接失败: {e}",
                host=hostname,
                port=port,
            ) from e
        except OSError as e:
            self.close()
            raise SSHConnectionError(
                f"网络错误: {e}",
                host=hostname,
                port=port,
            ) from e
        except Exception as e:
            self.close()
            raise SSHConnectionError(
                f"连接失败: {type(e).__name__}: {e}",
                host=hostname,
                port=port,
            ) from e

        # Keep reused (persistent) connections alive and detect dead peers, so
        # callers that hold a connection across operations avoid silent stalls.
        transport = self._client.get_transport()
        if transport is not None:
            transport.set_keepalive(15)

    def is_alive(self) -> bool:
        """True if connected and the underlying transport is still active."""
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def _configure_host_keys(self, client: paramiko.SSHClient, app_known_hosts: Path) -> None:
        for kh in (Path(os.path.expanduser("~/.ssh/known_hosts")), app_known_hosts):
            try:
                if kh.is_file():
                    client.load_host_keys(str(kh))
            except OSError:
                pass
        if getattr(self._server, "trust_on_first_use", False):
            client.set_missing_host_key_policy(_AutoAddAndSavePolicy(app_known_hosts))
        else:
            client.set_missing_host_key_policy(paramiko.RejectPolicy())

    def _open_proxy_jump_channel(
        self,
        proxy_jump: str,
        target_host: str,
        target_port: int,
        fallback_username: str,
        fallback_key_path: str,
        app_known_hosts: Path,
    ) -> Any:
        previous_transport = None
        for jump in _proxy_jump_specs(proxy_jump):
            user, alias, explicit_port = _split_jump_spec(jump)
            jump_lookup = _load_ssh_config_lookup(alias)
            jump_host = str(jump_lookup.get("hostname") or alias)
            jump_user = user or str(jump_lookup.get("user") or fallback_username)
            lookup_port = jump_lookup.get("port")
            jump_port = (
                explicit_port
                if explicit_port is not None
                else int(str(lookup_port)) if lookup_port is not None else 22
            )
            jump_key_path = _first_identity_file(jump_lookup) or fallback_key_path

            jump_client = paramiko.SSHClient()
            self._configure_host_keys(jump_client, app_known_hosts)
            jump_kwargs: dict[str, Any] = {
                "hostname": jump_host,
                "port": jump_port,
                "username": jump_user,
                "timeout": self._timeout,
                "banner_timeout": self._timeout,
            }
            if previous_transport is not None:
                try:
                    jump_kwargs["sock"] = previous_transport.open_channel(
                        "direct-tcpip",
                        (jump_host, jump_port),
                        ("", 0),
                    )
                except Exception as exc:
                    jump_client.close()
                    self.close()
                    raise SSHConnectionError(
                        f"ProxyJump channel to {jump_host}:{jump_port} failed: {exc}",
                        host=jump_host,
                        port=jump_port,
                    ) from exc
            if self._server.auth_method == "key" and jump_key_path:
                jump_kwargs["pkey"] = self._resolve_key(os.path.expanduser(jump_key_path))

            try:
                jump_client.connect(**jump_kwargs)
            except Exception as exc:
                jump_client.close()
                self.close()
                raise SSHConnectionError(
                    f"ProxyJump connection to {jump_host}:{jump_port} failed: {exc}",
                    host=jump_host,
                    port=jump_port,
                ) from exc
            self._jump_clients.append(jump_client)
            previous_transport = jump_client.get_transport()
            if previous_transport is None:
                jump_client.close()
                self.close()
                raise SSHConnectionError(
                    "ProxyJump connection did not create an SSH transport",
                    host=jump_host,
                    port=jump_port,
                )

        if previous_transport is None:
            raise SSHConnectionError("ProxyJump is empty", host=target_host, port=target_port)
        try:
            return previous_transport.open_channel(
                "direct-tcpip",
                (target_host, target_port),
                ("", 0),
            )
        except Exception as exc:
            self.close()
            raise SSHConnectionError(
                f"ProxyJump channel to {target_host}:{target_port} failed: {exc}",
                host=target_host,
                port=target_port,
            ) from exc

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
        while self._jump_clients:
            jump_client = self._jump_clients.pop()
            jump_client.close()

    # -- 命令执行 -------------------------------------------------------

    def open_session(self):
        """Open a raw SSH channel session. Raises SSHConnectionError if not connected."""
        if self._client is None:
            raise SSHConnectionError("未连接，请先调用 connect()")
        transport = self._client.get_transport()
        if transport is None or not transport.is_active():
            raise SSHConnectionError("SSH 传输不可用，请重新连接", host=self._server.host)
        return transport.open_session()

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
        encrypted = False
        for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                return key_class.from_private_key_file(str(p))
            except paramiko.PasswordRequiredException:
                encrypted = True
            except paramiko.SSHException:
                continue
        if encrypted:
            raise SSHConnectionError(
                f"SSH 私钥已加密，暂不支持带密码短语的私钥。请提供未加密的私钥，"
                f"或改用 ssh-agent（不配置 key_path）: {key_path}"
            )
        raise SSHConnectionError(f"无法识别或加载私钥: {key_path}")
