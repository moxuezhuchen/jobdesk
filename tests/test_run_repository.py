"""Transactional SQLite run repository tests."""

from __future__ import annotations

import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord
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
