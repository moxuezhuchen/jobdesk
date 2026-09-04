"""SQLite schema management and migrations for run_repository."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ._paths import _lexical_absolute

SCHEMA_VERSION = 8

# Re-export so the package root can expose the constant.
__all__ = ["SCHEMA_VERSION"]


def _create_tables(connection: sqlite3.Connection) -> None:
    """Create all base tables and indexes (idempotent)."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """)
    connection.execute("""
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
        """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            status TEXT NOT NULL,
            position INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (run_id, task_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """)
    connection.execute("""
        CREATE INDEX IF NOT EXISTS tasks_run_status_idx
            ON tasks(run_id, status)
        """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS migration_errors (
            legacy_path TEXT PRIMARY KEY,
            message TEXT NOT NULL
        )
        """)


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    """Upgrade schema v1 atomically inside the initialization transaction."""
    connection.execute("""
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
        """)
    connection.execute("CREATE INDEX operations_run_id_idx ON operations(run_id)")
    connection.execute("UPDATE schema_metadata SET value = '2' WHERE key = 'schema_version'")


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Add the independent workspace allow-list without trusting journals."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS workspace_roots (
            workspace_root TEXT PRIMARY KEY,
            registered_at TEXT NOT NULL
        )
        """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS delete_operation_workspaces (
            operation_id TEXT PRIMARY KEY,
            workspace_root TEXT NOT NULL,
            FOREIGN KEY (operation_id) REFERENCES operations(operation_id)
                ON DELETE CASCADE,
            FOREIGN KEY (workspace_root) REFERENCES workspace_roots(workspace_root)
        )
        """)
    timestamp = datetime.now().isoformat()
    rows = connection.execute("SELECT DISTINCT local_dir FROM runs WHERE local_dir <> ''").fetchall()
    for row in rows:
        raw_workspace = str(row["local_dir"])
        workspace_path = Path(raw_workspace)
        if not workspace_path.is_absolute():
            continue
        workspace = _lexical_absolute(workspace_path)
        connection.execute(
            "INSERT OR IGNORE INTO workspace_roots(workspace_root, registered_at) VALUES (?, ?)",
            (str(workspace), timestamp),
        )
    connection.execute("UPDATE schema_metadata SET value = '3' WHERE key = 'schema_version'")


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    """Add nullable ownership leases for submit operation recovery."""
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(operations)")}
    if "owner_id" not in columns:
        connection.execute("ALTER TABLE operations ADD COLUMN owner_id TEXT")
    if "lease_expires_at" not in columns:
        connection.execute("ALTER TABLE operations ADD COLUMN lease_expires_at TEXT")
    connection.execute("UPDATE schema_metadata SET value = '4' WHERE key = 'schema_version'")


def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
    """Add submit_activity_log table for persisting SubmitPage activity."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS submit_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            run_id TEXT,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE SET NULL
        )
        """)
    connection.execute("CREATE INDEX IF NOT EXISTS submit_activity_log_ts_idx ON submit_activity_log(ts)")
    connection.execute("UPDATE schema_metadata SET value = '5' WHERE key = 'schema_version'")


def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
    """Persist the producer identity accepted by the ConfFlow handshake."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS run_provenance (
            run_id TEXT PRIMARY KEY,
            capability_json TEXT NOT NULL,
            resolved_executable TEXT NOT NULL,
            resolved_realpath TEXT NOT NULL DEFAULT '',
            producer_version TEXT NOT NULL DEFAULT '',
            producer_build_commit TEXT,
            producer_dirty INTEGER,
            wheel_sha256 TEXT,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """)
    connection.execute("UPDATE schema_metadata SET value = '6' WHERE key = 'schema_version'")


def _migrate_v6_to_v7(connection: sqlite3.Connection) -> None:
    """Add the immutable configuration-contract snapshot for each run."""
    connection.execute("""
        CREATE TABLE IF NOT EXISTS run_configuration_bindings (
            run_id TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL CHECK(
                length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            content_schema TEXT NOT NULL CHECK(length(content_schema) BETWEEN 1 AND 256),
            contract_id TEXT NOT NULL CHECK(length(contract_id) BETWEEN 1 AND 256),
            contract_version TEXT NOT NULL CHECK(length(contract_version) BETWEEN 1 AND 256),
            schema_id TEXT NOT NULL CHECK(length(schema_id) BETWEEN 1 AND 256),
            schema_sha256 TEXT NOT NULL CHECK(
                length(schema_sha256) = 64 AND schema_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            fixture_set TEXT NOT NULL CHECK(length(fixture_set) BETWEEN 1 AND 256),
            fixture_sha256 TEXT NOT NULL CHECK(
                length(fixture_sha256) = 64 AND fixture_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 256),
            configured_executable TEXT NOT NULL CHECK(length(configured_executable) BETWEEN 1 AND 4096),
            resolved_executable TEXT NOT NULL CHECK(length(resolved_executable) BETWEEN 1 AND 4096),
            canonical_executable_identity_json TEXT NOT NULL CHECK(
                length(canonical_executable_identity_json) BETWEEN 2 AND 65536
                AND json_valid(canonical_executable_identity_json)
                AND jobdesk_is_canonical_json_object(canonical_executable_identity_json) = 1
            ),
            canonical_producer_provenance_json TEXT NOT NULL CHECK(
                length(canonical_producer_provenance_json) BETWEEN 2 AND 65536
                AND json_valid(canonical_producer_provenance_json)
                AND jobdesk_is_canonical_json_object(canonical_producer_provenance_json) = 1
            ),
            validated_at TEXT NOT NULL CHECK(length(validated_at) BETWEEN 1 AND 256),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """)
    _ensure_v7_binding_guards(connection)
    connection.execute("UPDATE schema_metadata SET value = '7' WHERE key = 'schema_version'")


def _migrate_v7_to_v8(connection: sqlite3.Connection) -> None:
    """Bind every accepted configuration explicitly to its selected server.

    Schema v7 stored the server only on the parent run row. Rebuild the
    binding table so the contract identity is self-contained and cannot be
    detached from the server at the persistence boundary. Existing v7 rows
    inherit the immutable server identity from their parent run.
    """

    old_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(run_configuration_bindings)")}
    if not old_columns:
        raise RuntimeError("schema v8 requires the configuration binding table")
    for trigger_name in (
        "run_configuration_bindings_authorize_cascade",
        "run_configuration_bindings_clear_cascade_context",
        *_v7_binding_trigger_definitions(),
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    connection.execute("DROP TABLE IF EXISTS run_configuration_bindings_v8")
    connection.execute("""
        CREATE TABLE run_configuration_bindings_v8 (
            run_id TEXT PRIMARY KEY,
            server_id TEXT NOT NULL CHECK(length(server_id) BETWEEN 1 AND 256),
            content_sha256 TEXT NOT NULL CHECK(
                length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            content_schema TEXT NOT NULL CHECK(length(content_schema) BETWEEN 1 AND 256),
            contract_id TEXT NOT NULL CHECK(length(contract_id) BETWEEN 1 AND 256),
            contract_version TEXT NOT NULL CHECK(length(contract_version) BETWEEN 1 AND 256),
            schema_id TEXT NOT NULL CHECK(length(schema_id) BETWEEN 1 AND 256),
            schema_sha256 TEXT NOT NULL CHECK(
                length(schema_sha256) = 64 AND schema_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            fixture_set TEXT NOT NULL CHECK(length(fixture_set) BETWEEN 1 AND 256),
            fixture_sha256 TEXT NOT NULL CHECK(
                length(fixture_sha256) = 64 AND fixture_sha256 NOT GLOB '*[^0-9a-f]*'
            ),
            source TEXT NOT NULL CHECK(length(source) BETWEEN 1 AND 256),
            configured_executable TEXT NOT NULL CHECK(length(configured_executable) BETWEEN 1 AND 4096),
            resolved_executable TEXT NOT NULL CHECK(length(resolved_executable) BETWEEN 1 AND 4096),
            canonical_executable_identity_json TEXT NOT NULL CHECK(
                length(canonical_executable_identity_json) BETWEEN 2 AND 65536
                AND json_valid(canonical_executable_identity_json)
                AND jobdesk_is_canonical_json_object(canonical_executable_identity_json) = 1
            ),
            canonical_producer_provenance_json TEXT NOT NULL CHECK(
                length(canonical_producer_provenance_json) BETWEEN 2 AND 65536
                AND json_valid(canonical_producer_provenance_json)
                AND jobdesk_is_canonical_json_object(canonical_producer_provenance_json) = 1
            ),
            validated_at TEXT NOT NULL CHECK(length(validated_at) BETWEEN 1 AND 256),
            FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
        )
        """)
    server_expression = "b.server_id" if "server_id" in old_columns else "r.server_id"
    if "server_id" in old_columns:
        mismatch = connection.execute("""
            SELECT 1
            FROM run_configuration_bindings AS b
            JOIN runs AS r ON r.run_id = b.run_id
            WHERE b.server_id <> r.server_id
            LIMIT 1
            """).fetchone()
        if mismatch is not None:
            raise RuntimeError("configuration binding server identity disagrees with its run")
    connection.execute(f"""
        INSERT INTO run_configuration_bindings_v8(
            run_id, server_id, content_sha256, content_schema, contract_id, contract_version,
            schema_id, schema_sha256, fixture_set, fixture_sha256, source,
            configured_executable, resolved_executable,
            canonical_executable_identity_json, canonical_producer_provenance_json,
            validated_at
        )
        SELECT b.run_id, {server_expression}, b.content_sha256, b.content_schema,
               b.contract_id, b.contract_version, b.schema_id, b.schema_sha256,
               b.fixture_set, b.fixture_sha256, b.source, b.configured_executable,
               b.resolved_executable, b.canonical_executable_identity_json,
               b.canonical_producer_provenance_json, b.validated_at
        FROM run_configuration_bindings AS b
        JOIN runs AS r ON r.run_id = b.run_id
        """)
    old_count = int(connection.execute("SELECT COUNT(*) FROM run_configuration_bindings").fetchone()[0])
    new_count = int(connection.execute("SELECT COUNT(*) FROM run_configuration_bindings_v8").fetchone()[0])
    if old_count != new_count:
        raise RuntimeError("configuration binding migration lost rows")
    connection.execute("DROP TABLE run_configuration_bindings")
    connection.execute("ALTER TABLE run_configuration_bindings_v8 RENAME TO run_configuration_bindings")
    connection.execute("UPDATE schema_metadata SET value = '8' WHERE key = 'schema_version'")


def _v7_binding_trigger_definitions() -> dict[str, str]:
    """Return the canonical v7 binding trigger definitions.

    The trigger names are part of the on-disk schema, but the names alone are
    not a sufficient integrity check: an old or tampered trigger can retain a
    valid name while changing the mutation it authorizes.  Keep the exact
    definitions in one place so initialization can both create and verify
    them.
    """
    return {
        "run_configuration_bindings_immutable_insert": """
            CREATE TRIGGER run_configuration_bindings_immutable_insert
            BEFORE INSERT ON run_configuration_bindings
            WHEN EXISTS (
                SELECT 1 FROM run_configuration_bindings
                WHERE run_configuration_bindings.run_id = NEW.run_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'configuration binding is immutable');
            END
        """,
        "run_configuration_bindings_immutable_update": """
            CREATE TRIGGER run_configuration_bindings_immutable_update
            BEFORE UPDATE ON run_configuration_bindings
            BEGIN
                SELECT RAISE(ABORT, 'configuration binding is immutable');
            END
        """,
        "run_configuration_bindings_immutable_delete": """
            CREATE TRIGGER run_configuration_bindings_immutable_delete
            BEFORE DELETE ON run_configuration_bindings
            WHEN EXISTS (
                SELECT 1 FROM runs
                WHERE runs.run_id = OLD.run_id
            )
            BEGIN
                SELECT RAISE(ABORT, 'configuration binding is immutable');
            END
        """,
    }


def _ensure_v7_binding_guards(connection: sqlite3.Connection) -> None:
    """Install the canonical v7 immutability guards atomically.

    A binding is a child of ``runs`` with ``ON DELETE CASCADE``.  SQLite runs
    the child DELETE trigger after the parent row is gone for that cascade, so
    a direct child DELETE (where the parent still exists) is rejected while a
    real parent DELETE is allowed.  The first v7 implementation used a
    writable context table and journal rows for this decision; those values
    are not trusted anymore.  The context table remains only as inert schema
    for databases that already have it.

    This function is called inside the initialization transaction.  Dropping
    and recreating all managed triggers there means a same-name stale trigger
    can never leave a database writable after initialization commits.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS run_configuration_binding_delete_context (
            run_id TEXT PRIMARY KEY
        )
        """)
    # These are legacy v7 names.  They wrote/read the context table and must
    # not remain active after the new parent-existence guard is installed.
    for trigger_name in (
        "run_configuration_bindings_authorize_cascade",
        "run_configuration_bindings_clear_cascade_context",
        *_v7_binding_trigger_definitions(),
    ):
        connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

    for trigger_sql in _v7_binding_trigger_definitions().values():
        connection.execute(trigger_sql)


def validate_future_schema(connection: sqlite3.Connection) -> None:
    """Reject databases with a schema version newer than the supported one."""
    metadata_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_metadata'"
    ).fetchone()
    if not metadata_exists:
        return
    row = connection.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'").fetchone()
    if row is not None and int(row[0]) > SCHEMA_VERSION:
        raise RuntimeError(f"database uses newer schema version {row[0]} (supported={SCHEMA_VERSION})")
