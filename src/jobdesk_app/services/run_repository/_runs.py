"""Run-level CRUD — includes helpers for record serialization and row mapping."""

from __future__ import annotations

import json
import sqlite3
import time as _time
from pathlib import Path

from ._operations_types import RunRecord


# ---------------------------------------------------------------------------
# Helpers (were _runs_helpers)
# ---------------------------------------------------------------------------


def _run_values(record: RunRecord) -> tuple:
    return (
        record.run_id,
        record.server_id,
        record.remote_dir,
        record.command_template,
        record.max_parallel,
        record.mode,
        record.created_at,
        record.local_dir,
        json.dumps(record.env_init_scripts, ensure_ascii=False),
        record.scheduler_type,
        json.dumps(record.resources, ensure_ascii=False),
    )


def _run_exists(connection: sqlite3.Connection, run_id: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone() is not None


def _insert_run(connection: sqlite3.Connection, record: RunRecord) -> None:
    connection.execute(
        """
        INSERT INTO runs(
            run_id, server_id, remote_dir, command_template, max_parallel,
            mode, created_at, local_dir, env_init_scripts_json,
            scheduler_type, resources_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _run_values(record),
    )


def _row_to_record(
    connection: sqlite3.Connection, row: sqlite3.Row, runs_dir: Path
) -> RunRecord:
    run_id = str(row["run_id"])
    run_dir = runs_dir / run_id
    summary_rows = connection.execute(
        """
        SELECT status, COUNT(*) AS task_count
        FROM tasks WHERE run_id = ? GROUP BY status ORDER BY status
        """,
        (run_id,),
    ).fetchall()
    return RunRecord(
        run_id=run_id,
        server_id=str(row["server_id"]),
        remote_dir=str(row["remote_dir"]),
        command_template=str(row["command_template"]),
        max_parallel=int(row["max_parallel"]),
        mode=str(row["mode"]),
        created_at=str(row["created_at"]),
        run_dir=run_dir,
        manifest_path=run_dir / "manifest.tsv",
        batch_path=run_dir / "batch.json",
        local_dir=str(row["local_dir"]),
        status_summary={str(item["status"]): int(item["task_count"]) for item in summary_rows},
        env_init_scripts=list(json.loads(row["env_init_scripts_json"])),
        scheduler_type=str(row["scheduler_type"]),
        resources=dict(json.loads(row["resources_json"])),
    )


# ---------------------------------------------------------------------------
# Public CRUD (were _runs)
# ---------------------------------------------------------------------------


def create_run(
    connection: sqlite3.Connection,
    record: RunRecord,
    tasks: list,
) -> RunRecord:
    """Create a run and its tasks atomically, erroring if a deletion is in progress."""
    deadline: float | None = None
    while True:
        connection.execute("BEGIN IMMEDIATE")
        tombstone = connection.execute(
            """SELECT 1 FROM operations
               WHERE run_id = ? AND kind = 'delete' AND completed_at IS NULL
               LIMIT 1""",
            (record.run_id,),
        ).fetchone()
        if tombstone is None:
            break
        connection.rollback()
        if deadline is None:
            from . import _DELETE_CLEANUP_LEADER_GRACE_SECONDS

            deadline = _time.monotonic() + _DELETE_CLEANUP_LEADER_GRACE_SECONDS
        if _time.monotonic() >= deadline:
            raise ValueError(
                f"run_id {record.run_id!r} cannot be reused while delete is incomplete"
            )
        _time.sleep(0.01)
    _insert_run(connection, record)
    _replace_tasks(connection, record.run_id, tasks)
    return record


def _replace_tasks(connection, run_id: str, tasks: list) -> None:
    from jobdesk_app.core.manifest import TaskRecord
    mismatched = [task.task_id for task in tasks if task.batch_id != run_id]
    if mismatched:
        raise ValueError(
            f"task batch_id does not match run_id {run_id!r}: "
            + ", ".join(mismatched)
        )
    connection.execute("DELETE FROM tasks WHERE run_id = ?", (run_id,))
    connection.executemany(
        """
        INSERT INTO tasks(run_id, task_id, status, position, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                task.task_id,
                task.status.value,
                position,
                json.dumps(task.model_dump(mode="json"), ensure_ascii=False),
            )
            for position, task in enumerate(tasks)
        ],
    )


def load_run(connection: sqlite3.Connection, runs_dir: Path, run_id: str) -> RunRecord:
    row = connection.execute(
        "SELECT * FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"run not found: {run_id}")
    return _row_to_record(connection, row, runs_dir)


def list_runs(connection: sqlite3.Connection, runs_dir: Path) -> list[RunRecord]:
    rows = connection.execute(
        "SELECT * FROM runs ORDER BY created_at DESC, run_id DESC"
    ).fetchall()
    return [_row_to_record(connection, row, runs_dir) for row in rows]


def load_tasks(connection: sqlite3.Connection, run_id: str) -> list:
    if not _run_exists(connection, run_id):
        raise KeyError(f"run not found: {run_id}")
    return _load_tasks(connection, run_id)


def _load_tasks(connection, run_id: str) -> list:
    from jobdesk_app.core.manifest import TaskRecord
    rows = connection.execute(
        "SELECT payload_json FROM tasks WHERE run_id = ? ORDER BY position",
        (run_id,),
    ).fetchall()
    return [TaskRecord.model_validate(json.loads(row["payload_json"])) for row in rows]


def update_run(connection: sqlite3.Connection, record: RunRecord) -> RunRecord:
    connection.execute("BEGIN IMMEDIATE")
    cursor = connection.execute(
        """
        UPDATE runs SET
            server_id = ?, remote_dir = ?, command_template = ?,
            max_parallel = ?, mode = ?, created_at = ?, local_dir = ?,
            env_init_scripts_json = ?, scheduler_type = ?, resources_json = ?
        WHERE run_id = ?
        """,
        _run_values(record)[1:] + (record.run_id,),
    )
    if cursor.rowcount == 0:
        raise KeyError(f"run not found: {record.run_id}")
    return record


def incomplete_delete_run_ids(connection: sqlite3.Connection) -> set[str]:
    """Return run IDs protected by an unfinished deletion tombstone."""
    rows = connection.execute(
        """SELECT DISTINCT run_id FROM operations
           WHERE kind = 'delete' AND completed_at IS NULL"""
    ).fetchall()
    return {str(row["run_id"]) for row in rows}
