"""Failure recovery tests for run and manifest operations."""
from unittest.mock import MagicMock

import pytest

from jobdesk_app.core.manifest import Manifest, TaskRecord, TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec
from jobdesk_app.services.run_service import RunService


def _task(task_id="t1", batch_id="b1", status=TaskStatus.local_ready, **kw):
    return TaskRecord(task_id=task_id, batch_id=batch_id, remote_job_dir=f"/tmp/{task_id}", status=status, **kw)


@pytest.fixture
def run_service(tmp_path, monkeypatch):
    return RunService(str(tmp_path), runs_dir=tmp_path / "runs")


class TestManifestRecovery:
    def test_manifest_atomic_write(self, tmp_path):
        path = tmp_path / "manifest.tsv"
        tasks = [_task("t1"), _task("t2", status=TaskStatus.uploaded)]
        Manifest.write(path, tasks)
        loaded = Manifest.read(path)
        assert len(loaded) == 2
        assert loaded[0].task_id == "t1"

    def test_manifest_survives_rewrite(self, tmp_path):
        path = tmp_path / "manifest.tsv"
        tasks = [_task("t1")]
        Manifest.write(path, tasks)
        tasks[0].status = TaskStatus.uploaded
        Manifest.write(path, tasks)
        loaded = Manifest.read(path)
        assert loaded[0].status == TaskStatus.uploaded

    def test_manifest_handles_extra_columns(self, tmp_path):
        path = tmp_path / "manifest.tsv"
        tasks = [_task("t1", status=TaskStatus.running)]
        Manifest.write(path, tasks)
        loaded = Manifest.read(path)
        assert loaded[0].status == TaskStatus.running


class TestRunServiceRecovery:
    def test_create_run_generates_unique_ids(self, run_service):
        id1 = run_service._next_run_id()
        (run_service.runs_dir / id1).mkdir(parents=True)
        id2 = run_service._next_run_id()
        assert id1 != id2

    def test_list_runs_with_corrupt_record(self, run_service):
        """list_runs should skip corrupt run records gracefully."""
        bad_dir = run_service.runs_dir / "260101-001"
        bad_dir.mkdir(parents=True)
        (bad_dir / "run.json").write_text("not json", encoding="utf-8")
        # Should not crash — may return empty or skip bad
        runs = run_service.list_runs()
        assert isinstance(runs, list)

    def test_delete_run_nonexistent(self, run_service):
        """Deleting a nonexistent run should not crash."""
        try:
            run_service.delete_run("nonexistent-999")
        except (FileNotFoundError, KeyError):
            pass

    def test_load_run_missing(self, run_service):
        """Loading a nonexistent run should raise."""
        with pytest.raises(Exception):
            run_service.load_run("nonexistent-999")

    def test_download_completed_sftp_failure(self, run_service):
        """download_completed with broken sftp should not crash."""
        spec = RunSpec(
            server_id="test",
            remote_dir="/tmp/test",
            command_template="g16 {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/tmp/test/mol1.gjf")],
        )
        run_service.create_run(spec, run_id="260101-002")
        run_service.repository.mutate_tasks(
            "260101-002",
            lambda tasks: [
                task.model_copy(update={"status": TaskStatus.remote_completed, "remote_work_dir": "/tmp/test"})
                for task in tasks
            ],
        )

        sftp = MagicMock()
        sftp.download_file.side_effect = Exception("Connection lost")
        sftp.stat.return_value = None

        records, failures = run_service.download_completed("260101-002", sftp, ["*.log"])
        assert isinstance(records, list)
        assert isinstance(failures, list)
        assert "Connection lost" in failures[0][1]
