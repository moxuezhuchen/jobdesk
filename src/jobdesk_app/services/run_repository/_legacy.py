"""Legacy run import from run.json / manifest.tsv."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ._operations_types import MigrationError, RunRecord
from ._runs import _insert_run, _run_exists, _replace_tasks


def retry_legacy_imports(
    connection: sqlite3.Connection,
    runs_dir: Path,
) -> list[MigrationError]:
    """Explicitly retry failed legacy imports and remove stale error records."""
    connection.execute("BEGIN IMMEDIATE")
    _import_legacy_runs(connection, runs_dir)
    return list_migration_errors(connection)


def list_migration_errors(connection: sqlite3.Connection) -> list[MigrationError]:
    rows = connection.execute(
        "SELECT legacy_path, message FROM migration_errors ORDER BY legacy_path"
    ).fetchall()
    return [
        MigrationError(legacy_path=Path(row["legacy_path"]), message=str(row["message"]))
        for row in rows
    ]


def _import_legacy_runs(connection: sqlite3.Connection, runs_dir: Path) -> None:
    marker = connection.execute(
        "SELECT value FROM schema_metadata WHERE key = 'legacy_import_complete'"
    ).fetchone()
    failed_paths = {
        str(row["legacy_path"])
        for row in connection.execute("SELECT legacy_path FROM migration_errors").fetchall()
    }
    run_dirs = sorted(path for path in runs_dir.iterdir() if path.is_dir())
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
            record = _load_legacy_record(run_dir)
            tasks = _load_legacy_tasks(manifest_path)
            if not _run_exists(connection, record.run_id):
                _insert_run(connection, record)
                _replace_tasks(connection, record.run_id, tasks)
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


def _load_legacy_record(run_dir: Path) -> RunRecord:
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


def _load_legacy_tasks(manifest_path: Path) -> list:
    if not manifest_path.exists():
        raise FileNotFoundError(f"legacy manifest not found: {manifest_path}")
    from jobdesk_app.core.manifest import Manifest
    return Manifest.read(manifest_path)
