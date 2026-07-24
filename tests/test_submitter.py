"""M5 测试: remote/submitter.py — 任务提交 mock 测试。

使用 mock SSH + fake SFTP，不连接真实服务器。
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord
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


def _make_task(
    task_id: str,
    status: TaskStatus = TaskStatus.uploaded,
    remote_job_dir: str = "",
    rendered_command: str = "echo hello",
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        batch_id="b1",
        task_files=[f"in/{task_id}.gjf"],
        remote_job_dir=remote_job_dir or f"/remote/b1/{task_id}",
        remote_task_files=[f"{task_id}.gjf"],
        rendered_command=rendered_command,
        status=status,
    )


def _make_workflow_task(*, dag: bool = False, resume: bool = False) -> TaskRecord:
    base = "cd /remote/submission && confflow water.xyz -c workflow.yaml -w water_confflow_work"
    task = _make_task("water", remote_job_dir="/remote/run/water", rendered_command=base)
    task.workflow_kind = "dag" if dag else "confflow"
    task.dry_run_command = f"{base} --dry-run"
    task.resume_command = f"{base} --resume"
    task.resume_dry_run_command = f"{base} --resume --dry-run"
    task.resume_requested = resume
    return task


def _capability_result(*, dag: bool = True) -> SSHResult:
    from jobdesk_app.core.confflow_contract import (
        EXPECTED_ARTIFACTS,
    )

    return SSHResult(
        command="confflow --capabilities --json",
        exit_code=0,
        stdout=json.dumps(
            {
                "schema_version": 2,
                "version": "1.4.2",
                "capabilities": {
                    "workflow_state": True,
                    "resume": True,
                    "dag": dag,
                },
                "artifacts": {
                    "run_summary": EXPECTED_ARTIFACTS.run_summary,
                    "workflow_stats": EXPECTED_ARTIFACTS.workflow_stats,
                    "workflow_state": EXPECTED_ARTIFACTS.workflow_state,
                },
            }
        ),
        stderr="",
        duration_seconds=0.01,
    )


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
        submitter = JobSubmitter(
            tasks=tasks, ssh=None, sftp=None, max_parallel=4, remote_batch_dir="/remote/b1", batch_id="b1"
        )
        selected = submitter.select_tasks(SubmitMode.all)
        assert len(selected) == 2
        assert {t.task_id for t in selected} == {"t2", "t3"}

    def test_no_uploaded_returns_empty(self):
        tasks = [_make_task("t1", TaskStatus.local_ready)]
        submitter = JobSubmitter(tasks=tasks, ssh=None, sftp=None, max_parallel=4, remote_batch_dir="/r", batch_id="b1")
        selected = submitter.select_tasks(SubmitMode.all)
        assert selected == []

    def test_selected_mode(self):
        tasks = [
            _make_task("t1", TaskStatus.uploaded),
            _make_task("t2", TaskStatus.uploaded),
            _make_task("t3", TaskStatus.uploaded),
        ]
        submitter = JobSubmitter(tasks=tasks, ssh=None, sftp=None, max_parallel=4, remote_batch_dir="/r", batch_id="b1")
        selected = submitter.select_tasks(SubmitMode.selected, ["t1", "t3"])
        assert len(selected) == 2
        assert {t.task_id for t in selected} == {"t1", "t3"}

    def test_selected_ignores_non_uploaded(self):
        tasks = [
            _make_task("t1", TaskStatus.uploaded),
            _make_task("t2", TaskStatus.running),
        ]
        submitter = JobSubmitter(tasks=tasks, ssh=None, sftp=None, max_parallel=4, remote_batch_dir="/r", batch_id="b1")
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
        assert ".jobdesk_status" in content or "actual success/failure" in content

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
        fake_ssh = MagicMock()
        fake_sftp = FakeSFTPWrapper()
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=fake_ssh,
            sftp=fake_sftp,
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
        )
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
        submitter = JobSubmitter(tasks=tasks, ssh=None, sftp=None, max_parallel=4, remote_batch_dir="/r", batch_id="b1")
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
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=MagicMock(),
            sftp=FakeSFTPWrapper(),
            max_parallel=4,
            remote_batch_dir="/r",
            batch_id="b1",
        )
        result = submitter.submit_batch(SubmitMode.all)
        assert len(result.errors) > 0

    def test_workflow_preflight_orders_capability_upload_dry_run_before_nohup(self):
        events: list[str] = []
        runner_text: list[str] = []
        task = _make_workflow_task()
        sftp = FakeSFTPWrapper()

        def upload(local_path, remote_path, **_kwargs):
            events.append(f"upload:{remote_path}")
            if str(remote_path).endswith("/.jobdesk_run.sh"):
                runner_text.append(Path(local_path).read_text(encoding="utf-8"))
            return TransferRecord(
                direction=TransferDirection.upload,
                local_path=str(local_path),
                remote_path=str(remote_path),
                status=TransferStatusEnum.transferred,
            )

        sftp.upload_file.side_effect = upload

        def run(command, **_kwargs):
            if "--capabilities --json" in command:
                events.append("capabilities")
                return _capability_result()
            if command.startswith("chmod +x"):
                events.append("chmod")
                return SSHResult(command, 0, "", "", 0.01)
            if "--dry-run" in command:
                events.append("dry-run")
                assert command.count("--dry-run") == 1
                assert "--resume" not in command
                return SSHResult(command, 0, "dry run ok", "", 0.01)
            if "nohup setsid" in command:
                events.append("nohup")
                return SSHResult(command, 0, "4321", "", 0.01)
            raise AssertionError(f"unexpected SSH command: {command}")

        remote_started = MagicMock(side_effect=lambda _ids: events.append("remote-started"))
        submitter = JobSubmitter(
            tasks=[task],
            ssh=SimpleNamespace(run=run),
            sftp=sftp,
            max_parallel=1,
            remote_batch_dir="/remote/run",
            batch_id="run",
            remote_started_callback=remote_started,
        )

        result = submitter.submit_batch()

        assert result.errors == []
        assert events[0] == "capabilities"
        assert max(i for i, event in enumerate(events) if event.startswith("upload:")) < events.index("dry-run")
        assert events.index("dry-run") < events.index("remote-started") < events.index("nohup")
        assert len(runner_text) == 1
        assert "--resume" not in runner_text[0]

    def test_capability_failure_cannot_upload_or_notify_remote_started(self):
        task = _make_workflow_task()
        ssh = MagicMock()
        ssh.run.return_value = SSHResult("capabilities", 0, "not json", "", 0.01)
        sftp = FakeSFTPWrapper()
        remote_started = MagicMock()
        submitter = JobSubmitter(
            tasks=[task],
            ssh=ssh,
            sftp=sftp,
            max_parallel=1,
            remote_batch_dir="/remote/run",
            batch_id="run",
            remote_started_callback=remote_started,
        )

        result = submitter.submit_batch()

        assert result.errors and "capability preflight failed" in result.errors[0]
        sftp.upload_file.assert_not_called()
        remote_started.assert_not_called()
        assert not any("nohup setsid" in call.args[0] for call in ssh.run.call_args_list)
        assert submitter._tasks[0].status == TaskStatus.uploaded

    def test_dry_run_failure_after_upload_cannot_notify_or_launch_nohup(self):
        task = _make_workflow_task()
        ssh = MagicMock()
        ssh.run.side_effect = [
            _capability_result(),
            SSHResult("chmod", 0, "", "", 0.01),
            SSHResult("dry-run", 2, "", "invalid workflow", 0.01),
        ]
        sftp = FakeSFTPWrapper()
        remote_started = MagicMock()
        submitter = JobSubmitter(
            tasks=[task],
            ssh=ssh,
            sftp=sftp,
            max_parallel=1,
            remote_batch_dir="/remote/run",
            batch_id="run",
            remote_started_callback=remote_started,
        )

        result = submitter.submit_batch()

        assert any("dry-run failed: invalid workflow" in error for error in result.errors)
        assert sftp.upload_file.call_count == 4
        remote_started.assert_not_called()
        assert not any("nohup setsid" in call.args[0] for call in ssh.run.call_args_list)
        assert submitter._tasks[0].status == TaskStatus.uploaded

    def test_dag_capability_is_required_only_for_dag_task(self):
        task = _make_workflow_task(dag=True)
        ssh = MagicMock()
        ssh.run.return_value = _capability_result(dag=False)
        submitter = JobSubmitter(
            tasks=[task],
            ssh=ssh,
            sftp=FakeSFTPWrapper(),
            max_parallel=1,
            remote_batch_dir="/remote/run",
            batch_id="run",
        )

        result = submitter.submit_batch()

        assert any("lacks required dag capability" in error for error in result.errors)

    def test_resume_preflight_and_runner_use_original_namespace_and_one_resume_flag(self):
        task = _make_workflow_task(resume=True)
        commands: list[str] = []
        runner_text: list[str] = []
        ssh = MagicMock()

        def run(command, **_kwargs):
            commands.append(command)
            if "--capabilities --json" in command:
                return _capability_result()
            if command.startswith("chmod +x"):
                return SSHResult(command, 0, "", "", 0.01)
            if "--dry-run" in command:
                return SSHResult(command, 0, "ok", "", 0.01)
            return SSHResult(command, 0, "4321", "", 0.01)

        ssh.run.side_effect = run
        sftp = FakeSFTPWrapper()

        def upload(local_path, remote_path, **_kwargs):
            if str(remote_path).endswith("/.jobdesk_run.sh"):
                runner_text.append(Path(local_path).read_text(encoding="utf-8"))
            return TransferRecord(
                direction=TransferDirection.upload,
                local_path=str(local_path),
                remote_path=str(remote_path),
                status=TransferStatusEnum.transferred,
            )

        sftp.upload_file.side_effect = upload
        submitter = JobSubmitter(
            tasks=[task],
            ssh=ssh,
            sftp=sftp,
            max_parallel=1,
            remote_batch_dir="/remote/run",
            batch_id="run",
        )

        result = submitter.submit_batch()

        assert result.errors == []
        dry_run = next(command for command in commands if "--dry-run" in command)
        assert dry_run.count("--resume") == 1
        assert dry_run.count("--dry-run") == 1
        assert "/remote/submission" in dry_run
        assert runner_text[0].count("--resume") == 1
        assert "/remote/submission" in runner_text[0]

    def test_submit_updates_manifest_to_submitted(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        ssh = self._make_mock_ssh(exit_code=0, stdout="4321")
        sftp = FakeSFTPWrapper()
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=ssh,
            sftp=sftp,
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
        )
        result = submitter.submit_batch(SubmitMode.all)
        assert len(result.errors) == 0
        assert result.updated_task_ids == ["t1"]
        # verify internal task state
        t1 = next(t for t in submitter._tasks if t.task_id == "t1")
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
        ssh = self._make_mock_ssh(exit_code=0, stdout="4321")
        sftp = FakeSFTPWrapper()
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=ssh,
            sftp=sftp,
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
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
        ssh = self._make_mock_ssh(exit_code=1, stderr="chmod: permission denied")
        sftp = FakeSFTPWrapper()
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=ssh,
            sftp=sftp,
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
        )
        result = submitter.submit_batch(SubmitMode.all)
        assert len(result.errors) > 0
        assert "chmod" in result.errors[0].lower()
        # Task status unchanged
        t1 = next(t for t in submitter._tasks if t.task_id == "t1")
        assert t1.status == TaskStatus.uploaded

    def test_submit_nohup_failure_marks_manifest_uncertain(self):
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        ssh = MagicMock()
        ssh.run = MagicMock(
            side_effect=[
                SSHResult("chmod", 0, "", "", 0.01),  # chmod ok
                SSHResult("nohup", 1, "", "nohup failed", 0.01),  # nohup fails
            ]
        )
        sftp = FakeSFTPWrapper()
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=ssh,
            sftp=sftp,
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
        )
        result = submitter.submit_batch(SubmitMode.all)
        assert len(result.errors) > 0
        t1 = next(t for t in submitter._tasks if t.task_id == "t1")
        assert t1.status == TaskStatus.uncertain
        assert t1.submitted_at is not None
        assert t1.error_message == "nohup start failed: nohup failed"

    def test_max_parallel_must_be_positive(self):
        with pytest.raises(ValueError, match="max_parallel"):
            JobSubmitter(ssh=None, sftp=None, max_parallel=0, remote_batch_dir="/r", batch_id="b1", tasks=[])

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
        tasks = [_make_task("t1", TaskStatus.uploaded, "/remote/b1/t1")]
        ssh = _MockSSHForSubmit(SSHResult("", 0, "4321", "", 0.01))
        sftp = FakeSFTPWrapper()
        submitter = JobSubmitter(
            tasks=tasks,
            ssh=ssh,
            sftp=sftp,
            max_parallel=4,
            remote_batch_dir="/remote/b1",
            batch_id="b1",
        )
        result = submitter.submit_batch(SubmitMode.all)
        assert result.control_script_path == "/remote/b1/_batch/batch_control.sh"
        assert result.control_log_path == "/remote/b1/_batch/batch_control.nohup.log"
        assert result.control_nohup_log_path == "/remote/b1/_batch/batch_control.nohup.log"
        assert "nohup" in result.nohup_command
