"""Delete operation journal and execution.

Each public function takes a (connection, runs_dir, ...) signature
and caller must call `with self._connection() as connection:` first.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ._operations_types import OperationRecord
from ._paths import _lexical_absolute, _reject_reparse_chain
from ._runs import _load_tasks
from ._workspaces import paths_equal, register_workspace

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Public delete functions — caller opens the connection transaction
# ---------------------------------------------------------------------------


def prepare_delete_run(
    connection: sqlite3.Connection,
    runs_dir: Path,
    run_id: str,
    *,
    run_dir: Path,
    results_root: Path,
    results_dir: Path,
) -> OperationRecord:
    """Persist everything needed to replay a run deletion.

    Caller must call `with self._connection() as connection:` first.
    """
    connection.execute("BEGIN IMMEDIATE")
    runs_root = _lexical_absolute(runs_dir)
    expected_run_dir = _lexical_absolute(runs_dir / run_id)
    if (
        not paths_equal(_lexical_absolute(run_dir), expected_run_dir)
        or not expected_run_dir.is_relative_to(runs_root)
    ):
        raise ValueError(f"unsafe run directory for deletion: {run_dir}")
    _reject_reparse_chain(runs_root, expected_run_dir)
    resolved_results_root = _lexical_absolute(results_root)
    if not Path(results_root).is_absolute() or resolved_results_root.name != "results":
        raise ValueError(f"unsafe results root for deletion: {results_root}")
    trusted_workspace = _lexical_absolute(resolved_results_root.parent)
    if not paths_equal(resolved_results_root, trusted_workspace / "results"):
        raise ValueError(f"unsafe results root for deletion: {results_root}")
    expected_results_dir = _lexical_absolute(resolved_results_root / run_id)
    if not paths_equal(_lexical_absolute(results_dir), expected_results_dir):
        raise ValueError(f"unsafe results directory for deletion: {results_dir}")
    _reject_reparse_chain(resolved_results_root, expected_results_dir)
    operation_id = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()
    run_trash_base = runs_dir / ".jobdesk-trash"
    results_trash_base = resolved_results_root / ".jobdesk-trash"
    if run_trash_base.is_symlink() or results_trash_base.is_symlink():
        raise ValueError("unsafe delete trash root")
    run_trash_root = _lexical_absolute(run_trash_base / operation_id)
    results_trash_root = _lexical_absolute(results_trash_base / operation_id)
    row = connection.execute(
        "SELECT * FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"run not found: {run_id}")
    from ._runs import _row_to_record
    record = _row_to_record(connection, row, runs_dir)
    if not record.local_dir:
        raise ValueError(f"run {run_id!r} has no absolute local_dir workspace anchor")
    recorded_workspace_path = Path(record.local_dir)
    if not recorded_workspace_path.is_absolute():
        raise ValueError(f"run {run_id!r} has no absolute local_dir workspace anchor")
    recorded_workspace = _lexical_absolute(recorded_workspace_path)
    if not paths_equal(recorded_workspace, trusted_workspace):
        raise ValueError(
            f"run local_dir does not match deletion workspace: "
            f"{recorded_workspace} != {trusted_workspace}"
        )
    tasks = _load_tasks(connection, run_id)
    incomplete_submit = connection.execute(
        """SELECT 1 FROM operations
           WHERE run_id = ? AND kind = 'submit' AND completed_at IS NULL
           LIMIT 1""",
        (run_id,),
    ).fetchone()
    if incomplete_submit is not None:
        raise ValueError(
            f"cannot delete run {run_id!r} with incomplete submit operation"
        )
    from jobdesk_app.core.lifecycle import TaskStatus
    active_statuses = {
        TaskStatus.submitting,
        TaskStatus.uncertain,
        TaskStatus.submitted,
        TaskStatus.running,
    }
    active_tasks = [
        task.task_id for task in tasks if task.status in active_statuses
    ]
    if active_tasks:
        raise ValueError(
            f"cannot delete run {run_id!r} with active remote tasks: "
            + ", ".join(active_tasks)
        )
    payload: dict = {
        "run": {
            "run_id": record.run_id,
            "server_id": record.server_id,
            "remote_dir": record.remote_dir,
            "command_template": record.command_template,
            "max_parallel": record.max_parallel,
            "mode": record.mode,
            "created_at": record.created_at,
            "local_dir": record.local_dir,
            "env_init_scripts": record.env_init_scripts,
            "scheduler_type": record.scheduler_type,
            "resources": record.resources,
        },
        "tasks": [task.model_dump(mode="json") for task in tasks],
        "run_dir": str(expected_run_dir),
        "results_root": str(resolved_results_root),
        "results_dir": str(expected_results_dir),
        "trash_run_dir": str(run_trash_root / "run"),
        "trash_results_dir": str(results_trash_root / "results"),
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    register_workspace(connection, trusted_workspace, timestamp)
    connection.execute(
        """
        INSERT INTO operations(
            operation_id, run_id, kind, phase, payload_json, last_error,
            created_at, updated_at, completed_at
        ) VALUES (?, ?, 'delete', 'prepared', ?, NULL, ?, ?, NULL)
        """,
        (operation_id, run_id, payload_json, timestamp, timestamp),
    )
    connection.execute(
        "INSERT INTO delete_operation_workspaces(operation_id, workspace_root) "
        "VALUES (?, ?)",
        (operation_id, str(trusted_workspace)),
    )
    return OperationRecord(
        operation_id, run_id, "delete", "prepared",
        dict(json.loads(payload_json)), None, timestamp, timestamp, None,
    )


def delete_run_metadata(
    connection: sqlite3.Connection,
    runs_dir: Path,
    operation_id: str,
) -> bool:
    """Delete metadata and journal that fact in one SQLite transaction.

    Caller must call `with self._connection() as connection:` first.
    """
    connection.execute("BEGIN IMMEDIATE")
    timestamp = datetime.now().isoformat()
    operation = connection.execute(
        """SELECT run_id FROM operations
           WHERE operation_id = ? AND kind = 'delete'
             AND phase = 'prepared' AND completed_at IS NULL""",
        (operation_id,),
    ).fetchone()
    if operation is None:
        return False
    connection.execute("DELETE FROM runs WHERE run_id = ?", (operation["run_id"],))
    cursor = connection.execute(
        """UPDATE operations SET phase = 'metadata_deleted', last_error = NULL,
               updated_at = ?
           WHERE operation_id = ? AND phase = 'prepared'
             AND completed_at IS NULL""",
        (timestamp, operation_id),
    )
    return cursor.rowcount == 1


def _record_delete_error(
    originating_connection: sqlite3.Connection,
    operation_id: str,
    error_message: str,
) -> None:
    """Record an error on a delete operation on the same connection.

    Called from within a rolled-back transaction to persist the error.
    """
    originating_connection.rollback()
    timestamp = datetime.now().isoformat()
    originating_connection.execute("BEGIN IMMEDIATE")
    originating_connection.execute(
        """UPDATE operations SET last_error = ?, updated_at = ?
           WHERE operation_id = ? AND phase = ? AND completed_at IS NULL
           AND last_error IS NULL""",
        (error_message, timestamp, operation_id, "metadata_deleted"),
    )


def execute_delete_isolation(
    connection: sqlite3.Connection,
    runs_dir: Path,
    operation_id: str,
    callback: Callable[[OperationRecord], None],
) -> bool:
    """Atomically select one winner to rename managed paths into trash.

    Caller must call `with self._connection() as connection:` first.
    This function opens its own connection for ensure_delete_trash_paths,
    then uses the caller's connection for the isolation transaction.
    """
    ensure_delete_trash_paths(connection, runs_dir, operation_id)
    return _execute_delete_isolation_impl(connection, runs_dir, operation_id, callback)


def _execute_delete_isolation_impl(
    connection: sqlite3.Connection,
    runs_dir: Path,
    operation_id: str,
    callback: Callable[[OperationRecord], None],
) -> bool:
    """Execute the isolation transaction.

    Called with an active connection (caller opened the transaction).
    Does NOT start a new transaction.
    """
    timestamp = datetime.now().isoformat()
    from ._operations import _row_to_operation
    row = connection.execute(
        """SELECT * FROM operations
           WHERE operation_id = ? AND kind = 'delete'
             AND phase = 'metadata_deleted' AND completed_at IS NULL""",
        (operation_id,),
    ).fetchone()
    if row is None:
        return False
    operation = _row_to_operation(row)
    _validate_operation_paths(operation, runs_dir)
    connection.execute("SAVEPOINT isolation_callback")
    try:
        callback(operation)
        connection.execute("RELEASE SAVEPOINT isolation_callback")
    except Exception as exc:
        connection.execute("ROLLBACK TO SAVEPOINT isolation_callback")
        raise exc
    cursor = connection.execute(
        """UPDATE operations
           SET phase = 'files_isolated', last_error = NULL, updated_at = ?
           WHERE operation_id = ? AND kind = 'delete'
             AND phase = 'metadata_deleted' AND completed_at IS NULL""",
        (timestamp, operation_id),
    )
    return cursor.rowcount == 1


def ensure_delete_trash_paths(
    connection: sqlite3.Connection,
    runs_dir: Path,
    operation_id: str,
) -> OperationRecord:
    """Backfill deterministic trash paths for journals written before this phase.

    Caller must call `with self._connection() as connection:` first.
    This function manages its own BEGIN IMMEDIATE transaction.
    """
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    timestamp = datetime.now().isoformat()
    from ._operations import _row_to_operation
    try:
        row = connection.execute(
            "SELECT * FROM operations WHERE operation_id = ? AND kind = 'delete'",
            (operation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"delete operation not found: {operation_id}")
        operation = _row_to_operation(row)
        payload = dict(operation.payload)
        has_run = bool(payload.get("trash_run_dir"))
        has_results = bool(payload.get("trash_results_dir"))
        if has_run != has_results:
            raise ValueError("incomplete delete trash paths")
        if not has_run:
            run_dir_str = str(operation.payload.get("run_dir", ""))
            results_root_str = str(operation.payload.get("results_root", ""))
            if not run_dir_str:
                raise ValueError("operation has no run_dir")
            if not results_root_str:
                raise ValueError("operation has no results_root")
            results_root = Path(results_root_str)
            run_trash = _lexical_absolute(runs_dir / ".jobdesk-trash" / operation_id / "run")
            results_trash = _lexical_absolute(results_root / ".jobdesk-trash" / operation_id / "results")
            payload["trash_run_dir"] = str(run_trash)
            payload["trash_results_dir"] = str(results_trash)
            connection.execute(
                """UPDATE operations SET payload_json = ?, updated_at = ?
                   WHERE operation_id = ? AND kind = 'delete'""",
                (json.dumps(payload, ensure_ascii=False), timestamp, operation_id),
            )
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ? AND kind = 'delete'",
                (operation_id,),
            ).fetchone()
            operation = _row_to_operation(row)
        _validate_operation_paths(operation, runs_dir)
        return operation
    except Exception:
        connection.rollback()
        raise


def _validate_operation_paths(operation: OperationRecord, runs_dir: Path) -> None:
    """Validate paths using the operation's stored run_dir to infer runs_root."""
    payload = operation.payload
    run_dir_str = str(payload.get("run_dir", ""))
    results_root_str = str(payload.get("results_root", ""))
    if not run_dir_str or not results_root_str:
        raise ValueError("operation missing run_dir or results_root")
    run_dir = _lexical_absolute(Path(run_dir_str))
    results_root = _lexical_absolute(Path(results_root_str))
    runs_root = _lexical_absolute(runs_dir)
    expected_run_dir = _lexical_absolute(runs_dir / operation.run_id)
    if not paths_equal(run_dir, expected_run_dir):
        raise ValueError(f"unsafe delete run path: {run_dir}")
    _reject_reparse_chain(runs_root, run_dir)
    results_dir_str = str(payload.get("results_dir", ""))
    if not results_dir_str:
        raise ValueError("operation missing results_dir")
    results_dir = _lexical_absolute(Path(results_dir_str))
    expected_results_dir = results_root / operation.run_id
    if results_dir != expected_results_dir:
        raise ValueError(f"unsafe delete results path: {results_dir}")
    _reject_reparse_chain(results_root, results_dir)
    trash_run_dir_str = str(payload.get("trash_run_dir", ""))
    trash_results_dir_str = str(payload.get("trash_results_dir", ""))
    if not trash_run_dir_str or not trash_results_dir_str:
        raise ValueError("operation missing trash paths")
    trash_run_dir = _lexical_absolute(Path(trash_run_dir_str))
    trash_results_dir = _lexical_absolute(Path(trash_results_dir_str))
    run_trash_root = runs_root / ".jobdesk-trash" / operation.operation_id
    results_trash_root = results_root / ".jobdesk-trash" / operation.operation_id
    if (
        not paths_equal(trash_run_dir, _lexical_absolute(run_trash_root / "run"))
        or not paths_equal(trash_results_dir, _lexical_absolute(results_trash_root / "results"))
        or not trash_run_dir.is_relative_to(runs_root)
        or not trash_results_dir.is_relative_to(results_root)
    ):
        raise ValueError("unsafe delete trash path")
    _reject_reparse_chain(runs_root, trash_run_dir)
    _reject_reparse_chain(results_root, trash_results_dir)


def complete_delete_isolated(
    connection: sqlite3.Connection,
    runs_dir: Path,
    operation_id: str,
) -> bool:
    """Advance from files_isolated to files_deleted, then to completed.

    Caller must call `with self._connection() as connection:` first.
    """
    connection.execute("BEGIN IMMEDIATE")
    timestamp = datetime.now().isoformat()
    cursor = connection.execute(
        """UPDATE operations
           SET phase = 'files_deleted', last_error = NULL, updated_at = ?
           WHERE operation_id = ? AND kind = 'delete'
             AND phase = 'files_isolated' AND completed_at IS NULL""",
        (timestamp, operation_id),
    )
    if cursor.rowcount != 1:
        return False
    cursor2 = connection.execute(
        """UPDATE operations
           SET phase = 'completed', last_error = NULL, updated_at = ?, completed_at = ?
           WHERE operation_id = ? AND kind = 'delete'
             AND phase = 'files_deleted' AND completed_at IS NULL""",
        (timestamp, timestamp, operation_id),
    )
    return cursor2.rowcount == 1
