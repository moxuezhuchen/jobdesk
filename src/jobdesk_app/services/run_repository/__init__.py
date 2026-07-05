"""Transactional SQLite persistence for JobDesk runs and tasks.

This package is a refactored version of the original single-file
run_repository.py, split into focused submodules while preserving the
exact same public API.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from jobdesk_app.core.manifest import TaskRecord

from ._delete import (
    _record_delete_error,
    complete_delete_isolated,
    delete_run_metadata,
    ensure_delete_trash_paths,
    execute_delete_isolation,
    prepare_delete_run,
)
from ._legacy import _import_legacy_runs, list_migration_errors, retry_legacy_imports
from ._operations import (
    advance_operation,
    create_operation,
    list_operations,
    prune_completed_operations,
    recover_legacy_orphan_submit_tasks,
)
from ._operations_types import MergeResult, MigrationError, OperationRecord, RunRecord
from ._runs import (
    create_run as _create_run,
)
from ._runs import (
    incomplete_delete_run_ids,
)
from ._runs import (
    list_runs as _list_runs,
)
from ._runs import (
    load_run as _load_run,
)
from ._runs import (
    load_tasks as _load_tasks,
)
from ._runs import (
    update_run as _update_run,
)

# Public re-exports (same names as original single-file module).
from ._schema import (
    SCHEMA_VERSION as SCHEMA_VERSION,  # noqa: F401 - explicit re-export
)
from ._schema import (
    SCHEMA_VERSION as _SCHEMA_VERSION,
)
from ._schema import (
    _create_tables,
    _migrate_v1_to_v2,
    _migrate_v2_to_v3,
    _migrate_v3_to_v4,
)
from ._submit import (
    acquire_submit_recovery,
    claim_submit_tasks,
    complete_submit_operation,
    finish_submit_operation,
    recover_submit_operation,
    release_claimed_submit_operation,
    renew_submit_lease,
    resolve_uncertain_tasks,
    start_submit_operation,
)
from ._submit import (
    record_submit_outcome as _record_submit_outcome,
)
from ._tasks import merge_tasks, mutate_tasks
from ._workspaces import delete_operation_workspace, list_workspace_roots


class RunRepository:
    """Own all writable local run state in one SQLite database."""

    def __init__(self, runs_dir: str | Path) -> None:
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.database_path = self.runs_dir / "jobdesk.db"
        self._validate_existing_schema()
        if not self._is_ready():
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
            if row is not None and int(row[0]) > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"database uses newer schema version {row[0]} "
                    f"(supported={_SCHEMA_VERSION})"
                )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _is_ready(self) -> bool:
        if not self.database_path.exists():
            return False
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            required = {
                "schema_metadata", "runs", "tasks", "migration_errors",
                "operations", "workspace_roots", "delete_operation_workspaces",
            }
            if not required.issubset(tables):
                return False
            metadata = dict(connection.execute("SELECT key, value FROM schema_metadata"))
            if (
                metadata.get("schema_version") != str(_SCHEMA_VERSION)
                or metadata.get("legacy_import_complete") != "1"
            ):
                return False
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
            return journal_mode is not None and str(journal_mode[0]).lower() == "wal"
        except (sqlite3.Error, ValueError):
            return False
        finally:
            connection.close()

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
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("BEGIN IMMEDIATE")
            metadata_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_metadata'"
            ).fetchone()
            if metadata_exists:
                version_row = connection.execute(
                    "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
                ).fetchone()
                if version_row is not None and int(version_row["value"]) > _SCHEMA_VERSION:
                    raise RuntimeError(
                        f"database uses newer schema version {version_row['value']} "
                        f"(supported={_SCHEMA_VERSION})"
                    )
            _create_tables(connection)
            version_row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if version_row is None:
                connection.execute(
                    "INSERT INTO schema_metadata(key, value) VALUES('schema_version', '1')"
                )
                current_version = 1
            else:
                current_version = int(version_row["value"])
            if current_version == 1:
                _migrate_v1_to_v2(connection)
                current_version = 2
            if current_version == 2:
                _migrate_v2_to_v3(connection)
                current_version = 3
            if current_version == 3:
                _migrate_v3_to_v4(connection)
            _import_legacy_runs(connection, self.runs_dir)

    def schema_version(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                raise RuntimeError("database has no schema version")
            return int(row["value"])

    def current_schema_version(self) -> int:
        return self.schema_version()

    def list_workspace_roots(self) -> list[Path]:
        with self._connection() as connection:
            return list_workspace_roots(connection)

    def delete_operation_workspace(self, operation_id: str) -> Path | None:
        with self._connection() as connection:
            return delete_operation_workspace(connection, operation_id)

    def create_operation(
        self, run_id: str, kind: str, phase: str, payload: dict[str, object]
    ) -> OperationRecord:
        with self._connection() as connection:
            return create_operation(connection, run_id, kind, phase, payload)

    def advance_operation(
        self,
        operation_id: str,
        expected_phase: str,
        phase: str,
        payload: dict[str, object] | None = None,
        last_error: str | None = None,
        complete: bool = False,
    ) -> bool:
        with self._connection() as connection:
            return advance_operation(
                connection, operation_id, expected_phase, phase, payload, last_error, complete
            )

    def list_operations(self, *, incomplete_only: bool = False) -> list[OperationRecord]:
        with self._connection() as connection:
            return list_operations(connection, incomplete_only=incomplete_only)

    def prune_completed_operations(self, older_than: datetime) -> int:
        with self._connection() as connection:
            return prune_completed_operations(connection, older_than)

    def recover_legacy_orphan_submit_tasks(self) -> int:
        with self._connection() as connection:
            return recover_legacy_orphan_submit_tasks(connection)

    def create_run(self, record: RunRecord, tasks: list[TaskRecord]) -> RunRecord:
        with self._connection() as connection:
            _create_run(connection, record, tasks)
        return self.load_run(record.run_id)

    def incomplete_delete_run_ids(self) -> set[str]:
        with self._connection() as connection:
            return incomplete_delete_run_ids(connection)

    def prepare_delete_run(
        self,
        run_id: str,
        *,
        run_dir: Path,
        results_root: Path,
        results_dir: Path,
    ) -> OperationRecord:
        with self._connection() as connection:
            return prepare_delete_run(
                connection, self.runs_dir, run_id,
                run_dir=run_dir, results_root=results_root, results_dir=results_dir,
            )

    def delete_run_metadata(self, operation_id: str) -> bool:
        with self._connection() as connection:
            return delete_run_metadata(connection, self.runs_dir, operation_id)

    def execute_delete_isolation(
        self,
        operation_id: str,
        callback: Callable[[OperationRecord], None],
    ) -> bool:
        exc_holder: list[BaseException | None] = [None]
        with self._connection() as connection:
            try:
                return execute_delete_isolation(
                    connection, self.runs_dir, operation_id, callback,
                )
            except BaseException as exc:
                exc_holder[0] = exc
                _record_delete_error(connection, operation_id, str(exc))
        if exc_holder[0] is not None:
            raise exc_holder[0]
        return False

    def ensure_delete_trash_paths(self, operation_id: str) -> OperationRecord:
        with self._connection() as connection:
            return ensure_delete_trash_paths(connection, self.runs_dir, operation_id)

    def complete_delete_isolated(self, operation_id: str) -> bool:
        with self._connection() as connection:
            return complete_delete_isolated(connection, self.runs_dir, operation_id)

    def record_submit_outcome(
        self,
        operation_id: str,
        *,
        task_ids: list[str],
        job_ids: dict[str, str],
        error: str | None = None,
        owner_id: str | None = None,
    ) -> bool:
        with self._connection() as connection:
            return _record_submit_outcome(
                connection, operation_id,
                task_ids=task_ids, job_ids=job_ids,
                error=error, owner_id=owner_id,
            )

    def load_run(self, run_id: str) -> RunRecord:
        with self._connection() as connection:
            return _load_run(connection, self.runs_dir, run_id)

    def list_runs(self) -> list[RunRecord]:
        with self._connection() as connection:
            return _list_runs(connection, self.runs_dir)

    def load_tasks(self, run_id: str) -> list[TaskRecord]:
        with self._connection() as connection:
            return _load_tasks(connection, run_id)

    def claim_submit_tasks(
        self,
        run_id: str,
        *,
        scheduler_type: str,
        resources: dict[str, object],
        env_init_scripts: list[str],
        per_task: bool,
        owner_id: str | None = None,
        lease_seconds: float = 60.0,
    ) -> tuple[list[TaskRecord], list[OperationRecord]]:
        with self._connection() as connection:
            return claim_submit_tasks(
                connection, run_id,
                scheduler_type=scheduler_type,
                resources=resources,
                env_init_scripts=env_init_scripts,
                per_task=per_task,
                owner_id=owner_id,
                lease_seconds=lease_seconds,
            )

    def renew_submit_lease(
        self, operation_id: str, owner_id: str, *, lease_seconds: float = 60.0
    ) -> bool:
        with self._connection() as connection:
            return renew_submit_lease(connection, operation_id, owner_id, lease_seconds=lease_seconds)

    def acquire_submit_recovery(
        self, operation_id: str, owner_id: str, *, lease_seconds: float = 60.0
    ) -> bool:
        with self._connection() as connection:
            return acquire_submit_recovery(connection, operation_id, owner_id, lease_seconds=lease_seconds)

    def start_submit_operation(
        self, operation_id: str, *, owner_id: str | None = None
    ) -> bool:
        with self._connection() as connection:
            return start_submit_operation(connection, operation_id, owner_id=owner_id)

    def finish_submit_operation(
        self,
        operation_id: str,
        *,
        task_ids: list[str],
        job_ids: dict[str, str],
        error: str | None = None,
        owner_id: str | None = None,
    ) -> bool:
        with self._connection() as connection:
            return finish_submit_operation(
                connection, operation_id,
                task_ids=task_ids, job_ids=job_ids, error=error, owner_id=owner_id,
            )

    def complete_submit_operation(
        self, operation_id: str, expected_phase: str, *, owner_id: str | None = None
    ) -> bool:
        with self._connection() as connection:
            return complete_submit_operation(
                connection, operation_id, expected_phase, owner_id=owner_id,
            )

    def release_claimed_submit_operation(
        self, operation_id: str, *, owner_id: str | None = None
    ) -> bool:
        with self._connection() as connection:
            return release_claimed_submit_operation(connection, operation_id, owner_id=owner_id)

    def recover_submit_operation(
        self, operation_id: str, *, owner_id: str | None = None
    ) -> bool:
        with self._connection() as connection:
            return recover_submit_operation(connection, operation_id, owner_id=owner_id)

    def resolve_uncertain_tasks(
        self,
        run_id: str,
        task_ids: list[str],
        *,
        action: str,
        remote_job_ids: dict[str, str] | None = None,
    ) -> tuple[list[str], list[TaskRecord]]:
        with self._connection() as connection:
            accepted, resolved = resolve_uncertain_tasks(
                connection, run_id, task_ids, action=action, remote_job_ids=remote_job_ids,
            )
        return accepted, resolved

    def mutate_tasks(
        self,
        run_id: str,
        mutation: Callable[[list[TaskRecord]], list[TaskRecord]],
    ) -> list[TaskRecord]:
        with self._connection() as connection:
            return mutate_tasks(connection, run_id, mutation)

    def merge_tasks(
        self,
        run_id: str,
        updates: list[TaskRecord],
        *,
        expected_tasks: dict[str, TaskRecord] | None = None,
    ) -> MergeResult:
        with self._connection() as connection:
            return merge_tasks(connection, run_id, updates, expected_tasks=expected_tasks)

    def update_run(self, record: RunRecord) -> RunRecord:
        with self._connection() as connection:
            _update_run(connection, record)
        return self.load_run(record.run_id)

    def list_migration_errors(self) -> list[MigrationError]:
        with self._connection() as connection:
            return list_migration_errors(connection)

    def retry_legacy_imports(self) -> list[MigrationError]:
        with self._connection() as connection:
            return retry_legacy_imports(connection, self.runs_dir)
