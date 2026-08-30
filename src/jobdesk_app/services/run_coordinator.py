"""Application use cases for run lifecycle operations.

Public methods are grouped by concern:

- Write operations: create_run, submit, refresh, download, cancel, delete
- Recovery: retry_failed, rerun, confirm_submitted, abandon_submit, recover_operations
- Composed: create_and_submit, refresh_and_download
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Protocol

from paramiko.ssh_exception import AuthenticationException, BadHostKeyException

from ..application.configuration_contract import (
    AdmissionStage,
    ConfigurationAdmission,
    ConfigurationAdmissionError,
    ConfigurationContractClient,
    ConfigurationValidationResult,
    VerifiedConfigurationContract,
)
from ..config.schema import ServerConfig
from ..core.confflow_preflight import ConfFlowCapabilities
from ..core.run import RunSpec
from ..core.submit import SubmitResult
from ..core.transfer import TransferRecord
from ..remote.confflow_probe import probe_confflow_capabilities
from .run_repository import RunRecord
from .run_service import RunService
from .scheduler_helpers import resources_from_server, scheduler_from_server
from .ssh_configuration_contract_client import SSHConfigurationContractClient


class RefreshResultProtocol(Protocol):
    """Common result surface shared by legacy and control refresh backends."""

    changed_count: int
    warnings: list[str]


class OperationFailure(str):
    """A display-compatible, structured application operation failure.

    ``OperationFailure`` intentionally subclasses :class:`str`.  Existing CLI
    and Qt callers can keep joining/printing ``RunOperationOutcome.errors`` and
    older tests can still construct an outcome with plain strings, while new
    callers can inspect the operation stage, stable code, retry hint, and task
    identity.  The underlying string value is always the user-facing message.
    """

    stage: str
    code: str
    message: str
    retryable: bool
    task_id: str | None
    cause_code: str | None

    def __new__(
        cls,
        stage: str = "operation",
        code: str = "operation_failed",
        message: str = "",
        retryable: bool = False,
        task_id: str | None = None,
        cause_code: str | None = None,
    ) -> "OperationFailure":
        # The public constructor follows the structured field order.  Plain
        # legacy text is normalized by ``_coerce_failure`` below.
        value = str(message)
        instance = str.__new__(cls, value)
        instance.stage = str(stage)
        instance.code = str(code)
        instance.message = value
        instance.retryable = bool(retryable)
        instance.task_id = task_id
        instance.cause_code = cause_code
        return instance

    @classmethod
    def from_text(
        cls,
        message: str,
        *,
        stage: str = "operation",
        code: str = "operation_failed",
        retryable: bool = False,
        task_id: str | None = None,
        cause_code: str | None = None,
    ) -> "OperationFailure":
        return cls(stage, code, str(message), retryable, task_id, cause_code)

    @property
    def text(self) -> str:
        """Compatibility alias for code that wants an explicit text value."""

        return self.message

    @property
    def display_text(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "task_id": self.task_id,
            "cause_code": self.cause_code,
        }


@dataclass(init=False)
class RunOperationOutcome:
    records: list[RunRecord]
    submit_results: list[SubmitResult]
    transfer_records: list[TransferRecord]
    failures: list[tuple[str, str]]
    errors: list[OperationFailure]
    refresh_result: RefreshResultProtocol | None = None
    changed_count: int = 0

    def __init__(
        self,
        records: Iterable[RunRecord] | None = None,
        submit_results: Iterable[SubmitResult] | None = None,
        transfer_records: Iterable[TransferRecord] | None = None,
        failures: Iterable[tuple[str, str]] | None = None,
        errors: Iterable[OperationFailure | str] | None = None,
        refresh_result: RefreshResultProtocol | None = None,
        changed_count: int = 0,
    ) -> None:
        """Create an outcome while normalizing legacy text errors.

        The constructor intentionally accepts ``str`` values for source
        compatibility, but the stored public field is always
        ``list[OperationFailure]``.
        """

        self.records = list(records or ())
        self.submit_results = list(submit_results or ())
        self.transfer_records = list(transfer_records or ())
        self.failures = list(failures or ())
        self.errors = [_coerce_failure(error) for error in (errors or ())]
        self.refresh_result = refresh_result
        self.changed_count = changed_count

    @property
    def error_messages(self) -> list[str]:
        """Return the legacy text-only view for UI/CLI presentation."""

        return [str(failure) for failure in self.errors]

    @property
    def structured_failures(self) -> list[OperationFailure]:
        """Alias useful to typed consumers that want the error records."""

        return list(self.errors)


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
        configuration_contract_client: ConfigurationContractClient | None = None,
    ) -> None:
        self.service = service
        self._server_lookup = server_lookup
        self._ssh_factory = ssh_factory
        self._sftp_factory = sftp_factory
        self._close_clients = close_clients
        self._connect_clients = connect_clients
        self._session_pool = session_pool
        self._configuration_contract_client = configuration_contract_client or SSHConfigurationContractClient()

    def server_config(self, server_id: str) -> ServerConfig:
        """Resolve a server through the coordinator's configured lookup port."""

        return self._server_lookup(server_id)

    @contextmanager
    def session(self, server_id: str, *, need_sftp: bool) -> Iterator[tuple[Any, Any | None]]:
        """Lease the shared SSH/SFTP session for application clients.

        Remote facades should use this public boundary instead of reaching into
        the coordinator's private client factory.  The coordinator remains the
        owner of connection creation, pooling, and cleanup semantics.
        """

        server = self.server_config(server_id)
        with self._clients(server_id, server, need_sftp=need_sftp) as clients:
            yield clients

    # ---- write ---------------------------------------------------------------

    def create_run(
        self,
        spec: RunSpec,
        *,
        run_id: str | None = None,
        local_dir: str = "",
    ) -> RunOperationOutcome:
        if spec.workflow_kind.value in {"confflow", "dag"}:
            error = ConfigurationAdmissionError("configuration_admission_required")
            return RunOperationOutcome(
                errors=[
                    OperationFailure.from_text(
                        str(error),
                        stage="create",
                        code=error.code,
                        retryable=False,
                    )
                ]
            )
        try:
            if spec.workflow_kind.value in {"confflow", "dag"} and not spec.confflow_executable:
                server = self._server_lookup(spec.server_id)
                configured = str(getattr(server, "confflow_executable", "") or "")
                if configured:
                    spec = replace(spec, confflow_executable=configured)
            record = self.service.create_run(spec, run_id=run_id, local_dir=local_dir)
            return RunOperationOutcome(records=[record])
        except Exception as exc:
            return _error_outcome("create", exc)

    def create_admitted_run(
        self,
        spec: RunSpec,
        admission: ConfigurationAdmission,
        *,
        run_id: str | None = None,
        local_dir: str = "",
    ) -> RunOperationOutcome:
        """Atomically create a workflow run with its accepted remote binding."""

        try:
            if spec.workflow_kind.value not in {"confflow", "dag"}:
                raise ValueError("configuration admission is only valid for ConfFlow workflows")
            if admission.contract.server_id != spec.server_id:
                raise ConfigurationAdmissionError("configuration_identity_mismatch")
            server = self._server_lookup(spec.server_id)
            configured = str(getattr(server, "confflow_executable", "") or "")
            if configured != admission.contract.configured_executable:
                raise ConfigurationAdmissionError("configuration_identity_mismatch")
            if spec.confflow_executable and spec.confflow_executable != configured:
                raise ConfigurationAdmissionError("configuration_identity_mismatch")
            if configured:
                spec = replace(spec, confflow_executable=configured)
            record = self.service.create_run_with_configuration_binding(
                spec,
                admission.to_configuration_binding(),
                run_id=run_id,
                local_dir=local_dir,
            )
            return RunOperationOutcome(records=[record])
        except ConfigurationAdmissionError as exc:
            return RunOperationOutcome(
                errors=[
                    OperationFailure.from_text(
                        str(exc),
                        stage="create",
                        code=exc.code,
                        retryable=exc.code == "configuration_admission_unavailable",
                    )
                ]
            )
        except Exception as exc:
            return _error_outcome("create", exc, code="configuration_admission_required")

    def submit(
        self,
        run_id: str,
        *,
        resource_overrides: dict[str, object] | None = None,
    ) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return _error_outcome("submit", exc, code="run_load_failed")
        return self._submit_record(record, resource_overrides=resource_overrides)

    def _submit_record(
        self,
        record: RunRecord,
        *,
        resource_overrides: dict[str, object] | None = None,
    ) -> RunOperationOutcome:
        run_id = record.run_id
        if record.workflow_kind is not None and record.workflow_kind.value in {"confflow", "dag"}:
            try:
                binding = self.service.load_configuration_binding(run_id)
                if binding is None:
                    raise ConfigurationAdmissionError("configuration_admission_required")
                self.verify_configuration_binding(
                    record.server_id,
                    binding,
                    require_dag=record.workflow_kind.value == "dag",
                )
            except ConfigurationAdmissionError as exc:
                return RunOperationOutcome(
                    records=[record],
                    errors=[
                        OperationFailure.from_text(
                            str(exc),
                            stage=exc.stage or "submit",
                            code=exc.code,
                            retryable=exc.retryable,
                            cause_code=exc.cause_code,
                        )
                    ],
                )
            except Exception as exc:
                return _error_outcome("submit", exc, code="configuration_admission_unavailable")
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
                    max_cores=getattr(server, "max_cores", None),
                )
            try:
                durable_record = self.service.load_run(run_id)
            except (KeyError, TypeError):
                durable_record = record
            return RunOperationOutcome(
                records=[durable_record],
                submit_results=[result],
                errors=[
                    OperationFailure.from_text(
                        error,
                        stage="submit",
                        code="submit_rejected",
                        retryable=True,
                    )
                    for error in result.errors
                ],
            )
        except Exception as exc:
            errors = [_failure_from_exception("submit", exc)]
            try:
                record = self.service.load_run(run_id)
            except Exception as load_exc:
                errors.append(
                    _failure_from_exception(
                        "submit",
                        load_exc,
                        code="reload_after_failure",
                    )
                )
            return RunOperationOutcome(records=[record], errors=errors)

    def refresh(self, run_id: str) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return _error_outcome("refresh", exc, code="run_load_failed")
        try:
            server = self._server_lookup(record.server_id)
            with self._clients(record.server_id, server, need_sftp=False) as (ssh, _sftp):
                result = self.service.refresh_run(run_id, ssh)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                refresh_result=result,
                errors=_refresh_failures(result),
                changed_count=result.changed_count,
            )
        except Exception as exc:
            return RunOperationOutcome(
                records=[record], errors=_structured_errors(_failure_from_exception("refresh", exc))
            )

    def download(self, run_id: str, patterns: list[str]) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return _error_outcome("download", exc, code="run_load_failed")
        try:
            server = self._server_lookup(record.server_id)
            with self._clients(record.server_id, server, need_sftp=True) as (_ssh, sftp):
                transfers, failures = self.service.download_completed(run_id, sftp, patterns)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                transfer_records=list(transfers),
                failures=list(failures),
                errors=[
                    OperationFailure.from_text(
                        f"{task_id}: {message}",
                        stage="download",
                        code="task_download_failed",
                        retryable=True,
                        task_id=task_id,
                    )
                    for task_id, message in failures
                ],
            )
        except Exception as exc:
            return RunOperationOutcome(
                records=[record], errors=_structured_errors(_failure_from_exception("download", exc))
            )

    def sync_progress(self, run_id: str) -> RunOperationOutcome:
        """Synchronize declared live-progress files without changing task state."""
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return _error_outcome("sync_progress", exc, code="run_load_failed")
        try:
            server = self._server_lookup(record.server_id)
            with self._clients(record.server_id, server, need_sftp=True) as (_ssh, sftp):
                transfers, failures = self.service.sync_progress(run_id, sftp)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                transfer_records=list(transfers),
                failures=list(failures),
                errors=[
                    OperationFailure.from_text(
                        f"{task_id}: {message}",
                        stage="sync_progress",
                        code="task_progress_failed",
                        retryable=True,
                        task_id=task_id,
                    )
                    for task_id, message in failures
                ],
            )
        except Exception as exc:
            return RunOperationOutcome(
                records=[record],
                errors=_structured_errors(_failure_from_exception("sync_progress", exc)),
            )

    def cancel(self, run_id: str) -> RunOperationOutcome:
        try:
            record = self.service.load_run(run_id)
        except Exception as exc:
            return _error_outcome("cancel", exc, code="run_load_failed")
        try:
            server = self._server_lookup(record.server_id)
            with self._clients(record.server_id, server, need_sftp=False) as (ssh, _sftp):
                changed, errors = self.service.cancel_run(run_id, ssh)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                errors=_cancel_failures(errors),
                changed_count=changed,
            )
        except Exception as exc:
            return RunOperationOutcome(
                records=[record], errors=_structured_errors(_failure_from_exception("cancel", exc))
            )

    def delete(self, run_id: str) -> RunOperationOutcome:
        try:
            self.service.delete_run(run_id)
            return RunOperationOutcome(changed_count=1)
        except Exception as exc:
            return _error_outcome("delete", exc)

    # ---- recovery -------------------------------------------------------------

    def retry_failed(self, run_id: str) -> RunOperationOutcome:
        try:
            changed = self.service.prepare_retry_failed(run_id)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                changed_count=changed,
            )
        except Exception as exc:
            return _error_outcome("retry_failed", exc)

    def rerun(self, run_id: str) -> RunOperationOutcome:
        try:
            changed = self.service.prepare_rerun(run_id)
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                changed_count=changed,
            )
        except Exception as exc:
            return _error_outcome("rerun", exc)

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

    def _resolve_uncertain(self, run_id: str, action: Callable[[], list[str]]) -> RunOperationOutcome:
        try:
            changed = action()
            return RunOperationOutcome(
                records=[self.service.load_run(run_id)],
                changed_count=len(changed),
            )
        except Exception as exc:
            return _error_outcome("resolve_uncertain", exc)

    def recover_operations(self, *, include_legacy_imports: bool = False) -> RunOperationOutcome:
        changed = 0
        errors: list[OperationFailure] = []
        if include_legacy_imports:
            try:
                migration_errors = self.service.retry_legacy_imports()
                errors.extend(
                    OperationFailure.from_text(
                        f"legacy migration failed for {error.legacy_path}: {error.message}",
                        stage="recovery",
                        code="legacy_migration_failed",
                        retryable=True,
                    )
                    for error in migration_errors
                )
            except Exception as exc:
                errors.append(_failure_from_exception("recovery", exc, code="legacy_migration_failed"))
        try:
            changed += self.service.recover_submit_operations()
        except Exception as exc:
            errors.append(_failure_from_exception("recovery", exc, code="submit_recovery_failed"))
        try:
            delete_changed, delete_errors = self.service.recover_delete_operations_globally()
            changed += delete_changed
            errors.extend(
                OperationFailure.from_text(
                    error,
                    stage="recovery",
                    code="delete_recovery_failed",
                    retryable=True,
                )
                for error in delete_errors
            )
        except Exception as exc:
            errors.append(_failure_from_exception("recovery", exc, code="delete_recovery_failed"))
        return RunOperationOutcome(changed_count=changed, errors=errors)

    def probe_capabilities(
        self,
        server_id: str,
        *,
        require_dag: bool = False,
    ) -> ConfFlowCapabilities:
        """Run the ConfFlow capability handshake through the shared pool.

        P-M1 (R-M1): the probe borrows the same SSH transport that
        subsequent upload/refresh traffic will use, so the GUI does
        not open a second connection on the upload path.  When no
        pool is configured (tests / CLI), we fall back to a fresh
        client via ``_clients``.
        """
        server = self._server_lookup(server_id)
        with self._clients(server_id, server, need_sftp=False) as (ssh, _sftp):
            return probe_confflow_capabilities(
                ssh,
                env_init_scripts=list(getattr(server, "env_init_scripts", []) or []),
                require_dag=require_dag,
                confflow_executable=str(getattr(server, "confflow_executable", "") or ""),
            )

    def resolve_configuration_contract(self, server_id: str) -> VerifiedConfigurationContract:
        """Resolve a producer contract through this coordinator's selected server session."""

        server = self._server_lookup(server_id)
        scripts = tuple(getattr(server, "env_init_scripts", []) or [])
        executable = str(getattr(server, "confflow_executable", "") or "")
        with self._clients(server_id, server, need_sftp=False) as (ssh, _sftp):
            capabilities = probe_confflow_capabilities(
                ssh,
                env_init_scripts=scripts,
                require_dag=False,
                confflow_executable=executable,
            )
            return self._configuration_contract_client.resolve(
                server_id=server_id,
                configured_executable=executable,
                env_init_scripts=scripts,
                ssh=ssh,
                capabilities=capabilities,
            )

    def validate_configuration(
        self,
        contract: VerifiedConfigurationContract,
        configuration: bytes,
    ) -> ConfigurationValidationResult:
        """Validate bytes remotely without adding the contract to upload/submit paths."""

        server = self._server_lookup(contract.server_id)
        scripts = tuple(getattr(server, "env_init_scripts", []) or [])
        configured = str(getattr(server, "confflow_executable", "") or "")
        if configured != contract.configured_executable:
            raise ValueError("configured ConfFlow executable changed after contract resolution")
        with self._clients(contract.server_id, server, need_sftp=False) as (ssh, _sftp):
            capabilities = probe_confflow_capabilities(
                ssh,
                env_init_scripts=scripts,
                require_dag=False,
                confflow_executable=configured,
            )
            current = self._configuration_contract_client.resolve(
                server_id=contract.server_id,
                configured_executable=configured,
                env_init_scripts=scripts,
                ssh=ssh,
                capabilities=capabilities,
            )
            if current.cache_key != contract.cache_key:
                raise ValueError("verified ConfFlow configuration contract changed before validation")
            return self._configuration_contract_client.validate(
                current,
                configuration,
                env_init_scripts=scripts,
                ssh=ssh,
            )

    def verify_configuration_binding(
        self,
        server_id: str,
        binding: Any,
        *,
        require_dag: bool = False,
    ) -> VerifiedConfigurationContract:
        """Fail closed if a persisted workflow binding no longer matches its producer."""

        stage: AdmissionStage = "server_lookup"
        try:
            server = self._server_lookup(server_id)
            stage = "local_config"
            scripts = tuple(getattr(server, "env_init_scripts", []) or [])
            executable = str(getattr(server, "confflow_executable", "") or "")
            stage = "connect"
            with self._clients(server_id, server, need_sftp=False) as (ssh, _sftp):
                stage = "capability_probe"
                capabilities = probe_confflow_capabilities(
                    ssh,
                    env_init_scripts=scripts,
                    require_dag=require_dag,
                    confflow_executable=executable,
                )
                stage = "contract_resolve"
                contract = self._configuration_contract_client.resolve(
                    server_id=server_id,
                    configured_executable=executable,
                    env_init_scripts=scripts,
                    ssh=ssh,
                    capabilities=capabilities,
                )
            stage = "identity_compare"
            current = ConfigurationAdmission(
                contract=contract,
                content_sha256=binding.content_sha256,
                validated_at=binding.validated_at,
            ).to_configuration_binding()
            if current != binding:
                raise ConfigurationAdmissionError(
                    "configuration_identity_mismatch",
                    stage="identity_compare",
                    cause_code="identity_mismatch",
                    retryable=False,
                )
            return contract
        except ConfigurationAdmissionError:
            raise
        except Exception as exc:
            raise ConfigurationAdmissionError(
                "configuration_admission_unavailable",
                stage=stage,
                cause_code=_admission_cause_code(stage, exc),
                retryable=_admission_failure_retryable(stage, exc),
            ) from exc

    def admit_configuration(
        self,
        server_id: str,
        configuration: bytes,
        *,
        require_dag: bool = False,
    ) -> ConfigurationAdmission:
        """Admit exact YAML bytes on one SSH session, without side effects."""

        if not isinstance(configuration, bytes):
            raise TypeError("configuration must be bytes")
        server = self._server_lookup(server_id)
        scripts = tuple(getattr(server, "env_init_scripts", []) or [])
        executable = str(getattr(server, "confflow_executable", "") or "")
        try:
            with self._clients(server_id, server, need_sftp=False) as (ssh, _sftp):
                capabilities = probe_confflow_capabilities(
                    ssh,
                    env_init_scripts=scripts,
                    require_dag=require_dag,
                    confflow_executable=executable,
                )
                contract = self._configuration_contract_client.resolve(
                    server_id=server_id,
                    configured_executable=executable,
                    env_init_scripts=scripts,
                    ssh=ssh,
                    capabilities=capabilities,
                )
                rechecked_capabilities = probe_confflow_capabilities(
                    ssh,
                    env_init_scripts=scripts,
                    require_dag=require_dag,
                    confflow_executable=executable,
                )
                current = self._configuration_contract_client.resolve(
                    server_id=server_id,
                    configured_executable=executable,
                    env_init_scripts=scripts,
                    ssh=ssh,
                    capabilities=rechecked_capabilities,
                )
                if current.cache_key != contract.cache_key:
                    raise ConfigurationAdmissionError("configuration_admission_unavailable")
                result = self._configuration_contract_client.validate(
                    current, configuration, env_init_scripts=scripts, ssh=ssh
                )
        except ConfigurationAdmissionError:
            raise
        except Exception as exc:
            raise ConfigurationAdmissionError("configuration_admission_unavailable") from exc
        if not result.valid:
            path = result.diagnostics[0].path if result.diagnostics else None
            raise ConfigurationAdmissionError("configuration_invalid", path)
        return ConfigurationAdmission(
            contract=current,
            content_sha256=sha256(configuration).hexdigest(),
            validated_at=ConfigurationAdmission.utc_now(),
            validation_result=result,
        )

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
            errors=[*refreshed.errors, *downloaded.errors],
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
    def _clients(self, server_id: str, server: ServerConfig, *, need_sftp: bool) -> Iterator[tuple[Any, Any | None]]:
        if self._session_pool is not None:
            with self._session_pool.lease(server_id, server, need_sftp=need_sftp) as lease:
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
    """Return the established text representation for an exception."""

    return f"{type(exc).__name__}: {exc}"


def _exception_code(exc: Exception) -> tuple[str, bool]:
    """Map common exception classes to stable UI/API failure metadata."""

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "transport_error", True
    if isinstance(exc, KeyError):
        return "not_found", False
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid_request", False
    return "operation_failed", True


def _admission_cause_code(stage: AdmissionStage, exc: Exception) -> str:
    """Return a bounded, non-sensitive classification for admission failures."""

    if stage == "server_lookup" and isinstance(exc, KeyError):
        return "server_not_found"
    if stage == "local_config" and isinstance(exc, (ValueError, TypeError, KeyError)):
        return "invalid_local_config"
    if isinstance(exc, BadHostKeyException):
        return "host_key_mismatch"
    if isinstance(exc, AuthenticationException):
        return "authentication_failed"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionError):
        return "connection_error"
    if isinstance(exc, OSError):
        return "transport_error"
    if isinstance(exc, (ValueError, TypeError)):
        return "invalid_response"
    return "producer_unavailable"


def _admission_failure_retryable(stage: AdmissionStage, exc: Exception) -> bool:
    if stage == "server_lookup" and isinstance(exc, KeyError):
        return False
    if stage == "local_config" and isinstance(exc, (ValueError, TypeError, KeyError)):
        return False
    if isinstance(exc, (AuthenticationException, BadHostKeyException)):
        return False
    return not isinstance(exc, (ValueError, TypeError))


def _failure_from_exception(
    stage: str,
    exc: Exception,
    *,
    code: str | None = None,
    retryable: bool | None = None,
) -> OperationFailure:
    inferred_code, inferred_retryable = _exception_code(exc)
    return OperationFailure.from_text(
        _error_text(exc),
        stage=stage,
        code=code or inferred_code,
        retryable=inferred_retryable if retryable is None else retryable,
    )


def _error_outcome(
    stage: str,
    exc: Exception,
    *,
    code: str | None = None,
    retryable: bool | None = None,
) -> RunOperationOutcome:
    return RunOperationOutcome(
        errors=_structured_errors(_failure_from_exception(stage, exc, code=code, retryable=retryable))
    )


def _structured_errors(*failures: OperationFailure) -> list[OperationFailure]:
    """Build a constructor-compatible list without dropping typed metadata."""

    return list(failures)


def _coerce_failure(value: OperationFailure | str) -> OperationFailure:
    if isinstance(value, OperationFailure):
        return value
    return OperationFailure.from_text(str(value))


def _refresh_failures(result: RefreshResultProtocol) -> list[OperationFailure]:
    failures = getattr(result, "failures", ())
    converted: list[OperationFailure] = []
    for failure in failures:
        task_id = getattr(failure, "task_id", None)
        stage = str(getattr(failure, "stage", "refresh"))
        message = str(getattr(failure, "reason", failure))
        converted.append(
            OperationFailure.from_text(
                f"{task_id}: {message}" if task_id else message,
                stage=stage,
                code="remote_status_failed",
                retryable=True,
                task_id=task_id,
            )
        )
    return converted


def _cancel_failures(errors: Iterable[str]) -> list[OperationFailure]:
    converted: list[OperationFailure] = []
    for error in errors:
        text = str(error)
        task_id, separator, message = text.partition(": ")
        converted.append(
            OperationFailure.from_text(
                text,
                stage="cancel",
                code="task_cancel_failed",
                retryable=True,
                task_id=task_id if separator else None,
            )
        )
    return converted
