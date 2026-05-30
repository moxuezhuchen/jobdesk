from pathlib import Path

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest
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


def test_create_run_persists_manifest_batch_and_run_json(tmp_path, runs_dir):
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
    assert (record.run_dir / "run.json").exists()
    assert (record.run_dir / "batch.json").exists()
    assert (record.run_dir / "manifest.tsv").exists()
    tasks = Manifest.read(record.manifest_path)
    assert [task.task_id for task in tasks] == ["a", "b"]
    assert all(task.status == TaskStatus.uploaded for task in tasks)
    assert all(task.server_id == "s1" for task in tasks)


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


def test_run_json_replace_failure_keeps_existing_record(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="run_atomic")
    original = (record.run_dir / "run.json").read_text(encoding="utf-8")

    def fail_replace(self, target):
        raise RuntimeError("replace failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(RuntimeError, match="replace failed"):
        service.update_run_from_manifest("run_atomic")

    assert (record.run_dir / "run.json").read_text(encoding="utf-8") == original


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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.submitted
    Manifest.write(record.manifest_path, tasks)

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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)

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
    assert (tmp_path / "results" / "run001" / "a" / "a.log").read_text(encoding="utf-8") == "ok"
    assert Manifest.read(record.manifest_path)[0].status == TaskStatus.downloaded


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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)
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
    assert (tmp_path / "results" / "run004" / "water" / "water_confflow_work" / "run_summary.json").exists()


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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            raise AssertionError("unsafe output must not be downloaded")

    records, failures = service.download_completed("run005", FakeSFTP(), ["*.log"])

    assert records == []
    assert failures == [("water", "unsafe declared result path: ../outside.json")]
    assert not (tmp_path / "results" / "outside.json").exists()
    task = Manifest.read(record.manifest_path)[0]
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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.failed
    Manifest.write(record.manifest_path, tasks)

    changed = service.prepare_retry_failed("run001")

    assert changed == 1
    assert Manifest.read(record.manifest_path)[0].status == TaskStatus.uploaded


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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "slurm"
    tasks[0].remote_job_id = "12345"
    Manifest.write(record.manifest_path, tasks)
    adapter = pytest.importorskip("unittest.mock").MagicMock()
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)
    ssh = object()

    changed, errors = service.cancel_run("run_cancel", ssh)

    adapter.cancel.assert_called_once_with(ssh, "12345")
    assert changed == 1
    assert errors == []
    assert Manifest.read(record.manifest_path)[0].status == TaskStatus.cancelled


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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "pbs"
    tasks[0].remote_job_id = "99"
    Manifest.write(record.manifest_path, tasks)
    adapter = pytest.importorskip("unittest.mock").MagicMock()
    adapter.cancel.side_effect = RuntimeError("qdel rejected")
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)

    changed, errors = service.cancel_run("run_cancel_fail", object())

    assert changed == 0
    assert "qdel rejected" in errors[0]
    assert Manifest.read(record.manifest_path)[0].status == TaskStatus.running


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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)

    class FailSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            raise TimeoutError("sftp timeout")

    _records, failures = service.download_completed("run_err", FailSFTP(), [".log"])

    assert failures
    task = Manifest.read(record.manifest_path)[0]
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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    tasks[0].error_message = "download: b.log: old error"
    Manifest.write(record.manifest_path, tasks)

    class OkSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            local_path = Path(local_path) if not isinstance(local_path, Path) else local_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord
            return TransferRecord(TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred)

    _records, failures = service.download_completed("run_retry", OkSFTP(), [".log"])

    assert not failures
    task = Manifest.read(record.manifest_path)[0]
    assert task.status == TaskStatus.downloaded
    assert task.error_message is None or "download:" not in task.error_message


def test_download_directory_creation_failure_persists_error_message(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/c.sh")],
    ), run_id="run_mkdir_fail")
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)

    failing_dir = tmp_path / "results" / "run_mkdir_fail" / "c"
    original_mkdir = Path.mkdir

    def fail_result_dir(self, *args, **kwargs):
        if self == failing_dir:
            raise PermissionError("results directory denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_result_dir)

    _records, failures = service.download_completed("run_mkdir_fail", object(), [".log"])

    assert failures == [("c", "results directory denied")]
    task = Manifest.read(record.manifest_path)[0]
    assert task.status == TaskStatus.remote_completed
    assert task.error_message == "download: results directory denied"



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
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)

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
    task = Manifest.read(record.manifest_path)[0]
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

    # Confirm metadata exists
    assert (record.run_dir / "run.json").exists()
    assert record.manifest_path.exists()

    import shutil
    original_rmtree = shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        if Path(path).resolve() == results_dir.resolve():
            raise PermissionError("locked")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    with pytest.raises(OSError, match="Failed to delete results"):
        service.delete_run("run_locked")

    # Metadata must survive
    assert (record.run_dir / "run.json").exists()
    assert record.manifest_path.exists()



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
    tasks = Manifest.read(record.manifest_path)
    for t in tasks:
        t.status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)

    sftp = MagicMock()
    _, failures = svc.download_completed("bsrun", sftp, patterns=["*.log"])

    sftp.download_file.assert_not_called()
    assert failures
    assert Manifest.read(record.manifest_path)[0].status == TaskStatus.remote_completed
