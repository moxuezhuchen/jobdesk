"""Workspace root registry for trusted workspace enforcement."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def list_workspace_roots(connection: sqlite3.Connection) -> list[Path]:
    """Return lexical workspace roots authorized by durable run metadata."""
    rows = connection.execute(
        "SELECT workspace_root FROM workspace_roots ORDER BY workspace_root"
    ).fetchall()
    return [Path(str(row["workspace_root"])) for row in rows]


def delete_operation_workspace(
    connection: sqlite3.Connection, operation_id: str
) -> Path | None:
    """Return the independently recorded workspace for a delete operation."""
    row = connection.execute(
        "SELECT workspace_root FROM delete_operation_workspaces "
        "WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    if row is None:
        return None
    return Path(str(row["workspace_root"]))


def register_workspace(connection: sqlite3.Connection, workspace: Path, timestamp: str) -> None:
    """Register a workspace root if not already present."""
    connection.execute(
        "INSERT OR IGNORE INTO workspace_roots(workspace_root, registered_at) "
        "VALUES (?, ?)",
        (str(workspace), timestamp),
    )
