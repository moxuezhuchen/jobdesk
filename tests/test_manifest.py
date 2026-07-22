"""测试 core/manifest.py - Manifest TSV 读写。"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import _MANIFEST_COLUMNS, Manifest, TaskRecord, manifest_lock


class TestManifestLock:
    def test_same_path_shares_one_lock_distinct_paths_differ(self, tmp_path):
        a = tmp_path / "sub" / "manifest.tsv"
        a_eq = tmp_path / "sub" / ".." / "sub" / "manifest.tsv"
        b = tmp_path / "other.tsv"
        assert manifest_lock(a) is manifest_lock(a_eq)
        assert manifest_lock(a) is not manifest_lock(b)

    def test_lock_is_reentrant(self, tmp_path):
        lock = manifest_lock(tmp_path / "m.tsv")
        with lock:
            with lock:
                assert True

    def test_lock_prevents_lost_update_under_concurrency(self, tmp_path):
        """Two threads each bump a distinct task's counter through a read-modify-write.

        With manifest_lock serializing the RMW, every increment survives. Without
        it, each thread writes back a stale copy of the other task and updates are
        lost (the sleep widens the window so the race would manifest reliably).
        """
        import threading
        import time

        p = tmp_path / "manifest.tsv"
        Manifest.write(
            p,
            [
                TaskRecord(task_id="a", batch_id="b", remote_job_dir="/x", error_message="0"),
                TaskRecord(task_id="b", batch_id="b", remote_job_dir="/y", error_message="0"),
            ],
        )
        iterations = 30
        barrier = threading.Barrier(2)

        def bump(index: int):
            barrier.wait()
            for _ in range(iterations):
                with manifest_lock(p):
                    tasks = Manifest.read(p)
                    current = tasks[index].error_message
                    assert current is not None
                    tasks[index].error_message = str(int(current) + 1)
                    time.sleep(0.0005)
                    Manifest.write(p, tasks)

        threads = [threading.Thread(target=bump, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = Manifest.read(p)
        assert final[0].error_message == str(iterations)
        assert final[1].error_message == str(iterations)


class TestTaskRecord:
    def test_create_task_record(self):
        task = TaskRecord(
            task_id="mol_001",
            batch_id="20260511_120000",
            task_files=["inputs/mol_001.gjf"],
            remote_job_dir="/remote/batch/mol_001",
            remote_task_files=["mol_001.gjf"],
            rendered_command="g16 mol_001.gjf",
        )
        assert task.task_id == "mol_001"
        assert task.batch_id == "20260511_120000"
        assert task.status == TaskStatus.local_ready
        assert task.group_key is None
        assert task.uploaded_at is None
        assert task.error_message is None

    def test_task_record_with_status(self):
        task = TaskRecord(
            task_id="t1",
            batch_id="b1",
            task_files=["in/t1.gjf"],
            remote_job_dir="/r/b/t1",
            remote_task_files=["t1.gjf"],
            rendered_command="cmd",
            status=TaskStatus.running,
            uploaded_at=datetime(2026, 5, 11, 12, 0, 0),
        )
        assert task.status == TaskStatus.running
        assert task.uploaded_at == datetime(2026, 5, 11, 12, 0, 0)


class TestManifestWrite:
    def test_write_empty_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [])
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "\t".join(_MANIFEST_COLUMNS) in content

    def test_write_single_task(self):
        task = TaskRecord(
            task_id="mol_001",
            batch_id="20260511_120000",
            task_files=["inputs/mol_001.gjf"],
            remote_job_dir="/remote/batch/mol_001",
            remote_task_files=["mol_001.gjf"],
            rendered_command="g16 mol_001.gjf",
            status=TaskStatus.local_ready,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [task])
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2  # header + 1 task
            assert "mol_001" in lines[1]

    def test_write_multiple_tasks(self):
        tasks = []
        for i in range(5):
            tasks.append(
                TaskRecord(
                    task_id=f"t_{i}",
                    batch_id="b1",
                    task_files=[f"in/t_{i}.gjf"],
                    remote_job_dir=f"/r/b/t_{i}",
                    remote_task_files=[f"t_{i}.gjf"],
                    rendered_command="cmd",
                    status=TaskStatus.local_ready,
                )
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, tasks)
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 6  # header + 5 tasks

    def test_write_creates_parent_dirs(self):
        task = TaskRecord(
            task_id="t1",
            batch_id="b1",
            task_files=["in/t1.gjf"],
            remote_job_dir="/r/b/t1",
            remote_task_files=["t1.gjf"],
            rendered_command="cmd",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "dir" / "manifest.tsv"
            Manifest.write(path, [task])
            assert path.exists()

    def test_write_replace_failure_keeps_existing_manifest(self, monkeypatch):
        task = TaskRecord(
            task_id="t1",
            batch_id="b1",
            task_files=["in/t1.gjf"],
            remote_job_dir="/r/b/t1",
            remote_task_files=["t1.gjf"],
            rendered_command="cmd",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            path.write_text("old manifest\n", encoding="utf-8")

            def fail_replace(self, target):
                raise RuntimeError("replace failed")

            monkeypatch.setattr(Path, "replace", fail_replace)

            with pytest.raises(RuntimeError, match="replace failed"):
                Manifest.write(path, [task])

            assert path.read_text(encoding="utf-8") == "old manifest\n"
            assert list(Path(tmpdir).glob("*.tmp")) == []

    def test_rewrites_use_distinct_temp_files(self, tmp_path, monkeypatch):
        path = tmp_path / "manifest.tsv"
        replaced_from = []
        original_replace = Path.replace

        def capture_replace(self, target):
            replaced_from.append(self)
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", capture_replace)
        Manifest.write(path, [])
        Manifest.write(path, [])

        assert replaced_from[0] != replaced_from[1]


class TestManifestRead:
    def test_read_empty_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [])
            tasks = Manifest.read(path)
            assert tasks == []

    def test_read_single_task(self):
        original = TaskRecord(
            task_id="mol_001",
            batch_id="20260511_120000",
            group_key="group_a",
            task_files=["inputs/mol_001.gjf"],
            remote_job_dir="/remote/batch/mol_001",
            remote_task_files=["mol_001.gjf"],
            rendered_command="g16 mol_001.gjf",
            status=TaskStatus.uploaded,
            uploaded_at=datetime(2026, 5, 11, 13, 0, 0),
            submitted_at=datetime(2026, 5, 11, 13, 1, 0),
            error_message=None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [original])
            tasks = Manifest.read(path)
            assert len(tasks) == 1
            loaded = tasks[0]
            assert loaded.task_id == original.task_id
            assert loaded.batch_id == original.batch_id
            assert loaded.group_key == "group_a"
            assert loaded.task_files == original.task_files
            assert loaded.remote_job_dir == original.remote_job_dir
            assert loaded.rendered_command == original.rendered_command
            assert loaded.status == TaskStatus.uploaded
            assert loaded.uploaded_at == original.uploaded_at
            assert loaded.submitted_at == original.submitted_at
            assert loaded.error_message is None

    def test_read_preserves_declared_result_files(self):
        original = TaskRecord(
            task_id="water",
            batch_id="run004",
            remote_job_dir="/remote/jobs/.jobdesk_runs/run004/water",
            remote_task_files=["water.xyz", "settings.yaml"],
            remote_result_files=[
                "water.txt",
                "water_confflow_work/run_summary.json",
            ],
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [original])

            loaded = Manifest.read(path)[0]

        assert loaded.remote_result_files == original.remote_result_files

    def test_read_preserves_declared_workflow_paths(self, tmp_path):
        original = TaskRecord(
            task_id="water",
            batch_id="run-paths",
            remote_job_dir="/remote/.jobdesk_runs/run-paths/water",
            workflow_kind="confflow",
            remote_config_path="/remote/submission/workflow.yaml",
            remote_workflow_dir="/remote/submission/water_confflow_work",
            remote_state_path="/remote/submission/water_confflow_work/.workflow_state.json",
            remote_stats_path="/remote/submission/water_confflow_work/workflow_stats.json",
            remote_log_path="/remote/.jobdesk_runs/run-paths/water/.jobdesk_submit.log",
            remote_result_paths=["/remote/submission/water.txt"],
            dry_run_command="confflow water.xyz --dry-run",
            resume_command="confflow water.xyz --resume",
            resume_dry_run_command="confflow water.xyz --resume --dry-run",
            resume_requested=True,
        )

        path = tmp_path / "manifest.tsv"
        Manifest.write(path, [original])

        assert Manifest.read(path)[0] == original

    def test_legacy_manifest_defaults_declared_workflow_paths(self, tmp_path):
        legacy_columns = [
            "task_id",
            "batch_id",
            "remote_job_dir",
            "status",
        ]
        path = tmp_path / "legacy.tsv"
        path.write_text(
            "\t".join(legacy_columns) + "\n" + "legacy\trun-old\t/remote/legacy\trunning\n",
            encoding="utf-8",
        )

        loaded = Manifest.read(path)[0]

        assert loaded.workflow_kind == ""
        assert loaded.remote_config_path == ""
        assert loaded.remote_workflow_dir == ""
        assert loaded.remote_state_path == ""
        assert loaded.remote_stats_path == ""
        assert loaded.remote_log_path == ""
        assert loaded.remote_result_paths == []
        assert loaded.dry_run_command == ""
        assert loaded.resume_command == ""
        assert loaded.resume_dry_run_command == ""
        assert loaded.resume_requested is False

    def test_read_preserves_remote_execution_identity(self):
        original = TaskRecord(
            task_id="water",
            batch_id="run006",
            remote_job_dir="/remote/jobs/.jobdesk_runs/run006/water",
            scheduler_type="slurm",
            remote_job_id="12345",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [original])

            loaded = Manifest.read(path)[0]

        assert loaded.scheduler_type == "slurm"
        assert loaded.remote_job_id == "12345"

    def test_read_preserves_all_timestamps(self):
        original = TaskRecord(
            task_id="t1",
            batch_id="b1",
            task_files=["in/t1.gjf"],
            remote_job_dir="/r/b/t1",
            remote_task_files=["t1.gjf"],
            rendered_command="cmd",
            status=TaskStatus.analyzed,
            uploaded_at=datetime(2026, 5, 11, 10, 0, 0),
            submitted_at=datetime(2026, 5, 11, 10, 1, 0),
            started_at=datetime(2026, 5, 11, 10, 2, 0),
            completed_at=datetime(2026, 5, 11, 12, 0, 0),
            downloaded_at=datetime(2026, 5, 11, 12, 30, 0),
            analyzed_at=datetime(2026, 5, 11, 13, 0, 0),
            error_message=None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [original])
            tasks = Manifest.read(path)
            loaded = tasks[0]
            assert loaded.status == TaskStatus.analyzed
            assert loaded.uploaded_at == datetime(2026, 5, 11, 10, 0, 0)
            assert loaded.submitted_at == datetime(2026, 5, 11, 10, 1, 0)
            assert loaded.started_at == datetime(2026, 5, 11, 10, 2, 0)
            assert loaded.completed_at == datetime(2026, 5, 11, 12, 0, 0)
            assert loaded.downloaded_at == datetime(2026, 5, 11, 12, 30, 0)
            assert loaded.analyzed_at == datetime(2026, 5, 11, 13, 0, 0)

    def test_read_preserves_none_values(self):
        original = TaskRecord(
            task_id="t1",
            batch_id="b1",
            task_files=["in/t1.gjf"],
            remote_job_dir="/r/b/t1",
            remote_task_files=["t1.gjf"],
            rendered_command="cmd",
            group_key=None,
            error_message=None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [original])
            tasks = Manifest.read(path)
            loaded = tasks[0]
            assert loaded.group_key is None
            assert loaded.error_message is None
            assert loaded.uploaded_at is None

    def test_read_error_message(self):
        original = TaskRecord(
            task_id="t1",
            batch_id="b1",
            task_files=["in/t1.gjf"],
            remote_job_dir="/r/b/t1",
            remote_task_files=["t1.gjf"],
            rendered_command="cmd",
            status=TaskStatus.failed,
            error_message="Connection timeout",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [original])
            tasks = Manifest.read(path)
            loaded = tasks[0]
            assert loaded.status == TaskStatus.failed
            assert loaded.error_message == "Connection timeout"

    def test_read_multiple_tasks_roundtrip(self):
        tasks = []
        statuses = [
            TaskStatus.local_ready,
            TaskStatus.uploaded,
            TaskStatus.running,
            TaskStatus.remote_completed,
            TaskStatus.downloaded,
            TaskStatus.analyzed,
            TaskStatus.failed,
        ]
        for i, status in enumerate(statuses):
            tasks.append(
                TaskRecord(
                    task_id=f"t_{i}",
                    batch_id="b1",
                    task_files=[f"in/t_{i}.gjf"],
                    remote_job_dir=f"/r/b/t_{i}",
                    remote_task_files=[f"t_{i}.gjf"],
                    rendered_command=f"cmd_{i}",
                    status=status,
                )
            )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, tasks)
            loaded = Manifest.read(path)
            assert len(loaded) == 7
            for i, (orig, ld) in enumerate(zip(tasks, loaded)):
                assert ld.task_id == orig.task_id
                assert ld.status == orig.status

    def test_read_utf8(self):
        original = TaskRecord(
            task_id="任务_001",
            batch_id="b1",
            task_files=["输入/测试.gjf"],
            remote_job_dir="/远程/批次/任务_001",
            remote_task_files=["测试.gjf"],
            rendered_command="命令",
            error_message="错误信息",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            Manifest.write(path, [original])
            tasks = Manifest.read(path)
            loaded = tasks[0]
            assert loaded.task_id == "任务_001"
            assert loaded.task_files == ["输入/测试.gjf"]
            assert loaded.remote_job_dir == "/远程/批次/任务_001"
            assert loaded.error_message == "错误信息"


class TestManifestCorruption:
    def test_invalid_json_field_reports_file_row_and_field(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manifest.tsv"
            row = {col: "" for col in _MANIFEST_COLUMNS}
            row.update(
                {
                    "task_id": "t1",
                    "batch_id": "b1",
                    "remote_job_dir": "/r/b1/t1",
                    "status": "local_ready",
                    "task_files": "[broken",
                }
            )
            path.write_text(
                "\t".join(_MANIFEST_COLUMNS) + "\n" + "\t".join(row[col] for col in _MANIFEST_COLUMNS) + "\n",
                encoding="utf-8",
            )

            with pytest.raises(ValueError) as exc:
                Manifest.read(path)

            message = str(exc.value)
            assert "manifest.tsv" in message
            assert "row 2" in message
            assert "task_files" in message
