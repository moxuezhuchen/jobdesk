"""SQLite schema management and migrations for run_repository."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ._paths import _lexical_absolute

SCHEMA_VERSION = 4

# Re-export so the package root can expose the constant.
__all__ = ["SCHEMA_VERSION"]


def _create_tables(connection: sqlite3.Connection) -> None:
    """Create all base tables and indexes (idempotent)."""
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


def validate_future_schema(connection: sqlite3.Connection) -> None:
    """Reject databases with a schema version newer than the supported one."""
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
