"""SSH-backed implementation of the application ConfFlow client contract."""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from typing import Any

from jobdesk_app.application.confflow_client import (
    ArtifactManifest,
    ConfFlowClientError,
    EventPage,
    RemoteRunReference,
    RemoteRunSnapshot,
    SubmitRequest,
    TaskSnapshot,
)
from jobdesk_app.application.configuration_contract import ConfigurationAdmissionError
from jobdesk_app.core.confflow_executable import (
    build_executable_identity_probe,
    parse_executable_identity_probe,
)
from jobdesk_app.core.confflow_preflight import (
    ConfFlowCapabilities,
    validate_confflow_production_capability,
)
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.remote.confflow_probe import ConfFlowCapabilityPreflightError, build_confflow_preflight_shell
from jobdesk_app.remote.errors import RemoteError
from jobdesk_app.remote.scheduler import (
    SchedulerAdapter,
    SchedulerSubmitRejected,
    make_adapter,
)
from jobdesk_app.services import confflow_control_artifacts as _artifacts
from jobdesk_app.services import confflow_control_handoff as _handoff
from jobdesk_app.services import confflow_control_launcher as _launcher
from jobdesk_app.services import confflow_control_reconciliation as _reconciliation
from jobdesk_app.services import confflow_control_run_state as _run_state
from jobdesk_app.services.confflow_control import (
    CONTROL_BACKEND,
    PROTOCOL_SCHEMA,
    ControlArtifactManifest,
    ControlEventPage,
    ControlProtocolError,
    ControlSnapshot,
    ControlTransport,
    build_prepare_request,
    is_terminal_state,
    is_valid_cursor,
)
from jobdesk_app.services.confflow_control_state import (
    load_state,
    save_state,
    save_state_with_task_projection,
)
from jobdesk_app.services.run_coordinator import OperationFailure, RunCoordinator, RunOperationOutcome
from jobdesk_app.services.ssh_confflow_control import (
    SSHControlTransport,
    build_control_execute_command,
    build_control_launcher_script,
    build_control_worker_command,
    resolve_control_state_root,
)

_MAX_DISPATCH_RECONCILE_ATTEMPTS = 3

# Private compatibility aliases keep established focused tests importable while
# the handoff collaborator owns the actual behavior.
_assert_path_under = _handoff.assert_path_under
_control_worker_enabled = _handoff.control_worker_enabled
_is_safe_absolute_remote_path = _handoff.is_safe_absolute_remote_path
_remote_input_path = _handoff.remote_input_path
_state_worker_attempt_root = _handoff.state_worker_attempt_root
_state_worker_executable = _handoff.state_worker_executable
_state_worker_handoff = _handoff.state_worker_handoff
_state_worker_handoff_path = _handoff.state_worker_handoff_path
_state_worker_input_path = _handoff.state_worker_input_path
_state_worker_work_dir = _handoff.state_worker_work_dir
_validate_safe_component = _handoff.validate_safe_component
_worker_executable_for = _handoff.worker_executable_for
_worker_handoff = _handoff.worker_handoff
_worker_handoff_digest = _handoff.worker_handoff_digest
_worker_state_root = _handoff.worker_state_root
_worker_task_digest = _handoff.worker_task_digest
_worker_work_dir_name = _handoff.worker_work_dir_name
_workflow_config_path = _handoff.workflow_config_path
_upload_control_worker_handoff = _handoff.upload_control_worker_handoff
_ensure_worker_remote_directories = _handoff.ensure_worker_remote_directories
_stage_remote_file = _handoff.stage_remote_file
_artifact_entries = _artifacts.artifact_entries
_assert_local_not_symlink = _artifacts.assert_local_not_symlink
_assert_remote_not_symlink = _artifacts.assert_remote_not_symlink
_assert_safe_relative_artifact_path = _artifacts.assert_safe_relative_artifact_path
_download_control_artifacts = _artifacts.download_control_artifacts
_pattern_matches = _artifacts.pattern_matches
_sha256_file = _handoff.sha256_file
_task_matches_terminal = _artifacts.task_matches_terminal
_work_dir_for_artifact = _artifacts.work_dir_for_artifact
_audit_timestamp = _run_state.audit_timestamp
_record_unknown_dispatch = _run_state.record_unknown_dispatch
_state_identity = _run_state.state_identity
_control_expected_identity = _run_state.control_expected_identity
_state_locator = _run_state.state_locator
_state_key = _run_state.state_key
_optional_string = _run_state.optional_string
_capability_payload = _run_state.capability_payload
_control_state = _run_state.control_state
_canonical_scheduler_type = _launcher.canonical_scheduler_type


class SSHConfFlowClient:
    """Application facade with one immutable backend choice per run."""

    def __init__(
        self,
        coordinator: RunCoordinator,
        server_id: str,
        *,
        control_transport_factory: Callable[[str, str], ControlTransport] | None = None,
        control_capability_factory: Callable[[], str] | None = None,
        scheduler_factory: Callable[[str], SchedulerAdapter] | None = None,
        backend_mode: str | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._server_id = server_id
        self._control_transport_factory = control_transport_factory
        self._control_capability_factory = control_capability_factory
        self._scheduler_factory = scheduler_factory or make_adapter
        self._selected_backend = CONTROL_BACKEND
        self._selected_state_locator: str | None = None
        self._selected_capability: ConfFlowCapabilities | None = None
        if backend_mode not in {None, CONTROL_BACKEND}:
            raise ValueError("only the control ConfFlow backend is supported after Phase F")

    def probe(self, *, require_dag: bool = False) -> ConfFlowCapabilities:
        try:
            capabilities = self._coordinator.probe_capabilities(self._server_id, require_dag=require_dag)
            if not isinstance(capabilities, ConfFlowCapabilities):
                raise ConfFlowClientError("ConfFlow capability probe did not return the required control capability")
            self._selected_capability = capabilities
            self._negotiate_backend(capabilities)
            return capabilities
        except (ConfFlowCapabilityPreflightError, ControlProtocolError, ValueError) as exc:
            raise ConfFlowClientError(str(exc)) from exc

    def probe_capabilities(self, server_id: str, *, require_dag: bool = False):
        try:
            return self._coordinator.probe_capabilities(server_id, require_dag=require_dag)
        except ConfFlowCapabilityPreflightError as exc:
            raise ConfFlowClientError(str(exc)) from exc

    def attach(self, run_id: str) -> SSHControlRunHandle:
        record = self._coordinator.service.load_run(run_id)
        if record.server_id != self._server_id:
            raise ConfFlowClientError(f"run {run_id!r} belongs to server {record.server_id!r}, not {self._server_id!r}")
        state = load_state(self._coordinator.service, run_id)
        if state is None:
            raise ConfFlowClientError(f"run {run_id} has no durable control state; legacy runs are retired")
        state = self._reconcile_control_dispatch(record, state)
        return SSHControlRunHandle(self, _reference_for(record, self._provenance(run_id), state))

    def restore_handle(self, value: dict[str, object]):
        saved = RemoteRunReference.from_dict(value)
        if saved.server_id != self._server_id:
            raise ConfFlowClientError(f"serialized handle belongs to server {saved.server_id!r}")
        handle = self.attach(saved.run_id)
        if handle.to_dict() != saved.to_dict():
            raise ConfFlowClientError("serialized handle identity no longer matches durable provenance")
        return handle

    def confirm_unresolved_dispatch_not_accepted(self, run_id: str, *, evidence: str) -> None:
        """Record an operator's scheduler-side non-acceptance proof for safe retry."""
        record = self._coordinator.service.load_run(run_id)
        state = load_state(self._coordinator.service, run_id)
        if state is None:
            raise ConfFlowClientError(f"run {run_id} has no durable control state")
        state = self._reconcile_control_dispatch(record, state)
        if state.get("dispatch_state") != "dispatching":
            raise ConfFlowClientError("control dispatch is no longer unresolved")
        if state.get("reconcile_attempts") != _MAX_DISPATCH_RECONCILE_ATTEMPTS:
            raise ConfFlowClientError("control dispatch has not completed bounded reconciliation")
        proof = evidence.strip()
        if not proof or len(proof) > 2048:
            raise ConfFlowClientError("scheduler non-acceptance evidence must be 1..2048 characters")
        failed = deepcopy(state)
        failed.update(
            {
                "dispatch_state": "failed",
                "dispatch_outcome": "rejected",
                "dispatch_error": "operator confirmed scheduler did not accept launcher",
                "dispatch_resolution": {
                    "kind": "scheduler_non_acceptance",
                    "evidence": proof,
                    "recorded_at": _audit_timestamp(),
                },
                "dispatch_updated_at": _audit_timestamp(),
                "recovery_state": "retry_authorized",
            }
        )
        save_state(self._coordinator.service, run_id, failed)

    def submit(self, request: SubmitRequest):
        handle, outcome = self.submit_with_outcome(request)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        if handle is None:
            raise ConfFlowClientError("submit returned no handle")
        return handle

    def submit_with_outcome(self, request: SubmitRequest):
        record = self._coordinator.service.load_run(request.run_id)
        workflow_kind = record.workflow_kind
        is_workflow = workflow_kind is not None and workflow_kind.value in {"confflow", "dag"}
        if is_workflow:
            binding = self._coordinator.service.load_configuration_binding(request.run_id)
            if binding is None:
                return None, SubmitResult(
                    request.run_id,
                    0,
                    record.remote_dir,
                    errors=["configuration admission is required before workflow submission"],
                )
            try:
                self._coordinator.verify_configuration_binding(
                    record.server_id,
                    binding,
                    require_dag=workflow_kind is not None and workflow_kind.value == "dag",
                )
            except ConfigurationAdmissionError as exc:
                failure = OperationFailure.from_text(
                    str(exc),
                    stage=exc.stage or "submit",
                    code=exc.code,
                    retryable=exc.retryable,
                    cause_code=exc.cause_code,
                )
                return None, SubmitResult(
                    request.run_id,
                    0,
                    record.remote_dir,
                    errors=[str(failure)],
                    structured_failures=[failure],
                )
        state = load_state(self._coordinator.service, request.run_id)
        if (
            state is None
            and is_workflow
            and (
                self._selected_backend != CONTROL_BACKEND
                or self._selected_capability is None
                or not self._selected_state_locator
            )
        ):
            try:
                self._ensure_control_submission_admission(
                    require_dag=workflow_kind is not None and workflow_kind.value == "dag"
                )
            except ConfFlowClientError as exc:
                failure = _control_admission_failure(exc)
                return None, SubmitResult(
                    request.run_id,
                    0,
                    record.remote_dir,
                    errors=[failure],
                    structured_failures=[failure],
                )
        if state is not None and state.get("backend") != CONTROL_BACKEND:
            return None, SubmitResult(
                request.run_id, 0, record.remote_dir, errors=["legacy ConfFlow backend is retired"]
            )
        return self._submit_control(request, record, state)

    def _ensure_control_submission_admission(self, *, require_dag: bool) -> None:
        """Make a fresh control submission self-sufficient before side effects.

        A durable run state is authoritative on retries.  For a new run, a
        caller-provided probe is merely an optimization: submit performs the
        missing capability/locator work itself.  A complete in-memory
        selection is reused so normal probe-then-submit paths do not issue a
        second remote probe.
        """

        capability = self._selected_capability
        if self._selected_backend != CONTROL_BACKEND or capability is None:
            self.probe(require_dag=require_dag)
        elif not self._selected_state_locator:
            try:
                self._negotiate_backend(capability)
            except (ConfFlowCapabilityPreflightError, ControlProtocolError, ValueError) as exc:
                raise ConfFlowClientError(str(exc)) from exc
        if self._selected_backend != CONTROL_BACKEND or not self._selected_state_locator:
            raise ConfFlowClientError("control backend has no durable producer state locator")

    def refresh_outcome(self, handle, patterns: list[str], *, download: bool):
        return handle.refresh_outcome(patterns, download=download)

    def download_outcome(self, handle, patterns: list[str]):
        return handle.download_outcome(patterns)

    def cancel_outcome(self, handle) -> RunOperationOutcome:
        try:
            handle.cancel()
            return RunOperationOutcome(
                records=[self._coordinator.service.load_run(handle.run_id)],
                changed_count=1,
            )
        except Exception as exc:
            return RunOperationOutcome(
                records=[self._coordinator.service.load_run(handle.run_id)],
                errors=[str(exc)],
            )

    def _submit_control(self, request: SubmitRequest, record: Any, state: dict[str, object] | None):
        try:
            retrying_failed_dispatch = False
            if state is not None:
                state = self._reconcile_control_dispatch(record, state)
                dispatch_state = state.get("dispatch_state")
                if dispatch_state == "submitted":
                    return self.attach(request.run_id), RunOperationOutcome(
                        records=[self._coordinator.service.load_run(request.run_id)],
                        submit_results=[
                            SubmitResult(
                                batch_id=request.run_id,
                                submitted_task_count=0,
                                remote_batch_dir=record.remote_dir,
                            )
                        ],
                    )
                if dispatch_state == "dispatching":
                    raise ConfFlowClientError("control launcher dispatch is unresolved; refusing duplicate submission")
                retrying_failed_dispatch = dispatch_state == "failed"
            tasks = self._coordinator.service.load_tasks(request.run_id)
            if retrying_failed_dispatch and state is not None and state.get("dispatch_outcome") == "worker_failed":
                return self._restart_control_worker(request, record, state, tasks)
            capability = self._selected_capability
            if capability is None and state is not None:
                capability = _capability_from_state(state)
            producer_identity = _state_identity(state)
            state_locator = _state_locator(state) or self._selected_state_locator
            if not state_locator:
                raise ConfFlowClientError("control backend has no durable producer state locator")
            if retrying_failed_dispatch and state is not None and state.get("dispatch_outcome") != "rejected":
                # A launcher can fail after the producer has already consumed
                # the execute intent.  The producer's prepare response is
                # required to remain ``prepared``, so blindly preparing again
                # would turn a retry into a protocol error or duplicate work.
                with self._control_session(request.run_id, state_locator, need_sftp=False) as (
                    transport,
                    _sftp,
                    _ssh,
                ):
                    producer_snapshot = transport.status(request.run_id)
                if producer_snapshot.state != "prepared":
                    raise ConfFlowClientError(
                        "control launcher failed after producer reached "
                        f"{producer_snapshot.state}; refusing duplicate prepare"
                    )
            if not producer_identity:
                if capability is None:
                    raise ConfFlowClientError("control backend has no accepted producer identity")
                producer_identity = self._measure_control_identity(capability)

            if not _control_worker_enabled(capability, state):
                raise ConfFlowClientError("control backend requires the producer-owned worker-handoff capability")
            if len(tasks) != 1:
                raise ConfFlowClientError(
                    "control worker handoff supports exactly one task; split the JobDesk batch before prepare"
                )
            task = tasks[0]
            producer_executable = self._launcher_executable(record, state or {}, tasks)
            worker_executable = _state_worker_executable(state) or _worker_executable_for(producer_executable)
            workflow_path = _workflow_config_path(tasks)
            if state is None:
                state_locator = _worker_state_root(state_locator, request.run_id)
                worker_attempt_root = posixpath.dirname(state_locator)
                worker_input_dir = posixpath.join(worker_attempt_root, "input")
                worker_results_dir = posixpath.join(worker_attempt_root, "results")
                worker_input_path = posixpath.join(worker_input_dir, task.remote_task_files[0])
                worker_work_dir = posixpath.join(worker_results_dir, _worker_work_dir_name(task))
                worker_handoff_path = posixpath.join(worker_input_dir, "worker-handoff.json")
                workflow_digest = self._remote_digest(request.run_id, state_locator, workflow_path)
                input_digest = self._remote_digest(request.run_id, state_locator, _remote_input_path(task))
                worker_handoff = _worker_handoff(
                    run_id=request.run_id,
                    workflow_path=posixpath.join(worker_attempt_root, "input", "workflow.yaml"),
                    workflow_digest=workflow_digest,
                    input_path=worker_input_path,
                    input_digest=input_digest,
                    work_dir=worker_work_dir,
                    task_id=task.task_id,
                )
            else:
                worker_handoff = _state_worker_handoff(state)
                worker_handoff_path = _state_worker_handoff_path(state)
                worker_attempt_root = _state_worker_attempt_root(state)
                worker_work_dir = _state_worker_work_dir(state)
                worker_input_path = _state_worker_input_path(worker_handoff)
                if not all(
                    isinstance(value, str) and value.startswith("/")
                    for value in (worker_handoff_path, worker_attempt_root, worker_work_dir)
                ):
                    raise ConfFlowClientError("control state has incomplete worker-handoff paths")
                workflow_digest = _worker_handoff_digest(worker_handoff, "workflow_config")
                input_digest = _worker_handoff_digest(worker_handoff, "tasks")
            input_manifest_bytes = _canonical_json(worker_handoff)
            input_digest = hashlib.sha256(input_manifest_bytes).hexdigest()
            request_frame = build_prepare_request(
                run_id=request.run_id,
                idempotency_key=_state_key(state, request.run_id),
                workflow_config={"path": "workflow.yaml", "sha256": workflow_digest},
                input_manifest={"path": "worker-handoff.json", "sha256": input_digest},
                expected_executable_identity=_control_expected_identity(producer_identity),
            )
            if state is not None and state.get("request_digest") not in {None, request_frame["request_digest"]}:
                raise ConfFlowClientError("control backend request differs from durable idempotency payload")

            if state is None:
                state = _control_state(
                    request.run_id,
                    state_locator=state_locator,
                    capability=capability,
                    producer_identity=producer_identity,
                    request_frame=request_frame,
                    snapshot=ControlSnapshot(request.run_id, 0, "prepared"),
                    previous=None,
                    workflow_path=workflow_path,
                    input_path=worker_handoff_path,
                    worker_handoff=worker_handoff,
                    worker_attempt_root=worker_attempt_root,
                    worker_work_dir=worker_work_dir,
                    worker_executable=worker_executable,
                )
                save_state(self._coordinator.service, request.run_id, state)

            with self._control_session(request.run_id, state_locator, need_sftp=True) as (transport, sftp, ssh):
                if sftp is None:
                    raise ConfFlowClientError("control launcher submission requires an SFTP session")
                _upload_control_worker_handoff(
                    sftp,
                    ssh,
                    worker_handoff=worker_handoff,
                    handoff_path=worker_handoff_path,
                    attempt_root=worker_attempt_root,
                    workflow_path=posixpath.join(worker_attempt_root, "input", "workflow.yaml"),
                    input_path=worker_input_path,
                    remote_workflow_path=workflow_path,
                    remote_input_path=_remote_input_path(task),
                    workflow_digest=workflow_digest,
                    input_digest=_worker_task_digest(worker_handoff),
                    handoff_bytes=input_manifest_bytes,
                )
                prepared = transport.prepare(request_frame)
                durable = _control_state(
                    request.run_id,
                    state_locator=state_locator,
                    capability=capability,
                    producer_identity=producer_identity,
                    request_frame=request_frame,
                    snapshot=prepared,
                    previous=state,
                    workflow_path=workflow_path,
                    input_path=worker_handoff_path,
                    worker_handoff=worker_handoff,
                    worker_attempt_root=worker_attempt_root,
                    worker_work_dir=worker_work_dir,
                    worker_executable=worker_executable,
                )
                save_state(self._coordinator.service, request.run_id, durable)
                self._apply_control_snapshot(prepared)
                self._persist_control_worker_paths(request.run_id, worker_work_dir)

                scheduler_type, resources, env_init_scripts = self._launcher_scheduler_details(
                    record, request.resource_overrides
                )
                launcher_dir, script_path, metadata_path, log_path = _launcher.launcher_paths(
                    record.remote_dir, request.run_id
                )
                command = (
                    f"{build_control_execute_command(producer_executable, state_locator, request.run_id)}"
                    f" && setsid --wait {build_control_worker_command(worker_executable, state_locator, request.run_id, worker_handoff_path)}"
                )
                script = build_control_launcher_script(
                    executable=producer_executable,
                    worker_executable=worker_executable,
                    handoff_path=worker_handoff_path,
                    state_root=state_locator,
                    run_id=request.run_id,
                    metadata_path=metadata_path,
                    scheduler_type=scheduler_type,
                    resources=resources,
                    env_init_scripts=env_init_scripts,
                )
                _script_bytes, script_sha256, script_size = _launcher.stage_launcher_script(
                    sftp,
                    launcher_dir,
                    script_path,
                    request.run_id,
                    script,
                    prefix="jobdesk-control-launcher-",
                )
                launcher = {
                    "content_schema": "jobdesk.confflow.launcher.v1",
                    "run_id": request.run_id,
                    "scheduler_type": scheduler_type,
                    "script_path": script_path,
                    "metadata_path": metadata_path,
                    "log_path": log_path,
                    "state_root": state_locator,
                    "command": command,
                    "script_sha256": script_sha256,
                    "script_size": script_size,
                }
                dispatching = _launcher.dispatching_state(
                    durable,
                    scheduler_type=scheduler_type,
                    launcher=launcher,
                    timestamp=_audit_timestamp(),
                )
                save_state(self._coordinator.service, request.run_id, dispatching)

                try:
                    scheduler_job_id = _launcher.submit_scheduler(
                        self._scheduler_factory,
                        scheduler_type=scheduler_type,
                        ssh=ssh,
                        script_path=script_path,
                        resources=resources,
                        on_rejected=lambda exc: save_state(
                            self._coordinator.service,
                            request.run_id,
                            _launcher.rejected_state(dispatching, error=str(exc), timestamp=_audit_timestamp()),
                        ),
                        on_unknown=lambda error: _record_unknown_dispatch(
                            self._coordinator.service, request.run_id, dispatching, error
                        ),
                        empty_job_error="scheduler adapter returned an empty control launcher job id",
                    )
                except SchedulerSubmitRejected as exc:
                    raise ConfFlowClientError(str(exc)) from exc
                except (RemoteError, OSError, RuntimeError, TimeoutError) as exc:
                    raise ConfFlowClientError(str(exc)) from exc
                try:
                    submitted = _launcher.submitted_state(
                        dispatching,
                        scheduler_job_id=scheduler_job_id,
                        timestamp=_audit_timestamp(),
                    )
                except ValueError as exc:
                    raise ConfFlowClientError(str(exc)) from exc
                save_state(self._coordinator.service, request.run_id, submitted)
            self._mark_control_submitted(request.run_id, scheduler_type, scheduler_job_id)
            result = SubmitResult(
                batch_id=request.run_id,
                submitted_task_count=len(tasks),
                remote_batch_dir=record.remote_dir,
                control_log_path=log_path,
                control_nohup_log_path=log_path if scheduler_type == "nohup" else "",
                control_script_path=script_path,
                nohup_command=command if scheduler_type == "nohup" else "",
                updated_task_ids=[task.task_id for task in tasks],
            )
            return self.attach(request.run_id), RunOperationOutcome(
                records=[self._coordinator.service.load_run(request.run_id)],
                submit_results=[result],
            )
        except (ControlProtocolError, ConfFlowClientError, OSError, ValueError) as exc:
            return None, RunOperationOutcome(
                records=[self._coordinator.service.load_run(request.run_id)],
                errors=[str(exc)],
            )

    def _negotiate_backend(self, capabilities: ConfFlowCapabilities) -> None:
        server = self._coordinator.server_config(self._server_id)
        executable = str(getattr(server, "confflow_executable", "") or "")
        validate_confflow_production_capability(
            capabilities,
            expected_executable=executable or None,
        )
        if not capabilities.control_worker:
            raise ValueError("ConfFlow production capability does not expose the producer worker handoff")
        if self._control_capability_factory is not None:
            self._selected_state_locator = self._control_capability_factory()
            self._selected_backend = CONTROL_BACKEND
            return
        if self._control_transport_factory is not None:
            self._selected_backend = CONTROL_BACKEND
            self._selected_state_locator = "/tmp/confflow-control"
            return
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        with self._coordinator.session(self._server_id, need_sftp=False) as (ssh, _sftp):
            transport = SSHControlTransport(
                ssh,
                None,
                executable=executable,
                state_root="/tmp/confflow-control",
                env_init_scripts=env_init_scripts,
            )
            transport.capabilities()
            state_locator = resolve_control_state_root(ssh, env_init_scripts=env_init_scripts)
        self._selected_backend = CONTROL_BACKEND
        self._selected_state_locator = state_locator

    @contextmanager
    def _control_session(self, run_id: str, state_locator: str, *, need_sftp: bool):
        if self._control_transport_factory is not None:
            transport = self._control_transport_factory(run_id, state_locator)
            yield transport, getattr(transport, "sftp", None), getattr(transport, "ssh", None)
            return
        server = self._coordinator.server_config(self._server_id)
        executable = str(getattr(server, "confflow_executable", "") or "")
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        with self._coordinator.session(self._server_id, need_sftp=need_sftp) as (ssh, sftp):
            yield SSHControlTransport(
                ssh,
                sftp,
                executable=executable,
                state_root=state_locator,
                env_init_scripts=env_init_scripts,
            ), sftp, ssh

    def _provenance(self, run_id: str) -> dict[str, object] | None:
        return self._coordinator.service.load_run_provenance(run_id)

    def _measure_control_identity(self, capabilities: ConfFlowCapabilities) -> dict[str, object]:
        executable = capabilities.executable
        if not isinstance(executable, dict) or not isinstance(executable.get("python"), str):
            raise ConfFlowClientError("control backend capability has no Python executable identity")
        python_executable = str(executable["python"])
        server = self._coordinator.server_config(self._server_id)
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        with self._coordinator.session(self._server_id, need_sftp=False) as (ssh, _sftp):
            result = ssh.run(
                build_confflow_preflight_shell(
                    build_executable_identity_probe(python_executable, python_executable), env_init_scripts
                ),
                timeout=30,
            )
        if result.exit_code != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.exit_code}"
            raise ConfFlowClientError(f"control executable identity probe failed: {detail}")
        identity = parse_executable_identity_probe(
            result.stdout,
            path=python_executable,
            python_executable=python_executable,
        )
        return identity.as_dict()

    def _remote_digest(self, run_id: str, state_locator: str, remote_path: str) -> str:
        del state_locator
        if not remote_path or not remote_path.startswith("/"):
            raise ConfFlowClientError("control backend workflow config path must be absolute")
        server = self._coordinator.server_config(self._server_id)
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        command = build_confflow_preflight_shell(
            f"sha256sum -- {shlex.quote(remote_path)} | awk '{{print $1}}'", env_init_scripts
        )
        with self._coordinator.session(self._server_id, need_sftp=False) as (ssh, _sftp):
            result = ssh.run(command, timeout=30)
        digest = result.stdout.strip()
        if result.exit_code != 0 or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.exit_code}"
            raise ConfFlowClientError(f"control workflow config digest failed: {detail}")
        return digest.lower()

    def _apply_control_snapshot(self, snapshot: ControlSnapshot) -> ControlSnapshot:
        service = self._coordinator.service
        state = load_state(service, snapshot.run_id)
        if state is None:
            raise ConfFlowClientError(f"run {snapshot.run_id} has no durable control state")
        raw_revision = state.get("revision", -1)
        if type(raw_revision) is not int or raw_revision < -1:
            raise ConfFlowClientError(f"run {snapshot.run_id} has invalid durable control revision")
        current_revision = raw_revision
        current_state = str(state.get("state", "prepared"))
        effective = _monotonic_snapshot(
            snapshot,
            current_revision=current_revision,
            current_state=current_state,
        )
        updated = deepcopy(state)
        updated["revision"] = effective.revision
        updated["state"] = effective.state
        mapped = _task_status_for_control(effective.state)
        projected = []
        for task in service.load_tasks(snapshot.run_id):
            if _is_local_terminal(task.status) and mapped not in {TaskStatus.downloaded, TaskStatus.analyzed}:
                projected.append(task)
                continue
            projected.append(task.model_copy(update={"status": mapped}, deep=True))
        save_state_with_task_projection(service, snapshot.run_id, updated, projected)
        return effective

    def _snapshot_for_run(self, run_id: str, producer_snapshot: ControlSnapshot) -> RemoteRunSnapshot:
        record = self._coordinator.service.load_run(run_id)
        tasks = self._coordinator.service.load_tasks(run_id)
        kind = record.workflow_kind.value if record.workflow_kind is not None else None
        return RemoteRunSnapshot(
            record.run_id,
            record.server_id,
            record.remote_dir,
            kind,
            dict(record.status_summary),
            tuple(
                TaskSnapshot(
                    task.task_id,
                    task.status.value,
                    task.remote_job_dir,
                    task.remote_workflow_dir,
                    task.remote_state_path,
                    tuple(task.remote_result_paths),
                    task.remote_job_id,
                    task.error_message,
                )
                for task in tasks
            ),
            revision=producer_snapshot.revision,
            backend=CONTROL_BACKEND,
            producer_state=producer_snapshot.state,
        )

    def _mark_control_submitted(self, run_id: str, scheduler_type: str, scheduler_job_id: str) -> None:
        service = self._coordinator.service
        now = datetime.now()

        def mutation(tasks):
            return [
                task.model_copy(
                    update={
                        "submitted_at": task.submitted_at or now,
                        "scheduler_type": scheduler_type,
                        "remote_job_id": scheduler_job_id,
                    },
                    deep=True,
                )
                for task in tasks
            ]

        service.mutate_tasks(run_id, mutation)

    def _persist_control_worker_paths(self, run_id: str, worker_work_dir: str) -> None:
        """Project the producer-owned worker result root into JobDesk tasks."""
        state_path = posixpath.normpath(worker_work_dir)
        stats_path = posixpath.join(state_path, "workflow_stats.json")
        workflow_state_path = posixpath.join(state_path, ".workflow_state.json")
        log_path = posixpath.join(state_path, "confflow.log")

        def mutation(tasks):
            return [
                task.model_copy(
                    update={
                        "remote_workflow_dir": state_path,
                        "remote_state_path": workflow_state_path,
                        "remote_stats_path": stats_path,
                        "remote_log_path": log_path,
                    },
                    deep=True,
                )
                for task in tasks
            ]

        self._coordinator.service.mutate_tasks(run_id, mutation)

    def _launcher_scheduler_details(self, record: Any, overrides: dict[str, object] | None):
        server = None
        try:
            server = self._coordinator.server_config(record.server_id)
        except AttributeError:
            pass
        scheduler_type, resources, env_init_scripts = _launcher.launcher_scheduler_details(record, server, overrides)
        record.scheduler_type = scheduler_type
        record.resources = {
            "cpus": resources.cpus,
            "memory_mb": resources.memory_mb,
            "walltime_minutes": resources.walltime_minutes,
            "partition": resources.partition,
            "account": resources.account,
            "gpus": resources.gpus,
            "extra_directives": list(resources.extra_directives),
        }
        record.env_init_scripts = env_init_scripts
        self._coordinator.service.update_run(record)
        return scheduler_type, resources, env_init_scripts

    def _launcher_executable(self, record: Any, state: dict[str, object], tasks: Iterable[Any]) -> str:
        try:
            server = self._coordinator.server_config(record.server_id)
        except AttributeError:
            server = None
        return _launcher.launcher_executable(record, state, tasks, server)

    def _reconcile_control_dispatch(self, record: Any, state: dict[str, object]) -> dict[str, object]:
        dispatch_state = state.get("dispatch_state")
        if state.get("backend") != CONTROL_BACKEND or dispatch_state not in {"dispatching", "submitted"}:
            return state
        launcher = state.get("launcher")
        if not isinstance(launcher, dict):
            raise ConfFlowClientError("control launcher dispatch has no durable launcher provenance")
        metadata_path = launcher.get("metadata_path")
        if not isinstance(metadata_path, str) or not metadata_path.startswith("/"):
            raise ConfFlowClientError("control launcher dispatch has an invalid metadata locator")
        state_locator = _state_locator(state)
        if not state_locator:
            raise ConfFlowClientError("control launcher dispatch has no producer state locator")
        with self._control_session(record.run_id, state_locator, need_sftp=True) as (_transport, sftp, _ssh):
            if sftp is None:
                return self._record_unresolved_dispatch(state) if dispatch_state == "dispatching" else state
            try:
                if hasattr(sftp, "stat") and sftp.stat(metadata_path) is None:
                    return self._record_unresolved_dispatch(state) if dispatch_state == "dispatching" else state
                raw = sftp.read_file_bytes(metadata_path, max_bytes=65536)
            except (FileNotFoundError, KeyError):
                return self._record_unresolved_dispatch(state) if dispatch_state == "dispatching" else state
        return _reconciliation.reconcile_launcher_metadata(
            raw,
            run_id=record.run_id,
            state=state,
            launcher=launcher,
            state_locator=state_locator,
            load_producer_snapshot=lambda: self._load_control_status(record.run_id, state_locator),
            is_terminal=is_terminal_state,
            apply_snapshot=self._apply_control_snapshot,
            save=lambda updated: save_state(self._coordinator.service, record.run_id, updated),
            mark_submitted=lambda scheduler_type, scheduler_job_id: self._mark_control_submitted(
                record.run_id, scheduler_type, scheduler_job_id
            ),
            timestamp=_audit_timestamp(),
        )

    def _load_control_status(self, run_id: str, state_locator: str) -> ControlSnapshot:
        """Acquire a fresh SSH-only lease after marker metadata is read."""
        with self._control_session(run_id, state_locator, need_sftp=False) as (
            status_transport,
            _status_sftp,
            _status_ssh,
        ):
            return status_transport.status(run_id)

    def _record_unresolved_dispatch(self, state: dict[str, object]) -> dict[str, object]:
        return _reconciliation.record_unresolved_dispatch(
            state,
            maximum_attempts=_MAX_DISPATCH_RECONCILE_ATTEMPTS,
            timestamp=_audit_timestamp(),
            save=lambda updated: save_state(self._coordinator.service, str(updated["run_id"]), updated),
        )

    def _restart_control_worker(
        self,
        request: SubmitRequest,
        record: Any,
        state: dict[str, object],
        tasks: list[Any],
    ):
        """Restart only the producer worker after an audited premature exit."""
        if len(tasks) != 1:
            raise ConfFlowClientError("control worker recovery requires exactly one durable task")
        state_locator = _state_locator(state)
        if not state_locator:
            raise ConfFlowClientError("control worker recovery has no producer state locator")
        with self._control_session(request.run_id, state_locator, need_sftp=True) as (transport, sftp, ssh):
            snapshot = transport.status(request.run_id)
            if is_terminal_state(snapshot.state):
                self._apply_control_snapshot(snapshot)
                return self.attach(request.run_id), RunOperationOutcome(
                    records=[self._coordinator.service.load_run(request.run_id)]
                )
            if sftp is None:
                raise ConfFlowClientError("control worker recovery requires an SFTP session")
            scheduler_type, resources, env_init_scripts = self._launcher_scheduler_details(
                record, request.resource_overrides
            )
            previous_attempt = state.get("dispatch_attempt", 0)
            if type(previous_attempt) is not int or previous_attempt < 1:
                raise ConfFlowClientError("control worker recovery has invalid dispatch history")
            worker_executable = _state_worker_executable(state)
            handoff_path = _state_worker_handoff_path(state)
            launcher_dir, script_path, metadata_path, log_path, command, script = _launcher.recovery_launcher_plan(
                remote_dir=record.remote_dir,
                run_id=request.run_id,
                attempt=previous_attempt + 1,
                state_locator=state_locator,
                worker_executable=worker_executable,
                handoff_path=handoff_path,
                scheduler_type=scheduler_type,
                resources=resources,
                env_init_scripts=env_init_scripts,
            )
            _script_bytes, script_sha256, script_size = _launcher.stage_launcher_script(
                sftp,
                launcher_dir,
                script_path,
                request.run_id,
                script,
                prefix="jobdesk-control-recovery-",
            )
            launcher = {
                "content_schema": "jobdesk.confflow.launcher.v1",
                "run_id": request.run_id,
                "scheduler_type": scheduler_type,
                "script_path": script_path,
                "metadata_path": metadata_path,
                "log_path": log_path,
                "state_root": state_locator,
                "command": command,
                "script_sha256": script_sha256,
                "script_size": script_size,
                "recovery": "worker_restart",
            }
            dispatching = deepcopy(state)
            dispatching.update(
                {
                    "dispatch_state": "dispatching",
                    "dispatch_outcome": "pending",
                    "dispatch_attempt": previous_attempt + 1,
                    "dispatch_updated_at": _audit_timestamp(),
                    "reconcile_attempts": 0,
                    "scheduler_type": scheduler_type,
                    "launcher": launcher,
                }
            )
            dispatching.pop("recovery_state", None)
            save_state(self._coordinator.service, request.run_id, dispatching)
            try:
                scheduler_job_id = _launcher.submit_scheduler(
                    self._scheduler_factory,
                    scheduler_type=scheduler_type,
                    ssh=ssh,
                    script_path=script_path,
                    resources=resources,
                    on_rejected=lambda exc: save_state(
                        self._coordinator.service,
                        request.run_id,
                        _launcher.rejected_state(dispatching, error=str(exc), timestamp=_audit_timestamp()),
                    ),
                    on_unknown=lambda error: _record_unknown_dispatch(
                        self._coordinator.service, request.run_id, dispatching, error
                    ),
                    empty_job_error="scheduler adapter returned an empty recovery job id",
                )
            except SchedulerSubmitRejected as exc:
                raise ConfFlowClientError(str(exc)) from exc
            except (RemoteError, OSError, RuntimeError, TimeoutError) as exc:
                raise ConfFlowClientError(str(exc)) from exc
            except ValueError as exc:
                raise ConfFlowClientError(str(exc)) from exc
            submitted = deepcopy(dispatching)
            submitted.update(
                {
                    "dispatch_state": "submitted",
                    "dispatch_outcome": "accepted",
                    "dispatch_updated_at": _audit_timestamp(),
                    "scheduler_job_id": scheduler_job_id,
                }
            )
            submitted_launcher = dict(launcher)
            submitted_launcher["scheduler_job_id"] = scheduler_job_id
            submitted["launcher"] = submitted_launcher
            save_state(self._coordinator.service, request.run_id, submitted)
        self._mark_control_submitted(request.run_id, scheduler_type, scheduler_job_id)
        return self.attach(request.run_id), RunOperationOutcome(
            records=[self._coordinator.service.load_run(request.run_id)],
            submit_results=[
                SubmitResult(
                    batch_id=request.run_id,
                    submitted_task_count=1,
                    remote_batch_dir=record.remote_dir,
                    control_log_path=log_path,
                    control_nohup_log_path=log_path if scheduler_type == "nohup" else "",
                    control_script_path=script_path,
                    nohup_command=command if scheduler_type == "nohup" else "",
                    updated_task_ids=[tasks[0].task_id],
                )
            ],
        )


class SSHControlRunHandle:
    """Remote handle backed solely by the persisted control protocol choice."""

    def __init__(self, client: SSHConfFlowClient, reference: RemoteRunReference) -> None:
        self._client = client
        self._reference = reference

    @property
    def run_id(self) -> str:
        return self._reference.run_id

    def to_dict(self) -> dict[str, object]:
        return self._reference.to_dict()

    def status(self) -> RemoteRunSnapshot:
        with self._session() as (transport, _sftp, _ssh):
            snapshot = transport.status(self.run_id)
        effective = self._client._apply_control_snapshot(snapshot)
        return self._client._snapshot_for_run(self.run_id, effective)

    def snapshot(self) -> RemoteRunSnapshot:
        return self.status()

    def events(self, *, after: str | None = None) -> EventPage:
        state = load_state(self._client._coordinator.service, self.run_id)
        cursor = after if after is not None else _optional_string(state, "cursor")
        with self._session() as (transport, _sftp, _ssh):
            page = transport.events(self.run_id, after=cursor)
        _validate_event_page_cursor(page, cursor)
        self._client._apply_control_snapshot(page.snapshot)
        updated = load_state(self._client._coordinator.service, self.run_id)
        if updated is None:
            raise ConfFlowClientError(f"run {self.run_id} lost durable control state")
        if page.next_cursor is not None:
            updated["cursor"] = page.next_cursor
        save_state(self._client._coordinator.service, self.run_id, updated)
        return EventPage(
            events=tuple(
                {"cursor": event.cursor, "revision": event.revision, "type": event.event_type} for event in page.events
            ),
            next_cursor=page.next_cursor,
        )

    def cancel(self) -> RemoteRunSnapshot:
        with self._session() as (transport, _sftp, _ssh):
            snapshot = transport.cancel(self.run_id)
        effective = self._client._apply_control_snapshot(snapshot)
        return self._client._snapshot_for_run(self.run_id, effective)

    def artifacts(self) -> ArtifactManifest:
        manifest = self._artifacts()
        entries = _artifact_entries(manifest.artifacts)
        return ArtifactManifest(self.run_id, entries, source="control-manifest")

    def download(self, patterns: list[str]) -> RemoteRunSnapshot:
        outcome = self.download_outcome(patterns)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        return self.status()

    def resume(self, *, checkpoint: str | None = None) -> RemoteRunSnapshot:
        with self._session() as (transport, _sftp, _ssh):
            snapshot = transport.resume(self.run_id, checkpoint=checkpoint)
        effective = self._client._apply_control_snapshot(snapshot)
        return self._client._snapshot_for_run(self.run_id, effective)

    def refresh_outcome(self, patterns: list[str], *, download: bool) -> RunOperationOutcome:
        try:
            self.events()
            snapshot = self.status()
            if download and snapshot.producer_state in {"completed", "failed", "cancelled"}:
                return self.download_outcome(patterns)
            return RunOperationOutcome(
                records=[self._client._coordinator.service.load_run(self.run_id)],
                refresh_result=ControlRefreshResult(changed_count=1, warnings=[]),
            )
        except Exception as exc:
            return RunOperationOutcome(
                records=[self._client._coordinator.service.load_run(self.run_id)],
                errors=[str(exc)],
            )

    def download_outcome(self, patterns: list[str]) -> RunOperationOutcome:
        try:
            manifest = self._artifacts()
            self._client._apply_control_snapshot(manifest.snapshot)
            with self._session(need_sftp=True) as (transport, sftp, _ssh):
                if sftp is None:
                    raise ConfFlowClientError("control artifact download requires an SFTP session")
                records, failures = _download_control_artifacts(
                    self._client._coordinator.service,
                    self.run_id,
                    manifest.artifacts,
                    patterns,
                    sftp,
                )
            return RunOperationOutcome(
                records=[self._client._coordinator.service.load_run(self.run_id)],
                transfer_records=records,
                failures=failures,
                errors=[f"{task_id}: {message}" for task_id, message in failures],
            )
        except Exception as exc:
            return RunOperationOutcome(
                records=[self._client._coordinator.service.load_run(self.run_id)],
                errors=[str(exc)],
            )

    def _artifacts(self) -> ControlArtifactManifest:
        with self._session() as (transport, _sftp, _ssh):
            return transport.artifacts(self.run_id)

    @contextmanager
    def _session(self, *, need_sftp: bool = False):
        state = load_state(self._client._coordinator.service, self.run_id)
        if state is None:
            raise ConfFlowClientError(f"run {self.run_id} has no durable control state")
        locator = _state_locator(state)
        if not locator:
            raise ConfFlowClientError(f"run {self.run_id} has no producer state locator")
        with self._client._control_session(self.run_id, locator, need_sftp=need_sftp) as session:
            yield session


class ControlRefreshResult:
    def __init__(self, *, changed_count: int, warnings: list[str]) -> None:
        self.changed_count = changed_count
        self.warnings = warnings


def _control_admission_failure(exc: ConfFlowClientError) -> OperationFailure:
    """Return a stable structured failure for pre-prepare admission."""

    cause = exc.__cause__
    cause_code: str | None = None
    retryable = False
    if isinstance(cause, ControlProtocolError):
        cause_code = cause.code
        retryable = cause.retryable
    elif isinstance(cause, ConfFlowCapabilityPreflightError):
        cause_code = "capability_probe_failed"
        retryable = True
    elif isinstance(cause, ValueError):
        cause_code = "capability_contract_invalid"
    return OperationFailure.from_text(
        str(exc),
        stage="control_backend_admission",
        code="control_backend_admission_unavailable",
        retryable=retryable,
        cause_code=cause_code,
    )


def _reference_for(
    record: Any, provenance: dict[str, object] | None, state: dict[str, object] | None
) -> RemoteRunReference:
    identity = dict(provenance or {})
    if state is not None and state.get("backend") == CONTROL_BACKEND:
        identity = dict(_state_identity(state))
        return RemoteRunReference(
            record.server_id,
            record.run_id,
            str(state.get("protocol_schema", PROTOCOL_SCHEMA)),
            identity,
            backend=CONTROL_BACKEND,
            state_locator=_state_locator(state),
        )
    raise ConfFlowClientError(f"run {record.run_id} has no durable control state; legacy runs are retired")


def _snapshot(record: Any, tasks: list[Any]) -> RemoteRunSnapshot:
    kind = record.workflow_kind.value if record.workflow_kind is not None else None
    return RemoteRunSnapshot(
        record.run_id,
        record.server_id,
        record.remote_dir,
        kind,
        dict(record.status_summary),
        tuple(
            TaskSnapshot(
                t.task_id,
                t.status.value,
                t.remote_job_dir,
                t.remote_workflow_dir,
                t.remote_state_path,
                tuple(t.remote_result_paths),
                t.remote_job_id,
                t.error_message,
            )
            for t in tasks
        ),
    )


def _capability_from_state(state: dict[str, object]) -> ConfFlowCapabilities | None:
    raw = state.get("capability")
    if not isinstance(raw, dict):
        return None
    try:
        from jobdesk_app.core.confflow_preflight import parse_confflow_capabilities

        return parse_confflow_capabilities(json.dumps(raw))
    except ValueError:
        return None


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _monotonic_snapshot(snapshot: ControlSnapshot, *, current_revision: int, current_state: str) -> ControlSnapshot:
    if snapshot.revision < current_revision:
        return ControlSnapshot(snapshot.run_id, current_revision, current_state)
    if snapshot.revision == current_revision and snapshot.state != current_state:
        return ControlSnapshot(snapshot.run_id, current_revision, current_state)
    if is_terminal_state(current_state) and not is_terminal_state(snapshot.state):
        return ControlSnapshot(snapshot.run_id, snapshot.revision, current_state)
    return snapshot


def _task_status_for_control(state: str) -> TaskStatus:
    return {
        "prepared": TaskStatus.uploaded,
        "queued": TaskStatus.submitted,
        "running": TaskStatus.running,
        "paused": TaskStatus.submitted,
        "completed": TaskStatus.remote_completed,
        "failed": TaskStatus.failed,
        "cancelled": TaskStatus.cancelled,
    }[state]


def _is_local_terminal(status: TaskStatus) -> bool:
    return status in {TaskStatus.downloaded, TaskStatus.analyzed}


def _validate_event_page_cursor(page: ControlEventPage, cursor: str | None) -> None:
    if cursor is not None and not is_valid_cursor(cursor):
        raise ControlProtocolError("events", "invalid_request", "durable cursor is malformed")
    # Cursor values are opaque by contract; only the producer can interpret
    # them.  The parser already validates strict revision order within this
    # page, while the producer applies ``after`` when serving the request.
    previous = -1
    for event in page.events:
        if event.revision <= previous:
            raise ControlProtocolError("events", "invalid_request", "event revisions are not strictly increasing")
        if cursor is not None and event.cursor == cursor:
            raise ControlProtocolError("events", "invalid_request", "event page repeats the requested cursor")
        previous = event.revision
    if page.next_cursor is not None and page.events and page.next_cursor != page.events[-1].cursor:
        raise ControlProtocolError("events", "invalid_request", "next_cursor does not match the final event")


# Private compatibility aliases keep established focused imports stable while
# the handoff collaborator owns staging behavior.
_upload_control_worker_handoff = _handoff.upload_control_worker_handoff
_ensure_worker_remote_directories = _handoff.ensure_worker_remote_directories
_stage_remote_file = _handoff.stage_remote_file


__all__ = [
    "SSHConfFlowClient",
    "SSHControlRunHandle",
]
