"""Concrete adapters for the public application facade contracts."""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from ..application.comparison import RunComparison, compare_runs
from ..application.confflow_client import SubmitRequest
from ..application.configuration_contract import ConfigurationAdmissionError
from ..application.facades import (
    DownloadResult,
    GuiPreferencesSnapshot,
    PresetSource,
    RecoveryResult,
    RemoteFileEntry,
    RunDetails,
    RunEvent,
    RunQuery,
    RunSummary,
    ServerSnapshot,
    SettingsSnapshot,
    SoftwareProfileSnapshot,
    StepPreset,
    SubmitRunResult,
    TaskSummary,
    TransferBatchResult,
    TransferResult,
    WorkflowPreset,
    WorkflowValidation,
)
from ..application.outcomes import OperationFailure, OperationOutcome
from ..application.submit_use_case import SubmitUseCase
from ..core.atomic_write import atomic_write_text
from ..core.file_transfer import OverwritePolicy
from ..core.run import RunSpec, WorkflowKind
from ..core.submit_payload import SubmitPayload
from ..core.workflow_spec import WorkflowSpec
from .config.servers import get_default_servers_path, load_servers
from .persistence.settings.analysis_profiles import AnalysisProfileStore
from .persistence.settings.gui_settings import GuiSettingsStore
from .persistence.settings.method_presets import MethodPresetStore, StepPresetStore
from .runtime.confflow_control_state import require_all_projections_match_authority
from .runtime.file_transfer_service import FileTransferService
from .runtime.run_coordinator import RunCoordinator, RunOperationOutcome
from .runtime.run_service import RunRecord, RunService
from .runtime.session_pool import SessionPool, pooled_sftp_factory
from .runtime.ssh_confflow_client import SSHConfFlowClient


def _failure(stage: str, exc: Exception, *, code: str = "operation_failed") -> OperationFailure:
    return OperationFailure(stage, code, str(exc), False)


def _failures(outcome: RunOperationOutcome) -> tuple[OperationFailure, ...]:
    return tuple(
        OperationFailure(
            item.stage,
            item.code,
            item.message,
            item.retryable,
            item.task_id,
            item.cause_code,
        )
        for item in outcome.errors
    )


def _run_summary(record: RunRecord) -> RunSummary:
    workflow_kind = record.workflow_kind
    return RunSummary(
        run_id=record.run_id,
        server_id=record.server_id,
        workflow_kind=getattr(workflow_kind, "value", workflow_kind),
        created_at=record.created_at,
        status_counts=tuple(sorted((str(key), int(value)) for key, value in record.status_summary.items())),
        remote_dir=record.remote_dir,
        command_template=record.command_template,
        run_dir=str(record.run_dir),
        local_dir=record.local_dir,
    )


def _run_details(service: RunService, record: RunRecord) -> RunDetails:
    tasks = tuple(
        TaskSummary(
            task_id=task.task_id,
            status=getattr(task.status, "value", str(task.status)),
            remote_job_id=task.remote_job_id,
            error_message=task.error_message,
            remote_task_files=tuple(getattr(task, "remote_task_files", ()) or ()),
        )
        for task in service.load_tasks(record.run_id)
    )
    return RunDetails(
        summary=_run_summary(record),
        remote_dir=record.remote_dir,
        local_dir=record.local_dir,
        tasks=tasks,
        mode=record.mode,
    )


class DefaultRunApplication:
    """Present immutable run views while retaining the proven coordinator."""

    def __init__(
        self,
        service: RunService,
        coordinator: RunCoordinator,
        session_pool: SessionPool,
        analysis_profiles: AnalysisProfileStore | None = None,
    ) -> None:
        self._service = service
        self._coordinator = coordinator
        self._session_pool = session_pool
        self._analysis_profiles = analysis_profiles or AnalysisProfileStore()
        self._subscriptions: set[_PollingRunSubscription] = set()
        self._subscriptions_lock = threading.Lock()

    def list_runs(self, query: RunQuery | None = None) -> tuple[RunSummary, ...]:
        values = tuple(_run_summary(record) for record in self._service.list_runs())
        if query is None:
            return values
        text = query.text.casefold().strip()
        return tuple(
            item
            for item in values
            if (not text or text in item.run_id.casefold() or text in item.server_id.casefold())
            and (query.workflow_kind is None or item.workflow_kind == query.workflow_kind)
            and (query.status is None or any(status == query.status and count for status, count in item.status_counts))
        )

    def create(self, spec: RunSpec, *, local_dir: str = "") -> OperationOutcome[RunDetails]:
        return self._details_outcome(self._coordinator.create_run(spec, local_dir=local_dir))

    def submit_existing(self, run_id: str, *, resource_overrides=None) -> OperationOutcome[RunDetails]:
        try:
            record = self._service.load_run(run_id)
            client = SSHConfFlowClient(self._coordinator, record.server_id)
            _handle, outcome = client.submit_with_outcome(
                SubmitRequest(run_id, resource_overrides=dict(resource_overrides or {}))
            )
            return self._details_outcome(outcome)
        except Exception as exc:
            return OperationOutcome.failure(_failure("submit", exc))

    def get_run(self, run_id: str) -> RunDetails:
        return _run_details(self._service, self._service.load_run(run_id))

    def _details_outcome(self, outcome: RunOperationOutcome) -> OperationOutcome[RunDetails]:
        value = _run_details(self._service, outcome.records[-1]) if outcome.records else None
        if value is not None:
            value = RunDetails(
                summary=value.summary,
                remote_dir=value.remote_dir,
                local_dir=value.local_dir,
                tasks=value.tasks,
                mode=value.mode,
                changed_count=outcome.changed_count,
                warnings=tuple(warning for result in outcome.submit_results for warning in result.warnings),
            )
        return OperationOutcome(value=value, failures=_failures(outcome))

    def refresh(self, run_id: str, *, download: bool = False) -> OperationOutcome[RunDetails]:
        record = self._service.load_run(run_id)
        if record.workflow_kind in {WorkflowKind.confflow, WorkflowKind.dag}:
            client = SSHConfFlowClient(self._coordinator, record.server_id)
            outcome = client.refresh_outcome(client.attach(run_id), [], download=download)
        else:
            outcome = (
                self._coordinator.refresh_and_download(run_id, []) if download else self._coordinator.refresh(run_id)
            )
        return self._details_outcome(outcome)

    def download(self, run_id: str, patterns: tuple[str, ...] = ()) -> OperationOutcome[DownloadResult]:
        record = self._service.load_run(run_id)
        if record.workflow_kind in {WorkflowKind.confflow, WorkflowKind.dag}:
            client = SSHConfFlowClient(self._coordinator, record.server_id)
            outcome = client.download_outcome(client.attach(run_id), list(patterns))
        else:
            outcome = self._coordinator.download(run_id, list(patterns))
        details = _run_details(self._service, outcome.records[-1]) if outcome.records else None
        value = None
        if details is not None:
            paths = tuple(str(getattr(item, "local_path", "")) for item in outcome.transfer_records)
            value = DownloadResult(details, tuple(path for path in paths if path))
        transfer_failures = tuple(
            OperationFailure(
                "download",
                "task_download_failed",
                f"{task_id}: {message}",
                True,
                task_id=task_id,
            )
            for task_id, message in outcome.failures
        )
        return OperationOutcome(
            value=value,
            failures=(*_failures(outcome), *transfer_failures),
        )

    def cancel(self, run_id: str) -> OperationOutcome[RunDetails]:
        return self._details_outcome(self._coordinator.cancel(run_id))

    def retry_failed(self, run_id: str) -> OperationOutcome[RunDetails]:
        return self._prepare_and_dispatch(self._coordinator.retry_failed(run_id))

    def prepare_retry_failed(self, run_id: str) -> OperationOutcome[RunDetails]:
        return self._details_outcome(self._coordinator.retry_failed(run_id))

    def rerun(self, run_id: str) -> OperationOutcome[RunDetails]:
        return self._prepare_and_dispatch(self._coordinator.rerun(run_id))

    def prepare_rerun(self, run_id: str) -> OperationOutcome[RunDetails]:
        return self._details_outcome(self._coordinator.rerun(run_id))

    def _prepare_and_dispatch(self, prepared: RunOperationOutcome) -> OperationOutcome[RunDetails]:
        if prepared.errors or not prepared.records:
            return self._details_outcome(prepared)
        record = prepared.records[-1]
        client = SSHConfFlowClient(self._coordinator, record.server_id)
        _handle, submitted = client.submit_with_outcome(SubmitRequest(record.run_id))
        combined = RunOperationOutcome(
            records=submitted.records or prepared.records,
            submit_results=submitted.submit_results,
            transfer_records=submitted.transfer_records,
            failures=submitted.failures,
            errors=[*prepared.errors, *submitted.errors],
            changed_count=prepared.changed_count + submitted.changed_count,
        )
        return self._details_outcome(combined)

    def resolve_uncertain(self, run_id, task_ids, *, action, remote_job_ids=None):
        outcome = (
            self._coordinator.confirm_submitted(run_id, task_ids, dict(remote_job_ids or {}))
            if action == "confirm"
            else self._coordinator.abandon_submit(run_id, task_ids)
        )
        return self._details_outcome(outcome)

    def delete(self, run_id: str) -> OperationOutcome[None]:
        outcome = self._coordinator.delete(run_id)
        return OperationOutcome(value=None, failures=_failures(outcome))

    def recover(self, *, include_legacy_imports: bool = False) -> OperationOutcome[RecoveryResult]:
        outcome = self._coordinator.recover_operations(include_legacy_imports=include_legacy_imports)
        return OperationOutcome(
            value=RecoveryResult(outcome.changed_count),
            failures=_failures(outcome),
        )

    def migration_failures(self) -> tuple[OperationFailure, ...]:
        return tuple(
            OperationFailure(
                "migration",
                "legacy_migration_failed",
                f"{item.legacy_path}: {item.message}",
                True,
            )
            for item in self._service.migration_errors()
        )

    def verify_rollback(self) -> OperationOutcome[None]:
        try:
            require_all_projections_match_authority(self._service)
            return OperationOutcome.success(None)
        except Exception as exc:
            return OperationOutcome.failure(_failure("verify_rollback", exc))

    def compare(
        self,
        workspace: str,
        run_ids: tuple[str, ...],
        *,
        energy_field: str,
        profile_name: str,
    ) -> OperationOutcome[RunComparison]:
        try:
            result = compare_runs(
                workspace,
                list(run_ids),
                energy_field,
                profile_name,
                profile_loader=self._analysis_profiles.get,
                run_source_factory=lambda _workspace: self._service,
            )
            return OperationOutcome.success(result)
        except Exception as exc:
            return OperationOutcome.failure(_failure("compare", exc))

    def submit(
        self,
        payload: SubmitPayload,
        *,
        dispatch: bool = True,
    ) -> OperationOutcome[SubmitRunResult]:
        """Own validation, admission, staging, durable creation and dispatch."""

        try:
            batch = SubmitUseCase().execute(payload)
            if not batch.ok:
                return OperationOutcome.failure(
                    *(OperationFailure("prepare", "invalid_submission", error, False) for error in batch.errors)
                )
            client = SSHConfFlowClient(self._coordinator, payload.server_id)
            workflow_specs = tuple(
                spec for spec in batch.specs if spec.workflow_kind in {WorkflowKind.confflow, WorkflowKind.dag}
            )
            admission = None
            validated_yaml = None
            if workflow_specs:
                if batch.yaml_local_path is None:
                    raise ValueError("Prepared workflow batch has no YAML document")
                validated_yaml = batch.yaml_local_path.read_bytes()
                admission = self._coordinator.admit_configuration(
                    payload.server_id,
                    validated_yaml,
                    require_dag=any(spec.workflow_kind == WorkflowKind.dag for spec in workflow_specs),
                )

            outcomes: list[RunOperationOutcome] = []
            for spec in batch.specs:
                if spec.workflow_kind in {WorkflowKind.confflow, WorkflowKind.dag}:
                    if admission is None:
                        raise ConfigurationAdmissionError("configuration_admission_required")
                    created = self._coordinator.create_admitted_run(
                        spec,
                        admission,
                        local_dir=str(self._service.workspace_dir),
                    )
                else:
                    created = self._coordinator.create_run(
                        spec,
                        local_dir=str(self._service.workspace_dir),
                    )
                outcomes.append(created)

            failures = tuple(item for outcome in outcomes for item in _failures(outcome))
            if failures:
                return OperationOutcome(failures=failures)

            self._upload_batch(batch, payload, client, validated_yaml)
            if dispatch:
                for created in tuple(outcomes):
                    if created.records:
                        _handle, submitted = client.submit_with_outcome(SubmitRequest(created.records[0].run_id))
                        outcomes.append(submitted)

            records = tuple(record for outcome in outcomes for record in outcome.records)
            unique_records = {record.run_id: record for record in records}
            failures = tuple(item for outcome in outcomes for item in _failures(outcome))
            warnings = tuple(
                warning for outcome in outcomes for result in outcome.submit_results for warning in result.warnings
            )
            details = tuple(_run_details(self._service, record) for record in unique_records.values())
            submitted_count = sum(
                result.submitted_task_count for outcome in outcomes for result in outcome.submit_results
            )
            return OperationOutcome(
                value=SubmitRunResult(details, submitted_count, warnings),
                failures=failures,
            )
        except Exception as exc:
            return OperationOutcome.failure(_failure("submit", exc))

    def _upload_batch(self, batch, payload, client, validated_yaml: bytes | None) -> None:
        workflow_specs = tuple(
            spec for spec in batch.specs if spec.workflow_kind in {WorkflowKind.confflow, WorkflowKind.dag}
        )
        if workflow_specs:
            client.probe(require_dag=any(spec.workflow_kind == WorkflowKind.dag for spec in workflow_specs))
        if not batch.local_paths and batch.yaml_local_path is None:
            return
        service = DefaultFilesApplication(self._session_pool)._service(payload.server_id)
        for local_path, remote_target in zip(batch.local_paths, batch.upload_targets, strict=True):
            _require_transfers(service.upload_path(local_path, remote_target), remote_target)
        if batch.yaml_local_path is None or not batch.yaml_local_path.exists():
            return
        if batch.yaml_remote_path is None:
            raise ValueError("Prepared workflow batch has no remote YAML target")
        if validated_yaml is None:
            _require_transfers(
                service.upload_path(batch.yaml_local_path, batch.yaml_remote_path),
                batch.yaml_remote_path,
            )
            return
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as staged:
            staged.write(validated_yaml)
            staged_path = Path(staged.name)
        try:
            _require_transfers(service.upload_path(staged_path, batch.yaml_remote_path), batch.yaml_remote_path)
        finally:
            staged_path.unlink(missing_ok=True)

    def subscribe(self, run_id: str, callback: Callable[[RunEvent], None]):
        subscription = _PollingRunSubscription(
            self._service,
            run_id,
            callback,
            on_close=self._discard_subscription,
        )
        with self._subscriptions_lock:
            self._subscriptions.add(subscription)
        subscription.start()
        return subscription

    def _discard_subscription(self, subscription: "_PollingRunSubscription") -> None:
        with self._subscriptions_lock:
            self._subscriptions.discard(subscription)

    def close(self) -> None:
        with self._subscriptions_lock:
            subscriptions = tuple(self._subscriptions)
        for subscription in subscriptions:
            subscription.close()


class _PollingRunSubscription:
    """Repository-backed event stream with an idempotent bounded close."""

    def __init__(self, service, run_id, callback, *, on_close, interval: float = 1.0):
        self._service = service
        self._run_id = run_id
        self._callback = callback
        self._on_close = on_close
        self._interval = interval
        self._stop = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run,
            name=f"jobdesk-run-subscription-{run_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        previous = None
        revision = 0
        while not self._stop.is_set():
            try:
                tasks = self._service.load_tasks(self._run_id)
                current = tuple(
                    (task.task_id, getattr(task.status, "value", str(task.status)), task.remote_job_id)
                    for task in tasks
                )
                if current != previous:
                    revision += 1
                    self._callback(RunEvent(self._run_id, "tasks_changed", revision))
                    previous = current
            except Exception:
                # A transient repository migration/write race is retried on
                # the next tick; operation failures remain on action calls.
                pass
            self._stop.wait(self._interval)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=max(1.0, self._interval * 2))
        self._on_close(self)


class DefaultFilesApplication:
    def __init__(self, session_pool: SessionPool, *, server_loader=load_servers) -> None:
        self._session_pool = session_pool
        self._server_loader = server_loader

    def _service(self, server_id: str) -> FileTransferService:
        server = self._server_loader().servers[server_id]
        return FileTransferService(pooled_sftp_factory(self._session_pool, server_id, server))

    def list_remote(self, server_id: str, remote_dir: str):
        try:
            entries = self._service(server_id).list_remote(remote_dir)
            return OperationOutcome.success(
                tuple(
                    RemoteFileEntry(
                        name=str(getattr(entry, "name", "")),
                        path=str(getattr(entry, "path", "")),
                        is_dir=bool(getattr(entry, "is_dir", False)),
                        size_bytes=getattr(entry, "size_bytes", None),
                        modified_at=getattr(entry, "modified_at", None),
                        permissions=str(getattr(entry, "permissions", "")),
                    )
                    for entry in entries
                )
            )
        except Exception as exc:
            return OperationOutcome.failure(_failure("list_remote", exc))

    def upload(
        self,
        server_id: str,
        local_path: str,
        remote_path: str,
        *,
        policy: str = "skip_same_size",
        dry_run: bool = False,
        progress_callback: Callable[[int, int], object] | None = None,
    ):
        try:
            records = self._service(server_id).upload_path(
                local_path,
                remote_path,
                policy=OverwritePolicy(policy),
                dry_run=dry_run,
                progress_callback=progress_callback,
            )
            return OperationOutcome.success(
                TransferBatchResult(tuple(_transfer_result(item) for item in _transfer_records(records)))
            )
        except Exception as exc:
            return OperationOutcome.failure(_failure("upload", exc))

    def download(
        self,
        server_id: str,
        remote_path: str,
        local_path: str,
        *,
        policy: str = "skip_same_size",
        dry_run: bool = False,
        progress_callback: Callable[[int, int], object] | None = None,
    ):
        try:
            records = self._service(server_id).download_path(
                remote_path,
                local_path,
                policy=OverwritePolicy(policy),
                dry_run=dry_run,
                progress_callback=progress_callback,
            )
            return OperationOutcome.success(
                TransferBatchResult(tuple(_transfer_result(item) for item in _transfer_records(records)))
            )
        except Exception as exc:
            return OperationOutcome.failure(_failure("download", exc))

    def mkdir(self, server_id: str, remote_dir: str) -> OperationOutcome[None]:
        try:
            self._service(server_id).mkdir_remote(remote_dir)
            return OperationOutcome.success(None)
        except Exception as exc:
            return OperationOutcome.failure(_failure("mkdir", exc))

    def rename(self, server_id: str, old_path: str, new_path: str) -> OperationOutcome[None]:
        try:
            self._service(server_id).rename_remote(old_path, new_path)
            return OperationOutcome.success(None)
        except Exception as exc:
            return OperationOutcome.failure(_failure("rename", exc))

    def delete(
        self,
        server_id: str,
        remote_path: str,
        *,
        recursive: bool = False,
        allowed_roots: tuple[str, ...] = (),
    ) -> OperationOutcome[None]:
        try:
            self._service(server_id).delete_remote(
                remote_path,
                recursive=recursive,
                extra_allowed_roots=list(allowed_roots),
            )
            return OperationOutcome.success(None)
        except Exception as exc:
            return OperationOutcome.failure(_failure("delete", exc))

    def preview_text(
        self,
        server_id: str,
        remote_path: str,
        *,
        max_bytes: int = 65536,
    ) -> OperationOutcome[str]:
        try:
            return OperationOutcome.success(
                self._service(server_id).preview_remote_text(remote_path, max_bytes=max_bytes)
            )
        except Exception as exc:
            return OperationOutcome.failure(_failure("preview", exc))


def _require_transfers(records, target: str) -> None:
    values: tuple[object, ...]
    if records is None:
        values = ()
    elif isinstance(records, (list, tuple)):
        values = tuple(records)
    else:
        values = (records,)
    for record in values:
        status = getattr(getattr(record, "status", None), "value", "")
        if status == "failed":
            reason = getattr(record, "reason", "")
            suffix = f": {reason}" if reason else ""
            raise RuntimeError(f"Upload failed for {target}{suffix}")


def _transfer_result(record) -> TransferResult:
    return TransferResult(
        local_path=str(record.local_path),
        remote_path=str(record.remote_path),
        transferred_bytes=int(record.size_bytes or 0),
        status=getattr(record.status, "value", str(record.status)),
        reason=str(record.reason or ""),
        direction=getattr(record.direction, "value", str(record.direction)),
        dry_run=bool(record.dry_run),
    )


def _transfer_records(value):
    return value if isinstance(value, list) else [value]


class DefaultWorkflowApplication:
    def __init__(
        self,
        preset_store: MethodPresetStore | None = None,
        step_store: StepPresetStore | None = None,
    ) -> None:
        self._presets = preset_store or MethodPresetStore()
        self._steps = step_store or StepPresetStore()

    def validate(self, server_id: str, document: bytes):
        del server_id
        try:
            WorkflowSpec.from_yaml(document.decode("utf-8"))
            return OperationOutcome.success(WorkflowValidation(True))
        except Exception as exc:
            return OperationOutcome.failure(
                _failure("workflow_validation", exc, code="invalid_workflow"),
                value=WorkflowValidation(False, (str(exc),)),
            )

    def prepare_submit(self, request):
        if request.configuration is None:
            return OperationOutcome.success(request)
        validation = self.validate(request.server_id, request.configuration)
        return OperationOutcome(value=request if validation.ok else None, failures=validation.failures)

    def list_presets(self) -> tuple[WorkflowPreset, ...]:
        return tuple(
            WorkflowPreset(preset.name, preset.path.read_bytes(), preset.source)
            for preset in self._presets.list_presets()
        )

    def get_preset(self, name: str, *, source: PresetSource = "user") -> WorkflowPreset:
        document = self._presets.load_yaml(name, source=source).encode("utf-8")
        return WorkflowPreset(name, document, source)

    def save_preset(self, name: str, document: bytes) -> OperationOutcome[WorkflowPreset]:
        try:
            path = self._presets.save_user_yaml(name, document.decode("utf-8"))
            return OperationOutcome.success(WorkflowPreset(name, path.read_bytes(), "user"))
        except Exception as exc:
            return OperationOutcome.failure(_failure("save_workflow", exc))

    def rename_preset(self, old_name: str, new_name: str) -> OperationOutcome[WorkflowPreset]:
        try:
            path = self._presets.rename_user(old_name, new_name)
            return OperationOutcome.success(WorkflowPreset(new_name, path.read_bytes(), "user"))
        except Exception as exc:
            return OperationOutcome.failure(_failure("rename_workflow", exc))

    def delete_preset(self, name: str) -> OperationOutcome[None]:
        try:
            self._presets.delete_user(name)
            return OperationOutcome.success(None)
        except Exception as exc:
            return OperationOutcome.failure(_failure("delete_workflow", exc))

    def list_step_presets(self) -> tuple[StepPreset, ...]:
        import yaml

        return tuple(
            StepPreset(
                preset.name,
                yaml.safe_dump(preset.step, sort_keys=False, allow_unicode=True).encode("utf-8"),
                preset.source,
            )
            for preset in self._steps.list_presets()
        )

    def get_step_preset(self, name: str, *, source: PresetSource) -> StepPreset:
        import yaml

        step = self._steps.load(name, source=source)
        return StepPreset(
            name,
            yaml.safe_dump(step, sort_keys=False, allow_unicode=True).encode("utf-8"),
            source,
        )

    def save_step_preset(self, name: str, document: bytes) -> OperationOutcome[StepPreset]:
        import yaml

        try:
            value = yaml.safe_load(document.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("step preset must be a YAML mapping")
            path = self._steps.save_user(name, value)
            return OperationOutcome.success(StepPreset(name, path.read_bytes(), "user"))
        except Exception as exc:
            return OperationOutcome.failure(_failure("save_step", exc))


class DefaultSettingsApplication:
    def __init__(self, *, servers_path: Path | None = None, gui_settings_store=None) -> None:
        self._servers_path = servers_path
        self._gui_settings_store = gui_settings_store or GuiSettingsStore()

    def snapshot(self) -> SettingsSnapshot:
        import yaml

        config = load_servers(self._servers_path)
        raw, _path = self._load_raw()
        raw_servers = raw.get("servers", {})
        if not isinstance(raw_servers, dict):
            raw_servers = {}
        return SettingsSnapshot(
            servers=tuple(
                ServerSnapshot(
                    key,
                    value.display_name,
                    value.host,
                    value.port,
                    value.username,
                    yaml.safe_dump(
                        raw_servers.get(key, {}),
                        sort_keys=False,
                        allow_unicode=True,
                    ).encode("utf-8"),
                )
                for key, value in sorted(config.servers.items())
            )
        )

    def save_server(self, server: ServerSnapshot, *, previous_server_id: str | None = None):
        import yaml

        try:
            raw, path = self._load_raw()
            servers = raw.setdefault("servers", {})
            if not isinstance(servers, dict):
                raise ValueError("servers.yaml 'servers' must be a mapping")
            source_id = previous_server_id or server.server_id
            existing = servers.get(source_id, {})
            if not isinstance(existing, dict):
                existing = {}
            document = yaml.safe_load(server.document.decode("utf-8")) if server.document else {}
            if document is None:
                document = {}
            if not isinstance(document, dict):
                raise ValueError("server document must be a YAML mapping")
            # A non-empty document is a complete round-trip snapshot.  Treat it
            # as authoritative so clearing an optional field really removes it;
            # unknown keys remain present because snapshot() included them.
            # Empty documents retain the compact DTO's legacy merge behaviour.
            merged = dict(document) if server.document else dict(existing)
            merged.update(
                {
                    "display_name": server.display_name,
                    "host": server.host,
                    "port": server.port,
                    "username": server.username,
                }
            )
            if source_id != server.server_id:
                servers.pop(source_id, None)
            servers[server.server_id] = merged
            self._write_raw(path, raw)
            return OperationOutcome.success(self.snapshot())
        except Exception as exc:
            return OperationOutcome.failure(_failure("save_server", exc))

    def delete_server(self, server_id: str):
        try:
            raw, path = self._load_raw()
            servers = raw.setdefault("servers", {})
            if not isinstance(servers, dict):
                raise ValueError("servers.yaml 'servers' must be a mapping")
            servers.pop(server_id, None)
            self._write_raw(path, raw)
            return OperationOutcome.success(self.snapshot())
        except Exception as exc:
            return OperationOutcome.failure(_failure("delete_server", exc))

    def preferences(self) -> GuiPreferencesSnapshot:
        settings = self._gui_settings_store.load()
        profiles = settings.software_profiles or {}
        return GuiPreferencesSnapshot(
            default_local_folder=settings.default_local_folder,
            text_editor_path=settings.text_editor_path,
            max_parallel=settings.max_parallel,
            language=settings.language,
            hide_dotfiles=settings.hide_dotfiles,
            software_profiles=tuple(
                SoftwareProfileSnapshot(
                    name,
                    str(profile.get("input_extensions", "")),
                    str(profile.get("command_template", "")),
                    str(profile.get("download_patterns", "")),
                )
                for name, profile in profiles.items()
            ),
        )

    def save_preferences(self, preferences: GuiPreferencesSnapshot):
        from dataclasses import replace

        try:
            existing = self._gui_settings_store.load()
            profiles = {
                profile.name: {
                    "input_extensions": profile.input_extensions,
                    "command_template": profile.command_template,
                    "download_patterns": profile.download_patterns,
                }
                for profile in preferences.software_profiles
            }
            self._gui_settings_store.save(
                replace(
                    existing,
                    default_local_folder=preferences.default_local_folder,
                    text_editor_path=preferences.text_editor_path,
                    max_parallel=preferences.max_parallel,
                    language=preferences.language,
                    hide_dotfiles=preferences.hide_dotfiles,
                    software_profiles=profiles,
                )
            )
            return OperationOutcome.success(self.preferences())
        except Exception as exc:
            return OperationOutcome.failure(_failure("save_preferences", exc))

    def _load_raw(self) -> tuple[dict, Path]:
        import yaml

        path = self._servers_path or get_default_servers_path()
        if not path.exists():
            return {"servers": {}}, path
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict):
            raise ValueError("servers.yaml must be a mapping")
        return value, path

    @staticmethod
    def _write_raw(path: Path, raw: dict) -> None:
        import yaml

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))


__all__ = [
    "DefaultFilesApplication",
    "DefaultRunApplication",
    "DefaultSettingsApplication",
    "DefaultWorkflowApplication",
]
