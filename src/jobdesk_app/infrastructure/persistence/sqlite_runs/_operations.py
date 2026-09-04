"""Operation journal CRUD — pure functions on a live connection."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord

from ._operations_types import OperationRecord
from ._runs import _load_tasks, _replace_tasks


def create_operation(
    connection: sqlite3.Connection,
    run_id: str,
    kind: str,
    phase: str,
    payload: dict,
) -> OperationRecord:
    operation_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    payload_json = json.dumps(payload, ensure_ascii=False)
    stored_payload = dict(json.loads(payload_json))
    connection.execute(
        """
        INSERT INTO operations(
            operation_id, run_id, kind, phase, payload_json, last_error,
            created_at, updated_at, completed_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL)
        """,
        (operation_id, run_id, kind, phase, payload_json, timestamp, timestamp),
    )
    return OperationRecord(
        operation_id=operation_id,
        run_id=run_id,
        kind=kind,
        phase=phase,
        payload=stored_payload,
        last_error=None,
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=None,
    )


def advance_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    expected_phase: str,
    phase: str,
    payload: dict | None = None,
    last_error: str | None = None,
    complete: bool = False,
) -> bool:
    timestamp = datetime.now().isoformat()
    cursor = connection.execute(
        """
        UPDATE operations SET
            phase = ?,
            payload_json = CASE WHEN ? THEN payload_json ELSE ? END,
            last_error = ?,
            updated_at = ?,
            completed_at = CASE WHEN ? THEN ? ELSE completed_at END
        WHERE operation_id = ? AND phase = ? AND completed_at IS NULL
        """,
        (
            phase,
            payload is None,
            None if payload is None else json.dumps(payload, ensure_ascii=False),
            last_error,
            timestamp,
            complete,
            timestamp,
            operation_id,
            expected_phase,
        ),
    )
    return cursor.rowcount == 1


def list_operations(connection: sqlite3.Connection, *, incomplete_only: bool = False) -> list[OperationRecord]:
    where = "WHERE completed_at IS NULL" if incomplete_only else ""
    rows = connection.execute(f"SELECT * FROM operations {where} ORDER BY created_at, operation_id").fetchall()
    return [_row_to_operation(row) for row in rows]


def prune_completed_operations(connection: sqlite3.Connection, older_than: datetime) -> int:
    cursor = connection.execute(
        "DELETE FROM operations WHERE completed_at IS NOT NULL AND completed_at < ?",
        (older_than.isoformat(),),
    )
    return cursor.rowcount


def recover_legacy_orphan_submit_tasks(
    connection: sqlite3.Connection,
) -> int:
    """Quarantine legacy submitting tasks that have no replay journal.

    This is deliberately an explicit recovery operation, not schema
    initialization: opening a repository must never rewrite task state.
    The task transition and its synthetic, completed journal decision are
    committed together under SQLite's write lock.
    """
    timestamp = datetime.now().isoformat()
    reason = "submit state had no matching incomplete operation journal"
    recovered = 0
    connection.execute("BEGIN IMMEDIATE")
    rows = connection.execute(
        """SELECT run_id, payload_json FROM operations
           WHERE kind = 'submit' AND completed_at IS NULL"""
    ).fetchall()
    protected: set[tuple[str, str]] = set()
    for row in rows:
        payload = json.loads(row["payload_json"])
        task_ids = payload.get("task_ids") if isinstance(payload, dict) else None
        if isinstance(task_ids, list):
            protected.update((str(row["run_id"]), str(task_id)) for task_id in task_ids)

    run_rows = connection.execute(
        "SELECT DISTINCT run_id FROM tasks WHERE status = ? ORDER BY run_id",
        (TaskStatus.submitting.value,),
    ).fetchall()
    for row in run_rows:
        run_id = str(row["run_id"])
        tasks = _load_tasks(connection, run_id)
        changed = False
        updated: list[TaskRecord] = []
        for task in tasks:
            if task.status != TaskStatus.submitting or (run_id, task.task_id) in protected:
                updated.append(task)
                continue
            changed = True
            recovered += 1
            updated.append(
                task.model_copy(
                    update={
                        "status": TaskStatus.uncertain,
                        "error_message": reason,
                    },
                    deep=True,
                )
            )
            payload_json = json.dumps(
                {
                    "task_ids": [task.task_id],
                    "recovery_decision": "uncertain",
                    "reason": reason,
                },
                ensure_ascii=False,
            )
            connection.execute(
                """INSERT INTO operations(
                       operation_id, run_id, kind, phase, payload_json,
                       last_error, created_at, updated_at, completed_at
                   ) VALUES (?, ?, 'submit', 'completed', ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    run_id,
                    payload_json,
                    reason,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
        if changed:
            _replace_tasks(connection, run_id, updated)
    return recovered


def _row_to_operation(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        operation_id=str(row["operation_id"]),
        run_id=str(row["run_id"]),
        kind=str(row["kind"]),
        phase=str(row["phase"]),
        payload=dict(json.loads(row["payload_json"])),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=(None if row["completed_at"] is None else str(row["completed_at"])),
        owner_id=None if row["owner_id"] is None else str(row["owner_id"]),
        lease_expires_at=(None if row["lease_expires_at"] is None else str(row["lease_expires_at"])),
    )
