"""Transactional SQLite persistence for JobDesk runs and tasks."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.lifecycle import TaskStatus
from ..core.manifest import Manifest, TaskRecord

SCHEMA_VERSION = 1


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
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
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
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, task_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS tasks_run_status_idx
                    ON tasks(run_id, status);
                CREATE TABLE IF NOT EXISTS migration_errors (
                    legacy_path TEXT PRIMARY KEY,
                    message TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT INTO schema_metadata(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )
            self._import_legacy_runs(connection)

    def create_run(self, record: RunRecord, tasks: list[TaskRecord]) -> RunRecord:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_run(connection, record)
            self._replace_tasks(connection, record.run_id, tasks)
        return self.load_run(record.run_id)

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

    def replace_tasks(self, run_id: str, tasks: list[TaskRecord]) -> list[TaskRecord]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            self._replace_tasks(connection, run_id, tasks)
        return self.load_tasks(run_id)

    def claim_uploaded_tasks(self, run_id: str) -> list[TaskRecord]:
        """Atomically claim uploaded tasks before performing remote submission.

        Claimed tasks are persisted as submitting so a second process cannot
        perform the same remote side effect.  Returned copies retain uploaded
        status because JobSubmitter selects that state.
        """
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            tasks = self._load_tasks(connection, run_id)
            claimed = [
                task.model_copy(deep=True)
                for task in tasks
                if task.status == TaskStatus.uploaded
            ]
            if claimed:
                claimed_ids = {task.task_id for task in claimed}
                claimed_at = datetime.now()
                persisted = [
                    task.model_copy(
                        update={"status": TaskStatus.submitting, "submitted_at": claimed_at},
                        deep=True,
                    )
                    if task.task_id in claimed_ids
                    else task
                    for task in tasks
                ]
                self._replace_tasks(connection, run_id, persisted)
        return claimed

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
        expected_statuses: dict[str, TaskStatus] | None = None,
    ) -> list[TaskRecord]:
        """Merge task updates without overwriting unrelated or stale state."""
        update_by_id = {task.task_id: task for task in updates}
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            current = self._load_tasks(connection, run_id)
            merged: list[TaskRecord] = []
            for task in current:
                update = update_by_id.get(task.task_id)
                expected = (expected_statuses or {}).get(task.task_id)
                if update is not None and (expected is None or task.status == expected):
                    merged.append(update.model_copy(deep=True))
                else:
                    merged.append(task)
            self._replace_tasks(connection, run_id, merged)
        return merged

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

    def delete_run(self, run_id: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"run not found: {run_id}")

    def list_migration_errors(self) -> list[MigrationError]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT legacy_path, message FROM migration_errors ORDER BY legacy_path"
            ).fetchall()
        return [
            MigrationError(legacy_path=Path(row["legacy_path"]), message=str(row["message"]))
            for row in rows
        ]

    def _import_legacy_runs(self, connection: sqlite3.Connection) -> None:
        marker = connection.execute(
            "SELECT value FROM schema_metadata WHERE key = 'legacy_import_complete'"
        ).fetchone()
        failed_paths = {
            str(row["legacy_path"])
            for row in connection.execute("SELECT legacy_path FROM migration_errors").fetchall()
        }
        if marker is not None and marker["value"] == "1" and not failed_paths:
            return
        for run_dir in sorted(self.runs_dir.iterdir()):
            if not run_dir.is_dir():
                continue
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
