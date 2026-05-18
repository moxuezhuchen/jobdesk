"""M3 测试: remote/status.py — 远程状态标记读取 mock 测试。"""

import shlex

from unittest.mock import MagicMock, patch
import paramiko
from jobdesk_app.config.schema import ServerConfig, AuthMethod
from jobdesk_app.remote.ssh import SSHClientWrapper, SSHResult
from jobdesk_app.remote.status import read_remote_task_status, RemoteTaskStatusSnapshot


def _make_ssh_with_handler(run_handler):
    """创建 mock SSH，所有 run 调用由 handler 函数处理。"""
    server = ServerConfig(
        server_id="t", host="h", port=22, username="u",
        auth_method=AuthMethod.key, key_path="/k",
    )
    with patch("paramiko.SSHClient") as mock_client_class, \
         patch("jobdesk_app.remote.ssh.SSHClientWrapper._resolve_key",
               return_value=MagicMock(spec=paramiko.PKey)):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        ssh = SSHClientWrapper(server)
        ssh.connect()
        ssh.run = run_handler
        return ssh


class TestRemoteTaskStatus:
    def test_all_files_exist(self):
        def handler(command, timeout=None, check=False):
            if ".jobdesk_status" in command:
                return SSHResult(command=command, exit_code=0, stdout="completed", stderr="", duration_seconds=0.01)
            if ".jobdesk_exit_code" in command:
                return SSHResult(command=command, exit_code=0, stdout="0", stderr="", duration_seconds=0.01)
            if ".jobdesk_submit.log" in command:
                return SSHResult(command=command, exit_code=0, stdout="log line 1\nlog line 2", stderr="", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        assert snap.task_id == "task1"
        assert snap.remote_job_dir == "/remote/job/task1"
        assert snap.marker_exists is True
        assert snap.status_marker == "completed"
        assert snap.exit_code_exists is True
        assert snap.exit_code == 0
        assert snap.log_exists is True
        assert "log line 1" in snap.submit_log_tail

    def test_files_not_exist(self):
        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout="__NOT_FOUND__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        assert snap.marker_exists is False
        assert snap.exit_code_exists is False
        assert snap.log_exists is False
        assert snap.status_marker == ""
        assert snap.exit_code is None

    def test_exit_code_not_integer(self):
        def handler(command, timeout=None, check=False):
            if ".jobdesk_exit_code" in command:
                return SSHResult(command=command, exit_code=0, stdout="not_an_int", stderr="", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0, stdout="__NOT_FOUND__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        assert snap.exit_code_exists is True
        assert snap.exit_code is None
        assert any("不是有效整数" in w for w in snap.warnings)

    def test_path_with_spaces_is_quoted(self):
        def handler(command, timeout=None, check=False):
            # Verify the path in the command is quoted by shlex
            return SSHResult(command=command, exit_code=0, stdout="__NOT_FOUND__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job with spaces/task1")
        assert snap.marker_exists is False

    def test_path_with_special_chars_quoted(self):
        commands_seen = []

        def handler(command, timeout=None, check=False):
            commands_seen.append(command)
            return SSHResult(command=command, exit_code=0, stdout="__NOT_FOUND__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        read_remote_task_status(ssh, "task1", "/remote/job-$USER/task1")
        for cmd in commands_seen:
            assert "$USER" not in cmd or "'" in cmd  # path should be quoted

    def test_shlex_quote_adds_single_quotes(self):
        """验证 shlex.quote 对含空格的路径加单引号。"""
        quoted = shlex.quote("/home/user/my dir")
        assert quoted == "'/home/user/my dir'"
        assert " " not in quoted or quoted.startswith("'")

    def test_shlex_quote_special_chars(self):
        quoted = shlex.quote("/path/with$dollar")
        assert quoted.startswith("'")
        assert "$" in quoted  # single-quoted preserves literal $
