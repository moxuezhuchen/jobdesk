"""Transactional SQLite run repository tests."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.services.run_repository import OperationRecord, RunRecord, RunRepository
from tests.repository_helpers import replace_tasks_for_test


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
        local_dir=str(runs_dir.parent.resolve()),
        env_init_scripts=["/etc/profile.d/chem.sh"],
        scheduler_type="slurm",
        resources={"cpus": 4, "memory_mb": 8192},
    )


def _task(
    task_id: str,
    status: TaskStatus = TaskStatus.local_ready,
    *,
    batch_id: str = "run-1",
) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        batch_id=batch_id,
        remote_job_dir=f"/remote/project/{batch_id}/{task_id}",
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


def _recover_orphan_submits_in_process(runs_dir: str) -> None:
    RunRepository(Path(runs_dir)).recover_legacy_orphan_submit_tasks()


def test_replace_tasks_rejects_batch_id_mismatch_without_changing_existing_rows(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    original = _task("a", TaskStatus.uploaded)
    repository.create_run(_record(repository.runs_dir), [original])
    mismatched = original.model_copy(update={"batch_id": "other-run"})

    with pytest.raises(ValueError, match="batch_id"):
        replace_tasks_for_test(repository, "run-1", [mismatched])

    assert repository.load_tasks("run-1") == [original]


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


def test_confirm_uncertain_tasks_uses_cas_and_preserves_metadata(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    submitted_at = datetime(2026, 6, 27, 9, 0, 0)
    uncertain_a = _task("a", TaskStatus.uncertain).model_copy(update={
        "scheduler_type": "slurm",
        "error_message": "submit response lost",
        "submitted_at": submitted_at,
        "task_files": ["a.gjf"],
    })
    uncertain_b = _task("b", TaskStatus.uncertain)
    uploaded = _task("c", TaskStatus.uploaded)
    repository.create_run(_record(repository.runs_dir), [uncertain_a, uncertain_b, uploaded])

    accepted, durable = repository.resolve_uncertain_tasks(
        "run-1", ["a", "c", "missing"], action="confirm",
        remote_job_ids={"a": "123", "c": "wrong"},
    )

    assert accepted == ["a"]
    by_id = {task.task_id: task for task in durable}
    assert by_id["a"].status == TaskStatus.submitted
    assert by_id["a"].remote_job_id == "123"
    assert by_id["a"].scheduler_type == "slurm"
    assert by_id["a"].error_message is None
    assert by_id["a"].submitted_at == submitted_at
    assert by_id["a"].task_files == ["a.gjf"]
    assert by_id["b"].status == TaskStatus.uncertain
    assert by_id["c"].status == TaskStatus.uploaded


def test_confirm_uncertain_sets_timestamp_but_only_writes_provided_job_ids(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(
        _record(repository.runs_dir),
        [_task("a", TaskStatus.uncertain), _task("b", TaskStatus.uncertain)],
    )

    accepted, durable = repository.resolve_uncertain_tasks(
        "run-1", ["a", "b"], action="confirm", remote_job_ids={"a": "321"}
    )

    assert accepted == ["a", "b"]
    by_id = {task.task_id: task for task in durable}
    assert by_id["a"].remote_job_id == "321"
    assert by_id["b"].remote_job_id is None
    assert by_id["a"].submitted_at is not None
    assert by_id["b"].submitted_at is not None


def test_abandon_uncertain_resets_execution_metadata_for_selected_tasks(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    timestamp = datetime(2026, 6, 27, 9, 0, 0)
    task = _task("a", TaskStatus.uncertain).model_copy(update={
        "scheduler_type": "pbs", "remote_job_id": "77", "error_message": "unknown",
        "submitted_at": timestamp, "started_at": timestamp, "completed_at": timestamp,
        "downloaded_at": timestamp, "analyzed_at": timestamp,
        "rendered_command": "custom command",
    })
    repository.create_run(_record(repository.runs_dir), [task])

    accepted, durable = repository.resolve_uncertain_tasks(
        "run-1", ["a", "unknown"], action="abandon"
    )

    assert accepted == ["a"]
    resolved = durable[0]
    assert resolved.status == TaskStatus.uploaded
    assert resolved.remote_job_id is None
    assert resolved.scheduler_type == "nohup"
    assert resolved.error_message is None
    assert resolved.submitted_at is None
    assert resolved.started_at is None
    assert resolved.completed_at is None
    assert resolved.downloaded_at is None
    assert resolved.analyzed_at is None
    assert resolved.rendered_command == "custom command"


def test_resolve_uncertain_rejects_status_changed_by_concurrent_writer(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(_record(runs_dir), [_task("a", TaskStatus.uncertain)])
    stale_selection = [task.task_id for task in repository.load_tasks("run-1")]
    _set_status_in_process(str(runs_dir), "a", TaskStatus.running.value)

    accepted, durable = repository.resolve_uncertain_tasks(
        "run-1", stale_selection, action="abandon"
    )

    assert accepted == []
    assert durable[0].status == TaskStatus.running


def test_initializes_versioned_wal_database(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")

    with sqlite3.connect(repository.database_path) as connection:
        version = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert version == "5"
    assert repository.schema_version() == 5
    assert repository.current_schema_version() == 5
    assert journal_mode.lower() == "wal"
    assert {"workspace_roots", "delete_operation_workspaces"}.issubset(tables)


def test_v3_migration_adds_nullable_submit_lease_columns(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("ALTER TABLE operations DROP COLUMN lease_expires_at")
        connection.execute("ALTER TABLE operations DROP COLUMN owner_id")
        connection.execute("UPDATE schema_metadata SET value = '3' WHERE key = 'schema_version'")

    upgraded = RunRepository(runs_dir)

    with sqlite3.connect(upgraded.database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(operations)")}
    assert upgraded.schema_version() == 4
    assert {"owner_id", "lease_expires_at"} <= columns


def test_submit_recovery_acquisition_rejects_live_lease_and_takes_expired_lease(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[],
        per_task=False, owner_id="owner-a", lease_seconds=120,
    )
    operation = operations[0]

    assert not repository.acquire_submit_recovery(
        operation.operation_id, "recovery-b", lease_seconds=120
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE operations SET lease_expires_at = ? WHERE operation_id = ?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                operation.operation_id,
            ),
        )
    assert repository.acquire_submit_recovery(
        operation.operation_id, "recovery-b", lease_seconds=120
    )
    stored = next(
        item for item in repository.list_operations()
        if item.operation_id == operation.operation_id
    )
    assert stored.owner_id == "recovery-b"


def test_submit_leases_use_utc_z_and_compare_offset_timestamps_by_instant(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[],
        per_task=False, owner_id="owner-a", lease_seconds=120,
    )
    operation = operations[0]
    assert operation.lease_expires_at is not None
    assert operation.lease_expires_at.endswith("Z")
    assert datetime.fromisoformat(
        operation.lease_expires_at.removesuffix("Z") + "+00:00"
    ).tzinfo == timezone.utc

    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    future_offset = future.astimezone(timezone(timedelta(hours=-12))).isoformat()
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE operations SET lease_expires_at = ? WHERE operation_id = ?",
            (future_offset, operation.operation_id),
        )
    assert not repository.acquire_submit_recovery(
        operation.operation_id, "recovery-b", lease_seconds=120
    )

    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    past_offset = past.astimezone(timezone(timedelta(hours=14))).isoformat()
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE operations SET lease_expires_at = ? WHERE operation_id = ?",
            (past_offset, operation.operation_id),
        )
    assert repository.acquire_submit_recovery(
        operation.operation_id, "recovery-b", lease_seconds=120
    )


def test_naive_submit_lease_is_invalid_and_does_not_block_recovery(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[],
        per_task=False, owner_id="owner-a", lease_seconds=120,
    )
    operation = operations[0]
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE operations SET lease_expires_at = ? WHERE operation_id = ?",
            ("9999-12-31T23:59:59.999999", operation.operation_id),
        )

    assert repository.acquire_submit_recovery(
        operation.operation_id, "recovery-b", lease_seconds=120
    )


def test_submit_phase_update_requires_matching_owner(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[],
        per_task=False, owner_id="owner-a", lease_seconds=120,
    )

    assert not repository.start_submit_operation(
        operations[0].operation_id, owner_id="owner-b"
    )
    assert repository.start_submit_operation(
        operations[0].operation_id, owner_id="owner-a"
    )
    assert not repository.finish_submit_operation(
        operations[0].operation_id, task_ids=["a"], job_ids={"a": "123"},
        owner_id="owner-b",
    )


def test_submit_claim_release_requires_matching_owner(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[],
        per_task=False, owner_id="owner-a", lease_seconds=120,
    )

    assert not repository.release_claimed_submit_operation(
        operations[0].operation_id, owner_id="owner-b"
    )
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting


def test_reopening_ready_repository_skips_write_initialization(
    tmp_path: Path, monkeypatch
) -> None:
    runs_dir = tmp_path / "runs"
    RunRepository(runs_dir)
    initialize = MagicMock(side_effect=AssertionError("write initialization repeated"))
    monkeypatch.setattr(RunRepository, "_initialize", initialize)

    reopened = RunRepository(runs_dir)

    assert reopened.schema_version() == 4
    initialize.assert_not_called()


def test_upgrades_v1_database_without_changing_task_state(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(_record(runs_dir), [_task("a", TaskStatus.running)])
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("DROP TABLE operations")
        connection.execute(
            "UPDATE schema_metadata SET value = '1' WHERE key = 'schema_version'"
        )

    upgraded = RunRepository(runs_dir)

    assert upgraded.current_schema_version() == 4
    assert upgraded.load_tasks("run-1")[0].status == TaskStatus.running
    with sqlite3.connect(upgraded.database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(operations)")
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(operations)")
        }
        foreign_keys = connection.execute("PRAGMA foreign_key_list(operations)").fetchall()
    assert columns == {
        "operation_id", "run_id", "kind", "phase", "payload_json", "last_error",
        "created_at", "updated_at", "completed_at", "owner_id", "lease_expires_at",
    }
    assert "operations_run_id_idx" in indexes
    assert foreign_keys == []


def test_upgrades_v2_registry_only_from_live_absolute_run_workspaces(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    trusted = tmp_path / "trusted"
    record = _record(runs_dir)
    record.local_dir = str(trusted.resolve())
    repository.create_run(
        record,
        [_task("a", TaskStatus.uploaded)],
    )
    relative = _record(runs_dir, "relative")
    relative.local_dir = "relative/workspace"
    repository.create_run(relative, [_task("b", TaskStatus.uploaded, batch_id="relative")])
    empty = _record(runs_dir, "empty")
    empty.local_dir = ""
    repository.create_run(empty, [_task("c", TaskStatus.uploaded, batch_id="empty")])
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("DROP TABLE workspace_roots")
        connection.execute(
            "UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'"
        )

    upgraded = RunRepository(runs_dir)

    assert upgraded.current_schema_version() == 4
    assert upgraded.list_workspace_roots() == [trusted.resolve()]
    assert upgraded.delete_operation_workspace("missing") is None


def test_v2_migration_never_trusts_delete_operation_payload(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    external = tmp_path / "external"
    operation = repository.create_operation(
        "deleted-run",
        "delete",
        "metadata_deleted",
        {
            "run": {"local_dir": str(external.resolve())},
            "results_root": str((external / "results").resolve()),
        },
    )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("DROP TABLE workspace_roots")
        connection.execute(
            "UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'"
        )

    upgraded = RunRepository(runs_dir)

    assert upgraded.list_workspace_roots() == []
    assert upgraded.delete_operation_workspace(operation.operation_id) is None
    stored = {item.operation_id: item for item in upgraded.list_operations()}
    assert stored[operation.operation_id].phase == "metadata_deleted"
    assert stored[operation.operation_id].completed_at is None


def test_operation_round_trip_and_compare_and_swap(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")

    created = repository.create_operation(
        "missing-run", "submit", "prepared", {"names": ["a", "β"], "count": 2}
    )
    assert repository.list_operations()[0].payload == {
        "names": ["a", "β"],
        "count": 2,
    }
    advanced = repository.advance_operation(
        created.operation_id,
        expected_phase="prepared",
        phase="remote_started",
        payload={"job_id": 42},
        last_error="transient",
    )
    stale = repository.advance_operation(
        created.operation_id,
        expected_phase="prepared",
        phase="wrong",
    )

    assert advanced is True
    assert stale is False
    listed = repository.list_operations()
    assert listed == [
        OperationRecord(
            operation_id=created.operation_id,
            run_id="missing-run",
            kind="submit",
            phase="remote_started",
            payload={"job_id": 42},
            last_error="transient",
            created_at=created.created_at,
            updated_at=listed[0].updated_at,
            completed_at=None,
        )
    ]


def test_create_operation_payload_isolated_from_caller_mutation(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    payload: dict[str, object] = {"nested": {"items": ["a"]}}

    created = repository.create_operation("run-1", "submit", "prepared", payload)
    nested = payload["nested"]
    assert isinstance(nested, dict)
    items = nested["items"]
    assert isinstance(items, list)
    items.append("changed")

    assert created.payload == {"nested": {"items": ["a"]}}
    assert repository.list_operations()[0].payload == {"nested": {"items": ["a"]}}


def test_complete_list_and_prune_operations(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    completed = repository.create_operation("run-1", "delete", "prepared", {})
    incomplete = repository.create_operation("run-2", "submit", "prepared", {})

    assert repository.advance_operation(
        completed.operation_id,
        expected_phase="prepared",
        phase="done",
        complete=True,
    )
    assert [item.operation_id for item in repository.list_operations(incomplete_only=True)] == [
        incomplete.operation_id
    ]

    deleted = repository.prune_completed_operations(datetime.now() + timedelta(seconds=1))

    assert deleted == 1
    assert [item.operation_id for item in repository.list_operations()] == [
        incomplete.operation_id
    ]


def test_prune_completed_operations_uses_strict_older_than_boundary(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    at_cutoff = repository.create_operation("run-1", "delete", "prepared", {})
    newer = repository.create_operation("run-2", "delete", "prepared", {})
    assert repository.advance_operation(
        at_cutoff.operation_id, "prepared", "done", complete=True
    )
    assert repository.advance_operation(
        newer.operation_id, "prepared", "done", complete=True
    )
    cutoff = datetime(2026, 6, 28, 12, 0, 0)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE operations SET completed_at = ? WHERE operation_id = ?",
            (cutoff.isoformat(), at_cutoff.operation_id),
        )
        connection.execute(
            "UPDATE operations SET completed_at = ? WHERE operation_id = ?",
            ((cutoff + timedelta(seconds=1)).isoformat(), newer.operation_id),
        )

    deleted = repository.prune_completed_operations(cutoff)

    assert deleted == 0
    assert {item.operation_id for item in repository.list_operations()} == {
        at_cutoff.operation_id,
        newer.operation_id,
    }


def test_completed_operation_cannot_be_advanced_again(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    operation = repository.create_operation(
        "run-1", "submit", "prepared", {"job_id": 1}
    )
    assert repository.advance_operation(
        operation.operation_id,
        expected_phase="prepared",
        phase="done",
        last_error="final",
        complete=True,
    )
    before = repository.list_operations()[0]

    advanced = repository.advance_operation(
        operation.operation_id,
        expected_phase="done",
        phase="reopened",
        payload={"job_id": 2},
        last_error="changed",
    )

    assert advanced is False
    assert repository.list_operations()[0] == before


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


def test_merge_tasks_preserves_unrelated_updates_and_rejects_stale_status(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(_record(runs_dir), [_task("a"), _task("b")])
    stale_tasks = repository.load_tasks("run-1")
    repository.mutate_tasks(
        "run-1",
        lambda tasks: [
            task.model_copy(update={"status": TaskStatus.running}) if task.task_id == "a" else task
            for task in tasks
        ],
    )
    proposed = [
        task.model_copy(update={"status": TaskStatus.failed})
        for task in stale_tasks
    ]

    merged = repository.merge_tasks(
        "run-1",
        proposed,
        expected_tasks={task.task_id: task for task in stale_tasks},
    )

    by_id = {task.task_id: task for task in merged.tasks}
    assert by_id["a"].status == TaskStatus.running
    assert by_id["b"].status == TaskStatus.failed
    assert merged.accepted_task_ids == {"b"}


def test_merge_tasks_rejects_same_status_concurrent_field_update(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(_record(runs_dir), [_task("a")])
    expected = repository.load_tasks("run-1")[0]
    repository.mutate_tasks(
        "run-1",
        lambda tasks: [
            task.model_copy(update={"error_message": "new durable error"}, deep=True)
            for task in tasks
        ],
    )
    stale_update = expected.model_copy(update={"remote_job_id": "stale-job"}, deep=True)

    merged = repository.merge_tasks(
        "run-1",
        [stale_update],
        expected_tasks={expected.task_id: expected},
    )

    assert merged.accepted_task_ids == set()
    assert merged.tasks[0].status == expected.status
    assert merged.tasks[0].error_message == "new durable error"
    assert merged.tasks[0].remote_job_id is None


def test_claim_submit_tasks_is_atomic_across_repositories(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    first = RunRepository(runs_dir)
    first.create_run(_record(runs_dir), [_task("a", TaskStatus.uploaded)])
    second = RunRepository(runs_dir)

    claimed_first, operations_first = first.claim_submit_tasks(
        "run-1", scheduler_type="nohup", resources={}, env_init_scripts=[], per_task=False
    )
    claimed_second, operations_second = second.claim_submit_tasks(
        "run-1", scheduler_type="nohup", resources={}, env_init_scripts=[], per_task=False
    )

    assert [task.task_id for task in claimed_first] == ["a"]
    assert claimed_first[0].status == TaskStatus.uploaded
    assert claimed_second == []
    assert len(operations_first) == 1
    assert operations_second == []
    persisted = first.load_tasks("run-1")[0]
    assert persisted.status == TaskStatus.submitting
    assert persisted.submitted_at is not None


def test_claim_submit_creates_operation_and_tasks_in_one_transaction(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])

    tasks, operations = repository.claim_submit_tasks(
        "run-1",
        scheduler_type="nohup",
        resources={"cpus": 4},
        env_init_scripts=["/etc/profile"],
        per_task=False,
    )

    assert [task.task_id for task in tasks] == ["a"]
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting
    assert len(operations) == 1
    assert operations[0].phase == "claimed"
    assert operations[0].payload == {
        "task_ids": ["a"],
        "scheduler_type": "nohup",
        "resources": {"cpus": 4},
        "env_init_scripts": ["/etc/profile"],
        "results": {},
    }


def test_recover_legacy_orphan_submit_marks_uncertain_and_journals_decision(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(
        _record(repository.runs_dir),
        [_task("orphan", TaskStatus.submitting), _task("safe", TaskStatus.uploaded)],
    )

    assert repository.recover_legacy_orphan_submit_tasks() == 1

    tasks = {task.task_id: task for task in repository.load_tasks("run-1")}
    assert tasks["orphan"].status == TaskStatus.uncertain
    assert "operation journal" in (tasks["orphan"].error_message or "")
    assert tasks["safe"].status == TaskStatus.uploaded
    operations = repository.list_operations()
    assert len(operations) == 1
    assert operations[0].kind == "submit"
    assert operations[0].phase == "completed"
    assert operations[0].completed_at is not None
    assert operations[0].payload["task_ids"] == ["orphan"]
    assert operations[0].payload["recovery_decision"] == "uncertain"


def test_repository_initialization_does_not_recover_legacy_orphan_submit(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(
        _record(runs_dir), [_task("orphan", TaskStatus.submitting)]
    )

    reopened = RunRepository(runs_dir)

    assert reopened.load_tasks("run-1")[0].status == TaskStatus.submitting
    assert reopened.list_operations() == []


def test_recover_legacy_orphan_submit_preserves_tasks_with_incomplete_operation(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(
        _record(repository.runs_dir), [_task("tracked", TaskStatus.uploaded)]
    )
    _, operations = repository.claim_submit_tasks(
        "run-1",
        scheduler_type="nohup",
        resources={},
        env_init_scripts=[],
        per_task=False,
    )

    assert repository.recover_legacy_orphan_submit_tasks() == 0
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting
    assert repository.list_operations(incomplete_only=True) == operations


def test_recover_legacy_orphan_submit_is_idempotent_and_concurrent(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    repository.create_run(
        _record(runs_dir), [_task("orphan", TaskStatus.submitting)]
    )

    workers = [
        multiprocessing.Process(
            target=_recover_orphan_submits_in_process, args=(str(runs_dir),)
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    assert repository.recover_legacy_orphan_submit_tasks() == 0
    assert repository.load_tasks("run-1")[0].status == TaskStatus.uncertain
    synthetic = [
        operation
        for operation in repository.list_operations()
        if operation.payload.get("recovery_decision") == "uncertain"
    ]
    assert len(synthetic) == 1


def test_submit_operation_confirm_persists_job_id_and_completes(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)

    assert repository.finish_submit_operation(
        operation.operation_id,
        task_ids=["a"],
        job_ids={"a": "123"},
    )

    task = repository.load_tasks("run-1")[0]
    assert (task.status, task.remote_job_id) == (TaskStatus.submitted, "123")
    completed = repository.list_operations()[0]
    assert completed.phase == "completed"
    assert completed.payload["results"] == {"a": {"job_id": "123"}}
    assert completed.completed_at is not None


def test_submit_outcome_persists_confirmed_before_completion(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)

    assert repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids={"a": "123"}
    )

    persisted = repository.list_operations()[0]
    assert persisted.phase == "confirmed"
    assert persisted.completed_at is None
    task = repository.load_tasks("run-1")[0]
    assert (task.status, task.remote_job_id) == (TaskStatus.submitted, "123")
    assert repository.complete_submit_operation(operation.operation_id, "confirmed")
    assert repository.list_operations()[0].phase == "completed"


def test_submit_outcome_persists_uncertain_before_completion(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)

    assert repository.record_submit_outcome(
        operation.operation_id,
        task_ids=["a"],
        job_ids={},
        error="response lost",
    )

    persisted = repository.list_operations()[0]
    assert persisted.phase == "uncertain"
    assert persisted.completed_at is None
    assert repository.load_tasks("run-1")[0].status == TaskStatus.uncertain
    assert repository.complete_submit_operation(operation.operation_id, "uncertain")
    completed = repository.list_operations()[0]
    assert completed.phase == "completed"
    assert completed.last_error == "response lost"


def test_start_submit_operation_rejects_non_submit_claim(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    operation = repository.create_operation("run-1", "delete", "claimed", {})

    assert not repository.start_submit_operation(operation.operation_id)
    assert repository.list_operations()[0].phase == "claimed"


@pytest.mark.parametrize("scheduler_type", [None, "", 42, {"name": "slurm"}])
def test_submit_outcome_rejects_invalid_scheduler_payload_with_diagnostic(
    tmp_path: Path, scheduler_type: object
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    payload = dict(operation.payload)
    payload["scheduler_type"] = scheduler_type
    assert repository.advance_operation(
        operation.operation_id, "claimed", "remote_started", payload=payload
    )

    assert not repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids={"a": "123"}
    )
    persisted = repository.list_operations()[0]
    assert persisted.phase == "remote_started"
    assert persisted.completed_at is None
    assert persisted.last_error == "remote_started submit payload is invalid"
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting


def test_submit_outcome_rejects_empty_uncertain_error_with_diagnostic(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)

    assert not repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids={}, error=""
    )
    persisted = repository.list_operations()[0]
    assert persisted.phase == "remote_started"
    assert persisted.completed_at is None
    assert persisted.last_error == "submit outcome error must be a non-empty string"
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting


def test_submit_outcome_rejects_missing_journal_task_without_advancing(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("DELETE FROM tasks WHERE run_id = ? AND task_id = ?", ("run-1", "a"))

    assert not repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids={"a": "123"}
    )

    persisted = repository.list_operations()[0]
    assert persisted.phase == "remote_started"
    assert persisted.completed_at is None


def test_submit_outcome_rejects_status_drift_without_partial_updates(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(
        _record(repository.runs_dir),
        [_task("a", TaskStatus.uploaded), _task("b", TaskStatus.uploaded)],
    )
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="nohup", resources={}, env_init_scripts=[], per_task=False
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)
    current = repository.load_tasks("run-1")
    current[1].status = TaskStatus.running
    replace_tasks_for_test(repository, "run-1", current)

    assert not repository.record_submit_outcome(
        operation.operation_id,
        task_ids=["a", "b"],
        job_ids={"a": "123", "b": "123"},
    )

    by_id = {task.task_id: task for task in repository.load_tasks("run-1")}
    assert by_id["a"].status == TaskStatus.submitting
    assert by_id["a"].remote_job_id is None
    assert by_id["b"].status == TaskStatus.running
    assert repository.list_operations()[0].phase == "remote_started"


def test_submit_outcome_rejects_duplicate_journal_task_ids(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    payload = dict(operation.payload)
    payload["task_ids"] = ["a", "a"]
    assert repository.advance_operation(
        operation.operation_id, "claimed", "remote_started", payload=payload
    )

    assert not repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids={"a": "123"}
    )
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting
    assert repository.list_operations()[0].phase == "remote_started"


@pytest.mark.parametrize("job_ids", [{"a": "123", "extra": "456"}, {"a": ""}])
def test_confirmed_submit_outcome_requires_exact_nonempty_job_ids(
    tmp_path: Path, job_ids: dict[str, str]
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)

    assert not repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids=job_ids
    )
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting
    assert repository.list_operations()[0].phase == "remote_started"


def test_uncertain_submit_outcome_requires_empty_job_ids(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)

    assert not repository.record_submit_outcome(
        operation.operation_id,
        task_ids=["a"],
        job_ids={"a": "123"},
        error="response lost",
    )
    assert repository.load_tasks("run-1")[0].status == TaskStatus.submitting
    assert repository.list_operations()[0].phase == "remote_started"


@pytest.mark.parametrize("phase", ["claimed", "remote_started"])
def test_recover_submit_rejects_missing_task_without_phase_change(
    tmp_path: Path, phase: str
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    if phase == "remote_started":
        assert repository.start_submit_operation(operation.operation_id)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("DELETE FROM tasks WHERE run_id = ? AND task_id = ?", ("run-1", "a"))

    assert not repository.recover_submit_operation(operation.operation_id)
    persisted = repository.list_operations()[0]
    assert persisted.phase == phase
    assert persisted.completed_at is None
    assert persisted.last_error == f"{phase} operation task set is invalid"


@pytest.mark.parametrize("phase", ["claimed", "remote_started"])
def test_recover_submit_rejects_task_status_drift(tmp_path: Path, phase: str) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    if phase == "remote_started":
        assert repository.start_submit_operation(operation.operation_id)
    task = repository.load_tasks("run-1")[0]
    replace_tasks_for_test(
        repository,
        "run-1",
        [task.model_copy(update={"status": TaskStatus.running})],
    )

    assert not repository.recover_submit_operation(operation.operation_id)
    persisted = repository.list_operations()[0]
    assert persisted.phase == phase
    assert persisted.completed_at is None
    assert persisted.last_error == f"{phase} operation task set is invalid"


def test_recover_uncertain_rejects_corrupt_payload_without_completion(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)
    assert repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids={}, error="response lost"
    )
    payload = dict(repository.list_operations()[0].payload)
    payload["task_ids"] = ["a", "a"]
    assert repository.advance_operation(
        operation.operation_id, "uncertain", "uncertain", payload=payload
    )

    assert not repository.recover_submit_operation(operation.operation_id)
    persisted = repository.list_operations()[0]
    assert persisted.phase == "uncertain"
    assert persisted.completed_at is None
    assert persisted.last_error == "uncertain operation task set is invalid"


def test_finish_submit_is_idempotent_when_recovery_completes_between_transactions(
    tmp_path: Path, monkeypatch
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)
    original_complete = repository.complete_submit_operation
    recovery_ran = False

    def recover_then_complete(operation_id: str, expected_phase: str) -> bool:
        nonlocal recovery_ran
        if not recovery_ran:
            recovery_ran = True
            assert repository.recover_submit_operation(operation_id)
        return original_complete(operation_id, expected_phase)

    monkeypatch.setattr(repository, "complete_submit_operation", recover_then_complete)

    assert repository.finish_submit_operation(
        operation.operation_id, task_ids=["a"], job_ids={"a": "123"}
    )
    completed = repository.list_operations()[0]
    assert completed.phase == "completed"
    assert completed.completed_at is not None


def test_complete_submit_rejects_unrelated_or_corrupt_completed_operation(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    operation = repository.create_operation(
        "run-1",
        "submit",
        "claimed",
        {
            "task_ids": ["a"],
            "scheduler_type": "slurm",
            "resources": {},
            "env_init_scripts": [],
            "results": {},
            "outcome_phase": "confirmed",
        },
    )
    assert repository.advance_operation(
        operation.operation_id, "claimed", "completed", complete=True
    )

    assert not repository.complete_submit_operation(operation.operation_id, "confirmed")


def test_complete_submit_rejects_non_submit_operation_even_with_matching_phase(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    task = _task("a", TaskStatus.submitted).model_copy(update={"remote_job_id": "123"})
    repository.create_run(_record(repository.runs_dir), [task])
    operation = repository.create_operation(
        "run-1",
        "delete",
        "confirmed",
        {
            "task_ids": ["a"],
            "outcome_phase": "confirmed",
            "results": {"a": {"job_id": "123"}},
        },
    )

    assert not repository.complete_submit_operation(operation.operation_id, "confirmed")
    assert repository.list_operations()[0].phase == "confirmed"


def test_complete_submit_rejects_corrupt_outcome_marker_before_first_cas(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    task = _task("a", TaskStatus.submitted).model_copy(update={"remote_job_id": "123"})
    repository.create_run(_record(repository.runs_dir), [task])
    operation = repository.create_operation(
        "run-1",
        "submit",
        "confirmed",
        {
            "task_ids": ["a"],
            "outcome_phase": "uncertain",
            "results": {"a": {"job_id": "123"}},
        },
    )

    assert not repository.complete_submit_operation(operation.operation_id, "confirmed")
    assert repository.list_operations()[0].phase == "confirmed"


def test_complete_submit_rejects_confirmed_result_with_extra_fields(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    task = _task("a", TaskStatus.submitted).model_copy(update={"remote_job_id": "123"})
    repository.create_run(_record(repository.runs_dir), [task])
    operation = repository.create_operation(
        "run-1",
        "submit",
        "confirmed",
        {
            "task_ids": ["a"],
            "outcome_phase": "confirmed",
            "results": {"a": {"job_id": "123", "error": "stale"}},
        },
    )

    assert not repository.complete_submit_operation(operation.operation_id, "confirmed")
    assert repository.list_operations()[0].phase == "confirmed"


def test_complete_submit_rejects_confirmed_task_with_stale_error(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    task = _task("a", TaskStatus.submitted).model_copy(
        update={"remote_job_id": "123", "error_message": "stale"}
    )
    repository.create_run(_record(repository.runs_dir), [task])
    operation = repository.create_operation(
        "run-1",
        "submit",
        "confirmed",
        {
            "task_ids": ["a"],
            "outcome_phase": "confirmed",
            "results": {"a": {"job_id": "123"}},
        },
    )

    assert not repository.complete_submit_operation(operation.operation_id, "confirmed")
    assert repository.list_operations()[0].phase == "confirmed"


def test_complete_confirmed_is_idempotent_after_task_advances(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)
    assert repository.finish_submit_operation(
        operation.operation_id, task_ids=["a"], job_ids={"a": "123"}
    )
    task = repository.load_tasks("run-1")[0].model_copy(
        update={"status": TaskStatus.running}
    )
    replace_tasks_for_test(repository, "run-1", [task])

    assert repository.complete_submit_operation(operation.operation_id, "confirmed")


def test_complete_uncertain_is_idempotent_after_manual_resolution(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)
    assert repository.finish_submit_operation(
        operation.operation_id,
        task_ids=["a"],
        job_ids={},
        error="response lost",
    )
    accepted, _ = repository.resolve_uncertain_tasks(
        "run-1", ["a"], action="abandon"
    )
    assert accepted

    assert repository.complete_submit_operation(operation.operation_id, "uncertain")


@pytest.mark.parametrize(
    ("payload_update", "task_update", "last_error"),
    [
        ({"outcome_phase": "confirmed"}, {}, "response lost"),
        ({"results": {"a": {"error": "different"}}}, {}, "response lost"),
        (
            {"results": {"a": {"error": "response lost", "job_id": "stale"}}},
            {},
            "response lost",
        ),
        ({}, {"error_message": "different"}, "response lost"),
        ({}, {"remote_job_id": "123"}, "response lost"),
        ({}, {}, "different"),
    ],
)
def test_recover_uncertain_rejects_inconsistent_outcome(
    tmp_path: Path,
    payload_update: dict[str, object],
    task_update: dict[str, object],
    last_error: str,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    repository.create_run(_record(repository.runs_dir), [_task("a", TaskStatus.uploaded)])
    _, operations = repository.claim_submit_tasks(
        "run-1", scheduler_type="slurm", resources={}, env_init_scripts=[], per_task=True
    )
    operation = operations[0]
    assert repository.start_submit_operation(operation.operation_id)
    assert repository.record_submit_outcome(
        operation.operation_id, task_ids=["a"], job_ids={}, error="response lost"
    )
    persisted = repository.list_operations()[0]
    payload = dict(persisted.payload)
    payload.update(payload_update)
    task = repository.load_tasks("run-1")[0].model_copy(update=task_update)
    replace_tasks_for_test(repository, "run-1", [task])
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE operations SET payload_json = ?, last_error = ? WHERE operation_id = ?",
            (json.dumps(payload), last_error, operation.operation_id),
        )

    assert not repository.recover_submit_operation(operation.operation_id)
    invalid = repository.list_operations()[0]
    assert invalid.phase == "uncertain"
    assert invalid.completed_at is None
    assert invalid.last_error == "uncertain submit outcome is invalid"


def test_list_and_delete_runs(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    first = _record(runs_dir, "run-1")
    repository.create_run(first, [_task("a")])
    second = _record(runs_dir, "run-2")
    second.created_at = "2026-06-27T11:00:00"
    repository.create_run(second, [_task("b", batch_id="run-2")])

    assert [record.run_id for record in repository.list_runs()] == ["run-2", "run-1"]

    operation = repository.prepare_delete_run(
        "run-1",
        run_dir=first.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / "run-1",
    )
    assert repository.delete_run_metadata(operation.operation_id)
    assert [record.run_id for record in repository.list_runs()] == ["run-2"]
    with pytest.raises(KeyError, match="run-1"):
        repository.load_run("run-1")


def test_prepare_delete_serializes_run_and_tasks_before_metadata_delete(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    tasks = [_task("a", batch_id=record.run_id), _task("b", batch_id=record.run_id)]
    repository.create_run(record, tasks)

    operation = repository.prepare_delete_run(
        "run-delete",
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / "run-delete",
    )

    assert operation.kind == "delete"
    assert operation.phase == "prepared"
    assert operation.payload["run"]["run_id"] == "run-delete"
    assert [item["task_id"] for item in operation.payload["tasks"]] == ["a", "b"]
    operation_trash = repository.runs_dir / ".jobdesk-trash" / operation.operation_id
    results_trash = tmp_path / "results" / ".jobdesk-trash" / operation.operation_id
    assert Path(str(operation.payload["trash_run_dir"])) == operation_trash / "run"
    assert Path(str(operation.payload["trash_results_dir"])) == results_trash / "results"


def test_prepare_delete_rejects_incomplete_submit_operation(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    repository.create_run(
        record,
        [_task("a", TaskStatus.uploaded, batch_id=record.run_id)],
    )
    repository.claim_submit_tasks(
        record.run_id,
        scheduler_type="slurm",
        resources={},
        env_init_scripts=[],
        per_task=True,
    )
    claimed = repository.load_tasks(record.run_id)
    replace_tasks_for_test(
        repository,
        record.run_id,
        [task.model_copy(update={"status": TaskStatus.uploaded}) for task in claimed],
    )

    with pytest.raises(ValueError, match="incomplete submit operation"):
        repository.prepare_delete_run(
            record.run_id,
            run_dir=record.run_dir,
            results_root=tmp_path / "results",
            results_dir=tmp_path / "results" / record.run_id,
        )

    assert repository.load_run(record.run_id).run_id == record.run_id


@pytest.mark.parametrize(
    "status",
    [
        TaskStatus.submitting,
        TaskStatus.uncertain,
        TaskStatus.submitted,
        TaskStatus.running,
    ],
)
def test_prepare_delete_rejects_active_remote_tasks(
    tmp_path: Path, status: TaskStatus
) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    repository.create_run(
        record,
        [_task("a", status, batch_id=record.run_id)],
    )

    with pytest.raises(ValueError, match="active remote tasks: a"):
        repository.prepare_delete_run(
            record.run_id,
            run_dir=record.run_dir,
            results_root=tmp_path / "results",
            results_dir=tmp_path / "results" / record.run_id,
        )

    assert repository.list_operations() == []
    assert repository.load_run(record.run_id).run_id == record.run_id


def test_delete_metadata_advances_operation_in_same_transaction(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    repository.create_run(record, [_task("a", batch_id=record.run_id)])
    operation = repository.prepare_delete_run(
        record.run_id, run_dir=record.run_dir, results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id
    )

    assert repository.delete_run_metadata(operation.operation_id)
    with pytest.raises(KeyError):
        repository.load_run(record.run_id)
    stored = next(item for item in repository.list_operations() if item.operation_id == operation.operation_id)
    assert stored.phase == "metadata_deleted"


def test_concurrent_delete_metadata_compare_and_swap_has_single_winner(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    first = RunRepository(runs_dir)
    record = _record(runs_dir, "run-delete")
    first.create_run(record, [_task("a", batch_id=record.run_id)])
    operation = first.prepare_delete_run(
        record.run_id, run_dir=record.run_dir, results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id
    )

    from concurrent.futures import ThreadPoolExecutor

    repositories = [RunRepository(runs_dir), RunRepository(runs_dir)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda repo: repo.delete_run_metadata(operation.operation_id), repositories))

    assert sorted(outcomes) == [False, True]


def test_prepare_delete_rejects_results_path_outside_managed_root(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    repository.create_run(record, [_task("a", batch_id=record.run_id)])

    with pytest.raises(ValueError, match="unsafe results directory"):
        repository.prepare_delete_run(
            record.run_id,
            run_dir=record.run_dir,
            results_root=tmp_path / "results",
            results_dir=tmp_path / "outside" / record.run_id,
        )

    assert repository.list_operations() == []
    assert repository.load_run(record.run_id).run_id == record.run_id


def test_prepare_delete_rejects_legacy_run_without_workspace_anchor(
    tmp_path: Path,
) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "legacy-empty-anchor")
    record.local_dir = ""
    repository.create_run(record, [_task("a", batch_id=record.run_id)])

    with pytest.raises(ValueError, match="no absolute local_dir workspace anchor"):
        repository.prepare_delete_run(
            record.run_id,
            run_dir=record.run_dir,
            results_root=tmp_path / "results",
            results_dir=tmp_path / "results" / record.run_id,
        )

    assert repository.list_operations() == []
    assert repository.load_run(record.run_id).run_id == record.run_id


def test_prepare_delete_rejects_run_id_path_outside_runs_root(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "../outside")
    repository.create_run(record, [_task("a", batch_id=record.run_id)])

    with pytest.raises(ValueError, match="unsafe run directory"):
        repository.prepare_delete_run(
            record.run_id,
            run_dir=record.run_dir,
            results_root=tmp_path / "results",
            results_dir=tmp_path / "outside-results",
        )

    assert repository.list_operations() == []
    assert repository.load_run(record.run_id).run_id == record.run_id


def test_create_run_rejects_id_with_incomplete_delete_tombstone(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    repository.create_run(record, [_task("old", batch_id=record.run_id)])
    operation = repository.prepare_delete_run(
        record.run_id, run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id,
    )
    assert repository.delete_run_metadata(operation.operation_id)

    with pytest.raises(ValueError, match="delete is incomplete"):
        repository.create_run(record, [_task("new", batch_id=record.run_id)])

    with pytest.raises(KeyError):
        repository.load_run(record.run_id)


def test_create_run_allows_id_after_delete_operation_completed(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    repository.create_run(record, [_task("old", batch_id=record.run_id)])
    operation = repository.prepare_delete_run(
        record.run_id, run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id,
    )
    assert repository.delete_run_metadata(operation.operation_id)
    assert repository.advance_operation(operation.operation_id, "metadata_deleted", "files_deleted")
    assert repository.advance_operation(
        operation.operation_id, "files_deleted", "completed", complete=True
    )

    assert repository.create_run(
        record, [_task("new", batch_id=record.run_id)]
    ).run_id == record.run_id


def test_execute_delete_isolation_records_callback_error_and_can_retry(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    record.run_dir.mkdir()
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    repository.create_run(record, [_task("old", batch_id=record.run_id)])
    operation = repository.prepare_delete_run(
        record.run_id, run_dir=record.run_dir,
        results_root=tmp_path / "results", results_dir=results_dir,
    )
    assert repository.delete_run_metadata(operation.operation_id)

    def fail_after_results(stored: OperationRecord) -> None:
        Path(str(stored.payload["results_dir"])).rmdir()
        raise PermissionError("run directory locked")

    with pytest.raises(PermissionError, match="locked"):
        repository.execute_delete_isolation(operation.operation_id, fail_after_results)

    failed = next(op for op in repository.list_operations() if op.operation_id == operation.operation_id)
    assert failed.phase == "metadata_deleted"
    assert failed.last_error == "run directory locked"

    def retry(stored: OperationRecord) -> None:
        results = Path(str(stored.payload["results_dir"]))
        run = Path(str(stored.payload["run_dir"]))
        if results.exists():
            results.rmdir()
        if run.exists():
            run.rmdir()

    assert repository.execute_delete_isolation(operation.operation_id, retry)
    advanced = next(op for op in repository.list_operations() if op.operation_id == operation.operation_id)
    assert advanced.phase == "files_isolated"
    assert advanced.last_error is None


def test_delete_isolation_callback_holds_short_single_winner_transaction(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir, "run-delete")
    repository.create_run(record, [_task("old", batch_id=record.run_id)])
    operation = repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id,
    )
    assert repository.delete_run_metadata(operation.operation_id)
    entered = threading.Event()
    release = threading.Event()

    def paused(_stored: OperationRecord) -> None:
        entered.set()
        assert release.wait(15)

    other_repository = RunRepository(repository.runs_dir)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(repository.execute_delete_isolation, operation.operation_id, paused)
        assert entered.wait(5)
        unrelated_write = pool.submit(
            other_repository.create_operation,
            "other-run", "submit", "claimed", {},
        )
        with pytest.raises(TimeoutError):
            unrelated_write.result(timeout=0.2)
        release.set()
        assert deleting.result(timeout=5)
        assert unrelated_write.result(timeout=5).run_id == "other-run"

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


def test_legacy_import_reports_task_batch_id_mismatch(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    _write_legacy_run(runs_dir, "legacy-1")
    manifest_path = runs_dir / "legacy-1" / "manifest.tsv"
    task = Manifest.read(manifest_path)[0].model_copy(update={"batch_id": "other-run"})
    Manifest.write(manifest_path, [task])

    repository = RunRepository(runs_dir)

    assert repository.list_runs() == []
    errors = repository.list_migration_errors()
    assert len(errors) == 1
    assert "batch_id" in errors[0].message


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


def test_malformed_legacy_run_is_retried_after_repair(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    broken_dir = runs_dir / "repaired"
    broken_dir.mkdir(parents=True)
    (broken_dir / "run.json").write_text("not json", encoding="utf-8")
    RunRepository(runs_dir)

    (broken_dir / "run.json").unlink()
    broken_dir.rmdir()
    _write_legacy_run(runs_dir, "repaired")
    repository = RunRepository(runs_dir)
    repository.retry_legacy_imports()

    assert repository.load_run("repaired").run_id == "repaired"
    assert repository.list_migration_errors() == []


def test_removed_malformed_legacy_run_clears_migration_error(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    broken_dir = runs_dir / "removed"
    broken_dir.mkdir(parents=True)
    (broken_dir / "run.json").write_text("not json", encoding="utf-8")
    repository = RunRepository(runs_dir)
    assert len(repository.list_migration_errors()) == 1

    (broken_dir / "run.json").unlink()
    broken_dir.rmdir()
    reopened = RunRepository(runs_dir)
    assert len(reopened.list_migration_errors()) == 1

    reopened.retry_legacy_imports()

    assert reopened.list_migration_errors() == []


def test_ready_database_restores_wal_after_external_journal_mode_change(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA journal_mode = DELETE").fetchone()[0] == "delete"

    RunRepository(runs_dir)

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_migration_errors_do_not_force_write_initialization_on_normal_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_dir = tmp_path / "runs"
    broken_dir = runs_dir / "broken"
    broken_dir.mkdir(parents=True)
    (broken_dir / "run.json").write_text("not json", encoding="utf-8")
    repository = RunRepository(runs_dir)
    expected_errors = repository.list_migration_errors()

    def fail_initialize(_repository: RunRepository) -> None:
        raise AssertionError("normal repository open must not perform write initialization")

    monkeypatch.setattr(RunRepository, "_initialize", fail_initialize)
    reopened = RunRepository(runs_dir)

    assert reopened.list_migration_errors() == expected_errors


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


def test_newer_schema_version_is_rejected_without_relabeling(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    database = runs_dir / "jobdesk.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO schema_metadata VALUES ('schema_version', '999')")

    with pytest.raises(RuntimeError, match="newer schema version"):
        RunRepository(runs_dir)

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0] == "999"
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        } == {"schema_metadata"}
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not database.with_name("jobdesk.db-wal").exists()
    assert not database.with_name("jobdesk.db-shm").exists()


def test_future_schema_race_is_rejected_inside_initialize_transaction(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "runs"
    repository = RunRepository(runs_dir)
    operation = repository.create_operation("run-1", "submit", "prepared", {})
    _write_legacy_run(runs_dir, "should-not-import")

    class RacingRepository(RunRepository):
        def _validate_existing_schema(self) -> None:
            super()._validate_existing_schema()
            with sqlite3.connect(self.database_path) as connection:
                connection.execute(
                    "UPDATE schema_metadata SET value = '999' "
                    "WHERE key = 'schema_version'"
                )

    with pytest.raises(RuntimeError, match="newer schema version"):
        RacingRepository(runs_dir)

    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
        ).fetchone()[0] == "999"
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert connection.execute(
            "SELECT phase FROM operations WHERE operation_id = ?",
            (operation.operation_id,),
        ).fetchone()[0] == "prepared"
