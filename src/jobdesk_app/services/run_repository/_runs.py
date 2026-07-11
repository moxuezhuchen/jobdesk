"""Run-level CRUD that wraps _runs_helpers."""

from __future__ import annotations

import sqlite3
import time as _time
from pathlib import Path

from ._operations_types import RunRecord
from ._runs_helpers import _insert_run, _row_to_record, _run_exists
from ._tasks_helpers import _load_tasks, _replace_tasks


def create_run(
    connection: sqlite3.Connection,
    record: RunRecord,
    tasks: list,
) -> RunRecord:
    """Create a run and its tasks atomically, erroring if a deletion is in progress.

    A concurrent ``recover_delete_operations`` may briefly leave an
    in-progress tombstone at ``phase = 'files_isolated'`` between the
    isolation commit and the final ``advance_operation(..., complete=True)``
    that sets ``completed_at``. The writer lock is released between those
    two SQL steps, so a freshly-submitted ``create_run`` can acquire the
    lock in that window and (incorrectly) observe the tombstone. To avoid
    that spurious failure we wait for ``_DELETE_CLEANUP_LEADER_GRACE_SECONDS``
    after acquiring the writer lock and seeing an incomplete tombstone,
    releasing the lock between polls so the leader can finish. Stuck
    leaders fail the wait cleanly with the original ``ValueError``.
    """
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
        # We hold the writer lock — leader cannot progress while we do.
        # Drop it so the leader can finish its cleanup, then retry.
        connection.rollback()
        # First time we see a tombstone we anchor the grace window to
        # *this* moment, not to the start of ``create_run`` (which may
        # have been blocked on the writer lock for the entire pause).
        # ``_DELETE_CLEANUP_LEADER_GRACE_SECONDS`` is defined in
        # ``run_repository.__init__`` *after* all the sibling-module
        # imports (intentional, to keep ruff happy), so we reach for it
        # through the package object rather than via a top-level
        # ``from . import`` (which would resolve before the constant
        # exists and raise ``ImportError``).
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


def update_run(connection: sqlite3.Connection, record: RunRecord) -> RunRecord:
    from ._runs_helpers import _run_values
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
