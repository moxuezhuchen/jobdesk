import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.core.transfer import TransferStatus
from jobdesk_app.remote.scheduler import ResourceSpec, SlurmAdapter
from jobdesk_app.services.run_service import RunService


@pytest.fixture
def runs_dir(tmp_path):
    d = tmp_path / "_global_runs"
    d.mkdir()
    return d


def test_create_run_persists_only_to_sqlite(tmp_path, runs_dir):
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=4,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.gjf"), RunSource("/remote/jobs/b.gjf")],
    )

    record = RunService(tmp_path, runs_dir=runs_dir).create_run(spec, run_id="run001")

    assert record.run_id == "run001"
    assert record.run_dir.exists()
    assert not (record.run_dir / "run.json").exists()
    assert not (record.run_dir / "batch.json").exists()
    assert not (record.run_dir / "manifest.tsv").exists()
    tasks = RunService(tmp_path, runs_dir=runs_dir).repository.load_tasks(record.run_id)
    assert [task.task_id for task in tasks] == ["a", "b"]
    assert all(task.status == TaskStatus.uploaded for task in tasks)
    assert all(task.server_id == "s1" for task in tasks)


def test_create_run_is_immediately_queryable_from_sqlite(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="sqlite-run")

    with sqlite3.connect(runs_dir / "jobdesk.db") as connection:
        assert connection.execute(
            "SELECT run_id FROM runs WHERE run_id = 'sqlite-run'"
        ).fetchone() == ("sqlite-run",)


def test_run_service_exposes_legacy_migration_errors(tmp_path, runs_dir):
    broken = runs_dir / "broken"
    broken.mkdir()
    (broken / "run.json").write_text("{broken", encoding="utf-8")

    service = RunService(tmp_path, runs_dir=runs_dir)

    errors = service.migration_errors()
    assert len(errors) == 1
    assert errors[0].legacy_path == broken


def test_create_run_rejects_duplicate_explicit_run_id(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )
    service.create_run(spec, run_id="duplicate")

    with pytest.raises(FileExistsError):
        service.create_run(spec, run_id="duplicate")


def test_next_run_id_considers_database_rows_without_directories(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    prefix = datetime.now().strftime("%y%m%d")
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id=f"{prefix}-001")
    record.run_dir.rmdir()

    assert service._next_run_id() == f"{prefix}-002"


def test_create_run_rejects_unsafe_explicit_run_id(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )

    with pytest.raises(ValueError, match="Invalid run_id"):
        service.create_run(spec, run_id="../outside")


def test_load_run_rejects_path_traversal(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)

    with pytest.raises(ValueError, match="Invalid run_id"):
        service.load_run("../outside")


def test_update_from_missing_legacy_manifest_is_noop(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_atomic")
    updated = service.update_run_from_manifest("run_atomic")

    assert updated == record
    assert not record.manifest_path.exists()


def test_list_runs_returns_latest_first(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    for run_id in ("run001", "run002"):
        service.create_run(RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource(f"/remote/jobs/{run_id}.sh")],
        ), run_id=run_id)

    runs = service.list_runs()

    assert [run.run_id for run in runs] == ["run002", "run001"]


def test_update_run_from_manifest_counts_statuses(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run001")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.submitted
    service.repository.replace_tasks(record.run_id, tasks)

    updated = service.update_run_from_manifest("run001")

    assert updated.status_summary == {"submitted": 1}


def test_download_completed_run_outputs(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run001")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    service.repository.replace_tasks(record.run_id, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            local_path = Path(local_path) if not isinstance(local_path, Path) else local_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord
            return TransferRecord(TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred)

    records, failures = service.download_completed("run001", FakeSFTP(), [".log"])

    assert not failures
    assert len(records) == 1
    assert (tmp_path / "a.log").read_text(encoding="utf-8") == "ok"
    assert not (tmp_path / "results").exists()
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.downloaded


def test_download_completed_uses_declared_nested_results(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="wsl",
        remote_dir="/remote/jobs",
        command_template="confflow {name} -c settings.yaml -w {basename}_confflow_work",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/water.xyz")],
        supporting_sources=[RunSource("/remote/jobs/settings.yaml")],
        result_templates=["{basename}.txt", "{basename}_confflow_work/run_summary.json"],
    ), run_id="run004")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    service.repository.replace_tasks(record.run_id, tasks)
    requested = []

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            requested.append(remote_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord
            return TransferRecord(TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred)

    records, failures = service.download_completed("run004", FakeSFTP(), ["*.log"])

    assert not failures
    assert len(records) == 2
    assert requested == [
        "/remote/jobs/water.txt",
        "/remote/jobs/water_confflow_work/run_summary.json",
    ]
    assert (tmp_path / "water_confflow_work" / "run_summary.json").exists()
    assert not (tmp_path / "results").exists()


def test_download_completed_rejects_declared_result_path_traversal(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="wsl",
        remote_dir="/remote/jobs",
        command_template="echo run",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/water.xyz")],
        result_templates=["../outside.json"],
    ), run_id="run005")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    service.repository.replace_tasks(record.run_id, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            raise AssertionError("unsafe output must not be downloaded")

    records, failures = service.download_completed("run005", FakeSFTP(), ["*.log"])

    assert records == []
    assert failures == [("water", "unsafe declared result path: ../outside.json")]
    assert not (tmp_path / "results" / "outside.json").exists()
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert task.error_message == "download: unsafe declared result path: ../outside.json"


def test_declared_outputs_pattern_semantics(tmp_path, runs_dir):
    """Plain filename patterns are used as-is; glob patterns expand to stem+suffix."""
    from jobdesk_app.core.manifest import TaskRecord
    from jobdesk_app.services.run_service import _declared_outputs

    task = TaskRecord(
        task_id="mol", batch_id="b1", remote_job_dir="/tmp/mol",
        remote_task_files=["mol.gjf"],
    )
    # glob patterns → stem expansion
    assert _declared_outputs(task, ["*.log"]) == ["mol.log"]
    assert _declared_outputs(task, [".log"]) == ["mol.log"]
    # plain filenames → exact (no stem prepend)
    assert _declared_outputs(task, ["result.log"]) == ["result.log"]
    assert _declared_outputs(task, ["summary.json"]) == ["summary.json"]
    assert _declared_outputs(task, ["subdir/result.json"]) == ["subdir/result.json"]


def test_prepare_retry_failed_marks_failed_tasks_uploaded(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run001")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.failed
    service.repository.replace_tasks(record.run_id, tasks)

    changed = service.prepare_retry_failed("run001")

    assert changed == 1
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.uploaded


def test_prepare_rerun_rejects_active_remote_tasks(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_active")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].remote_job_id = "12345"
    service.repository.replace_tasks(record.run_id, tasks)

    with pytest.raises(ValueError, match="active remote tasks"):
        service.prepare_rerun("run_active")

    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.running
    assert task.remote_job_id == "12345"


def test_prepare_rerun_clears_execution_metadata_for_terminal_tasks(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_done")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.failed
    tasks[0].submitted_at = datetime(2026, 5, 31, 8, 0, 0)
    tasks[0].completed_at = datetime(2026, 5, 31, 8, 1, 0)
    tasks[0].scheduler_type = "slurm"
    tasks[0].remote_job_id = "999"
    tasks[0].error_message = "old failure"
    service.repository.replace_tasks(record.run_id, tasks)

    changed = service.prepare_rerun("run_done")

    assert changed == 1
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.uploaded
    assert task.submitted_at is None
    assert task.completed_at is None
    assert task.remote_job_id is None
    assert task.scheduler_type == "nohup"
    assert task.error_message is None


def test_submit_run_persists_and_reuses_execution_strategy(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_strategy")
    captured = []

    class FakeSubmitter:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def submit_batch(self):
            return SubmitResult("run_strategy", 1, "/remote/jobs")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", FakeSubmitter)
    resources = ResourceSpec(cpus=8, memory_mb=4096, walltime_minutes=60)

    service.submit_run(
        "run_strategy", object(), object(),
        env_init_scripts=["/opt/module.sh"],
        scheduler=SlurmAdapter(),
        resources=resources,
    )
    service.submit_run("run_strategy", object(), object())

    loaded = service.load_run("run_strategy")
    assert loaded.scheduler_type == "slurm"
    assert loaded.env_init_scripts == ["/opt/module.sh"]
    assert loaded.resources["cpus"] == 8
    assert isinstance(captured[1]["scheduler"], SlurmAdapter)
    assert captured[1]["env_init_scripts"] == ["/opt/module.sh"]
    assert captured[1]["resources"].memory_mb == 4096


def test_submit_run_skips_tasks_claimed_by_another_process(tmp_path, runs_dir, monkeypatch):
    first = RunService(tmp_path, runs_dir=runs_dir)
    first.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_claimed")
    assert first.repository.claim_uploaded_tasks("run_claimed")
    submitter = MagicMock()
    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", submitter)

    result = RunService(tmp_path, runs_dir=runs_dir).submit_run(
        "run_claimed", object(), object()
    )

    assert result.submitted_task_count == 0
    submitter.assert_not_called()


def test_submit_run_checkpoints_each_scheduler_success_before_batch_finishes(
    tmp_path, runs_dir, monkeypatch
):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh"), RunSource("/remote/jobs/b.sh")],
    ), run_id="run_partial_submit")

    class CrashingSubmitter:
        def __init__(self, **kwargs):
            self.tasks = kwargs["tasks"]
            self.checkpoint = kwargs["task_update_callback"]

        def submit_batch(self):
            submitted = self.tasks[0].model_copy(update={
                "status": TaskStatus.submitted,
                "scheduler_type": "slurm",
                "remote_job_id": "12345",
            })
            self.checkpoint([submitted])
            raise RuntimeError("process crashed after first remote submission")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", CrashingSubmitter)

    with pytest.raises(RuntimeError, match="process crashed"):
        service.submit_run(
            "run_partial_submit", object(), object(), scheduler=SlurmAdapter()
        )

    tasks = service.repository.load_tasks("run_partial_submit")
    assert tasks[0].status == TaskStatus.submitted
    assert tasks[0].remote_job_id == "12345"
    assert tasks[1].status == TaskStatus.uploaded


def test_losing_submitter_does_not_overwrite_execution_resources(tmp_path, runs_dir):
    first = RunService(tmp_path, runs_dir=runs_dir)
    first.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_claimed_resources")
    assert first.repository.claim_uploaded_tasks("run_claimed_resources")

    RunService(tmp_path, runs_dir=runs_dir).submit_run(
        "run_claimed_resources",
        object(),
        object(),
        resources=ResourceSpec(cpus=99),
    )

    assert first.load_run("run_claimed_resources").resources == {}


def test_cancel_run_cancels_remote_job_before_recording_terminal_state(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_cancel")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "slurm"
    tasks[0].remote_job_id = "12345"
    service.repository.replace_tasks(record.run_id, tasks)
    adapter = pytest.importorskip("unittest.mock").MagicMock()
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)
    ssh = object()

    changed, errors = service.cancel_run("run_cancel", ssh)

    adapter.cancel.assert_called_once_with(ssh, "12345")
    assert changed == 1
    assert errors == []
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.cancelled


def test_cancel_run_does_not_claim_cancel_when_remote_cancel_fails(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_cancel_fail")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "pbs"
    tasks[0].remote_job_id = "99"
    service.repository.replace_tasks(record.run_id, tasks)
    adapter = pytest.importorskip("unittest.mock").MagicMock()
    adapter.cancel.side_effect = RuntimeError("qdel rejected")
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)

    changed, errors = service.cancel_run("run_cancel_fail", object())

    assert changed == 0
    assert "qdel rejected" in errors[0]
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.running


def test_download_failure_persists_error_message_to_manifest(tmp_path, runs_dir):
    """When SFTP download fails, error_message should be written to manifest."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_err")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    service.repository.replace_tasks(record.run_id, tasks)

    class FailSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            raise TimeoutError("sftp timeout")

    _records, failures = service.download_completed("run_err", FailSFTP(), [".log"])

    assert failures
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert "download:" in task.error_message
    assert "sftp timeout" in task.error_message


def test_successful_download_clears_previous_download_error(tmp_path, runs_dir):
    """After retry succeeds, the download error_message must be cleared."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/b.sh")],
    ), run_id="run_retry")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    tasks[0].error_message = "download: b.log: old error"
    service.repository.replace_tasks(record.run_id, tasks)

    class OkSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            local_path = Path(local_path) if not isinstance(local_path, Path) else local_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord
            return TransferRecord(TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred)

    _records, failures = service.download_completed("run_retry", OkSFTP(), [".log"])

    assert not failures
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.downloaded
    assert task.error_message is None or "download:" not in task.error_message


def test_download_directory_creation_failure_persists_error_message(tmp_path, runs_dir, monkeypatch):
    download_dir = tmp_path / "downloads"
    service = RunService(download_dir, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/c.sh")],
    ), run_id="run_mkdir_fail")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    service.repository.replace_tasks(record.run_id, tasks)

    original_mkdir = Path.mkdir

    def fail_download_dir(self, *args, **kwargs):
        if self == download_dir:
            raise PermissionError("download directory denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_download_dir)

    _records, failures = service.download_completed("run_mkdir_fail", object(), [".log"])

    assert failures == [("c", "download directory denied")]
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert task.error_message == "download: download directory denied"



def test_download_completed_persists_transfer_record_failed_reason(tmp_path, runs_dir):
    """When sftp.download_file returns TransferStatus.failed, the reason must be persisted."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_failed_rec")
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    service.repository.replace_tasks(record.run_id, tasks)

    class FailedRecordSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord
            return TransferRecord(
                TransferDirection.download, str(local_path), remote_path,
                status=TransferStatus.failed,
                reason="remote file not found",
            )

    _records, failures = service.download_completed("run_failed_rec", FailedRecordSFTP(), [".log"])

    assert failures
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert "remote file not found" in task.error_message
    assert task.error_message.startswith("download:")



def test_delete_run_preserves_metadata_when_results_deletion_fails(tmp_path, runs_dir, monkeypatch):
    """If results_dir deletion fails, run_dir (metadata) must not be lost."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_locked")

    # Create a results directory
    results_dir = tmp_path / "results" / "run_locked"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "output.log").write_text("data", encoding="utf-8")

    # Confirm SQLite metadata exists.
    assert service.load_run(record.run_id).run_id == record.run_id

    import shutil
    original_rmtree = shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        if Path(path).resolve() == results_dir.resolve():
            raise PermissionError("locked")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    with pytest.raises(OSError, match="Failed to delete results"):
        service.delete_run("run_locked")

    # SQLite metadata must survive.
    assert service.load_run(record.run_id).run_id == record.run_id


def test_delete_run_restores_directory_when_database_delete_fails(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_db_locked")
    monkeypatch.setattr(
        service.repository,
        "delete_run",
        MagicMock(side_effect=sqlite3.OperationalError("database is locked")),
    )

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        service.delete_run(record.run_id)

    assert record.run_dir.is_dir()


def test_refresh_run_reports_only_changes_committed_by_compare_and_swap(
    tmp_path, runs_dir, monkeypatch
):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_refresh_race")
    original = service.repository.load_tasks(record.run_id)
    refreshed = [
        original[0].model_copy(update={"status": TaskStatus.running}, deep=True)
    ]
    refresh_result = SimpleNamespace(changed_count=1)
    monkeypatch.setattr(
        "jobdesk_app.remote.status_refresh.refresh_task_statuses",
        lambda *_args, **_kwargs: (refresh_result, refreshed),
    )
    monkeypatch.setattr(service.repository, "merge_tasks", lambda *_args, **_kwargs: original)

    result = service.refresh_run(record.run_id, object())

    assert result.changed_count == 0



def test_create_run_rejects_relative_remote_dir(tmp_path, runs_dir):
    from jobdesk_app.remote.errors import RemotePathError
    spec = RunSpec(
        server_id="s1", remote_dir="relative/path", command_template="g16 {name}",
        max_parallel=1, mode=RunMode.selected_files, sources=[RunSource("/remote/a.gjf")],
    )
    with pytest.raises(RemotePathError):
        RunService(tmp_path, runs_dir=runs_dir).create_run(spec, run_id="rel")


def test_create_run_rejects_remote_source_with_parent_ref(tmp_path, runs_dir):
    from jobdesk_app.remote.errors import RemotePathError
    spec = RunSpec(
        server_id="s1", remote_dir="/remote/jobs", command_template="g16 {name}",
        max_parallel=1, mode=RunMode.selected_files,
        sources=[RunSource("/remote/../etc/passwd")],
    )
    with pytest.raises(RemotePathError):
        RunService(tmp_path, runs_dir=runs_dir).create_run(spec, run_id="parref")


def test_download_completed_rejects_backslash_result_traversal(tmp_path, runs_dir):
    from unittest.mock import MagicMock
    spec = RunSpec(
        server_id="s1", remote_dir="/remote/jobs", command_template="g16 {name}",
        max_parallel=1, mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.gjf")],
        result_templates=["..\\evil.txt"],
    )
    svc = RunService(tmp_path, runs_dir=runs_dir)
    record = svc.create_run(spec, run_id="bsrun")
    tasks = svc.repository.load_tasks(record.run_id)
    for t in tasks:
        t.status = TaskStatus.remote_completed
    svc.repository.replace_tasks(record.run_id, tasks)

    sftp = MagicMock()
    _, failures = svc.download_completed("bsrun", sftp, patterns=["*.log"])

    sftp.download_file.assert_not_called()
    assert failures
    assert svc.repository.load_tasks(record.run_id)[0].status == TaskStatus.remote_completed
