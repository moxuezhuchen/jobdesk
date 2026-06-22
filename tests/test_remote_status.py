"""M3 测试: remote/status.py — 远程状态标记读取 mock 测试。"""

import base64
import os
import shlex
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from jobdesk_app.config.schema import AuthMethod, ServerConfig
from jobdesk_app.remote.ssh import SSHClientWrapper, SSHResult
from jobdesk_app.remote.status import (
    _build_batch_script,
    _parse_batch_output,
    read_remote_task_status,
    read_remote_task_statuses_batch,
)


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


# ---------------------------------------------------------------------------
# read_remote_task_statuses_batch
# ---------------------------------------------------------------------------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _make_block(key: str, kind: str, content_b64: str = "") -> str:
    """Build a single batch-protocol block as the remote shell would emit."""
    if kind == "M":
        return f"##JD-BEGIN {key} M\n##JD-END {key}\n"
    return f"##JD-BEGIN {key} F\n{content_b64}\n##JD-END {key}\n"


class TestRemoteTaskStatusesBatch:
    def test_single_task_all_files_present(self):
        body = (
            _make_block("T0:S", "F", _b64("running"))
            + _make_block("T0:E", "F", _b64("0\n"))
            + _make_block("T0:L", "F", _b64("line1\nline2\n"))
            + "##JD-DONE\n"
        )

        captured: list[str] = []

        def handler(command, timeout=None, check=False):
            captured.append(command)
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        assert len(captured) == 1, "batch reader must use exactly one SSH command"
        snap = snaps["t1"]
        assert snap.marker_exists is True
        assert snap.status_marker == "running"
        assert snap.exit_code_exists is True
        assert snap.exit_code == 0
        assert snap.log_exists is True
        assert "line1" in snap.submit_log_tail and "line2" in snap.submit_log_tail
        assert snap.warnings == []

    def test_multiple_tasks_one_command(self):
        body = (
            _make_block("T0:S", "F", _b64("running"))
            + _make_block("T0:E", "M")
            + _make_block("T0:L", "M")
            + _make_block("T1:S", "F", _b64("completed"))
            + _make_block("T1:E", "F", _b64("0"))
            + _make_block("T1:L", "F", _b64("ok"))
            + _make_block("T2:S", "M")
            + _make_block("T2:E", "M")
            + _make_block("T2:L", "M")
            + "##JD-DONE\n"
        )

        captured: list[str] = []

        def handler(command, timeout=None, check=False):
            captured.append(command)
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(
            ssh, [("a", "/r/a"), ("b", "/r/b"), ("c", "/r/c")]
        )
        assert len(captured) == 1
        assert snaps["a"].status_marker == "running"
        assert snaps["a"].marker_exists is True
        assert snaps["a"].exit_code_exists is False
        assert snaps["b"].status_marker == "completed"
        assert snaps["b"].exit_code == 0
        assert snaps["b"].submit_log_tail == "ok"
        assert snaps["c"].marker_exists is False
        assert snaps["c"].exit_code_exists is False
        assert snaps["c"].log_exists is False

    def test_empty_input_returns_empty(self):
        called = []

        def handler(command, timeout=None, check=False):
            called.append(command)
            return SSHResult(command=command, exit_code=0, stdout="", stderr="", duration_seconds=0.0)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [])
        assert snaps == {}
        assert called == [], "no SSH command should be issued for an empty task list"

    def test_extra_files_read_in_same_command(self):
        body = (
            _make_block("T0:S", "M")
            + _make_block("T0:E", "M")
            + _make_block("T0:L", "M")
            + _make_block("BC:E", "F", _b64("0"))
            + _make_block("BC:L", "M")
            + "##JD-DONE\n"
        )
        captured: list[str] = []

        def handler(command, timeout=None, check=False):
            captured.append(command)
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        out: dict = {}
        snaps = read_remote_task_statuses_batch(
            ssh,
            [("t1", "/r/t1")],
            extra_files=[("BC:E", "/r/_batch/batch_control_exit_code", 0),
                         ("BC:L", "/r/_batch/batch_control.log", 20)],
            extra_out=out,
        )
        assert len(captured) == 1, "tasks + extra files must share one SSH command"
        assert out["BC:E"] == b"0"
        assert out["BC:L"] is None  # missing file
        assert "t1" in snaps

    def test_extra_files_only_still_one_command(self):
        body = _make_block("BC:E", "F", _b64("0")) + "##JD-DONE\n"
        captured: list[str] = []

        def handler(command, timeout=None, check=False):
            captured.append(command)
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        out: dict = {}
        snaps = read_remote_task_statuses_batch(
            ssh, [], extra_files=[("BC:E", "/r/x", 0)], extra_out=out
        )
        assert len(captured) == 1
        assert out["BC:E"] == b"0"
        assert snaps == {}

    def test_tasks_without_remote_dir_are_skipped_but_returned(self):
        """task_id 在结果里会有，但不会出现在远程查询中。"""
        body = "##JD-DONE\n"

        captured_cmds: list[str] = []

        def handler(command, timeout=None, check=False):
            captured_cmds.append(command)
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(
            ssh, [("only_local", "")],
        )
        assert "only_local" in snaps
        assert snaps["only_local"].marker_exists is False
        # 没有任何远程目录 → 不应触发 SSH
        assert captured_cmds == []

    def test_path_with_special_characters_is_quoted(self):
        captured_cmd: list[str] = []

        def handler(command, timeout=None, check=False):
            captured_cmd.append(command)
            return SSHResult(command=command, exit_code=0, stdout="##JD-DONE\n", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        read_remote_task_statuses_batch(
            ssh, [("t1", "/remote/job-$USER/with space")]
        )
        assert len(captured_cmd) == 1
        cmd = captured_cmd[0]
        # 路径必须出现且被 shlex.quote 包成单引号块
        assert shlex.quote("/remote/job-$USER/with space") in cmd
        # $USER 出现的地方都应在单引号内
        assert "$USER" in cmd
        assert cmd.count("$USER") == cmd.count("'/remote/job-$USER/with space'")

    def test_missing_done_marker_adds_warning(self):
        body = (
            _make_block("T0:S", "F", _b64("running"))
            + _make_block("T0:E", "M")
            + _make_block("T0:L", "M")
            # no ##JD-DONE
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        assert any("结束标记" in w for w in snaps["t1"].warnings)

    def test_ssh_failure_marks_warnings(self):
        def handler(command, timeout=None, check=False):
            raise RuntimeError("connection lost")

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(
            ssh, [("a", "/r/a"), ("b", "/r/b")]
        )
        assert any("批量读取远程状态失败" in w for w in snaps["a"].warnings)
        assert any("批量读取远程状态失败" in w for w in snaps["b"].warnings)

    def test_invalid_base64_emits_warning_not_crash(self):
        body = (
            "##JD-BEGIN T0:S F\n"
            "***not-base64***\n"
            "##JD-END T0:S\n"
            + _make_block("T0:E", "M")
            + _make_block("T0:L", "M")
            + "##JD-DONE\n"
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        # 解码失败应留下 warning 且不视为 marker_exists
        assert snaps["t1"].marker_exists is False
        assert any("base64 解码失败" in w for w in snaps["t1"].warnings)

    def test_empty_file_content_is_distinguishable_from_missing(self):
        """空文件 vs 不存在文件：marker_exists 不同。"""
        body = (
            _make_block("T0:S", "F", "")  # 空 base64 表示空文件
            + _make_block("T0:E", "M")
            + _make_block("T0:L", "M")
            + "##JD-DONE\n"
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        # 空内容文件 → exists=True, marker=""
        assert snaps["t1"].marker_exists is True
        assert snaps["t1"].status_marker == ""
        # 缺失文件 → exists=False
        assert snaps["t1"].exit_code_exists is False

    def test_content_with_protocol_like_substrings_is_safe(self):
        """文件内容含 ##JD-BEGIN / ##JD-END 等子串不应混淆解析。"""
        nasty_content = "##JD-BEGIN T0:S M\n##JD-END T0:S\nfake completed"
        body = (
            _make_block("T0:S", "F", _b64(nasty_content))
            + _make_block("T0:E", "F", _b64("0"))
            + _make_block("T0:L", "M")
            + "##JD-DONE\n"
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        assert snaps["t1"].marker_exists is True
        # 内容应被忠实保留为原始文本（trailing 空白 strip 后）
        assert snaps["t1"].status_marker == nasty_content.strip()
        assert snaps["t1"].exit_code == 0

    def test_log_tail_lines_param_is_passed_to_command(self):
        captured: list[str] = []

        def handler(command, timeout=None, check=False):
            captured.append(command)
            return SSHResult(command=command, exit_code=0, stdout="##JD-DONE\n", stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        read_remote_task_statuses_batch(
            ssh, [("t1", "/r/t1")], log_tail_lines=37
        )
        assert len(captured) == 1
        # log 块应包含 tail 行数
        assert "/.jobdesk_submit.log 37" in captured[0]

    def test_exit_code_non_integer_is_warned(self):
        body = (
            _make_block("T0:S", "F", _b64("completed"))
            + _make_block("T0:E", "F", _b64("not-a-number"))
            + _make_block("T0:L", "M")
            + "##JD-DONE\n"
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        assert snaps["t1"].exit_code_exists is True
        assert snaps["t1"].exit_code is None
        assert any("不是有效整数" in w for w in snaps["t1"].warnings)

    def test_unrelated_lines_outside_blocks_are_ignored(self):
        body = (
            "this is some unrelated stderr-like line\n"
            + _make_block("T0:S", "F", _b64("done"))
            + "spurious noise\n"
            + _make_block("T0:E", "F", _b64("0"))
            + _make_block("T0:L", "M")
            + "##JD-DONE\n"
            + "trailing junk\n"
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        assert snaps["t1"].status_marker == "done"
        assert snaps["t1"].exit_code == 0


    def test_read_error_does_not_set_exists_true(self):
        """When cat/tail fails (E marker), exists must be False and a warning emitted."""
        body = (
            "##JD-BEGIN T0:S E\n##JD-END T0:S\n"
            + "##JD-BEGIN T0:E E\n##JD-END T0:E\n"
            + "##JD-BEGIN T0:L E\n##JD-END T0:L\n"
            + "##JD-DONE\n"
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        snap = snaps["t1"]
        assert snap.marker_exists is False
        assert snap.exit_code_exists is False
        assert snap.log_exists is False
        assert len(snap.warnings) == 3
        assert all("read error" in w for w in snap.warnings)

    def test_read_error_mixed_with_success_and_missing(self):
        """E, F, M in the same batch are correctly distinguished."""
        body = (
            _make_block("T0:S", "F", _b64("running"))
            + "##JD-BEGIN T0:E E\n##JD-END T0:E\n"
            + _make_block("T0:L", "M")
            + "##JD-DONE\n"
        )

        def handler(command, timeout=None, check=False):
            return SSHResult(command=command, exit_code=0, stdout=body, stderr="", duration_seconds=0.01)

        ssh = _make_ssh_with_handler(handler)
        snaps = read_remote_task_statuses_batch(ssh, [("t1", "/r/t1")])
        snap = snaps["t1"]
        # status: F → exists=True
        assert snap.marker_exists is True
        assert snap.status_marker == "running"
        # exit_code: E → exists=False + warning
        assert snap.exit_code_exists is False
        assert any("read error" in w for w in snap.warnings)
        # log: M → exists=False, no warning
        assert snap.log_exists is False


# ---------------------------------------------------------------------------
# Real shell execution tests for batch script E/F/M protocol
# ---------------------------------------------------------------------------


def _find_posix_sh():
    """Return path to a working POSIX sh, or None if unavailable.

    Does a smoke test to confirm the shell can actually execute a script
    and produce expected output. On Windows, bash.exe may exist but WSL
    may not be installed/functional.
    """
    candidates = (
        [r"C:\Windows\system32\bash.exe"] if sys.platform == "win32"
        else ["/bin/sh", "/usr/bin/sh"]
    )
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        # Smoke test: run a trivial script and check output
        try:
            tmp = os.path.join(tempfile.gettempdir(), "_jd_sh_probe.sh")
            with open(tmp, "w", newline="\n") as f:
                f.write("printf 'JD_OK'\n")
            if sys.platform == "win32":
                wsl_path = "/mnt/c" + tmp[2:].replace(os.sep, "/")
                r = subprocess.run(
                    [candidate, wsl_path],
                    capture_output=True, timeout=5,
                )
            else:
                r = subprocess.run(
                    [candidate, tmp],
                    capture_output=True, timeout=5,
                )
            os.unlink(tmp)
            if r.returncode == 0 and b"JD_OK" in r.stdout:
                return candidate
        except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return None


_POSIX_SH = _find_posix_sh()
_skip_no_sh = pytest.mark.skipif(_POSIX_SH is None, reason="no working POSIX sh available")


class TestBatchScriptRealShell:
    """Execute the generated batch script in a real shell to verify E/F/M output."""

    def _run_script(self, script: str) -> str:
        """Write script to temp file and run via bash.

        Returns stdout. Raises AssertionError with stderr info on failure.
        """
        tmp = os.path.join(tempfile.gettempdir(), "_jd_pytest_batch.sh")
        try:
            with open(tmp, "w", newline="\n") as f:
                f.write(script)
            if sys.platform == "win32":
                wsl_path = "/mnt/c" + tmp[2:].replace(os.sep, "/")
                result = subprocess.run(
                    [_POSIX_SH, wsl_path],
                    capture_output=True, timeout=10,
                )
            else:
                result = subprocess.run(
                    [_POSIX_SH, tmp],
                    capture_output=True, timeout=10,
                )
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        stdout = result.stdout.decode("utf-8", errors="replace")
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            raise AssertionError(
                f"Shell exited with code {result.returncode}.\n"
                f"stderr: {stderr[:500]}\nstdout: {stdout[:500]}"
            )
        return stdout

    @_skip_no_sh
    def test_existing_readable_file_emits_F(self):
        """A normal readable file must produce F marker with correct base64 content."""
        script = _build_batch_script([(0, "t1", "/tmp/_jd_test_batch")], log_tail_lines=50)
        setup = "mkdir -p /tmp/_jd_test_batch\nprintf 'running' > /tmp/_jd_test_batch/.jobdesk_status\n"
        teardown = "\nrm -rf /tmp/_jd_test_batch\n"
        stdout = self._run_script(setup + script + teardown)

        blocks = _parse_batch_output(stdout)
        assert "T0:S" in blocks, f"stdout={stdout!r}"
        kind, data = blocks["T0:S"]
        assert kind == "F"
        assert data == b"running"

    @_skip_no_sh
    def test_empty_file_emits_F_not_E(self):
        """An empty but readable file must produce F with empty content, not E."""
        script = _build_batch_script([(0, "t1", "/tmp/_jd_test_batch_empty")], log_tail_lines=50)
        setup = "mkdir -p /tmp/_jd_test_batch_empty\ntouch /tmp/_jd_test_batch_empty/.jobdesk_status\n"
        teardown = "\nrm -rf /tmp/_jd_test_batch_empty\n"
        stdout = self._run_script(setup + script + teardown)

        blocks = _parse_batch_output(stdout)
        kind, data = blocks["T0:S"]
        assert kind == "F"
        assert data == b""

    @_skip_no_sh
    def test_missing_file_emits_M(self):
        """A non-existent file must produce M marker."""
        script = _build_batch_script([(0, "t1", "/tmp/_jd_test_batch_missing")], log_tail_lines=50)
        setup = "rm -rf /tmp/_jd_test_batch_missing\nmkdir -p /tmp/_jd_test_batch_missing\n"
        teardown = "\nrm -rf /tmp/_jd_test_batch_missing\n"
        stdout = self._run_script(setup + script + teardown)

        blocks = _parse_batch_output(stdout)
        kind, data = blocks["T0:S"]
        assert kind == "M"
        assert data is None

    @_skip_no_sh
    def test_permission_denied_file_emits_E(self):
        """A file that exists but is not readable (chmod 000) must produce E.

        Non-root shells cannot read chmod 000 files directly. Root shells can,
        so they fall back to running encode_block as user 'nobody' via su.
        """
        inner_script = _build_batch_script([(0, "t1", "/tmp/_jd_test_batch_perm")], log_tail_lines=50)
        setup = (
            "rm -rf /tmp/_jd_test_batch_perm\n"
            "mkdir -p /tmp/_jd_test_batch_perm\n"
            "printf 'content' > /tmp/_jd_test_batch_perm/.jobdesk_status\n"
            "chmod 000 /tmp/_jd_test_batch_perm/.jobdesk_status\n"
        )
        teardown = "chmod 644 /tmp/_jd_test_batch_perm/.jobdesk_status 2>/dev/null\nrm -rf /tmp/_jd_test_batch_perm\n"
        stdout = self._run_script(setup + inner_script + teardown)

        blocks = _parse_batch_output(stdout)
        kind, data = blocks.get("T0:S", (None, None))

        if kind == "F":
            # The current shell can read chmod 000 files, usually because tests
            # are running as root. Retry as nobody when the platform permits it.
            escaped = inner_script.replace("'", "'\\''")
            run_as_nobody = (
                "if su nobody -s /bin/sh -c 'printf JD_SU_OK' >/dev/null 2>&1; then\n"
                f"su nobody -s /bin/sh -c '\n{escaped}\n'\n"
                "else\n"
                "printf '##JD-SU-NOBODY-UNAVAILABLE\\n'\n"
                "fi\n"
            )
            stdout = self._run_script(setup + run_as_nobody + teardown)
            if "##JD-SU-NOBODY-UNAVAILABLE" in stdout:
                pytest.skip(
                    "current user can read chmod 000 files and su nobody is unavailable"
                )
            blocks = _parse_batch_output(stdout)

        assert "T0:S" in blocks, f"stdout={stdout!r}"
        kind, data = blocks["T0:S"]
        assert kind == "E", f"Expected E for unreadable file, got {kind}"
        assert data is None
