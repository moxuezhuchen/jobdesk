"""M5 测试: remote/submitter.py — 任务提交 mock 测试。

使用 mock SSH + fake SFTP，不连接真实服务器。
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.core.submit import SubmitMode
from jobdesk_app.core.transfer import TransferDirection, TransferRecord
from jobdesk_app.core.transfer import TransferStatus as TransferStatusEnum
from jobdesk_app.remote.scheduler import SlurmAdapter
from jobdesk_app.remote.ssh import SSHResult
from jobdesk_app.remote.submitter import JobSubmitter

# ---- fake SFTP client (reusing from test_sftp but minimal) ------------


class FakeSFTPClient:
    def __init__(self):
        self._files: dict[str, bytes] = {}
        self._put_calls: list[tuple] = []
        self._get_calls: list[tuple] = []
        self._mkdir_calls: list[str] = []

    def put(self, local: str, remote: str, confirm: bool = True):
        self._put_calls.append((local, remote))
        data = Path(local).read_bytes()
        self._files[remote] = data

    def get(self, remote: str, local: str):
        self._get_calls.append((remote, local))

    def stat(self, remote: str):
        if remote in self._files:
            return MagicMock(st_size=len(self._files[remote]))
        raise FileNotFoundError(remote)

    def mkdir(self, remote: str):
        self._mkdir_calls.append(remote)

    def close(self):
        pass


def _fake_mkdir_p(self, remote_dir: str):
    if remote_dir and remote_dir != "/":
        parts = remote_dir.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else f"/{part}"
            if current not in self._sftp._mkdir_calls:
                self._sftp._mkdir_calls.append(current)


class FakeSFTPWrapper:
    def __init__(self):
        self._sftp = FakeSFTPClient()
        self.upload_file = MagicMock(
            side_effect=lambda *a, **kw: TransferRecord(
                direction=TransferDirection.upload,
                local_path=str(a[0]) if a else "",
                remote_path=str(a[1]) if len(a) > 1 else "",
                status=TransferStatusEnum.transferred,
                reason="mock upload",
            )
        )
        self.mkdir_p = MagicMock(side_effect=_fake_mkdir_p.__get__(self))
        self.close = MagicMock()


# ---- helpers -----------------------------------------------------------


def _make_task(task_id: str, status: TaskStatus = TaskStatus.uploaded,
               remote_job_dir: str = "", rendered_command: str = "echo hello") -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        batch_id="b1",
        task_files=[f"in/{task_id}.gjf"],
        remote_job_dir=remote_job_dir or f"/remote/b1/{task_id}",
        remote_task_files=[f"{task_id}.gjf"],
        rendered_command=rendered_command,
        status=status,
    )


def _write_manifest(path: Path, tasks: list[TaskRecord]) -> None:
    Manifest.write(path, tasks)


# ---- task selection ----------------------------------------------------


class TestTaskSelection:
    def test_selects_only_uploaded(self):
        tasks = [
            _make_task("t1", TaskStatus.local_ready),
            _make_task("t2", TaskStatus.uploaded),
            _make_task("t3", TaskStatus.uploaded),
            _make_task("t4", TaskStatus.submitted),
            _make_task("t5", TaskStatus.running),
            _make_task("t6", TaskStatus.remote_completed),
            _make_task("t7", TaskStatus.downloaded),
            _make_task("t8", TaskStatus.analyzed),
            _make_task("t9", TaskStatus.failed),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            submitter = JobSubmitter(mp, None, None, 4, "/remote/b1", "b1")
            selected = submitter.select_tasks(SubmitMode.all)
            assert len(selected) == 2
            assert {t.task_id for t in selected} == {"t2", "t3"}

    def test_no_uploaded_returns_empty(self):
        tasks = [_make_task("t1", TaskStatus.local_ready)]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            submitter = JobSubmitter(mp, None, None, 4, "/r", "b1")
            selected = submitter.select_tasks(SubmitMode.all)
            assert selected == []

    def test_selected_mode(self):
        tasks = [
            _make_task("t1", TaskStatus.uploaded),
            _make_task("t2", TaskStatus.uploaded),
            _make_task("t3", TaskStatus.uploaded),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            submitter = JobSubmitter(mp, None, None, 4, "/r", "b1")
            selected = submitter.select_tasks(SubmitMode.selected, ["t1", "t3"])
            assert len(selected) == 2
            assert {t.task_id for t in selected} == {"t1", "t3"}

    def test_selected_ignores_non_uploaded(self):
        tasks = [
            _make_task("t1", TaskStatus.uploaded),
            _make_task("t2", TaskStatus.running),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            submitter = JobSubmitter(mp, None, None, 4, "/r", "b1")
            selected = submitter.select_tasks(SubmitMode.selected, ["t1", "t2"])
            assert len(selected) == 1
            assert selected[0].task_id == "t1"


# ---- script generation ------------------------------------------------


class TestGenerateScripts:
    def test_task_runner_contains_status_markers(self):
        task = _make_task("t1", rendered_command="echo test123")
        content = JobSubmitter.generate_task_runner(task)
        assert "#!/usr/bin/env bash" in content
        assert ".jobdesk_status" in content
        assert ".jobdesk_exit_code" in content
        assert ".jobdesk_submit.log" in content
        assert "echo test123" in content
        assert "completed" in content
        assert "failed" in content

    def test_task_runner_contains_rendered_command(self):
        task = _make_task("t1", rendered_command="g16 mol.gjf")
        content = JobSubmitter.generate_task_runner(task)
        assert "g16 mol.gjf" in content

    def test_task_runner_uses_lf_newlines(self):
        task = _make_task("t1")
        content = JobSubmitter.generate_task_runner(task)
        assert "\r\n" not in content

    def test_launch_script_cd_and_bash(self):
        content = JobSubmitter.generate_launch_script("t1", "/remote/b1/t1")
        assert "#!/usr/bin/env bash" in content
        assert "cd" in content
        assert ".jobdesk_run.sh" in content

    def test_tasks_tsv_fields(self):
        tasks = [
            _make_task("t1", remote_job_dir="/remote/b1/t1"),
            _make_task("t2", remote_job_dir="/remote/b1/t2"),
        ]
        content = JobSubmitter.generate_tasks_tsv(tasks, "/remote/b1")
        lines = content.strip().split("\n")
        assert lines[0] == "task_id\tremote_job_dir\trunner_path"
        assert len(lines) == 3
        assert "t1" in lines[1]
        assert "/remote/b1/t1" in lines[1]

    def test_tasks_tsv_task_id_with_tab_raises(self):
        tasks = [_make_task("t\t1")]
        with pytest.raises(ValueError, match="task_id"):
            JobSubmitter.generate_tasks_tsv(tasks, "/r")

    def test_tasks_tsv_task_id_with_newline_raises(self):
        tasks = [_make_task("t\n1")]
        with pytest.raises(ValueError, match="task_id"):
            JobSubmitter.generate_tasks_tsv(tasks, "/r")

    def test_tasks_tsv_task_id_with_shell_substitution_raises(self):
        tasks = [_make_task("bad$(touch pwned)")]
        with pytest.raises(ValueError, match="task_id"):
            JobSubmitter.generate_tasks_tsv(tasks, "/r")

    def test_task_runner_quotes_whole_event_log_line(self):
        task = _make_task("mol-1")
        content = JobSubmitter.generate_task_runner(task)

        assert 'printf "%s\\n" "RUNNING mol-1" >> ../_batch/events.log' in content
        assert 'printf "%s\\n" "DONE mol-1 $rc" >> ../_batch/events.log' in content

    def test_batch_control_contains_xargs_p(self):
        content = JobSubmitter.generate_batch_control(4, "/remote/b1", 10)
        assert "xargs -r -P" in content
        assert "4" in content
        # M5.1: xargs 使用安全传参 bash -c 'bash "$1"' _"{}"
        assert "bash -c" in content
        assert 'bash "$1"' in content

    def test_batch_control_says_finished_not_completed(self):
        content = JobSubmitter.generate_batch_control(4, "/remote/b1", 10)
        assert "BATCH_FINISHED" in content
        assert "BATCH_COMPLETED" not in content

    def test_batch_control_records_exit_code(self):
        content = JobSubmitter.generate_batch_control(4, "/remote/b1", 10)
        assert "batch_control_exit_code" in content

    def test_batch_control_finished_comment(self):
        content = JobSubmitter.generate_batch_control(4, "/remote/b1", 10)
        assert ".jobdesk_status" in content or "以 .jobdesk_status" in content

    def test_batch_control_does_not_use_ls(self):
        content = JobSubmitter.generate_batch_control(4, "/remote/b1", 10)
        assert " ls " not in content

    def test_batch_control_checks_xargs_exists(self):
        content = JobSubmitter.generate_batch_control(4, "/remote/b1", 10)
        assert "command -v xargs" in content

    def test_batch_control_uses_tasks_tsv_cut(self):
        content = JobSubmitter.generate_batch_control(4, "/remote/b1", 10)
        assert "tasks.tsv" in content
        assert "cut -f3" in content or "launch_list" in content


# ---- dry-run -----------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_plan(self):
        tasks = [
            _make_task("t1", TaskStatus.uploaded, "/remote/b1/t1"),
            _make_task("t2", TaskStatus.uploaded, "/remote/b1/t2"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            fake_ssh = MagicMock()
            fake_sftp = FakeSFTPWrapper()
            submitter = JobSubmitter(mp, fake_ssh, fake_sftp, 4, "/remote/b1", "b1")
            plan = submitter.dry_run(SubmitMode.all)
            assert plan.dry_run is True
            assert plan.task_count == 2
            assert plan.max_parallel == 4
            assert len(plan.generated_files) > 0
            assert "setsid" in plan.control_command
            assert "echo $!" in plan.control_command
            # 验证没有副作用
            fake_sftp.upload_file.assert_not_called()
            fake_ssh.run.assert_not_called()

    def test_dry_run_no_uploaded_tasks(self):
        tasks = [_make_task("t1", TaskStatus.local_ready)]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            submitter = JobSubmitter(mp, None, None, 4, "/r", "b1")
            plan = submitter.dry_run(SubmitMode.all)
            assert plan.task_count == 0


# ---- submit ------------------------------------------------------------


class _MockSSHForSubmit:
    def __init__(self, run_return):
        self.run = MagicMock(return_value=run_return)


class TestSubmit:
    def _make_mock_ssh(self, exit_code=0, stdout="", stderr=""):
        return _MockSSHForSubmit(
            SSHResult(command="", exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=0.01)
        )

    def test_submit_no_tasks_returns_error(self):
        tasks = [_make_task("t1", TaskStatus.local_ready)]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            submitter = JobSubmitter(mp, MagicMock(), FakeSFTPWrapper(), 4, "/r", "b1")
            result = submitter.submit_batch(SubmitMode.all)
            assert len(result.errors) > 0

    def test_submit_updates_manifest_to_submitted(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            ssh = self._make_mock_ssh(exit_code=0, stdout="4321")  # chmod succeeds, nohup returns PID
            sftp = FakeSFTPWrapper()
            submitter = JobSubmitter(mp, ssh, sftp, 4, "/remote/b1", "b1")
            result = submitter.submit_batch(SubmitMode.all)
            assert len(result.errors) == 0
            assert result.updated_task_ids == ["t1"]
            # re-read manifest and verify status
            updated = Manifest.read(mp)
            t1 = next(t for t in updated if t.task_id == "t1")
            assert t1.status == TaskStatus.submitted
            assert t1.submitted_at is not None
            assert t1.scheduler_type == "nohup"
            assert t1.remote_job_id == "4321"

    def test_submit_updates_object_tasks_without_manifest(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        ssh = self._make_mock_ssh(exit_code=0, stdout="4321")
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=ssh,
            sftp=FakeSFTPWrapper(),
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
        )

        result = submitter.submit_batch(SubmitMode.all)

        assert result.errors == []
        assert result.updated_tasks[0].status == TaskStatus.submitted
        assert result.updated_tasks[0].remote_job_id == "4321"

    def test_nohup_checkpoint_failure_propagates(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=self._make_mock_ssh(exit_code=0, stdout="4321"),
            sftp=FakeSFTPWrapper(),
            max_parallel=1,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
            task_update_callback=MagicMock(side_effect=RuntimeError("database locked")),
        )

        with pytest.raises(RuntimeError, match="database locked"):
            submitter.submit_batch(SubmitMode.all)

    def test_scheduler_checkpoint_failure_propagates(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        scheduler = SlurmAdapter()
        scheduler.submit = MagicMock(return_value="123")
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=self._make_mock_ssh(exit_code=0),
            sftp=FakeSFTPWrapper(),
            max_parallel=1,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
            scheduler=scheduler,
            task_update_callback=MagicMock(side_effect=RuntimeError("database locked")),
        )

        with pytest.raises(RuntimeError, match="database locked"):
            submitter.submit_batch(SubmitMode.all)

    def test_scheduler_reports_durable_success_before_later_upload_failure(self):
        tasks = [
            _make_task("t1", TaskStatus.uploaded, "/remote/b1/t1"),
            _make_task("t2", TaskStatus.uploaded, "/remote/b1/t2"),
        ]
        scheduler = SlurmAdapter()
        scheduler.submit = MagicMock(return_value="123")
        sftp = FakeSFTPWrapper()

        def upload_or_fail(*args, **kwargs):
            if str(args[1]).endswith("/t2/.jobdesk_run.sh"):
                raise RuntimeError("upload failed")
            return TransferRecord(
                direction=TransferDirection.upload,
                local_path=str(args[0]),
                remote_path=str(args[1]),
                status=TransferStatusEnum.transferred,
                reason="mock upload",
            )

        sftp.upload_file.side_effect = upload_or_fail
        checkpoints: list[TaskRecord] = []
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=self._make_mock_ssh(exit_code=0),
            sftp=sftp,
            max_parallel=1,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
            scheduler=scheduler,
            task_update_callback=lambda updates: checkpoints.extend(updates),
        )

        result = submitter.submit_batch(SubmitMode.all)

        assert result.updated_task_ids == ["t1"]
        assert result.submitted_task_count == 1
        assert [task.status for task in result.updated_tasks] == [
            TaskStatus.submitted,
            TaskStatus.uploaded,
        ]
        assert result.updated_tasks[0].remote_job_id == "123"
        assert checkpoints[0].task_id == "t1"
        assert checkpoints[0].status == TaskStatus.submitted
        assert any("upload failed" in error for error in result.errors)

    def test_nohup_without_pid_marks_tasks_uncertain(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        ssh = self._make_mock_ssh(exit_code=0, stdout="")
        checkpoints = []
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=ssh,
            sftp=FakeSFTPWrapper(),
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
            task_update_callback=lambda updates: checkpoints.extend(updates),
        )

        result = submitter.submit_batch(SubmitMode.all)

        assert result.errors == ["nohup start did not return a remote process id"]
        assert result.submitted_task_count == 0
        assert result.updated_tasks[0].status == TaskStatus.uncertain
        assert result.updated_tasks[0].submitted_at is not None
        assert result.updated_tasks[0].remote_job_id is None
        assert checkpoints[0].status == TaskStatus.uncertain
        assert checkpoints[0].submitted_at is not None
        assert checkpoints[0].error_message == "nohup start did not return a remote process id"

    def test_scheduler_submit_exception_marks_task_uncertain(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        scheduler = SlurmAdapter()
        scheduler.submit = MagicMock(side_effect=RuntimeError("response lost"))
        checkpoints = []
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=self._make_mock_ssh(exit_code=0),
            sftp=FakeSFTPWrapper(),
            max_parallel=1,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
            scheduler=scheduler,
            task_update_callback=lambda updates: checkpoints.extend(updates),
        )

        result = submitter.submit_batch(SubmitMode.all)

        assert result.errors == ["task t1: submit failed: response lost"]
        assert result.updated_tasks[0].status == TaskStatus.uncertain
        assert result.updated_tasks[0].submitted_at is not None
        assert checkpoints[0].status == TaskStatus.uncertain
        assert checkpoints[0].error_message == "submit failed: response lost"

    def test_scheduler_empty_job_id_marks_task_uncertain(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        scheduler = SlurmAdapter()
        scheduler.submit = MagicMock(return_value="")
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=self._make_mock_ssh(exit_code=0),
            sftp=FakeSFTPWrapper(),
            max_parallel=1,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
            scheduler=scheduler,
        )

        result = submitter.submit_batch(SubmitMode.all)

        assert result.updated_tasks[0].status == TaskStatus.uncertain
        assert result.updated_tasks[0].remote_job_id is None

    def test_submit_uses_control_subdir_for_all_remote_paths(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            ssh = self._make_mock_ssh(exit_code=0, stdout="4321")
            sftp = FakeSFTPWrapper()
            submitter = JobSubmitter(
                mp, ssh, sftp, 4, "/remote/b1", "b1",
                control_subdir="_batch/g16",
            )

            result = submitter.submit_batch(SubmitMode.all)

            assert result.errors == []
            uploaded_paths = [call.args[1] for call in sftp.upload_file.call_args_list]
            assert "/remote/b1/_batch/g16/tasks.tsv" in uploaded_paths
            assert "/remote/b1/_batch/g16/batch_control.sh" in uploaded_paths
            assert "/remote/b1/_batch/g16/launch_t1.sh" in uploaded_paths
            assert "/remote/b1/_batch/tasks.tsv" not in uploaded_paths
            assert any("cd /remote/b1/_batch/g16" in c.args[0] for c in ssh.run.call_args_list)

    def test_submit_chmod_failure_no_manifest_update(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            ssh = self._make_mock_ssh(exit_code=1, stderr="chmod: permission denied")
            sftp = FakeSFTPWrapper()
            submitter = JobSubmitter(mp, ssh, sftp, 4, "/remote/b1", "b1")
            result = submitter.submit_batch(SubmitMode.all)
            assert len(result.errors) > 0
            assert "chmod" in result.errors[0].lower()
            # Manifest not updated
            updated = Manifest.read(mp)
            t1 = next(t for t in updated if t.task_id == "t1")
            assert t1.status == TaskStatus.uploaded

    def test_submit_nohup_failure_marks_manifest_uncertain(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, tasks)
            ssh = MagicMock()
            ssh.run = MagicMock(side_effect=[
                SSHResult("chmod", 0, "", "", 0.01),  # chmod ok
                SSHResult("nohup", 1, "", "nohup failed", 0.01),  # nohup fails
            ])
            sftp = FakeSFTPWrapper()
            submitter = JobSubmitter(mp, ssh, sftp, 4, "/remote/b1", "b1")
            result = submitter.submit_batch(SubmitMode.all)
            assert len(result.errors) > 0
            updated = Manifest.read(mp)
            t1 = next(t for t in updated if t.task_id == "t1")
            assert t1.status == TaskStatus.uncertain
            assert t1.submitted_at is not None
            assert t1.error_message == "nohup start failed: nohup failed"

    def test_max_parallel_must_be_positive(self):
        with pytest.raises(ValueError, match="max_parallel"):
            with tempfile.TemporaryDirectory() as tmpdir:
                mp = Path(tmpdir) / "manifest.tsv"
                _write_manifest(mp, [])
                JobSubmitter(mp, None, None, 0, "/r", "b1")

    def test_remote_paths_are_quoted(self):
        """验证 launch 脚本中的路径使用了 shlex.quote。"""
        content = JobSubmitter.generate_launch_script("t1", "/path/with spaces/t1")
        assert "cd " in content
        # shlex.quote 在路径含空格时会添加单引号
        assert "'" in content

    def test_launch_script_with_spaces_handled(self):
        """路径含空格时 launch 脚本应正确 quote。"""
        content = JobSubmitter.generate_launch_script("t1", "/opt/my data/task 1")
        assert "cd " in content
        # 应包含 quoted 路径
        assert "'" in content or '"' in content

    def test_script_content_is_lf(self):
        task = _make_task("t1")
        runner = JobSubmitter.generate_task_runner(task)
        launch = JobSubmitter.generate_launch_script("t1", "/r/t1")
        control = JobSubmitter.generate_batch_control(4, "/r", 1)
        assert "\r\n" not in runner
        assert "\r\n" not in launch
        assert "\r\n" not in control

    def test_batch_control_does_not_re_discover_inputs(self):
        content = JobSubmitter.generate_batch_control(4, "/r", 1)
        # 不应包含 find/ls 输入文件逻辑
        assert "ls " not in content


# ---- result fields -----------------------------------------------------


class TestSubmitResult:
    def test_result_has_control_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mp = Path(tmpdir) / "manifest.tsv"
            _write_manifest(mp, [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")])
            ssh = _MockSSHForSubmit(
                SSHResult("", 0, "4321", "", 0.01)
            )
            sftp = FakeSFTPWrapper()
            submitter = JobSubmitter(mp, ssh, sftp, 4, "/remote/b1", "b1")
            result = submitter.submit_batch(SubmitMode.all)
            assert result.control_script_path == "/remote/b1/_batch/batch_control.sh"
            assert result.control_log_path == "/remote/b1/_batch/batch_control.nohup.log"
            assert result.control_nohup_log_path == "/remote/b1/_batch/batch_control.nohup.log"
            assert "nohup" in result.nohup_command
