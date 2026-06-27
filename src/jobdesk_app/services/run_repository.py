"""Transactional SQLite persistence for JobDesk runs and tasks."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..core.manifest import TaskRecord

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


class RunRepository:
    """Own all writable local run state in one SQLite database."""

    def __init__(self, runs_dir: str | Path) -> None:
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.runs_dir / "jobdesk.db"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
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
                """
            )
            connection.execute(
                """
                INSERT INTO schema_metadata(key, value) VALUES('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(SCHEMA_VERSION),),
            )

    def create_run(self, record: RunRecord, tasks: list[TaskRecord]) -> RunRecord:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_run(connection, record)
            self._replace_tasks(connection, record.run_id, tasks)
        return self.load_run(record.run_id)

    def load_run(self, run_id: str) -> RunRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"run not found: {run_id}")
            return self._row_to_record(connection, row)

    def list_runs(self) -> list[RunRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC, run_id DESC"
            ).fetchall()
            return [self._row_to_record(connection, row) for row in rows]

    def load_tasks(self, run_id: str) -> list[TaskRecord]:
        with self._connect() as connection:
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            return self._load_tasks(connection, run_id)

    def replace_tasks(self, run_id: str, tasks: list[TaskRecord]) -> list[TaskRecord]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            self._replace_tasks(connection, run_id, tasks)
        return self.load_tasks(run_id)

    def mutate_tasks(
        self,
        run_id: str,
        mutation: Callable[[list[TaskRecord]], list[TaskRecord]],
    ) -> list[TaskRecord]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if not self._run_exists(connection, run_id):
                raise KeyError(f"run not found: {run_id}")
            tasks = self._load_tasks(connection, run_id)
            updated = mutation(tasks)
            self._replace_tasks(connection, run_id, updated)
        return updated

    def update_run(self, record: RunRecord) -> RunRecord:
        with self._connect() as connection:
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"run not found: {run_id}")

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
