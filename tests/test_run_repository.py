"""Transactional SQLite run repository tests."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.services.run_repository import RunRecord, RunRepository


def _record(runs_dir: Path, run_id: str = "run-1") -> RunRecord:
    run_dir = runs_dir / run_id
    return RunRecord(
        run_id=run_id,
        server_id="server",
        remote_dir="/remote/project",
        command_template="g16 {name}",
        max_parallel=2,
        mode="selected_files",
        created_at="2026-06-27T10:00:00",
        run_dir=run_dir,
        manifest_path=run_dir / "manifest.tsv",
        batch_path=run_dir / "batch.json",
        local_dir="C:/work/project",
        env_init_scripts=["/etc/profile.d/chem.sh"],
        scheduler_type="slurm",
        resources={"cpus": 4, "memory_mb": 8192},
    )


def _task(task_id: str, status: TaskStatus = TaskStatus.local_ready) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        batch_id="run-1",
        remote_job_dir=f"/remote/project/run-1/{task_id}",
        rendered_command=f"g16 {task_id}.gjf",
        status=status,
    )


def _set_status_in_process(runs_dir: str, task_id: str, status: str) -> None:
    repository = RunRepository(Path(runs_dir))

    def mutation(tasks: list[TaskRecord]) -> list[TaskRecord]:
        return [
            task.model_copy(update={"status": TaskStatus(status)}) if task.task_id == task_id else task
            for task in tasks
        ]

    repository.mutate_tasks("run-1", mutation)


def _write_legacy_run(runs_dir: Path, run_id: str = "legacy-1") -> dict[str, bytes]:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    record = _record(runs_dir, run_id)
    data = {
        "run_id": record.run_id,
        "server_id": record.server_id,
        "remote_dir": record.remote_dir,
        "command_template": record.command_template,
        "max_parallel": record.max_parallel,
        "mode": record.mode,
        "created_at": record.created_at,
        "local_dir": record.local_dir,
        "status_summary": {"submitted": 1},
        "env_init_scripts": record.env_init_scripts,
        "scheduler_type": record.scheduler_type,
        "resources": record.resources,
    }
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
    task = _task("a", TaskStatus.submitted).model_copy(update={"batch_id": run_id})
    Manifest.write(run_dir / "manifest.tsv", [task])
    (run_dir / "batch.json").write_text("{}", encoding="utf-8")
    return {path.name: path.read_bytes() for path in run_dir.iterdir() if path.is_file()}


def test_initializes_versioned_wal_database(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")

    with sqlite3.connect(repository.database_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert version == "1"
    assert journal_mode.lower() == "wal"


def test_round_trip_run_and_tasks_with_derived_summary(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(
        _record(runs_dir),
        [_task("a", TaskStatus.running), _task("b", TaskStatus.failed)],
    )

    loaded = repository.load_run("run-1")

    assert loaded.server_id == "server"
    assert loaded.env_init_scripts == ["/etc/profile.d/chem.sh"]
    assert loaded.resources == {"cpus": 4, "memory_mb": 8192}
    assert loaded.status_summary == {"failed": 1, "running": 1}
    assert [task.task_id for task in repository.load_tasks("run-1")] == ["a", "b"]


def test_mutation_rolls_back_when_callback_raises(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(_record(runs_dir), [_task("a")])

    def failing_mutation(tasks: list[TaskRecord]) -> list[TaskRecord]:
        tasks[0].status = TaskStatus.running
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        repository.mutate_tasks("run-1", failing_mutation)

    assert repository.load_tasks("run-1")[0].status == TaskStatus.local_ready


def test_two_processes_do_not_lose_distinct_task_updates(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(_record(runs_dir), [_task("a"), _task("b")])
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=_set_status_in_process,
            args=(str(runs_dir), task_id, status.value),
        )
        for task_id, status in (("a", TaskStatus.running), ("b", TaskStatus.failed))
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0

    assert repository.load_run("run-1").status_summary == {"failed": 1, "running": 1}


def test_list_and_delete_runs(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(_record(runs_dir, "run-1"), [_task("a")])
    second = _record(runs_dir, "run-2")
    second.created_at = "2026-06-27T11:00:00"
    repository.create_run(second, [_task("b")])

    assert [record.run_id for record in repository.list_runs()] == ["run-2", "run-1"]

    repository.delete_run("run-1")

    assert [record.run_id for record in repository.list_runs()] == ["run-2"]
    with pytest.raises(KeyError, match="run-1"):
        repository.load_run("run-1")


def test_imports_legacy_run_without_modifying_legacy_files(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    before = _write_legacy_run(runs_dir)

    repository = RunRepository(runs_dir)

    assert repository.load_run("legacy-1").status_summary == {"submitted": 1}
    assert repository.load_tasks("legacy-1")[0].batch_id == "legacy-1"
    after = {
        path.name: path.read_bytes()
        for path in (runs_dir / "legacy-1").iterdir()
        if path.is_file()
    }
    assert after == before


def test_malformed_legacy_run_is_reported_while_valid_run_imports(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_legacy_run(runs_dir, "valid")
    invalid_dir = runs_dir / "invalid"
    invalid_dir.mkdir(parents=True)
    (invalid_dir / "run.json").write_text("{broken", encoding="utf-8")
    (invalid_dir / "manifest.tsv").write_text("task_id\n", encoding="utf-8")

    repository = RunRepository(runs_dir)

    assert [record.run_id for record in repository.list_runs()] == ["valid"]
    errors = repository.list_migration_errors()
    assert len(errors) == 1
    assert errors[0].legacy_path == invalid_dir
    assert "invalid" in errors[0].message.lower() or "json" in errors[0].message.lower()


def test_legacy_import_is_idempotent(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_legacy_run(runs_dir)

    RunRepository(runs_dir)
    RunRepository(runs_dir)

    with sqlite3.connect(runs_dir / "jobdesk.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'legacy_import_complete'"
        ).fetchone()[0] == "1"
