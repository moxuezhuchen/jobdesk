"""Application use cases for run lifecycle operations.

Public methods are grouped by concern:

- Write operations: create_run, submit, refresh, download, cancel, delete
- Recovery: retry_failed, rerun, confirm_submitted, abandon_submit, recover_operations
- Composed: create_and_submit, refresh_and_download
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from ..config.schema import ServerConfig
from ..core.run import RunSpec
from ..core.submit import SubmitResult
from ..core.transfer import TransferRecord
from .run_repository import RunRecord
from .run_service import RunService
from .scheduler_helpers import resources_from_server, scheduler_from_server


@dataclass
class RunOperationOutcome:
    records: list[RunRecord] = field(default_factory=list)
    submit_results: list[SubmitResult] = field(default_factory=list)
    transfer_records: list[TransferRecord] = field(default_factory=list)
    failures: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    refresh_result: Any | None = None
    changed_count: int = 0


class RunCoordinator:
    """Coordinate persistence, remote sessions, and lifecycle services."""

    def __init__(
        self,
        service: RunService,
        *,
        server_lookup: Callable[[str], ServerConfig],
        ssh_factory: Callable[[ServerConfig], Any],
        sftp_factory: Callable[[Any], Any],
        close_clients: bool = True,
        connect_clients: bool = True,
        session_pool: Any | None = None,
    ) -> None:
        self.service = service
        self._server_lookup = server_lookup
        self._ssh_factory = ssh_factory
        self._sftp_factory = sftp_factory
        self._close_clients = close_clients
        self._connect_clients = connect_clients
        self._session_pool = session_pool

    # ---- write ---------------------------------------------------------------

    def create_run(
        self,
        spec: RunSpec,
        *,
        run_id: str | None = None,
        local_dir: str = "",
    ) -> RunOperationOutcome:
        try:
            record = self.service.create_run(spec, run_id=run_id, local_dir=local_dir)
            return RunOperationOutcome(records=[record])
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])

    def submit(
        self,
        run_id: str,
        *,
        resource_overrides: dict[str, object] | None = None,
    ) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        return self._submit_record(record, resource_overrides=resource_overrides)

    def _submit_record(
        self,
        record: RunRecord,
        *,
        resource_overrides: dict[str, object] | None = None,
    ) -> RunOperationOutcome:
        run_id = record.run_id
        try:
            server = self._server_lookup(record.server_id)
            scheduler = scheduler_from_server(server)
            resources = resources_from_server(server, resource_overrides)
            with self._clients(record.server_id, server, need_sftp=True) as (ssh, sftp):
                result = self.service.submit_run(
                    run_id,
                    ssh,
                    sftp,
                    env_init_scripts=list(server.env_init_scripts or []),
                    scheduler=scheduler,
                    resources=resources,
                )
            try:
                durable_record = self.service.load_run(run_id)
            except (KeyError, TypeError):
                durable_record = record
            return RunOperationOutcome(
                records=[durable_record],
                submit_results=[result],
                errors=list(result.errors),
            )
        except Exception as exc:
            errors = [_error_text(exc)]
            try:
                record = self.service.load_run(run_id)
            except Exception as load_exc:
                errors.append(f"reload after submit failure failed: {_error_text(load_exc)}")
            return RunOperationOutcome(records=[record], errors=errors)

    def refresh(self, run_id: str) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        try:
            server = self._server_lookup(record.server_id)
            with self._clients(record.server_id, server, need_sftp=False) as (ssh, _sftp):
                result = self.service.refresh_run(run_id, ssh)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                refresh_result=result,
                changed_count=result.changed_count,
            )
        except Exception as exc:
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])

    def download(self, run_id: str, patterns: list[str]) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        try:
            server = self._server_lookup(record.server_id)
            with self._clients(record.server_id, server, need_sftp=True) as (_ssh, sftp):
                transfers, failures = self.service.download_completed(run_id, sftp, patterns)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                transfer_records=list(transfers),
                failures=list(failures),
                errors=[f"{task_id}: {message}" for task_id, message in failures],
            )
        except Exception as exc:
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])

    def cancel(self, run_id: str) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        try:
            server = self._server_lookup(record.server_id)
            with self._clients(record.server_id, server, need_sftp=False) as (ssh, _sftp):
                changed, errors = self.service.cancel_run(run_id, ssh)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                errors=errors,
                changed_count=changed,
            )
        except Exception as exc:
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])

    def delete(self, run_id: str) -> RunOperationOutcome:
        try:
            self.service.delete_run(run_id)
            return RunOperationOutcome(changed_count=1)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])

    # ---- recovery -------------------------------------------------------------

    def retry_failed(self, run_id: str) -> RunOperationOutcome:
        try:
            changed = self.service.prepare_retry_failed(run_id)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                changed_count=changed,
            )
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])

    def rerun(self, run_id: str) -> RunOperationOutcome:
        try:
            changed = self.service.prepare_rerun(run_id)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                changed_count=changed,
            )
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])

    def confirm_submitted(
        self,
        run_id: str,
        task_ids: Iterable[str],
        remote_job_ids: dict[str, str] | None = None,
    ) -> RunOperationOutcome:
        return self._resolve_uncertain(
            run_id,
            lambda: self.service.confirm_submitted(run_id, task_ids, remote_job_ids),
        )

    def abandon_submit(self, run_id: str, task_ids: Iterable[str]) -> RunOperationOutcome:
        return self._resolve_uncertain(
            run_id,
            lambda: self.service.abandon_submit(run_id, task_ids),
        )

    def _resolve_uncertain(
        self, run_id: str, action: Callable[[], list[str]]
    ) -> RunOperationOutcome:
        try:
            changed = action()
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                changed_count=len(changed),
            )
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])

    def recover_operations(
        self, *, include_legacy_imports: bool = False
    ) -> RunOperationOutcome:
        changed = 0
        errors: list[str] = []
        if include_legacy_imports:
            try:
                migration_errors = self.service.retry_legacy_imports()
                errors.extend(
                    f"legacy migration failed for {error.legacy_path}: {error.message}"
                    for error in migration_errors
                )
            except Exception as exc:
                errors.append(_error_text(exc))
        try:
            changed += self.service.recover_submit_operations()
        except Exception as exc:
            errors.append(_error_text(exc))
        try:
            delete_changed, delete_errors = (
                self.service.recover_delete_operations_globally()
            )
            changed += delete_changed
            errors.extend(delete_errors)
        except Exception as exc:
            errors.append(_error_text(exc))
        return RunOperationOutcome(changed_count=changed, errors=errors)

    # ---- composed -------------------------------------------------------------

    def create_and_submit(self, spec: RunSpec, *, local_dir: str = "") -> RunOperationOutcome:
        created = self.create_run(spec, local_dir=local_dir)
        if created.errors or not created.records:
            return created
        return self._submit_record(created.records[0])

    def refresh_and_download(
        self,
        run_id: str,
        patterns: list[str],
    ) -> RunOperationOutcome:
        refreshed = self.refresh(run_id)
        if refreshed.errors or not refreshed.records:
            return refreshed
        if refreshed.records[0].status_summary.get("remote_completed", 0) <= 0:
            return refreshed
        downloaded = self.download(run_id, patterns)
        return RunOperationOutcome(
            records=downloaded.records or refreshed.records,
            transfer_records=downloaded.transfer_records,
            failures=downloaded.failures,
            errors=downloaded.errors,
            refresh_result=refreshed.refresh_result,
            changed_count=refreshed.changed_count,
        )

    # ---- helpers -------------------------------------------------------------

    def _close(self, sftp: Any | None, ssh: Any | None) -> None:
        if not self._close_clients:
            return
        for client in (sftp, ssh):
            if client is None:
                continue
            try:
                client.close()
            except Exception:
                pass

    @contextmanager
    def _clients(
        self, server_id: str, server: ServerConfig, *, need_sftp: bool
    ) -> Iterator[tuple[Any, Any | None]]:
        if self._session_pool is not None:
            with self._session_pool.lease(
                server_id, server, need_sftp=need_sftp
            ) as lease:
                yield lease.ssh, lease.sftp
            return
        ssh = None
        sftp = None
        try:
            ssh = self._ssh_factory(server)
            if self._connect_clients:
                ssh.connect()
            if need_sftp:
                sftp = self._sftp_factory(ssh)
            yield ssh, sftp
        finally:
            self._close(sftp, ssh)


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
