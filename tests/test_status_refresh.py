"""M6 测试: remote/status_refresh.py — 状态恢复 mock 测试。"""

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.remote.ssh import SSHResult
from jobdesk_app.remote.status import RemoteTaskStatusSnapshot
from jobdesk_app.remote.status_refresh import (
    _parse_batch_control,
    _recover_status,
    refresh_batch_status,
    refresh_task_statuses,
)


def _make_task(task_id: str, status: TaskStatus, remote_job_dir: str = "") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        batch_id="b1",
        task_files=[f"in/{task_id}.gjf"],
        remote_job_dir=remote_job_dir or f"/remote/b1/{task_id}",
        remote_task_files=[f"{task_id}.gjf"],
        rendered_command="echo hello",
        status=status,
    )


def _mock_ssh_for_refresh(marker: str = "", exit_code: int | None = None,
                           marker_exists: bool = True, exit_code_exists: bool = True,
                           log_tail: str = ""):
    """创建 mock SSH，read_remote_task_status 返回预设值。"""
    def fake_read(ssh, task_id, remote_job_dir, log_tail_lines=50):
        return RemoteTaskStatusSnapshot(
            task_id=task_id,
            remote_job_dir=remote_job_dir,
            status_marker=marker,
            exit_code=exit_code,
            submit_log_tail=log_tail,
            marker_exists=marker_exists,
            exit_code_exists=exit_code_exists,
            log_exists=bool(log_tail),
        )
    return fake_read


# ---- status recovery rules ---------------------------------------------


class TestRecoveryRules:
    """测试核心状态恢复规则（纯逻辑，不依赖 SSH）。"""

    def test_submitted_plus_running_marker(self):
        task = _make_task("t1", TaskStatus.submitted)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "running", None, "", True, False, False)
        new, snap = _recover_status(TaskStatus.submitted, rs, task)
        assert new == TaskStatus.running

    def test_submitting_claim_is_not_advanced_by_remote_marker(self):
        task = _make_task("t1", TaskStatus.submitting)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "running", None, "", True, False, False)

        new, _snap = _recover_status(TaskStatus.submitting, rs, task)

        assert new == TaskStatus.submitting

    def test_stale_submitting_claim_recovers_completed_remote_task(self):
        task = _make_task("t1", TaskStatus.submitting)
        task.submitted_at = datetime.now() - timedelta(seconds=61)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)

        new, _snap = _recover_status(
            TaskStatus.submitting,
            rs,
            task,
            stale_timeout_seconds=60,
        )

        assert new == TaskStatus.remote_completed

    def test_stale_submitting_claim_survives_incomplete_remote_read(self):
        task = _make_task("t1", TaskStatus.submitting)
        task.submitted_at = datetime.now() - timedelta(seconds=61)
        rs = RemoteTaskStatusSnapshot(
            "t1", "/r/t1", warnings=["batch read failed"], marker_exists=False
        )

        new, snap = _recover_status(
            TaskStatus.submitting,
            rs,
            task,
            stale_timeout_seconds=60,
        )

        assert new == TaskStatus.submitting
        assert any("incomplete" in warning for warning in snap.warnings)

    def test_stale_ambiguous_submission_without_marker_requires_manual_reconciliation(self):
        task = _make_task("t1", TaskStatus.submitting)
        task.submitted_at = datetime.now() - timedelta(seconds=61)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", marker_exists=False)

        new, snap = _recover_status(
            TaskStatus.submitting,
            rs,
            task,
            stale_timeout_seconds=60,
        )

        assert new == TaskStatus.submitting
        assert any("manual reconciliation" in warning for warning in snap.warnings)

    def test_running_plus_completed_plus_exit_0(self):
        task = _make_task("t1", TaskStatus.running)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)
        new, snap = _recover_status(TaskStatus.running, rs, task)
        assert new == TaskStatus.remote_completed

    def test_running_plus_failed_marker(self):
        task = _make_task("t1", TaskStatus.running)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "failed", 1, "", True, True, False)
        new, snap = _recover_status(TaskStatus.running, rs, task)
        assert new == TaskStatus.failed
        assert snap.failure_reason is not None

    def test_running_plus_exit_nonzero(self):
        task = _make_task("t1", TaskStatus.running)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 1, "", True, True, False)
        new, snap = _recover_status(TaskStatus.running, rs, task)
        assert new == TaskStatus.failed

    def test_submitted_missing_marker(self):
        task = _make_task("t1", TaskStatus.submitted)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "", None, "", False, False, False)
        new, snap = _recover_status(TaskStatus.submitted, rs, task)
        assert new == TaskStatus.submitted
        assert any("无状态文件" in w for w in snap.warnings)

    def test_remote_completed_unchanged(self):
        task = _make_task("t1", TaskStatus.remote_completed)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)
        new, _ = _recover_status(TaskStatus.remote_completed, rs, task)
        assert new == TaskStatus.remote_completed

    def test_downloaded_not_rolled_back(self):
        task = _make_task("t1", TaskStatus.downloaded)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "running", None, "", True, False, False)
        new, _ = _recover_status(TaskStatus.downloaded, rs, task)
        assert new == TaskStatus.downloaded

    def test_analyzed_not_rolled_back(self):
        task = _make_task("t1", TaskStatus.analyzed)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "running", None, "", True, False, False)
        new, _ = _recover_status(TaskStatus.analyzed, rs, task)
        assert new == TaskStatus.analyzed

    def test_failed_stays_failed(self):
        task = _make_task("t1", TaskStatus.failed)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)
        new, _ = _recover_status(TaskStatus.failed, rs, task)
        assert new == TaskStatus.failed

    def test_uploaded_plus_running_marker(self):
        task = _make_task("t1", TaskStatus.uploaded)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "running", None, "", True, False, False)
        new, _ = _recover_status(TaskStatus.uploaded, rs, task)
        assert new == TaskStatus.running

    def test_completed_marker_but_exit_code_missing_keeps_current(self):
        """marker=completed but .jobdesk_exit_code missing → keep current status + warning."""
        task = _make_task("t1", TaskStatus.running)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", None, "", True, False, False)
        new, snap = _recover_status(TaskStatus.running, rs, task)
        assert new == TaskStatus.running  # non-terminal, not failed
        assert snap.failure_reason is not None
        assert "退出码缺失" in snap.failure_reason
        assert any("exit_code" in w.lower() or "退出码" in w for w in snap.warnings)

    def test_exit_code_missing_then_appears_resolves(self):
        """Two refreshes: first sees completed+missing → stays running; second sees exit_code=0 → remote_completed."""
        task = _make_task("t1", TaskStatus.running)
        # First refresh: exit_code missing
        rs1 = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", None, "", True, False, False)
        new1, _ = _recover_status(TaskStatus.running, rs1, task)
        assert new1 == TaskStatus.running
        # Second refresh: exit_code arrives
        rs2 = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)
        new2, snap2 = _recover_status(TaskStatus.running, rs2, task)
        assert new2 == TaskStatus.remote_completed
        assert not snap2.warnings

    def test_completed_marker_exit_code_zero_succeeds(self):
        """marker=completed + exit_code=0 → remote_completed, no warning."""
        task = _make_task("t1", TaskStatus.running)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)
        new, snap = _recover_status(TaskStatus.running, rs, task)
        assert new == TaskStatus.remote_completed
        assert not snap.warnings

    def test_local_ready_stays(self):
        task = _make_task("t1", TaskStatus.local_ready)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)
        new, _ = _recover_status(TaskStatus.local_ready, rs, task)
        assert new == TaskStatus.local_ready

    def test_running_missing_marker_stale_timeout_fails(self):
        """running 任务远程无状态文件且超过 stale 超时 → 判失败（避免永久挂起）。"""
        from datetime import datetime, timedelta

        task = _make_task("t1", TaskStatus.running)
        task.submitted_at = datetime.now() - timedelta(seconds=600)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "", None, "", False, False, False)
        new, snap = _recover_status(TaskStatus.running, rs, task, stale_timeout_seconds=300)
        assert new == TaskStatus.failed
        assert snap.failure_reason is not None

    def test_running_missing_marker_within_timeout_stays(self):
        from datetime import datetime, timedelta

        task = _make_task("t1", TaskStatus.running)
        task.submitted_at = datetime.now() - timedelta(seconds=10)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "", None, "", False, False, False)
        new, snap = _recover_status(TaskStatus.running, rs, task, stale_timeout_seconds=300)
        assert new == TaskStatus.running
        assert any("无状态文件" in w for w in snap.warnings)

    def test_running_missing_marker_no_timeout_stays(self):
        task = _make_task("t1", TaskStatus.running)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "", None, "", False, False, False)
        new, snap = _recover_status(TaskStatus.running, rs, task)
        assert new == TaskStatus.running
        assert any("无状态文件" in w for w in snap.warnings)


# ---- refresh_batch_status integration ----------------------------------


class TestRefreshBatchStatus:
    def test_refreshes_object_tasks_without_manifest(self):
        tasks = [_make_task("t1", TaskStatus.running, "/r/t1")]
        mock_ssh = MagicMock()
        with patch(
            "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
            return_value={
                "t1": RemoteTaskStatusSnapshot(
                    "t1", "/r/t1", "completed", 0, "", True, True, False
                )
            },
        ):
            result, updated = refresh_task_statuses(mock_ssh, tasks, "/r", "b1")

        assert result.changed_count == 1
        assert updated[0].status == TaskStatus.remote_completed
        assert tasks[0].status == TaskStatus.running

    def test_refresh_with_mock(self):
        tasks = [
            _make_task("t1", TaskStatus.submitted, "/r/t1"),
            _make_task("t2", TaskStatus.running, "/r/t2"),
            _make_task("t3", TaskStatus.remote_completed, "/r/t3"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={
                    "t1": RemoteTaskStatusSnapshot("t1", "/r/t1", "running", None, "", True, False, False),
                    "t2": RemoteTaskStatusSnapshot("t2", "/r/t2", "completed", 0, "", True, True, True),
                    "t3": RemoteTaskStatusSnapshot("t3", "/r/t3", "completed", 0, "", True, True, False),
                },
            ):
                result = refresh_batch_status(mock_ssh, mp, "/r", "b1", write=False)
                assert result.task_count == 3
                assert result.changed_count == 2  # t1: submitted→running, t2: running→remote_completed

    def test_refresh_write_updates_manifest(self):
        tasks = [
            _make_task("t1", TaskStatus.running, "/r/t1"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={
                    "t1": RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False),
                },
            ):
                result = refresh_batch_status(mock_ssh, mp, "/r", "b1", write=True)
                assert result.changed_count == 1

            # re-read manifest
            updated = Manifest.read(mp)
            t1 = next(t for t in updated if t.task_id == "t1")
            assert t1.status == TaskStatus.remote_completed
            assert t1.completed_at is not None

    def test_refresh_write_false_does_not_update(self):
        tasks = [_make_task("t1", TaskStatus.running, "/r/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={
                    "t1": RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False),
                },
            ):
                refresh_batch_status(mock_ssh, mp, "/r", "b1", write=False)

            updated = Manifest.read(mp)
            t1 = next(t for t in updated if t.task_id == "t1")
            assert t1.status == TaskStatus.running

    def test_refresh_default_stale_timeout_fails_old_running_task(self):
        task = _make_task("t1", TaskStatus.running, "/r/t1")
        task.submitted_at = datetime.now() - timedelta(days=2)
        tasks = [task]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={
                    "t1": RemoteTaskStatusSnapshot("t1", "/r/t1", "", None, "", False, False, False),
                },
            ):
                result = refresh_batch_status(mock_ssh, mp, "/r", "b1", write=True)

            assert result.changed_count == 1
            updated = Manifest.read(mp)
            assert updated[0].status == TaskStatus.failed
            assert updated[0].error_message is not None

    def test_refresh_does_not_fail_stale_task_when_remote_read_failed(self):
        task = _make_task("t1", TaskStatus.running, "/r/t1")
        task.submitted_at = datetime.now() - timedelta(days=2)
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, [task])

            mock_ssh = MagicMock()
            mock_ssh.run.side_effect = RuntimeError("temporary ssh read failure")

            result = refresh_batch_status(mock_ssh, mp, "/r", "b1", write=True)

            assert result.changed_count == 0
            updated = Manifest.read(mp)
            assert updated[0].status == TaskStatus.running
            assert updated[0].error_message is None
            assert result.snapshots[0].warnings

    def test_runtime_failures_generated(self):
        tasks = [_make_task("t1", TaskStatus.running, "/r/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={
                    "t1": RemoteTaskStatusSnapshot(
                        "t1", "/r/t1", "failed", 1, "error log", True, True, True
                    ),
                },
            ):
                result = refresh_batch_status(mock_ssh, mp, "/r", "b1", write=False)
                assert len(result.failures) == 1
                assert result.failures[0].stage == "runtime"
                assert result.failures[0].task_id == "t1"

    def test_refresh_uses_single_ssh_command_for_task_files(self):
        """N 个 task + batch_control 应只触发「1 次」批量 SSH 命令，
        而不是 3N+2 条，且不再单独为 batch_control 调用 ssh.run。"""
        tasks = [
            _make_task("t1", TaskStatus.submitted, "/r/t1"),
            _make_task("t2", TaskStatus.submitted, "/r/t2"),
            _make_task("t3", TaskStatus.submitted, "/r/t3"),
            _make_task("t4", TaskStatus.submitted, "/r/t4"),
            _make_task("t5", TaskStatus.submitted, "/r/t5"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            # 把批量读取替换为可控的返回，避免真正构造脚本
            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={
                    t.task_id: RemoteTaskStatusSnapshot(
                        t.task_id, t.remote_job_dir, "", None, "", False, False, False
                    )
                    for t in tasks
                },
            ) as mock_batch:
                refresh_batch_status(mock_ssh, mp, "/r", "b1", write=False)
                # 批量读 1 次，与 task 数无关
                assert mock_batch.call_count == 1
                # batch_control 已并入批量读取，不再单独 ssh.run
                assert mock_ssh.run.call_count == 0

    def test_refresh_single_call_even_without_remote_dirs(self):
        """即使所有 task 都没有 remote_job_dir，仍需 1 次批量读取来获取 batch_control。"""
        tasks = [_make_task("t1", TaskStatus.local_ready, remote_job_dir="-")]
        for t in tasks:
            t.remote_job_dir = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={},
            ) as mock_batch:
                refresh_batch_status(mock_ssh, mp, "/r", "b1", write=False)
                assert mock_batch.call_count == 1

    def test_refresh_reads_batch_control_nohup_log(self):
        """nohup submission redirects batch_control output to batch_control.nohup.log."""
        tasks = [_make_task("t1", TaskStatus.submitted, "/r/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_statuses_batch",
                return_value={
                    "t1": RemoteTaskStatusSnapshot("t1", "/r/t1", "", None, "", False, False, False),
                },
            ) as mock_batch:
                refresh_batch_status(MagicMock(), mp, "/r", "b1", write=False)

        extra_files = mock_batch.call_args.kwargs["extra_files"]
        assert ("BC:L", "/r/_batch/batch_control.nohup.log", 20) in extra_files
        assert ("BC:L", "/r/_batch/batch_control.log", 20) not in extra_files


# ---- batch_control ------------------------------------------------


class TestBatchControlReading:
    def test_exit_code_zero(self):
        snap = _parse_batch_control({"BC:E": b"0", "BC:L": b"BATCH_FINISHED\n"})
        assert snap.exit_code == 0
        assert snap.finished_marker_found is True

    def test_exit_code_nonzero(self):
        snap = _parse_batch_control({"BC:E": b"1", "BC:L": b"BATCH_FINISHED\n"})
        assert snap.exit_code == 1
        assert snap.finished_marker_found is True
        assert any("非零" in w for w in snap.warnings)

    def test_missing_files(self):
        snap = _parse_batch_control({"BC:E": None, "BC:L": None})
        assert snap.exit_code is None
        assert snap.finished_marker_found is False
