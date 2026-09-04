"""SQLite serialization for immutable per-run configuration bindings."""

from __future__ import annotations

import sqlite3

from jobdesk_app.core.configuration_binding import ConfigurationBinding


def insert_configuration_binding(connection: sqlite3.Connection, run_id: str, binding: ConfigurationBinding) -> None:
    """Insert one immutable binding for ``run_id`` within its caller's transaction."""
    connection.execute(
        """
        INSERT INTO run_configuration_bindings(
            run_id, server_id, content_sha256, content_schema, contract_id, contract_version,
            schema_id, schema_sha256, fixture_set, fixture_sha256, source,
            configured_executable, resolved_executable,
            canonical_executable_identity_json, canonical_producer_provenance_json,
            validated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            binding.server_id,
            binding.content_sha256,
            binding.content_schema,
            binding.contract_id,
            binding.contract_version,
            binding.schema_id,
            binding.schema_sha256,
            binding.fixture_set,
            binding.fixture_sha256,
            binding.source,
            binding.configured_executable,
            binding.resolved_executable,
            binding.canonical_executable_identity_json,
            binding.canonical_producer_provenance_json,
            binding.validated_at,
        ),
    )


def load_configuration_binding(connection: sqlite3.Connection, run_id: str) -> ConfigurationBinding | None:
    """Load the immutable accepted configuration binding, if the run has one."""
    row = connection.execute("SELECT * FROM run_configuration_bindings WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return ConfigurationBinding(
        server_id=str(row["server_id"]),
        content_sha256=str(row["content_sha256"]),
        content_schema=str(row["content_schema"]),
        contract_id=str(row["contract_id"]),
        contract_version=str(row["contract_version"]),
        schema_id=str(row["schema_id"]),
        schema_sha256=str(row["schema_sha256"]),
        fixture_set=str(row["fixture_set"]),
        fixture_sha256=str(row["fixture_sha256"]),
        source=str(row["source"]),
        configured_executable=str(row["configured_executable"]),
        resolved_executable=str(row["resolved_executable"]),
        canonical_executable_identity_json=str(row["canonical_executable_identity_json"]),
        canonical_producer_provenance_json=str(row["canonical_producer_provenance_json"]),
        validated_at=str(row["validated_at"]),
    )
