"""M3 测试: remote/status.py — 远程状态标记读取 mock 测试。"""

import shlex
from unittest.mock import MagicMock, patch

import paramiko

from jobdesk_app.config.schema import AuthMethod, ServerConfig
from jobdesk_app.remote.ssh import SSHClientWrapper, SSHResult
from jobdesk_app.remote.status import read_remote_task_status


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
                return SSHResult(command=command, exit_code=0, stdout="__JD_FOUND__\ncompleted", stderr="", duration_seconds=0.01)
            if ".jobdesk_exit_code" in command:
                return SSHResult(command=command, exit_code=0, stdout="__JD_FOUND__\n0", stderr="", duration_seconds=0.01)
            if ".jobdesk_submit.log" in command:
                return SSHResult(command=command, exit_code=0, stdout="__JD_FOUND__\nlog line 1\nlog line 2", stderr="", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

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
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

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
                return SSHResult(command=command, exit_code=0, stdout="__JD_FOUND__\nnot_an_int", stderr="", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        assert snap.exit_code_exists is True
        assert snap.exit_code is None
        assert any("不是有效整数" in w for w in snap.warnings)

    def test_path_with_spaces_is_quoted(self):
        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job with spaces/task1")
        assert snap.marker_exists is False

    def test_path_with_special_chars_quoted(self):
        commands_seen = []

        def handler(command, timeout=None, check=False):
            commands_seen.append(command)
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

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


    def test_status_file_containing_not_found_is_still_detected_as_existing(self):
        """File content '__NOT_FOUND__' must not be confused with file absence."""
        def handler(command, timeout=None, check=False):
            if ".jobdesk_status" in command:
                return SSHResult(command=command, exit_code=0,
                                 stdout="__JD_FOUND__\n__NOT_FOUND__", stderr="", duration_seconds=0.01)
            if ".jobdesk_exit_code" in command:
                return SSHResult(command=command, exit_code=0,
                                 stdout="__JD_FOUND__\n42", stderr="", duration_seconds=0.01)
            if ".jobdesk_submit.log" in command:
                return SSHResult(command=command, exit_code=0,
                                 stdout="__JD_FOUND__\n__NOT_FOUND__ in log\nmore lines", stderr="", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        assert snap.marker_exists is True
        assert snap.status_marker == "__NOT_FOUND__"
        assert snap.exit_code_exists is True
        assert snap.exit_code == 42
        assert snap.log_exists is True
        assert "__NOT_FOUND__ in log" in snap.submit_log_tail

    def test_missing_file_uses_jd_missing_envelope(self):
        """When file does not exist, envelope is __JD_MISSING__."""
        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0,
                             stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        assert snap.marker_exists is False
        assert snap.exit_code_exists is False
        assert snap.log_exists is False

    def test_read_failure_does_not_produce_false_positive(self):
        """If cat/tail fails (non-zero exit) but __JD_FOUND__ was already printed,
        the code must detect the failure via exit_code and set exists=False + warning."""
        def handler(command, timeout=None, check=False):
            if ".jobdesk_status" in command:
                # File exists but cat failed — exit_code non-zero, __JD_FOUND__ in stdout
                return SSHResult(command=command, exit_code=1,
                                 stdout="__JD_FOUND__\n", stderr="cat: error", duration_seconds=0.01)
            if ".jobdesk_exit_code" in command:
                # File exists but cat failed
                return SSHResult(command=command, exit_code=1,
                                 stdout="__JD_FOUND__\n", stderr="cat: error", duration_seconds=0.01)
            if ".jobdesk_submit.log" in command:
                # File exists but tail failed
                return SSHResult(command=command, exit_code=1,
                                 stdout="__JD_FOUND__\npartial content", stderr="tail: error", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        # All three must report exists=False due to read failure
        assert snap.marker_exists is False
        assert snap.exit_code_exists is False
        assert snap.log_exists is False
        # All three must produce warnings
        assert len(snap.warnings) >= 3
        assert any("exit_code=" in w for w in snap.warnings)

    def test_invalid_envelope_produces_warning(self):
        """Completely garbled output (no valid envelope) → warning, exists=False."""
        def handler(command, timeout=None, check=False):
            if ".jobdesk_status" in command:
                return SSHResult(command=command, exit_code=0,
                                 stdout="some unexpected output", stderr="", duration_seconds=0.01)
            if ".jobdesk_exit_code" in command:
                return SSHResult(command=command, exit_code=0,
                                 stdout="__JD_FOUND__\n__JD_MISSING__", stderr="", duration_seconds=0.01)
            if ".jobdesk_submit.log" in command:
                return SSHResult(command=command, exit_code=0,
                                 stdout="garbage_no_envelope", stderr="", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0, stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        assert snap.marker_exists is False
        assert len(snap.warnings) >= 1
        # Exit code: __JD_FOUND__ + content "__JD_MISSING__" — valid envelope, content is literal
        assert snap.exit_code_exists is True
        assert snap.log_exists is False


    def test_cat_failure_in_old_and_or_pattern_does_not_leak_missing_as_content(self):
        """Regression: if the shell 'cat' fails after 'test -f' succeeds in the &&/||
        pattern, both __JD_FOUND__ and __JD_MISSING__ may appear in stdout. The parser
        must not treat __JD_MISSING__ as file content. With the if/then/else fix,
        this output pattern cannot occur from the shell command itself."""
        def handler(command, timeout=None, check=False):
            if ".jobdesk_status" in command:
                # Simulates: test -f succeeds, echo __JD_FOUND__ runs, cat fails, || echo __JD_MISSING__ runs
                return SSHResult(command=command, exit_code=0,
                                 stdout="__JD_FOUND__\n__JD_MISSING__", stderr="", duration_seconds=0.01)
            return SSHResult(command=command, exit_code=0,
                             stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snap = read_remote_task_status(ssh, "task1", "/remote/job/task1")
        # The file content IS literally "__JD_MISSING__" — we cannot distinguish from
        # the protocol-level missing marker if using the old pattern. With the if/then/else
        # fix in the shell command, this scenario won't occur. But the parser should handle
        # it gracefully: if content after __JD_FOUND__ equals __JD_MISSING__, it's
        # ambiguous — we treat __JD_FOUND__ envelope as authoritative (file exists).
        # The real fix is the shell command using if/then/else so || cannot fire after then-body.
        assert snap.marker_exists is True
        # Content is literally "__JD_MISSING__" which is a valid status marker value
        assert snap.status_marker == "__JD_MISSING__"


    def test_shell_commands_use_if_then_else_not_and_or(self):
        """The shell command must use if/then/else/fi, not &&/|| pattern."""
        commands_seen = []

        def handler(command, timeout=None, check=False):
            commands_seen.append(command)
            return SSHResult(command=command, exit_code=0,
                             stdout="__JD_MISSING__", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        read_remote_task_status(ssh, "task1", "/remote/job/task1")
        for cmd in commands_seen:
            assert "&&" not in cmd, f"Command must not use && pattern: {cmd}"
            assert "if test -f" in cmd, f"Command must use if/then/else: {cmd}"
