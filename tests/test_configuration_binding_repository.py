"""Contract tests for schema-v8 configuration-binding persistence."""

from __future__ import annotations

import dataclasses
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from jobdesk_app.core.configuration_binding import ConfigurationBinding
from jobdesk_app.core.run import RunMode, RunSpec
from jobdesk_app.infrastructure.persistence.sqlite_runs import RunRepository
from jobdesk_app.infrastructure.runtime.run_service import RunService
from tests.test_run_repository import _record, _task


@contextmanager
def _sqlite_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Keep the transaction context and explicitly close test connections."""
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _binding() -> ConfigurationBinding:
    return ConfigurationBinding(
        server_id="server",
        content_sha256="a" * 64,
        content_schema="workflow-v2",
        contract_id="confflow.workflow",
        contract_version="2",
        schema_id="confflow.workflow.schema",
        schema_sha256="b" * 64,
        fixture_set="contract-v2",
        fixture_sha256="c" * 64,
        source="remote-contract",
        configured_executable="confflow",
        resolved_executable="/opt/confflow/bin/confflow",
        canonical_executable_identity_json='{"path":"/opt/confflow/bin/confflow"}',
        canonical_producer_provenance_json='{"version":"1.5.0"}',
        validated_at="2026-08-20T12:00:00+00:00",
    )


def _binding_values(run_id: str, binding: ConfigurationBinding, *, source: str | None = None) -> tuple[str, ...]:
    return (
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
        source if source is not None else binding.source,
        binding.configured_executable,
        binding.resolved_executable,
        binding.canonical_executable_identity_json,
        binding.canonical_producer_provenance_json,
        binding.validated_at,
    )


def test_v6_fixture_migrates_to_v8_and_preserves_existing_run(tmp_path: Path) -> None:
    runs_dir = tmp_path / "runs"
    initial = RunRepository(runs_dir)
    initial.create_run(_record(runs_dir), [_task("a")])
    with _sqlite_connection(initial.database_path) as connection:
        connection.execute("DROP TABLE run_configuration_bindings")
        connection.execute("UPDATE schema_metadata SET value = '6' WHERE key = 'schema_version'")

    upgraded = RunRepository(runs_dir)

    assert upgraded.schema_version() == 8
    assert upgraded.load_run("run-1").run_id == "run-1"
    assert upgraded.load_configuration_binding("run-1") is None


def test_v8_binding_round_trip_is_immutable_and_one_per_run(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    binding = _binding()
    repository.create_run_with_configuration_binding(_record(repository.runs_dir), [_task("a")], binding)

    assert repository.schema_version() == 8
    assert repository.load_configuration_binding("run-1") == binding
    with pytest.raises(sqlite3.IntegrityError):
        with repository._connection() as connection:
            from jobdesk_app.infrastructure.persistence.sqlite_runs._configuration_bindings import (
                insert_configuration_binding,
            )

            insert_configuration_binding(connection, "run-1", binding)
    with pytest.raises(dataclasses.FrozenInstanceError):
        binding.source = "other"  # type: ignore[misc]


def test_v7_binding_migration_backfills_explicit_server_identity(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    binding = _binding()
    repository.create_run_with_configuration_binding(_record(repository.runs_dir), [_task("a")], binding)

    with _sqlite_connection(repository.database_path) as connection:
        for trigger in (
            "run_configuration_bindings_immutable_insert",
            "run_configuration_bindings_immutable_update",
            "run_configuration_bindings_immutable_delete",
        ):
            connection.execute(f"DROP TRIGGER {trigger}")
        connection.execute("ALTER TABLE run_configuration_bindings RENAME TO run_configuration_bindings_v8")
        connection.execute("""
            CREATE TABLE run_configuration_bindings (
                run_id TEXT PRIMARY KEY,
                content_sha256 TEXT NOT NULL,
                content_schema TEXT NOT NULL,
                contract_id TEXT NOT NULL,
                contract_version TEXT NOT NULL,
                schema_id TEXT NOT NULL,
                schema_sha256 TEXT NOT NULL,
                fixture_set TEXT NOT NULL,
                fixture_sha256 TEXT NOT NULL,
                source TEXT NOT NULL,
                configured_executable TEXT NOT NULL,
                resolved_executable TEXT NOT NULL,
                canonical_executable_identity_json TEXT NOT NULL,
                canonical_producer_provenance_json TEXT NOT NULL,
                validated_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            )
            """)
        connection.execute("""
            INSERT INTO run_configuration_bindings(
                run_id, content_sha256, content_schema, contract_id, contract_version,
                schema_id, schema_sha256, fixture_set, fixture_sha256, source,
                configured_executable, resolved_executable,
                canonical_executable_identity_json, canonical_producer_provenance_json,
                validated_at
            )
            SELECT run_id, content_sha256, content_schema, contract_id, contract_version,
                   schema_id, schema_sha256, fixture_set, fixture_sha256, source,
                   configured_executable, resolved_executable,
                   canonical_executable_identity_json, canonical_producer_provenance_json,
                   validated_at
            FROM run_configuration_bindings_v8
            """)
        connection.execute("DROP TABLE run_configuration_bindings_v8")
        connection.execute("UPDATE schema_metadata SET value = '7' WHERE key = 'schema_version'")

    upgraded = RunRepository(repository.runs_dir)

    assert upgraded.schema_version() == 8
    assert upgraded.load_configuration_binding("run-1") == binding


def test_database_rejects_direct_mutation_and_noncanonical_binding_sql(tmp_path: Path) -> None:
    """Schema guards remain effective even if an in-process caller bypasses the API."""
    repository = RunRepository(tmp_path / "runs")
    binding = _binding()
    repository.create_run_with_configuration_binding(_record(repository.runs_dir), [_task("a")], binding)
    repository.create_run(_record(repository.runs_dir, "run-2"), [_task("b", batch_id="run-2")])

    with repository._connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE run_configuration_bindings SET source = 'tampered' WHERE run_id = 'run-1'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM run_configuration_bindings WHERE run_id = 'run-1'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO run_configuration_bindings VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "run-2",
                    binding.server_id,
                    "A" * 64,
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
                    '{"z":1,"a":2}',
                    binding.canonical_producer_provenance_json,
                    binding.validated_at,
                ),
            )
        ddl = str(
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'run_configuration_bindings'"
            ).fetchone()[0]
        )
        trigger_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'run_configuration_bindings'"
            )
        }

    assert "NOT GLOB '*[^0-9a-f]*'" in ddl
    assert "jobdesk_is_canonical_json_object" in ddl
    assert trigger_names == {
        "run_configuration_bindings_immutable_delete",
        "run_configuration_bindings_immutable_insert",
        "run_configuration_bindings_immutable_update",
    }


@pytest.mark.parametrize("recursive_triggers", [0, 1])
@pytest.mark.parametrize("statement_kind", ["replace", "upsert"])
def test_database_rejects_sql_binding_replacement(tmp_path: Path, recursive_triggers: int, statement_kind: str) -> None:
    """An existing binding cannot be replaced through SQLite conflict syntax."""
    repository = RunRepository(tmp_path / "runs")
    binding = _binding()
    repository.create_run_with_configuration_binding(_record(repository.runs_dir), [_task("a")], binding)

    values = _binding_values("run-1", binding, source="tampered")
    with repository._connection() as connection:
        connection.execute(f"PRAGMA recursive_triggers = {recursive_triggers}")
        if statement_kind == "replace":
            statement = """
                INSERT OR REPLACE INTO run_configuration_bindings(
                    run_id, server_id, content_sha256, content_schema, contract_id, contract_version,
                    schema_id, schema_sha256, fixture_set, fixture_sha256, source,
                    configured_executable, resolved_executable,
                    canonical_executable_identity_json, canonical_producer_provenance_json,
                    validated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        else:
            statement = """
                INSERT INTO run_configuration_bindings(
                    run_id, server_id, content_sha256, content_schema, contract_id, contract_version,
                    schema_id, schema_sha256, fixture_set, fixture_sha256, source,
                    configured_executable, resolved_executable,
                    canonical_executable_identity_json, canonical_producer_provenance_json,
                    validated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET source = excluded.source
            """
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(statement, values)

    assert repository.load_configuration_binding("run-1") == binding


def test_direct_delete_stays_blocked_with_audited_journal_but_cascade_succeeds(tmp_path: Path) -> None:
    """Only the parent-run FK cascade may consume a prepared delete journal."""
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir)
    repository.create_run_with_configuration_binding(record, [_task("a")], _binding())
    operation = repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id,
    )

    # Merely having a prepared journal must not turn a direct binding DELETE
    # into an alternate public deletion path.
    with repository._connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM run_configuration_bindings WHERE run_id = ?", (record.run_id,))

    assert repository.load_configuration_binding(record.run_id) == _binding()
    assert repository.delete_run_metadata(operation.operation_id)
    assert repository.load_configuration_binding(record.run_id) is None
    with repository._connection() as connection:
        assert connection.execute("SELECT * FROM run_configuration_binding_delete_context").fetchall() == []


def test_direct_parent_delete_cascade_removes_binding_without_journal(tmp_path: Path) -> None:
    """The binding DELETE guard permits only SQLite's parent FK cascade."""
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir)
    repository.create_run_with_configuration_binding(record, [_task("a")], _binding())

    with repository._connection() as connection:
        connection.execute("DELETE FROM runs WHERE run_id = ?", (record.run_id,))

    assert repository.load_configuration_binding(record.run_id) is None


def test_binding_delete_does_not_trust_writable_context_or_journal_tables(tmp_path: Path) -> None:
    """Forged rows in legacy context/journal tables cannot authorize a child delete."""
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir)
    repository.create_run_with_configuration_binding(record, [_task("a")], _binding())

    with repository._connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO run_configuration_binding_delete_context(run_id) VALUES (?)",
            (record.run_id,),
        )
        connection.execute(
            """
            INSERT INTO operations(
                operation_id, run_id, kind, phase, payload_json, last_error,
                created_at, updated_at, completed_at
            ) VALUES ('forged-delete', ?, 'delete', 'prepared', '{}', NULL, 'now', 'now', NULL)
            """,
            (record.run_id,),
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM run_configuration_bindings WHERE run_id = ?", (record.run_id,))

    assert repository.load_configuration_binding(record.run_id) == _binding()


def test_v7_reopen_repairs_same_name_legacy_delete_trigger(tmp_path: Path) -> None:
    """A same-name v7 journal guard is replaced before the database is usable."""
    repository = RunRepository(tmp_path / "runs")
    record = _record(repository.runs_dir)
    repository.create_run_with_configuration_binding(record, [_task("a")], _binding())

    with _sqlite_connection(repository.database_path) as connection:
        connection.execute("DROP TRIGGER run_configuration_bindings_immutable_delete")
        connection.execute("""
            CREATE TRIGGER run_configuration_bindings_immutable_delete
            BEFORE DELETE ON run_configuration_bindings
            WHEN NOT EXISTS (
                SELECT 1 FROM run_configuration_binding_delete_context
                WHERE run_id = OLD.run_id
            ) OR NOT EXISTS (
                SELECT 1 FROM operations
                WHERE operations.run_id = OLD.run_id
                  AND operations.kind = 'delete'
                  AND operations.phase = 'prepared'
                  AND operations.completed_at IS NULL
            )
            BEGIN
                SELECT RAISE(ABORT, 'configuration binding is immutable');
            END
            """)

    reopened = RunRepository(repository.runs_dir)
    operation = reopened.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id,
    )
    with reopened._connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM run_configuration_bindings WHERE run_id = ?", (record.run_id,))
        trigger_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'run_configuration_bindings_immutable_delete'"
        ).fetchone()[0]
    assert "FROM runs" in str(trigger_sql)
    assert reopened.delete_run_metadata(operation.operation_id)


def test_ready_v7_reopen_repairs_missing_insert_guard(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    binding = _binding()
    repository.create_run_with_configuration_binding(_record(repository.runs_dir), [_task("a")], binding)

    with _sqlite_connection(repository.database_path) as connection:
        connection.execute("DROP TRIGGER run_configuration_bindings_immutable_insert")

    reopened = RunRepository(repository.runs_dir)
    with reopened._connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "INSERT OR REPLACE INTO run_configuration_bindings SELECT * FROM run_configuration_bindings "
                "WHERE run_id = ?",
                ("run-1",),
            )
    assert reopened.load_configuration_binding("run-1") == binding


def test_v7_nonready_reopen_preserves_immutability_guards(tmp_path: Path) -> None:
    """WAL/legacy initialization repair must never leave a v7 binding writable."""
    repository = RunRepository(tmp_path / "runs")
    repository.create_run_with_configuration_binding(_record(repository.runs_dir), [_task("a")], _binding())
    with _sqlite_connection(repository.database_path) as connection:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("UPDATE schema_metadata SET value = '0' WHERE key = 'legacy_import_complete'")

    reopened = RunRepository(repository.runs_dir)

    with reopened._connection() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE run_configuration_bindings SET source = 'tampered' WHERE run_id = 'run-1'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("DELETE FROM run_configuration_bindings WHERE run_id = 'run-1'")


def test_binding_rejects_noncanonical_or_invalid_values() -> None:
    with pytest.raises(ValueError, match="server_id"):
        _binding().__class__(**{**_binding().__dict__, "server_id": ""})
    with pytest.raises(ValueError, match="content_sha256"):
        _binding().__class__(**{**_binding().__dict__, "content_sha256": "not-a-digest"})
    with pytest.raises(ValueError, match="canonical JSON"):
        _binding().__class__(**{**_binding().__dict__, "canonical_executable_identity_json": '{ "path":"x" }'})
    with pytest.raises(ValueError, match="valid JSON"):
        _binding().__class__(**{**_binding().__dict__, "canonical_executable_identity_json": '{"path":NaN}'})


def _spec() -> RunSpec:
    return RunSpec(
        server_id="server",
        remote_dir="/remote/project",
        command_template="g16 {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
    )


def test_service_rolls_back_database_and_empty_run_dir_when_binding_write_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.IntegrityError("binding failure")

    monkeypatch.setattr("jobdesk_app.infrastructure.persistence.sqlite_runs.insert_configuration_binding", fail)

    with pytest.raises(sqlite3.IntegrityError, match="binding failure"):
        service.create_run_with_configuration_binding(_spec(), _binding(), run_id="rolled-back")

    assert service.repository.list_runs() == []
    assert not (service.runs_dir / "rolled-back").exists()


def test_future_schema_is_rejected_before_initialization(tmp_path: Path) -> None:
    repository = RunRepository(tmp_path / "runs")
    with _sqlite_connection(repository.database_path) as connection:
        connection.execute("UPDATE schema_metadata SET value = '9' WHERE key = 'schema_version'")

    with pytest.raises(RuntimeError, match="newer schema version 9"):
        RunRepository(repository.runs_dir)
