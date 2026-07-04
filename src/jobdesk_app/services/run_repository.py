"""Transactional SQLite persistence for JobDesk runs and tasks."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from uuid import uuid4

from ..core.lifecycle import TaskStatus
from ..core.manifest import Manifest, TaskRecord

SCHEMA_VERSION = 4


def _utc_lease_timestamp(value: datetime) -> str:
    """Serialize a lease instant in one lexically stable UTC representation."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_lease_timestamp(value: str) -> datetime:
    """Parse an explicitly zoned ISO lease timestamp as a UTC instant."""
    parsed = datetime.fromisoformat(
        value[:-1] + "+00:00" if value.endswith("Z") else value
    )
    if parsed.tzinfo is None:
        raise ValueError("submit lease timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _lexical_absolute(path: Path) -> Path:
    """Make a path absolute without following links or reparse points."""
    return Path(os.path.abspath(path))


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(details, "st_file_attributes", 0))
    is_junction = getattr(path, "is_junction", None)
    return bool(
        stat.S_ISLNK(details.st_mode)
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or (is_junction is not None and is_junction())
    )


def _reject_reparse_chain(root: Path, path: Path) -> None:
    root = _lexical_absolute(root)
    path = _lexical_absolute(path)
    if not path.is_relative_to(root):
        raise ValueError(f"unsafe path outside managed root: {path}")
    current = root
    for part in path.relative_to(root).parts:
        if _is_reparse_point(current):
            raise ValueError(f"unsafe link or reparse point: {current}")
        current = current / part
    if _is_reparse_point(current):
        raise ValueError(f"unsafe link or reparse point: {current}")


@dataclass
class RunRecord:
    run_id: str
    server_id: str
    remote_dir: str
    command_template: str
    max_parallel: int
    mode: str
    created_at: str
    run_dir: Path
    manifest_path: Path
    batch_path: Path
    local_dir: str = ""
    status_summary: dict[str, int] = field(default_factory=dict)
    env_init_scripts: list[str] = field(default_factory=list)
    scheduler_type: str = "nohup"
    resources: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    run_id: str
    kind: str
    phase: str
    payload: dict[str, object]
    last_error: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    owner_id: str | None = None
    lease_expires_at: str | None = None


@dataclass(frozen=True)
class MergeResult:
    tasks: list[TaskRecord]
    accepted_task_ids: set[str]


@dataclass(frozen=True)
class MigrationError:
    legacy_path: Path
    message: str


class RunRepository:
    """Own all writable local run state in one SQLite database."""

    def __init__(self, runs_dir: str | Path) -> None:
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.runs_dir / "jobdesk.db"
        self._validate_existing_schema()
        if not self._is_ready():
            self._initialize()

    def _validate_existing_schema(self) -> None:
        """Reject future schemas before applying connection pragmas or DDL."""
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            metadata_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_metadata'"
            ).fetchone()
            if not metadata_exists:
                return
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is not None and int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database uses newer schema version {row[0]} "
                    f"(supported={SCHEMA_VERSION})"
                )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _is_ready(self) -> bool:
        """Return whether normal reads can skip schema and migration writes."""
        if not self.database_path.exists():
            return False
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            required = {
                "schema_metadata",
                "runs",
                "tasks",
                "migration_errors",
                "operations",
                "workspace_roots",
                "delete_operation_workspaces",
            }
            if not required.issubset(tables):
                return False
            metadata = dict(connection.execute("SELECT key, value FROM schema_metadata"))
            if (
                metadata.get("schema_version") != str(SCHEMA_VERSION)
                or metadata.get("legacy_import_complete") != "1"
            ):
                return False
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
            return journal_mode is not None and str(journal_mode[0]).lower() == "wal"
        except (sqlite3.Error, ValueError):
            return False
        finally:
            connection.close()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            metadata_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_metadata'"
            ).fetchone()
            if metadata_exists:
                version_row = connection.execute(
                    "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
                ).fetchone()
                if version_row is not None and int(version_row["value"]) > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"database uses newer schema version {version_row['value']} "
                        f"(supported={SCHEMA_VERSION})"
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    remote_dir TEXT NOT NULL,
                    command_template TEXT NOT NULL,
                    max_parallel INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    local_dir TEXT NOT NULL DEFAULT '',
                    env_init_scripts_json TEXT NOT NULL DEFAULT '[]',
                    scheduler_type TEXT NOT NULL DEFAULT 'nohup',
                    resources_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, task_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS tasks_run_status_idx
                    ON tasks(run_id, status)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_errors (
                    legacy_path TEXT PRIMARY KEY,
                    message TEXT NOT NULL
                )
                """
            )
            version_row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if version_row is None:
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES('schema_version', '1')"
                )
                current_version = 1
            else:
                current_version = int(version_row["value"])
            if current_version == 1:
                self._migrate_v1_to_v2(connection)
                current_version = 2
            if current_version == 2:
                self._migrate_v2_to_v3(connection)
                current_version = 3
            if current_version == 3:
                self._migrate_v3_to_v4(connection)
            self._import_legacy_runs(connection)

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        """Upgrade schema v1 atomically inside the initialization transaction."""
        connection.execute(
            """
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                phase TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
            """
        )
        connection.execute(
            "CREATE INDEX operations_run_id_idx ON operations(run_id)"
        )
        connection.execute(
            "UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'"
        )

    @staticmethod
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        """Add the independent workspace allow-list without trusting journals."""
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_roots (
                workspace_root TEXT PRIMARY KEY,
                registered_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS delete_operation_workspaces (
                operation_id TEXT PRIMARY KEY,
                workspace_root TEXT NOT NULL,
                FOREIGN KEY (operation_id) REFERENCES operations(operation_id)
                    ON DELETE CASCADE,
                FOREIGN KEY (workspace_root) REFERENCES workspace_roots(workspace_root)
            )
            """
        )
        timestamp = datetime.now().isoformat()
        rows = connection.execute(
            "SELECT DISTINCT local_dir FROM runs WHERE local_dir <> ''"
        ).fetchall()
        for row in rows:
            raw_workspace = str(row["local_dir"])
            workspace_path = Path(raw_workspace)
            if not workspace_path.is_absolute():
                continue
            workspace = _lexical_absolute(workspace_path)
            connection.execute(
                "INSERT OR IGNORE INTO workspace_roots(workspace_root, registered_at) "
                "VALUES (?, ?)",
                (str(workspace), timestamp),
            )
        connection.execute(
            "UPDATE schema_metadata SET value = '3' WHERE key = 'schema_version'"
        )

    @staticmethod
    def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
        """Add nullable ownership leases for submit operation recovery."""
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(operations)")
        }
        if "owner_id" not in columns:
            connection.execute("ALTER TABLE operations ADD COLUMN owner_id TEXT")
        if "lease_expires_at" not in columns:
            connection.execute(
                "ALTER TABLE operations ADD COLUMN lease_expires_at TEXT"
            )
        connection.execute(
            "UPDATE schema_metadata SET value = '4' WHERE key = 'schema_version'"
        )

    def schema_version(self) -> int:
        """Return the persisted schema version without modifying repository state."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
        if row is None:
            raise RuntimeError("database has no schema version")
        return int(row["value"])

    def current_schema_version(self) -> int:
        """Compatibility alias for :meth:`schema_version`."""
        return self.schema_version()

    def list_workspace_roots(self) -> list[Path]:
        """Return lexical workspace roots authorized by durable run metadata."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT workspace_root FROM workspace_roots ORDER BY workspace_root"
            ).fetchall()
        return [Path(str(row["workspace_root"])) for row in rows]

    def delete_operation_workspace(self, operation_id: str) -> Path | None:
        """Return the independently recorded workspace for a delete operation."""
        with self._connection() as connection:
            row = connection.execute(
                "SELECT workspace_root FROM delete_operation_workspaces "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return Path(str(row["workspace_root"]))

    def create_operation(
        self,
        run_id: str,
        kind: str,
        phase: str,
        payload: dict[str, object],
    ) -> OperationRecord:
        operation_id = str(uuid4())
        timestamp = datetime.now().isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False)
        stored_payload = dict(json.loads(payload_json))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO operations(
                    operation_id, run_id, kind, phase, payload_json, last_error,
                    created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL)
                """,
                (
                    operation_id,
                    run_id,
                    kind,
                    phase,
                    payload_json,
                    timestamp,
                    timestamp,
                ),
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
        self,
        operation_id: str,
        expected_phase: str,
        phase: str,
        payload: dict[str, object] | None = None,
        last_error: str | None = None,
        complete: bool = False,
    ) -> bool:
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
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

    def list_operations(self, *, incomplete_only: bool = False) -> list[OperationRecord]:
        where = "WHERE completed_at IS NULL" if incomplete_only else ""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM operations {where} ORDER BY created_at, operation_id"
            ).fetchall()
        return [self._row_to_operation(row) for row in rows]

    def prune_completed_operations(self, older_than: datetime) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM operations WHERE completed_at IS NOT NULL AND completed_at < ?",
                (older_than.isoformat(),),
            )
        return cursor.rowcount

    def recover_legacy_orphan_submit_tasks(self) -> int:
        """Quarantine legacy ``submitting`` tasks that have no replay journal.

        This is deliberately an explicit recovery operation, not schema
        initialization: opening a repository must never rewrite task state.
        The task transition and its synthetic, completed journal decision are
        committed together under SQLite's write lock.
        """
        timestamp = datetime.now().isoformat()
        reason = "submit state had no matching incomplete operation journal"
        recovered = 0
        with self._connection() as connection:
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
                    protected.update(
                        (str(row["run_id"]), str(task_id)) for task_id in task_ids
                    )

            run_rows = connection.execute(
                "SELECT DISTINCT run_id FROM tasks WHERE status = ? ORDER BY run_id",
                (TaskStatus.submitting.value,),
            ).fetchall()
            for row in run_rows:
                run_id = str(row["run_id"])
                tasks = self._load_tasks(connection, run_id)
                changed = False
                updated: list[TaskRecord] = []
                for task in tasks:
                    if (
                        task.status != TaskStatus.submitting
                        or (run_id, task.task_id) in protected
                    ):
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
                            str(uuid4()),
                            run_id,
                            payload_json,
                            reason,
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    )
                if changed:
                    self._replace_tasks(connection, run_id, updated)
        return recovered

    @staticmethod
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
            completed_at=(
                None if row["completed_at"] is None else str(row["completed_at"])
            ),
            owner_id=None if row["owner_id"] is None else str(row["owner_id"]),
            lease_expires_at=(
                None
                if row["lease_expires_at"] is None
                else str(row["lease_expires_at"])
            ),
        )

    def create_run(self, record: RunRecord, tasks: list[TaskRecord]) -> RunRecord:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            tombstone = connection.execute(
                """SELECT 1 FROM operations
                   WHERE run_id = ? AND kind = 'delete' AND completed_at IS NULL
                   LIMIT 1""",
                (record.run_id,),
            ).fetchone()
            if tombstone is not None:
                raise ValueError(
                    f"run_id {record.run_id!r} cannot be reused while delete is incomplete"
                )
            self._insert_run(connection, record)
            self._replace_tasks(connection, record.run_id, tasks)
        return self.load_run(record.run_id)

    def incomplete_delete_run_ids(self) -> set[str]:
        """Return run IDs protected by an unfinished deletion tombstone."""
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT DISTINCT run_id FROM operations
                   WHERE kind = 'delete' AND completed_at IS NULL"""
            ).fetchall()
        return {str(row["run_id"]) for row in rows}

    def prepare_delete_run(
        self,
        run_id: str,
        *,
        run_dir: Path,
        results_root: Path,
        results_dir: Path,
    ) -> OperationRecord:
        """Persist everything needed to replay a run deletion."""
        runs_root = _lexical_absolute(self.runs_dir)
        expected_run_dir = _lexical_absolute(self.runs_dir / run_id)
        if (
            _lexical_absolute(run_dir) != expected_run_dir
            or not expected_run_dir.is_relative_to(runs_root)
        ):
            raise ValueError(f"unsafe run directory for deletion: {run_dir}")
        _reject_reparse_chain(runs_root, expected_run_dir)
        resolved_results_root = _lexical_absolute(results_root)
        if not Path(results_root).is_absolute() or resolved_results_root.name != "results":
            raise ValueError(f"unsafe results root for deletion: {results_root}")
        trusted_workspace = _lexical_absolute(resolved_results_root.parent)
        if resolved_results_root != trusted_workspace / "results":
            raise ValueError(f"unsafe results root for deletion: {results_root}")
        expected_results_dir = _lexical_absolute(resolved_results_root / run_id)
        if _lexical_absolute(results_dir) != expected_results_dir:
            raise ValueError(f"unsafe results directory for deletion: {results_dir}")
        _reject_reparse_chain(resolved_results_root, expected_results_dir)
        operation_id = str(uuid4())
        timestamp = datetime.now().isoformat()
        run_trash_base = self.runs_dir / ".jobdesk-trash"
        results_trash_base = resolved_results_root / ".jobdesk-trash"
        if run_trash_base.is_symlink() or results_trash_base.is_symlink():
            raise ValueError("unsafe delete trash root")
        run_trash_root = _lexical_absolute(run_trash_base / operation_id)
        results_trash_root = _lexical_absolute(results_trash_base / operation_id)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"run not found: {run_id}")
            record = self._row_to_record(connection, row)
            if not record.local_dir:
                raise ValueError(
                    f"run {run_id!r} has no absolute local_dir workspace anchor"
                )
            recorded_workspace_path = Path(record.local_dir)
            if not recorded_workspace_path.is_absolute():
                raise ValueError(
                    f"run {run_id!r} has no absolute local_dir workspace anchor"
                )
            recorded_workspace = _lexical_absolute(recorded_workspace_path)
            if recorded_workspace != trusted_workspace:
                raise ValueError(
                    f"run local_dir does not match deletion workspace: "
                    f"{recorded_workspace} != {trusted_workspace}"
                )
            tasks = self._load_tasks(connection, run_id)
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
            payload: dict[str, object] = {
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
            connection.execute(
                "INSERT OR IGNORE INTO workspace_roots(workspace_root, registered_at) "
                "VALUES (?, ?)",
                (str(trusted_workspace), timestamp),
            )
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

    def delete_run_metadata(self, operation_id: str) -> bool:
        """Delete metadata and journal that fact in one SQLite transaction."""
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
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

    def execute_delete_isolation(
        self,
        operation_id: str,
        callback: Callable[[OperationRecord], None],
    ) -> bool:
        """Atomically select one winner to rename managed paths into trash.

        Trash parents must already exist.  Inside the transaction the callback
        is restricted to bounded local metadata checks and the two same-volume
        atomic renames; recursive, remote, and other unbounded I/O is forbidden.
        """
        timestamp = datetime.now().isoformat()
        try:
            self.ensure_delete_trash_paths(operation_id)
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT * FROM operations
                       WHERE operation_id = ? AND kind = 'delete'
                         AND phase = 'metadata_deleted' AND completed_at IS NULL""",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    return False
                operation = self._row_to_operation(row)
                self._validate_delete_operation_paths(operation)
                callback(operation)
                cursor = connection.execute(
                    """UPDATE operations
                       SET phase = 'files_isolated', last_error = NULL, updated_at = ?
                       WHERE operation_id = ? AND kind = 'delete'
                         AND phase = 'metadata_deleted' AND completed_at IS NULL""",
                    (timestamp, operation_id),
                )
            return cursor.rowcount == 1
        except Exception as exc:
            # The file callback can have completed only part of its idempotent
            # work.  Its transaction rolls back; record the error separately
            # while leaving metadata_deleted retryable.
            self.advance_operation(
                operation_id,
                "metadata_deleted",
                "metadata_deleted",
                last_error=str(exc),
            )
            raise

    def ensure_delete_trash_paths(self, operation_id: str) -> OperationRecord:
        """Backfill deterministic trash paths for journals written before this phase."""
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ? AND kind = 'delete'",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"delete operation not found: {operation_id}")
            operation = self._row_to_operation(row)
            payload = dict(operation.payload)
            has_run = bool(payload.get("trash_run_dir"))
            has_results = bool(payload.get("trash_results_dir"))
            if has_run != has_results:
                raise ValueError("incomplete delete trash paths")
            if not has_run:
                results_root = _lexical_absolute(
                    Path(str(payload.get("results_root", "")))
                )
                run_trash = _lexical_absolute(
                    self.runs_dir / ".jobdesk-trash" / operation_id / "run"
                )
                results_trash = _lexical_absolute(
                    results_root / ".jobdesk-trash" / operation_id / "results"
                )
                payload["trash_run_dir"] = str(run_trash)
                payload["trash_results_dir"] = str(results_trash)
                connection.execute(
                    """UPDATE operations SET payload_json = ?, updated_at = ?
                       WHERE operation_id = ? AND kind = 'delete'""",
                    (json.dumps(payload, ensure_ascii=False), timestamp, operation_id),
                )
                operation = OperationRecord(
                    operation.operation_id,
                    operation.run_id,
                    operation.kind,
                    operation.phase,
                    payload,
                    operation.last_error,
                    operation.created_at,
                    timestamp,
                    operation.completed_at,
                )
        self._validate_delete_operation_paths(operation)
        return operation

    def _validate_delete_operation_paths(self, operation: OperationRecord) -> None:
        runs_root = _lexical_absolute(self.runs_dir)
        run_dir = _lexical_absolute(Path(str(operation.payload.get("run_dir", ""))))
        expected_run_dir = _lexical_absolute(self.runs_dir / operation.run_id)
        if run_dir != expected_run_dir:
            raise ValueError(f"unsafe delete run path: {run_dir}")
        _reject_reparse_chain(runs_root, run_dir)
        results_root = _lexical_absolute(
            Path(str(operation.payload.get("results_root", "")))
        )
        results_dir = _lexical_absolute(
            Path(str(operation.payload.get("results_dir", "")))
        )
        expected_results_dir = _lexical_absolute(results_root / operation.run_id)
        if results_dir != expected_results_dir:
            raise ValueError(f"unsafe delete results path: {results_dir}")
        _reject_reparse_chain(results_root, results_dir)
        run_trash_root = (
            self.runs_dir / ".jobdesk-trash" / operation.operation_id
        )
        results_trash_root = (
            results_root / ".jobdesk-trash" / operation.operation_id
        )
        run_trash_root = _lexical_absolute(run_trash_root)
        results_trash_root = _lexical_absolute(results_trash_root)
        trash_run_dir = _lexical_absolute(
            Path(str(operation.payload.get("trash_run_dir", "")))
        )
        trash_results_dir = _lexical_absolute(
            Path(str(operation.payload.get("trash_results_dir", "")))
        )
        if (
            trash_run_dir != run_trash_root / "run"
            or trash_results_dir != results_trash_root / "results"
            or not trash_run_dir.is_relative_to(runs_root)
            or not trash_results_dir.is_relative_to(results_root)
        ):
            raise ValueError("unsafe delete trash path")
        _reject_reparse_chain(runs_root, trash_run_dir)
        _reject_reparse_chain(results_root, trash_results_dir)

    def load_run(self, run_id: str) -> RunRecord:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"run not found: {run_id}")
            return self._row_to_record(connection, row)

    def list_runs(self) -> list[RunRecord]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, run_id DESC"
            ).fetchall()
            return [self._row_to_record(connection, row) for row in rows]

    def load_tasks(self, run_id: str) -> list[TaskRecord]:
        with self._connection() as connection:
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            return self._load_tasks(connection, run_id)

    def claim_submit_tasks(
        self,
        run_id: str,
        *,
        scheduler_type: str,
        resources: dict[str, object],
        env_init_scripts: list[str],
        per_task: bool,
        owner_id: str | None = None,
        lease_seconds: float = 60.0,
    ) -> tuple[list[TaskRecord], list[OperationRecord]]:
        """Claim uploaded tasks and create their submit journal entries atomically."""
        timestamp = datetime.now().isoformat()
        lease_expires_at = (
            _utc_lease_timestamp(
                datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
            )
            if owner_id is not None
            else None
        )
        operations: list[OperationRecord] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            current = self._load_tasks(connection, run_id)
            claimed = [
                task.model_copy(deep=True)
                for task in current
                if task.status == TaskStatus.uploaded
            ]
            if not claimed:
                return [], []
            claimed_ids = {task.task_id for task in claimed}
            claimed_at = datetime.now()
            self._replace_tasks(
                connection,
                run_id,
                [
                    task.model_copy(
                        update={"status": TaskStatus.submitting, "submitted_at": claimed_at},
                        deep=True,
                    )
                    if task.task_id in claimed_ids
                    else task
                    for task in current
                ],
            )
            groups = [[task.task_id] for task in claimed] if per_task else [
                [task.task_id for task in claimed]
            ]
            for task_ids in groups:
                operation_id = str(uuid4())
                payload: dict[str, object] = {
                    "task_ids": task_ids,
                    "scheduler_type": scheduler_type,
                    "resources": resources,
                    "env_init_scripts": env_init_scripts,
                    "results": {},
                }
                payload_json = json.dumps(payload, ensure_ascii=False)
                connection.execute(
                    """
                    INSERT INTO operations(
                        operation_id, run_id, kind, phase, payload_json, last_error,
                        created_at, updated_at, completed_at, owner_id, lease_expires_at
                    ) VALUES (?, ?, 'submit', 'claimed', ?, NULL, ?, ?, NULL, ?, ?)
                    """,
                    (
                        operation_id, run_id, payload_json, timestamp, timestamp,
                        owner_id, lease_expires_at,
                    ),
                )
                operations.append(OperationRecord(
                    operation_id, run_id, "submit", "claimed",
                    dict(json.loads(payload_json)), None, timestamp, timestamp, None,
                    owner_id, lease_expires_at,
                ))
        return claimed, operations

    def renew_submit_lease(
        self, operation_id: str, owner_id: str, *, lease_seconds: float = 60.0
    ) -> bool:
        timestamp = datetime.now(timezone.utc)
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE operations SET lease_expires_at = ?, updated_at = ? "
                "WHERE operation_id = ? AND kind = 'submit' AND owner_id = ? "
                "AND completed_at IS NULL",
                (
                    _utc_lease_timestamp(
                        timestamp + timedelta(seconds=lease_seconds)
                    ),
                    timestamp.isoformat(), operation_id, owner_id,
                ),
            )
            if cursor.rowcount == 1:
                return True
            completed = connection.execute(
                "SELECT 1 FROM operations WHERE operation_id = ? "
                "AND kind = 'submit' AND owner_id = ? AND completed_at IS NOT NULL",
                (operation_id, owner_id),
            ).fetchone()
        return completed is not None

    def acquire_submit_recovery(
        self, operation_id: str, owner_id: str, *, lease_seconds: float = 60.0
    ) -> bool:
        now = datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_id, lease_expires_at FROM operations "
                "WHERE operation_id = ? AND kind = 'submit' AND completed_at IS NULL",
                (operation_id,),
            ).fetchone()
            if row is None:
                return False
            current_owner = row["owner_id"]
            current_lease = row["lease_expires_at"]
            if current_owner is not None and current_lease is not None:
                try:
                    lease_is_live = (
                        _parse_lease_timestamp(str(current_lease)) > now
                    )
                except (TypeError, ValueError):
                    lease_is_live = False
                if lease_is_live:
                    return False
            cursor = connection.execute(
                """
                UPDATE operations SET owner_id = ?, lease_expires_at = ?, updated_at = ?
                WHERE operation_id = ? AND kind = 'submit' AND completed_at IS NULL
                  AND owner_id IS ? AND lease_expires_at IS ?
                """,
                (
                    owner_id,
                    _utc_lease_timestamp(now + timedelta(seconds=lease_seconds)),
                    now.isoformat(), operation_id, current_owner, current_lease,
                ),
            )
        return cursor.rowcount == 1

    def start_submit_operation(
        self, operation_id: str, *, owner_id: str | None = None
    ) -> bool:
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE operations SET phase = 'remote_started', updated_at = ? "
                "WHERE operation_id = ? AND kind = 'submit' AND phase = 'claimed' "
                "AND completed_at IS NULL AND owner_id IS ?",
                (timestamp, operation_id, owner_id),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _validated_operation_task_ids(
        operation: OperationRecord,
        current: list[TaskRecord],
        expected_status: TaskStatus,
    ) -> set[str] | None:
        payload_task_ids = operation.payload.get("task_ids")
        if not isinstance(payload_task_ids, list) or not payload_task_ids:
            return None
        if not all(
            isinstance(task_id, str) and bool(task_id) for task_id in payload_task_ids
        ):
            return None
        typed_task_ids = cast(list[str], payload_task_ids)
        selected = set(typed_task_ids)
        if len(selected) != len(typed_task_ids):
            return None
        current_by_id = {task.task_id: task for task in current}
        if any(
            task_id not in current_by_id
            or current_by_id[task_id].status != expected_status
            for task_id in selected
        ):
            return None
        return selected

    @staticmethod
    def _record_operation_validation_error(
        connection: sqlite3.Connection,
        operation: OperationRecord,
        timestamp: str,
    ) -> None:
        connection.execute(
            "UPDATE operations SET last_error = ?, updated_at = ? "
            "WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
            (
                f"{operation.phase} operation task set is invalid",
                timestamp,
                operation.operation_id,
                operation.phase,
            ),
        )

    def finish_submit_operation(
        self,
        operation_id: str,
        *,
        task_ids: list[str],
        job_ids: dict[str, str],
        error: str | None = None,
        owner_id: str | None = None,
    ) -> bool:
        """Persist an outcome phase, then complete it in a separate transaction."""
        phase = "uncertain" if error is not None else "confirmed"
        return self.record_submit_outcome(
            operation_id,
            task_ids=task_ids,
            job_ids=job_ids,
            error=error,
            owner_id=owner_id,
        ) and (
            self.complete_submit_operation(operation_id, phase)
            if owner_id is None
            else self.complete_submit_operation(
                operation_id, phase, owner_id=owner_id
            )
        )

    def record_submit_outcome(
        self,
        operation_id: str,
        *,
        task_ids: list[str],
        job_ids: dict[str, str],
        error: str | None = None,
        owner_id: str | None = None,
    ) -> bool:
        """Atomically persist task results and the durable outcome phase."""
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ? AND kind = 'submit' "
                "AND phase = 'remote_started' AND completed_at IS NULL AND owner_id IS ?",
                (operation_id, owner_id),
            ).fetchone()
            if row is None:
                return False
            operation = self._row_to_operation(row)
            scheduler_type = operation.payload.get("scheduler_type")
            if not isinstance(scheduler_type, str) or not scheduler_type.strip():
                connection.execute(
                    "UPDATE operations SET last_error = ?, updated_at = ? "
                    "WHERE operation_id = ? AND phase = 'remote_started' "
                    "AND completed_at IS NULL",
                    (
                        "remote_started submit payload is invalid",
                        timestamp,
                        operation_id,
                    ),
                )
                return False
            if error is not None and (
                not isinstance(error, str) or not error.strip()
            ):
                connection.execute(
                    "UPDATE operations SET last_error = ?, updated_at = ? "
                    "WHERE operation_id = ? AND phase = 'remote_started' "
                    "AND completed_at IS NULL",
                    (
                        "submit outcome error must be a non-empty string",
                        timestamp,
                        operation_id,
                    ),
                )
                return False
            valid_requested_ids = (
                bool(task_ids)
                and all(isinstance(task_id, str) and bool(task_id) for task_id in task_ids)
                and len(set(task_ids)) == len(task_ids)
            )
            current = self._load_tasks(connection, operation.run_id)
            selected = self._validated_operation_task_ids(
                operation, current, TaskStatus.submitting
            )
            if (
                selected is None
                or not valid_requested_ids
                or selected != set(task_ids)
            ):
                return False
            uncertain = error is not None
            if uncertain and job_ids:
                return False
            if not uncertain and (
                set(job_ids) != selected
                or any(
                    not isinstance(job_id, str) or not job_id
                    for job_id in job_ids.values()
                )
            ):
                return False
            updated = []
            for task in current:
                if task.task_id not in selected:
                    updated.append(task)
                    continue
                updated.append(task.model_copy(update={
                    "status": TaskStatus.uncertain if uncertain else TaskStatus.submitted,
                    "scheduler_type": scheduler_type,
                    "remote_job_id": None if uncertain else job_ids.get(task.task_id),
                    "error_message": error,
                }, deep=True))
            self._replace_tasks(connection, operation.run_id, updated)
            phase = "uncertain" if uncertain else "confirmed"
            payload = dict(operation.payload)
            payload["outcome_phase"] = phase
            payload["results"] = {
                task_id: ({"error": error} if uncertain else {"job_id": job_ids.get(task_id)})
                for task_id in task_ids
            }
            cursor = connection.execute(
                """
                UPDATE operations SET phase = ?, payload_json = ?, last_error = ?,
                    updated_at = ?
                WHERE operation_id = ? AND phase = 'remote_started' AND completed_at IS NULL
                    AND owner_id IS ?
                """,
                (
                    phase, json.dumps(payload, ensure_ascii=False), error, timestamp,
                    operation_id, owner_id,
                ),
            )
            return cursor.rowcount == 1

    def complete_submit_operation(
        self, operation_id: str, expected_phase: str, *, owner_id: str | None = None
    ) -> bool:
        if expected_phase not in {"confirmed", "uncertain"}:
            raise ValueError(f"invalid submit outcome phase: {expected_phase}")
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                return False
            operation = self._row_to_operation(row)
            if operation.owner_id != owner_id:
                return False
            if operation.phase not in {expected_phase, "completed"}:
                return False
            if operation.phase == "completed":
                return (
                    operation.completed_at is not None
                    and self._submit_journal_outcome_validation_error(
                        operation, expected_phase
                    )
                    is None
                )
            current = self._load_tasks(connection, operation.run_id)
            if self._submit_outcome_validation_error(
                operation, current, expected_phase
            ) is not None:
                return False
            cursor = connection.execute(
                "UPDATE operations SET phase = 'completed', updated_at = ?, completed_at = ? "
                "WHERE operation_id = ? AND kind = 'submit' AND phase = ? "
                "AND completed_at IS NULL AND owner_id IS ?",
                (timestamp, timestamp, operation_id, expected_phase, owner_id),
            )
            return cursor.rowcount == 1

    def _submit_journal_outcome_validation_error(
        self,
        operation: OperationRecord,
        expected_phase: str,
    ) -> str | None:
        invalid = f"{expected_phase} submit outcome is invalid"
        if operation.kind != "submit" or operation.payload.get("outcome_phase") != expected_phase:
            return invalid
        payload_task_ids = operation.payload.get("task_ids")
        if (
            not isinstance(payload_task_ids, list)
            or not payload_task_ids
            or not all(
                isinstance(task_id, str) and bool(task_id)
                for task_id in payload_task_ids
            )
            or len(set(payload_task_ids)) != len(payload_task_ids)
        ):
            return invalid
        task_ids = set(cast(list[str], payload_task_ids))
        results = operation.payload.get("results")
        if not isinstance(results, dict) or set(results) != task_ids:
            return invalid
        typed_results = cast(dict[str, object], results)
        for task_id in task_ids:
            result = typed_results[task_id]
            if not isinstance(result, dict):
                return invalid
            if expected_phase == "confirmed":
                job_id = result.get("job_id")
                if (
                    set(result) != {"job_id"}
                    or operation.last_error is not None
                    or not isinstance(job_id, str)
                    or not job_id
                ):
                    return invalid
            elif (
                set(result) != {"error"}
                or not isinstance(operation.last_error, str)
                or not operation.last_error
                or result.get("error") != operation.last_error
            ):
                return invalid
        return None

    def _submit_outcome_validation_error(
        self,
        operation: OperationRecord,
        current: list[TaskRecord],
        expected_phase: str,
    ) -> str | None:
        journal_error = self._submit_journal_outcome_validation_error(
            operation, expected_phase
        )
        if journal_error is not None:
            return journal_error
        expected_status = (
            TaskStatus.submitted
            if expected_phase == "confirmed"
            else TaskStatus.uncertain
        )
        task_ids = self._validated_operation_task_ids(
            operation, current, expected_status
        )
        results = operation.payload["results"]
        if task_ids is None:
            return f"{expected_phase} submit outcome is invalid"
        typed_results = cast(dict[str, object], results)
        current_by_id = {task.task_id: task for task in current}
        for task_id in task_ids:
            result = typed_results[task_id]
            assert isinstance(result, dict)
            task = current_by_id[task_id]
            if expected_phase == "confirmed":
                job_id = result.get("job_id")
                if (
                    task.error_message is not None
                    or job_id != task.remote_job_id
                ):
                    return "confirmed submit outcome is invalid"
            elif (
                task.error_message != operation.last_error
                or task.remote_job_id is not None
            ):
                return "uncertain submit outcome is invalid"
        return None

    def release_claimed_submit_operation(
        self, operation_id: str, *, owner_id: str | None = None
    ) -> bool:
        """Roll back one preflight-only submit claim without touching later phases."""
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ? AND kind = 'submit' "
                "AND phase = 'claimed' AND completed_at IS NULL AND owner_id IS ?",
                (operation_id, owner_id),
            ).fetchone()
            if row is None:
                return False
            operation = self._row_to_operation(row)
            current = self._load_tasks(connection, operation.run_id)
            task_ids = self._validated_operation_task_ids(
                operation, current, TaskStatus.submitting
            )
            if task_ids is None:
                self._record_operation_validation_error(connection, operation, timestamp)
                return False
            released = [
                task.model_copy(
                    update={
                        "status": TaskStatus.uploaded,
                        "submitted_at": None,
                        "remote_job_id": None,
                        "error_message": None,
                    },
                    deep=True,
                )
                if task.task_id in task_ids
                else task
                for task in current
            ]
            self._replace_tasks(connection, operation.run_id, released)
            cursor = connection.execute(
                "UPDATE operations SET phase = 'completed', updated_at = ?, completed_at = ? "
                "WHERE operation_id = ? AND phase = 'claimed' AND completed_at IS NULL",
                (timestamp, timestamp, operation_id),
            )
            return cursor.rowcount == 1

    def recover_submit_operation(
        self, operation_id: str, *, owner_id: str | None = None
    ) -> bool:
        """Resolve one interrupted submit operation using its durable phase."""
        timestamp = datetime.now().isoformat()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id = ? AND kind = 'submit' "
                "AND completed_at IS NULL AND owner_id IS ?",
                (operation_id, owner_id),
            ).fetchone()
            if row is None:
                return False
            operation = self._row_to_operation(row)
            current = self._load_tasks(connection, operation.run_id)
            scheduler_type: str | None = None
            if operation.phase == "claimed":
                expected_status = TaskStatus.submitting
                status = TaskStatus.uploaded
                error = None
            elif operation.phase == "remote_started":
                recorded_scheduler = operation.payload.get("scheduler_type")
                if not isinstance(recorded_scheduler, str) or not recorded_scheduler.strip():
                    connection.execute(
                        "UPDATE operations SET last_error = ?, updated_at = ? "
                        "WHERE operation_id = ? AND phase = 'remote_started' "
                        "AND completed_at IS NULL",
                        (
                            "remote_started submit payload is invalid",
                            timestamp,
                            operation_id,
                        ),
                    )
                    return False
                scheduler_type = recorded_scheduler
                expected_status = TaskStatus.submitting
                status = TaskStatus.uncertain
                error = "submission interrupted after remote command started"
            elif operation.phase == "confirmed":
                expected_status = TaskStatus.submitted
                status = None
                error = None
            elif operation.phase == "uncertain":
                expected_status = TaskStatus.uncertain
                status = None
                error = None
            else:
                return False
            task_ids = self._validated_operation_task_ids(
                operation, current, expected_status
            )
            if task_ids is None:
                self._record_operation_validation_error(connection, operation, timestamp)
                return False
            if operation.phase in {"confirmed", "uncertain"}:
                outcome_error = self._submit_outcome_validation_error(
                    operation, current, operation.phase
                )
                if outcome_error is not None:
                    connection.execute(
                        "UPDATE operations SET last_error = ?, updated_at = ? "
                        "WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
                        (outcome_error, timestamp, operation_id, operation.phase),
                    )
                    return False
                return connection.execute(
                    "UPDATE operations SET phase = 'completed', updated_at = ?, completed_at = ? "
                    "WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
                    (timestamp, timestamp, operation_id, operation.phase),
                ).rowcount == 1
            updated = []
            for task in current:
                if task.task_id not in task_ids or task.status != TaskStatus.submitting:
                    updated.append(task)
                    continue
                values: dict[str, object] = {"status": status, "error_message": error}
                if status == TaskStatus.uploaded:
                    values.update({"submitted_at": None, "remote_job_id": None})
                if scheduler_type is not None:
                    values["scheduler_type"] = scheduler_type
                updated.append(task.model_copy(update=values, deep=True))
            self._replace_tasks(connection, operation.run_id, updated)
            return connection.execute(
                "UPDATE operations SET phase = 'completed', last_error = ?, updated_at = ?, "
                "completed_at = ? WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
                (error, timestamp, timestamp, operation_id, operation.phase),
            ).rowcount == 1

    def resolve_uncertain_tasks(
        self,
        run_id: str,
        task_ids: list[str],
        *,
        action: str,
        remote_job_ids: dict[str, str] | None = None,
    ) -> tuple[list[str], list[TaskRecord]]:
        """Resolve selected uncertain tasks with one transactional CAS."""
        if action not in {"confirm", "abandon"}:
            raise ValueError(f"unsupported uncertain task resolution: {action}")
        selected = set(task_ids)
        job_ids = remote_job_ids or {}
        accepted: list[str] = []
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            current = self._load_tasks(connection, run_id)
            resolved: list[TaskRecord] = []
            now = datetime.now()
            for task in current:
                if task.task_id not in selected or task.status != TaskStatus.uncertain:
                    resolved.append(task)
                    continue
                accepted.append(task.task_id)
                if action == "confirm":
                    updates: dict[str, object] = {
                        "status": TaskStatus.submitted,
                        "error_message": None,
                        "submitted_at": task.submitted_at or now,
                    }
                    if task.task_id in job_ids:
                        updates["remote_job_id"] = job_ids[task.task_id]
                else:
                    updates = {
                        "status": TaskStatus.uploaded,
                        "remote_job_id": None,
                        "scheduler_type": "nohup",
                        "error_message": None,
                        "submitted_at": None,
                        "started_at": None,
                        "completed_at": None,
                        "downloaded_at": None,
                        "analyzed_at": None,
                    }
                resolved.append(task.model_copy(update=updates, deep=True))
            if accepted:
                self._replace_tasks(connection, run_id, resolved)
        return accepted, resolved

    def mutate_tasks(
        self,
        run_id: str,
        mutation: Callable[[list[TaskRecord]], list[TaskRecord]],
    ) -> list[TaskRecord]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            tasks = self._load_tasks(connection, run_id)
            updated = mutation(tasks)
            self._replace_tasks(connection, run_id, updated)
        return updated

    def merge_tasks(
        self,
        run_id: str,
        updates: list[TaskRecord],
        *,
        expected_tasks: dict[str, TaskRecord] | None = None,
    ) -> MergeResult:
        """Merge task updates when the complete previously-read row still matches."""
        update_by_id = {task.task_id: task for task in updates}
        accepted_task_ids: set[str] = set()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            current = self._load_tasks(connection, run_id)
            merged: list[TaskRecord] = []
            for task in current:
                update = update_by_id.get(task.task_id)
                expected_matches = expected_tasks is None or (
                    task.task_id in expected_tasks and task == expected_tasks[task.task_id]
                )
                if update is not None and expected_matches:
                    merged.append(update.model_copy(deep=True))
                    accepted_task_ids.add(task.task_id)
                else:
                    merged.append(task)
            self._replace_tasks(connection, run_id, merged)
        return MergeResult(tasks=merged, accepted_task_ids=accepted_task_ids)

    def update_run(self, record: RunRecord) -> RunRecord:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE runs SET
                    server_id = ?, remote_dir = ?, command_template = ?,
                    max_parallel = ?, mode = ?, created_at = ?, local_dir = ?,
                    env_init_scripts_json = ?, scheduler_type = ?, resources_json = ?
                WHERE run_id = ?
                """,
                self._run_values(record)[1:] + (record.run_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"run not found: {record.run_id}")
        return self.load_run(record.run_id)

    def list_migration_errors(self) -> list[MigrationError]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT legacy_path, message FROM migration_errors ORDER BY legacy_path"
            ).fetchall()
        return [
            MigrationError(legacy_path=Path(row["legacy_path"]), message=str(row["message"]))
            for row in rows
        ]

    def retry_legacy_imports(self) -> list[MigrationError]:
        """Explicitly retry failed legacy imports and remove stale error records."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._import_legacy_runs(connection)
        return self.list_migration_errors()

    def _import_legacy_runs(self, connection: sqlite3.Connection) -> None:
        marker = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'legacy_import_complete'"
        ).fetchone()
        failed_paths = {
            str(row["legacy_path"])
            for row in connection.execute("SELECT legacy_path FROM migration_errors").fetchall()
        }
        run_dirs = sorted(path for path in self.runs_dir.iterdir() if path.is_dir())
        legacy_paths = {
            str(run_dir)
            for run_dir in run_dirs
            if (run_dir / "run.json").exists() or (run_dir / "manifest.tsv").exists()
        }
        stale_failed_paths = failed_paths - legacy_paths
        if stale_failed_paths:
            connection.executemany(
                "DELETE FROM migration_errors WHERE legacy_path = ?",
                [(path,) for path in sorted(stale_failed_paths)],
            )
            failed_paths.intersection_update(legacy_paths)
        if marker is not None and marker["value"] == "1" and not failed_paths:
            return
        for run_dir in run_dirs:
            run_path = run_dir / "run.json"
            manifest_path = run_dir / "manifest.tsv"
            if not run_path.exists() and not manifest_path.exists():
                continue
            if marker is not None and marker["value"] == "1" and str(run_dir) not in failed_paths:
                continue
            connection.execute("SAVEPOINT legacy_run")
            try:
                record = self._load_legacy_record(run_dir)
                tasks = self._load_legacy_tasks(manifest_path)
                if not self._run_exists(connection, record.run_id):
                    self._insert_run(connection, record)
                    self._replace_tasks(connection, record.run_id, tasks)
                connection.execute(
                    "DELETE FROM migration_errors WHERE legacy_path = ?",
                    (str(run_dir),),
                )
                connection.execute("RELEASE SAVEPOINT legacy_run")
            except Exception as exc:
                connection.execute("ROLLBACK TO SAVEPOINT legacy_run")
                connection.execute("RELEASE SAVEPOINT legacy_run")
                connection.execute(
                    """
                    INSERT INTO migration_errors(legacy_path, message) VALUES(?, ?)
                    ON CONFLICT(legacy_path) DO UPDATE SET message = excluded.message
                    """,
                    (str(run_dir), f"legacy JSON/TSV import failed: {exc}"),
                )
        connection.execute(
            """
            INSERT INTO schema_metadata(key, value) VALUES('legacy_import_complete', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )

    def _load_legacy_record(self, run_dir: Path) -> RunRecord:
        data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        run_id = str(data["run_id"])
        if run_id != run_dir.name:
            raise ValueError(
                f"legacy run_id {run_id!r} does not match directory {run_dir.name!r}"
            )
        return RunRecord(
            run_id=run_id,
            server_id=str(data["server_id"]),
            remote_dir=str(data["remote_dir"]),
            command_template=str(data["command_template"]),
            max_parallel=int(data["max_parallel"]),
            mode=str(data["mode"]),
            created_at=str(data["created_at"]),
            run_dir=run_dir,
            manifest_path=run_dir / "manifest.tsv",
            batch_path=run_dir / "batch.json",
            local_dir=str(data.get("local_dir", "")),
            env_init_scripts=[str(value) for value in data.get("env_init_scripts", [])],
            scheduler_type=str(data.get("scheduler_type", "nohup") or "nohup"),
            resources=dict(data.get("resources", {})),
        )

    @staticmethod
    def _load_legacy_tasks(manifest_path: Path) -> list[TaskRecord]:
        if not manifest_path.exists():
            raise FileNotFoundError(f"legacy manifest not found: {manifest_path}")
        return Manifest.read(manifest_path)

    def _insert_run(self, connection: sqlite3.Connection, record: RunRecord) -> None:
        connection.execute(
            """
            INSERT INTO runs(
                run_id, server_id, remote_dir, command_template, max_parallel,
                mode, created_at, local_dir, env_init_scripts_json,
                scheduler_type, resources_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._run_values(record),
        )

    @staticmethod
    def _run_values(record: RunRecord) -> tuple[object, ...]:
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

    @staticmethod
    def _run_exists(connection: sqlite3.Connection, run_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone() is not None

    @staticmethod
    def _load_tasks(connection: sqlite3.Connection, run_id: str) -> list[TaskRecord]:
        rows = connection.execute(
            "SELECT payload_json FROM tasks WHERE run_id = ? ORDER BY position",
            (run_id,),
        ).fetchall()
        return [TaskRecord.model_validate(json.loads(row["payload_json"])) for row in rows]

    @staticmethod
    def _replace_tasks(
        connection: sqlite3.Connection,
        run_id: str,
        tasks: list[TaskRecord],
    ) -> None:
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

    def _row_to_record(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> RunRecord:
        run_id = str(row["run_id"])
        run_dir = self.runs_dir / run_id
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
