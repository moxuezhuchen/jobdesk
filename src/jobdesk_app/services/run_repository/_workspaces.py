"""Workspace root registry for trusted workspace enforcement, and cross-platform path comparison."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path comparison (was _path_compare)
# ---------------------------------------------------------------------------


def paths_equal(a: Path, b: Path) -> bool:
    """Return True if two paths are equivalent across platforms.

    On Windows, comparison is case-insensitive and slash-insensitive.
    On POSIX, comparison is strict lexical equality.
    """
    if sys.platform == "win32":
        return os.path.normcase(str(a)) == os.path.normcase(str(b))
    return a == b


# ---------------------------------------------------------------------------
# Workspace registry (was _workspaces)
# ---------------------------------------------------------------------------


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
