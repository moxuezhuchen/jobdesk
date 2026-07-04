"""Run record CRUD helpers."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ._operations_types import RunRecord


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
