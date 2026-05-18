from pathlib import Path

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest
from jobdesk_app.core.run import RunMode, RunSource, RunSpec
from jobdesk_app.services.run_service import RunService
from jobdesk_app.core.transfer import TransferStatus


def test_create_run_persists_manifest_batch_and_run_json(tmp_path):
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=4,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.gjf"), RunSource("/remote/jobs/b.gjf")],
    )

    record = RunService(tmp_path).create_run(spec, run_id="run001")

    run_dir = tmp_path / ".jobdesk" / "runs" / "run001"
    assert record.run_id == "run001"
    assert (run_dir / "run.json").exists()
    assert (run_dir / "batch.json").exists()
    assert (run_dir / "manifest.tsv").exists()
    tasks = Manifest.read(run_dir / "manifest.tsv")
    assert [task.task_id for task in tasks] == ["a", "b"]
    assert all(task.status == TaskStatus.uploaded for task in tasks)
    assert all(task.server_id == "s1" for task in tasks)


def test_list_runs_returns_latest_first(tmp_path):
    service = RunService(tmp_path)
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


def test_update_run_from_manifest_counts_statuses(tmp_path):
    service = RunService(tmp_path)
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


def test_download_completed_run_outputs(tmp_path):
    service = RunService(tmp_path)
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
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord
            return TransferRecord(TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred)

    records, failures = service.download_completed("run001", FakeSFTP(), ["result.log"])

    assert not failures
    assert len(records) == 1
    assert (tmp_path / "results" / "run001" / "a" / "result.log").read_text(encoding="utf-8") == "ok"
    assert Manifest.read(record.manifest_path)[0].status == TaskStatus.downloaded


def test_prepare_retry_failed_marks_failed_tasks_uploaded(tmp_path):
    service = RunService(tmp_path)
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
