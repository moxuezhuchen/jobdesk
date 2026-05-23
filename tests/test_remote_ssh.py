"""M3 测试: remote/ssh.py — SSHClientWrapper mock 测试。

不依赖真实服务器，完全通过 mock paramiko.SSHClient 测试。
"""

from unittest.mock import MagicMock, patch

import paramiko
import pytest

from jobdesk_app.config.schema import AuthMethod, ServerConfig
from jobdesk_app.remote.errors import SSHCommandError, SSHConnectionError
from jobdesk_app.remote.ssh import SSHClientWrapper, SSHResult, _AutoAddAndSavePolicy


def _make_server(host="test.example.com", port=22, username="testuser",
                 auth_method=AuthMethod.key, key_path="/fake/key"):
    return ServerConfig(
        server_id="test",
        host=host,
        port=port,
        username=username,
        auth_method=auth_method,
        key_path=key_path,
    )


class MockSSHWrapper(SSHClientWrapper):
    """测试用 SSH wrapper，跳过真实的 _resolve_key 调用。"""

    @staticmethod
    def _resolve_key(key_path: str):
        return MagicMock(spec=paramiko.PKey)


def _mock_channel(stdout_data: bytes = b"", stderr_data: bytes = b"", exit_code: int = 0):
    """创建一个模拟的 channel，支持 recv_ready/recv/exit_status_ready 循环。"""
    mock = MagicMock()
    stdout_buf = [stdout_data]
    stderr_buf = [stderr_data]
    mock.recv_exit_status.return_value = exit_code

    def _recv_ready():
        return len(stdout_buf[0]) > 0

    def _recv_stderr_ready():
        return len(stderr_buf[0]) > 0

    def _recv(size):
        data = stdout_buf[0][:size]
        stdout_buf[0] = stdout_buf[0][size:]
        return data

    def _recv_stderr(size):
        data = stderr_buf[0][:size]
        stderr_buf[0] = stderr_buf[0][size:]
        return data

    def _exit_status_ready():
        return len(stdout_buf[0]) == 0 and len(stderr_buf[0]) == 0

    mock.recv_ready = _recv_ready
    mock.recv_stderr_ready = _recv_stderr_ready
    mock.recv = _recv
    mock.recv_stderr = _recv_stderr
    mock.exit_status_ready = _exit_status_ready
    mock.settimeout = MagicMock()
    return mock


def _mock_stdout(content: str, stderr_content: str = ""):
    m = MagicMock()
    m.channel = _mock_channel(stdout_data=content.encode("utf-8"), stderr_data=stderr_content.encode("utf-8"))
    m.read.return_value = content.encode("utf-8")
    return m


def _mock_stderr(content: str = ""):
    m = MagicMock()
    m.read.return_value = content.encode("utf-8")
    return m


class TestSSHResult:
    def test_result_fields(self):
        r = SSHResult(command="echo ok", exit_code=0, stdout="ok", stderr="", duration_seconds=0.5)
        assert r.command == "echo ok"
        assert r.exit_code == 0
        assert r.stdout == "ok"
        assert r.stderr == ""
        assert r.duration_seconds == 0.5


class TestSSHClientWrapper:
    def test_context_manager(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.exec_command.return_value = (MagicMock(), _mock_stdout("jobdesk-alive"), _mock_stderr())

            with MockSSHWrapper(server, timeout=5) as ssh:
                result = ssh.run("echo test", check=False)
                assert result.exit_code == 0
                assert result.stdout == "jobdesk-alive"

    def test_run_success(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.exec_command.return_value = (MagicMock(), _mock_stdout("hello"), _mock_stderr())

            ssh = MockSSHWrapper(server)
            ssh.connect()
            result = ssh.run("echo hello")
            assert result.exit_code == 0
            assert "hello" in result.stdout

    def test_run_nonzero_exit(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            stdout = _mock_stdout("output", stderr_content="error")
            stderr = _mock_stderr("error")
            stdout.channel.recv_exit_status.return_value = 1
            mock_client.exec_command.return_value = (MagicMock(), stdout, stderr)

            ssh = MockSSHWrapper(server)
            ssh.connect()
            result = ssh.run("false", check=False)
            assert result.exit_code == 1
            assert result.stderr == "error"

    def test_run_check_raises_on_nonzero(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            stdout = _mock_stdout("fail", stderr_content="reason")
            stderr = _mock_stderr("reason")
            stdout.channel.recv_exit_status.return_value = 2
            mock_client.exec_command.return_value = (MagicMock(), stdout, stderr)

            ssh = MockSSHWrapper(server)
            ssh.connect()
            with pytest.raises(SSHCommandError) as exc_info:
                ssh.run("bad_cmd", check=True)
            assert exc_info.value.exit_code == 2
            assert "bad_cmd" in str(exc_info.value)
            assert exc_info.value.stderr == "reason"

    def test_run_not_connected(self):
        server = _make_server()
        ssh = MockSSHWrapper(server)
        with pytest.raises(SSHConnectionError, match="未连接"):
            ssh.run("echo test")

    def test_run_timeout_when_command_hangs(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            # Channel that never produces output and never exits
            channel = MagicMock()
            channel.recv_ready = lambda: False
            channel.recv_stderr_ready = lambda: False
            channel.exit_status_ready = lambda: False
            channel.settimeout = MagicMock()
            stdout = MagicMock()
            stdout.channel = channel
            mock_client.exec_command.return_value = (MagicMock(), stdout, MagicMock())

            ssh = MockSSHWrapper(server, timeout=0.05)
            ssh.connect()
            with pytest.raises(SSHCommandError):
                ssh.run("hang", timeout=0.05)

    def test_connect_failure(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.connect.side_effect = paramiko.SSHException("auth failed")

            ssh = MockSSHWrapper(server)
            with pytest.raises(SSHConnectionError, match="SSH 连接失败"):
                ssh.connect()

    def test_connect_network_error(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.connect.side_effect = OSError("Connection refused")

            ssh = MockSSHWrapper(server)
            with pytest.raises(SSHConnectionError, match="网络错误"):
                ssh.connect()

    def test_connect_starts_configured_wsl_distro_before_ssh(self):
        server = ServerConfig(
            server_id="wsl",
            host="127.0.0.1",
            username="root",
            auth_method=AuthMethod.key,
            key_path="/fake/key",
            wsl_distro="Ubuntu",
        )
        with patch("jobdesk_app.remote.ssh.sys.platform", "win32"), \
             patch("jobdesk_app.remote.ssh.subprocess.run") as run_wsl, \
             patch("jobdesk_app.remote.ssh._is_local_port_open", return_value=False), \
             patch("paramiko.SSHClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            ssh = MockSSHWrapper(server, timeout=7)
            ssh.connect()

        import subprocess as _sp
        run_wsl.assert_called_once_with(
            ["wsl.exe", "-d", "Ubuntu", "--", "true"],
            check=True,
            capture_output=True,
            timeout=7,
            creationflags=_sp.CREATE_NO_WINDOW,
        )
        mock_client_class.return_value.connect.assert_called_once()

    def test_wsl_bootstrap_skipped_when_local_port_already_open(self):
        """If local SSH port is already listening, skip WSL wakeup."""
        server = ServerConfig(
            server_id="wsl",
            host="127.0.0.1",
            port=22,
            username="root",
            auth_method=AuthMethod.key,
            key_path="/fake/key",
            wsl_distro="Ubuntu",
        )
        with patch("jobdesk_app.remote.ssh.sys.platform", "win32"), \
             patch("jobdesk_app.remote.ssh.subprocess.run") as run_wsl, \
             patch("jobdesk_app.remote.ssh._is_local_port_open", return_value=True), \
             patch("paramiko.SSHClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            ssh = MockSSHWrapper(server, timeout=5)
            ssh.connect()

        run_wsl.assert_not_called()
        mock_client_class.return_value.connect.assert_called_once()

    def test_wsl_bootstrap_skipped_for_non_local_host(self):
        """Non-local host with wsl_distro should not trigger local WSL wakeup."""
        server = ServerConfig(
            server_id="remote",
            host="192.168.1.100",
            port=22,
            username="user",
            auth_method=AuthMethod.key,
            key_path="/fake/key",
            wsl_distro="Ubuntu",
        )
        with patch("jobdesk_app.remote.ssh.sys.platform", "win32"), \
             patch("jobdesk_app.remote.ssh.subprocess.run") as run_wsl, \
             patch("paramiko.SSHClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            ssh = MockSSHWrapper(server, timeout=5)
            ssh.connect()

        run_wsl.assert_not_called()

    def test_connect_rejects_unknown_host_keys_by_default(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            MockSSHWrapper(server).connect()

        policy = mock_client_class.return_value.set_missing_host_key_policy.call_args.args[0]
        assert isinstance(policy, paramiko.RejectPolicy)

    def test_connect_can_explicitly_enable_trust_on_first_use(self):
        server = _make_server().model_copy(update={"trust_on_first_use": True})
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client_class.return_value = MagicMock()

            MockSSHWrapper(server).connect()

        policy = mock_client_class.return_value.set_missing_host_key_policy.call_args.args[0]
        assert policy.__class__.__name__ == "_AutoAddAndSavePolicy"

    def test_trust_on_first_use_does_not_ignore_key_persistence_failure(self, tmp_path):
        client = MagicMock()
        client.get_host_keys.return_value.save.side_effect = OSError("permission denied")
        policy = _AutoAddAndSavePolicy(tmp_path / "known_hosts")

        with pytest.raises(OSError, match="permission denied"):
            policy.missing_host_key(client, "wsl", MagicMock())

    def test_key_not_found(self):
        server = _make_server(key_path="/nonexistent/key")
        with patch("pathlib.Path.exists", return_value=False), \
             patch("paramiko.SSHClient.connect", side_effect=OSError("mock")):
            ssh = SSHClientWrapper(server)
            with pytest.raises(SSHConnectionError, match="私钥不存在"):
                ssh.connect()

    def test_password_auth_rejected(self):
        server = _make_server(auth_method=AuthMethod.password)
        ssh = MockSSHWrapper(server)
        with pytest.raises(SSHConnectionError, match="password 认证"):
            ssh.connect()

    def test_utf8_output(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.exec_command.return_value = (MagicMock(), _mock_stdout("你好世界"), _mock_stderr())

            ssh = MockSSHWrapper(server)
            ssh.connect()
            result = ssh.run("echo test")
            assert result.stdout == "你好世界"

    def test_test_connection_success(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.exec_command.return_value = (MagicMock(), _mock_stdout("jobdesk-alive"), _mock_stderr())

            ssh = MockSSHWrapper(server)
            ssh.connect()
            assert ssh.test_connection() is True

    def test_close(self):
        server = _make_server()
        with patch("paramiko.SSHClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client

            ssh = MockSSHWrapper(server)
            ssh.connect()
            ssh.close()
            mock_client.close.assert_called_once()
            assert ssh._client is None

    def test_no_private_info_in_repr(self):
        server = _make_server()
        ssh = MockSSHWrapper(server)
        r = repr(ssh)
        assert "test.example.com" not in r.lower()  # host not in default repr
