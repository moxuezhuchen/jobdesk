"""Application use cases for run lifecycle operations."""

from __future__ import annotations

from collections.abc import Callable
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
    ) -> None:
        self.service = service
        self._server_lookup = server_lookup
        self._ssh_factory = ssh_factory
        self._sftp_factory = sftp_factory
        self._close_clients = close_clients
        self._connect_clients = connect_clients

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

    def create_and_submit(self, spec: RunSpec, *, local_dir: str = "") -> RunOperationOutcome:
        created = self.create_run(spec, local_dir=local_dir)
        if created.errors or not created.records:
            return created
        return self._submit_record(created.records[0])

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
        ssh = None
        sftp = None
        try:
            server = self._server_lookup(record.server_id)
            scheduler = scheduler_from_server(server)
            resources = resources_from_server(server, resource_overrides)
            ssh = self._ssh_factory(server)
            if self._connect_clients:
                ssh.connect()
            sftp = self._sftp_factory(ssh)
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
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])
        finally:
            self._close(sftp, ssh)

    def refresh(self, run_id: str) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        ssh = None
        try:
            server = self._server_lookup(record.server_id)
            ssh = self._ssh_factory(server)
            if self._connect_clients:
                ssh.connect()
            result = self.service.refresh_run(run_id, ssh)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                refresh_result=result,
                changed_count=result.changed_count,
            )
        except Exception as exc:
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])
        finally:
            self._close(None, ssh)

    def refresh_and_download(
        self,
        run_id: str,
        patterns: list[str],
    ) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        ssh = None
        sftp = None
        try:
            server = self._server_lookup(record.server_id)
            ssh = self._ssh_factory(server)
            if self._connect_clients:
                ssh.connect()
            sftp = self._sftp_factory(ssh)
            refresh_result = self.service.refresh_run(run_id, ssh)
            transfers, failures = self.service.download_completed(run_id, sftp, patterns)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                transfer_records=list(transfers),
                failures=list(failures),
                errors=[f"{task_id}: {message}" for task_id, message in failures],
                refresh_result=refresh_result,
                changed_count=refresh_result.changed_count,
            )
        except Exception as exc:
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])
        finally:
            self._close(sftp, ssh)

    def download(self, run_id: str, patterns: list[str]) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        ssh = None
        sftp = None
        try:
            server = self._server_lookup(record.server_id)
            ssh = self._ssh_factory(server)
            if self._connect_clients:
                ssh.connect()
            sftp = self._sftp_factory(ssh)
            transfers, failures = self.service.download_completed(run_id, sftp, patterns)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                transfer_records=list(transfers),
                failures=list(failures),
                errors=[f"{task_id}: {message}" for task_id, message in failures],
            )
        except Exception as exc:
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])
        finally:
            self._close(sftp, ssh)

    def cancel(self, run_id: str) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])
        ssh = None
        try:
            server = self._server_lookup(record.server_id)
            ssh = self._ssh_factory(server)
            if self._connect_clients:
                ssh.connect()
            changed, errors = self.service.cancel_run(run_id, ssh)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                errors=errors,
                changed_count=changed,
            )
        except Exception as exc:
            return RunOperationOutcome(records=[record], errors=[_error_text(exc)])
        finally:
            self._close(None, ssh)

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

    def delete(self, run_id: str) -> RunOperationOutcome:
        try:
            self.service.delete_run(run_id)
            return RunOperationOutcome(changed_count=1)
        except Exception as exc:
            return RunOperationOutcome(errors=[_error_text(exc)])


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"
