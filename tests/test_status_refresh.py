"""M6 测试: remote/status_refresh.py — 状态恢复 mock 测试。"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.remote.ssh import SSHResult
from jobdesk_app.remote.status import RemoteTaskStatusSnapshot
from jobdesk_app.remote.status_refresh import (
    _read_batch_control,
    _recover_status,
    refresh_batch_status,
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

    def test_local_ready_stays(self):
        task = _make_task("t1", TaskStatus.local_ready)
        rs = RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False)
        new, _ = _recover_status(TaskStatus.local_ready, rs, task)
        assert new == TaskStatus.local_ready


# ---- refresh_batch_status integration ----------------------------------


class TestRefreshBatchStatus:
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
                "jobdesk_app.remote.status_refresh.read_remote_task_status",
                side_effect=[
                    RemoteTaskStatusSnapshot("t1", "/r/t1", "running", None, "", True, False, False),
                    RemoteTaskStatusSnapshot("t2", "/r/t2", "completed", 0, "", True, True, True),
                    RemoteTaskStatusSnapshot("t3", "/r/t3", "completed", 0, "", True, True, False),
                ],
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
                "jobdesk_app.remote.status_refresh.read_remote_task_status",
                return_value=RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False),
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
                "jobdesk_app.remote.status_refresh.read_remote_task_status",
                return_value=RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False),
            ):
                refresh_batch_status(mock_ssh, mp, "/r", "b1", write=False)

            updated = Manifest.read(mp)
            t1 = next(t for t in updated if t.task_id == "t1")
            assert t1.status == TaskStatus.running

    def test_runtime_failures_generated(self):
        tasks = [_make_task("t1", TaskStatus.running, "/r/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_status",
                return_value=RemoteTaskStatusSnapshot("t1", "/r/t1", "failed", 1, "error log", True, True, True),
            ):
                result = refresh_batch_status(mock_ssh, mp, "/r", "b1", write=False)
                assert len(result.failures) == 1
                assert result.failures[0].stage == "runtime"
                assert result.failures[0].task_id == "t1"


# ---- batch_control ------------------------------------------------


class TestBatchControlReading:
    def test_exit_code_zero(self):
        mock_ssh = MagicMock()
        mock_ssh.run = MagicMock(side_effect=[
            SSHResult("", 0, "0", "", 0.01),
            SSHResult("", 0, "BATCH_FINISHED\n", "", 0.01),
        ])
        snap = _read_batch_control(mock_ssh, "/r")
        assert snap.exit_code == 0
        assert snap.finished_marker_found is True

    def test_exit_code_nonzero(self):
        mock_ssh = MagicMock()
        mock_ssh.run = MagicMock(side_effect=[
            SSHResult("", 0, "1", "", 0.01),
            SSHResult("", 0, "BATCH_FINISHED\n", "", 0.01),
        ])
        snap = _read_batch_control(mock_ssh, "/r")
        assert snap.exit_code == 1
        assert snap.finished_marker_found is True
        assert any("非零" in w for w in snap.warnings)
