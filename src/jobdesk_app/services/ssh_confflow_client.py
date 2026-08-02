"""SSH-backed implementation of the application ConfFlow client contract."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import shlex
import stat
import tempfile
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jobdesk_app.application.confflow_client import (
    ArtifactEntry,
    ArtifactManifest,
    ConfFlowClientError,
    EventPage,
    RemoteRunReference,
    RemoteRunSnapshot,
    SubmitRequest,
    TaskSnapshot,
    UnsupportedRemoteRunOperation,
)
from jobdesk_app.core.confflow_executable import (
    build_executable_identity_probe,
    parse_executable_identity_probe,
)
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.remote.confflow_probe import ConfFlowCapabilityPreflightError, build_confflow_preflight_shell
from jobdesk_app.remote.scheduler import ResourceSpec, SchedulerAdapter, make_adapter
from jobdesk_app.services.confflow_control import (
    CONTROL_BACKEND,
    LEGACY_BACKEND,
    PROTOCOL_SCHEMA,
    ControlArtifact,
    ControlArtifactManifest,
    ControlEventPage,
    ControlProtocolError,
    ControlSnapshot,
    ControlTransport,
    ControlUnsupported,
    build_prepare_request,
    is_terminal_state,
    is_valid_cursor,
)
from jobdesk_app.services.confflow_control_state import load_state, save_state
from jobdesk_app.services.run_coordinator import RunCoordinator, RunOperationOutcome
from jobdesk_app.services.ssh_confflow_control import (
    SSHControlTransport,
    build_control_execute_command,
    build_control_launcher_script,
    resolve_control_state_root,
)


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
        self._selected_backend: str | None = None
        self._selected_state_locator: str | None = None
        self._selected_capability: ConfFlowCapabilities | None = None
        self._backend_mode = (backend_mode or os.environ.get("JOBDESK_CONFFLOW_BACKEND", "auto")).strip().lower()
        if self._backend_mode not in {"auto", LEGACY_BACKEND, CONTROL_BACKEND}:
            raise ValueError("JOBDESK_CONFFLOW_BACKEND must be auto, legacy, or control")

    def probe(self, *, require_dag: bool = False) -> ConfFlowCapabilities:
        try:
            capabilities = self._coordinator.probe_capabilities(self._server_id, require_dag=require_dag)
            if not isinstance(capabilities, ConfFlowCapabilities):
                self._selected_backend = LEGACY_BACKEND
                return capabilities
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

    def attach(self, run_id: str) -> SSHRemoteRunHandle | SSHControlRunHandle:
        record = self._coordinator.service.load_run(run_id)
        if record.server_id != self._server_id:
            raise ConfFlowClientError(
                f"run {run_id!r} belongs to server {record.server_id!r}, not {self._server_id!r}"
            )
        state = load_state(self._coordinator.service, run_id)
        if state is not None and state.get("backend") == CONTROL_BACKEND:
            state = self._reconcile_control_dispatch(record, state)
            return SSHControlRunHandle(self, _reference_for(record, self._provenance(run_id), state))
        return SSHRemoteRunHandle(
            self._coordinator,
            _reference_for(record, self._provenance(run_id), state),
        )

    def restore_handle(self, value: dict[str, object]):
        saved = RemoteRunReference.from_dict(value)
        if saved.server_id != self._server_id:
            raise ConfFlowClientError(f"serialized handle belongs to server {saved.server_id!r}")
        handle = self.attach(saved.run_id)
        if handle.to_dict() != saved.to_dict():
            raise ConfFlowClientError("serialized handle identity no longer matches durable provenance")
        return handle

    def submit(self, request: SubmitRequest):
        handle, outcome = self.submit_with_outcome(request)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        if handle is None:
            raise ConfFlowClientError("submit returned no handle")
        return handle

    def submit_with_outcome(self, request: SubmitRequest):
        record = self._coordinator.service.load_run(request.run_id)
        state = load_state(self._coordinator.service, request.run_id)
        if (
            state is None
            and self._selected_backend is None
            and record.workflow_kind is not None
            and record.workflow_kind.value in {"confflow", "dag"}
        ):
            try:
                self.probe(
                    require_dag=record.workflow_kind is not None and record.workflow_kind.value == "dag"
                )
            except ConfFlowClientError as exc:
                return None, SubmitResult(request.run_id, 0, record.remote_dir, errors=[str(exc)])
        backend = state.get("backend") if state is not None else self._selected_backend or LEGACY_BACKEND
        if backend == CONTROL_BACKEND:
            return self._submit_control(request, record, state)
        if state is None:
            save_state(
                self._coordinator.service,
                request.run_id,
                {
                    "content_schema": "jobdesk.confflow.backend.v1",
                    "run_id": request.run_id,
                    "backend": LEGACY_BACKEND,
                },
            )
        outcome = self._coordinator.submit(request.run_id, resource_overrides=request.resource_overrides)
        if outcome.errors:
            return None, outcome
        return self.attach(request.run_id), outcome

    def refresh_outcome(self, handle, patterns: list[str], *, download: bool):
        if isinstance(handle, SSHControlRunHandle):
            return handle.refresh_outcome(patterns, download=download)
        if download:
            return self._coordinator.refresh_and_download(handle.run_id, patterns)
        return self._coordinator.refresh(handle.run_id)

    def download_outcome(self, handle, patterns: list[str]):
        if isinstance(handle, SSHControlRunHandle):
            return handle.download_outcome(patterns)
        return self._coordinator.download(handle.run_id, patterns)

    def cancel_outcome(self, handle) -> RunOperationOutcome:
        if not isinstance(handle, SSHControlRunHandle):
            return self._coordinator.cancel(handle.run_id)
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
                    raise ConfFlowClientError(
                        "control launcher dispatch is unresolved; refusing duplicate submission"
                    )
            tasks = self._coordinator.service.repository.load_tasks(request.run_id)
            capability = self._selected_capability
            if capability is None and state is not None:
                capability = _capability_from_state(state)
            producer_identity = _state_identity(state)
            state_locator = _state_locator(state) or self._selected_state_locator
            if not state_locator:
                raise ConfFlowClientError("control backend has no durable producer state locator")
            if not producer_identity:
                if capability is None:
                    raise ConfFlowClientError("control backend has no accepted producer identity")
                producer_identity = self._measure_control_identity(capability)

            input_manifest = _input_manifest(record, tasks)
            input_manifest_bytes = _canonical_json(input_manifest)
            input_digest = hashlib.sha256(input_manifest_bytes).hexdigest()
            workflow_path = _workflow_config_path(tasks)
            workflow_digest = self._remote_digest(request.run_id, state_locator, workflow_path)
            request_frame = build_prepare_request(
                run_id=request.run_id,
                idempotency_key=_state_key(state, request.run_id),
                workflow_config={"path": PurePosixPath(workflow_path).name, "sha256": workflow_digest},
                input_manifest={"path": "input-manifest.json", "sha256": input_digest},
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
                    input_path=posixpath.join(record.remote_dir, ".jobdesk-control", "input-manifest.json"),
                )
                save_state(self._coordinator.service, request.run_id, state)

            with self._control_session(request.run_id, state_locator, need_sftp=True) as (transport, sftp, ssh):
                if sftp is None:
                    raise ConfFlowClientError("control launcher submission requires an SFTP session")
                if sftp is not None:
                    _upload_input_manifest(sftp, record.remote_dir, input_manifest_bytes)
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
                    input_path=posixpath.join(record.remote_dir, ".jobdesk-control", "input-manifest.json"),
                )
                save_state(self._coordinator.service, request.run_id, durable)
                self._apply_control_snapshot(prepared)

                scheduler_type, resources, env_init_scripts = self._launcher_scheduler_details(
                    record, request.resource_overrides
                )
                launcher_dir = posixpath.join(record.remote_dir.rstrip("/"), ".jobdesk-control", "launcher")
                script_path = posixpath.join(launcher_dir, f"{request.run_id}.sh")
                metadata_path = posixpath.join(launcher_dir, f"{request.run_id}.json")
                log_path = posixpath.join(launcher_dir, ".jobdesk_submit.log")
                command = build_control_execute_command(
                    self._launcher_executable(record, state, tasks), state_locator, request.run_id
                )
                script = build_control_launcher_script(
                    executable=self._launcher_executable(record, state, tasks),
                    state_root=state_locator,
                    run_id=request.run_id,
                    metadata_path=metadata_path,
                    scheduler_type=scheduler_type,
                    resources=resources,
                    env_init_scripts=env_init_scripts,
                )
                sftp.mkdir_p(launcher_dir)
                with tempfile.TemporaryDirectory(prefix="jobdesk-control-launcher-") as temp_dir:
                    local_script = Path(temp_dir) / f"{request.run_id}.sh"
                    script_bytes = script.encode("utf-8")
                    local_script.write_bytes(script_bytes)
                    sftp.upload_file(local_script, script_path, overwrite=True)
                launcher = {
                    "content_schema": "jobdesk.confflow.launcher.v1",
                    "run_id": request.run_id,
                    "scheduler_type": scheduler_type,
                    "script_path": script_path,
                    "metadata_path": metadata_path,
                    "log_path": log_path,
                    "state_root": state_locator,
                    "command": command,
                    "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
                    "script_size": len(script_bytes),
                }
                dispatching = deepcopy(durable)
                dispatching.update(
                    {
                        "dispatch_state": "dispatching",
                        "scheduler_type": scheduler_type,
                        "launcher": launcher,
                    }
                )
                save_state(self._coordinator.service, request.run_id, dispatching)

                scheduler = self._scheduler_factory(scheduler_type)
                scheduler_job_id = scheduler.submit(ssh, script_path, resources)
                if not isinstance(scheduler_job_id, str) or not scheduler_job_id:
                    raise ConfFlowClientError("scheduler adapter returned an empty control launcher job id")
                submitted = deepcopy(dispatching)
                submitted.update(
                    {
                        "dispatch_state": "submitted",
                        "scheduler_job_id": scheduler_job_id,
                    }
                )
                submitted_launcher = dict(launcher)
                submitted_launcher["scheduler_job_id"] = scheduler_job_id
                submitted["launcher"] = submitted_launcher
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
        if self._backend_mode == LEGACY_BACKEND:
            self._selected_backend = LEGACY_BACKEND
            return
        if self._backend_mode == CONTROL_BACKEND and self._control_capability_factory is not None:
            self._selected_state_locator = self._control_capability_factory()
            self._selected_backend = CONTROL_BACKEND
            return
        if self._backend_mode == CONTROL_BACKEND and self._control_transport_factory is not None:
            self._selected_backend = CONTROL_BACKEND
            self._selected_state_locator = "/tmp/confflow-control"
            return
        server = self._coordinator._server_lookup(self._server_id)  # noqa: SLF001 - negotiation uses the coordinator lease
        executable = str(getattr(server, "confflow_executable", "") or "")
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        try:
            with self._coordinator._clients(self._server_id, server, need_sftp=False) as (ssh, _sftp):  # noqa: SLF001
                if self._control_capability_factory is not None:
                    state_locator = self._control_capability_factory()
                else:
                    transport = SSHControlTransport(
                        ssh,
                        None,
                        executable=executable,
                        state_root="/tmp/confflow-control",
                        env_init_scripts=env_init_scripts,
                    )
                    transport.capabilities()
                    state_locator = resolve_control_state_root(ssh, env_init_scripts=env_init_scripts)
        except ControlUnsupported:
            if self._backend_mode == CONTROL_BACKEND:
                raise
            self._selected_backend = LEGACY_BACKEND
            return
        self._selected_backend = CONTROL_BACKEND
        self._selected_state_locator = state_locator

    @contextmanager
    def _control_session(self, run_id: str, state_locator: str, *, need_sftp: bool):
        if self._control_transport_factory is not None:
            transport = self._control_transport_factory(run_id, state_locator)
            yield transport, getattr(transport, "sftp", None), getattr(transport, "ssh", None)
            return
        server = self._coordinator._server_lookup(self._server_id)  # noqa: SLF001
        executable = str(getattr(server, "confflow_executable", "") or "")
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        with self._coordinator._clients(self._server_id, server, need_sftp=need_sftp) as (ssh, sftp):  # noqa: SLF001
            yield SSHControlTransport(
                ssh,
                sftp,
                executable=executable,
                state_root=state_locator,
                env_init_scripts=env_init_scripts,
            ), sftp, ssh

    def _provenance(self, run_id: str) -> dict[str, object] | None:
        return self._coordinator.service.repository.load_run_provenance(run_id)

    def _measure_control_identity(self, capabilities: ConfFlowCapabilities) -> dict[str, object]:
        executable = capabilities.executable
        if not isinstance(executable, dict) or not isinstance(executable.get("python"), str):
            raise ConfFlowClientError("control backend capability has no Python executable identity")
        python_executable = str(executable["python"])
        server = self._coordinator._server_lookup(self._server_id)  # noqa: SLF001
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        with self._coordinator._clients(self._server_id, server, need_sftp=False) as (ssh, _sftp):  # noqa: SLF001
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
        server = self._coordinator._server_lookup(self._server_id)  # noqa: SLF001
        env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
        command = build_confflow_preflight_shell(f"sha256sum -- {shlex.quote(remote_path)} | awk '{{print $1}}'", env_init_scripts)
        with self._coordinator._clients(self._server_id, server, need_sftp=False) as (ssh, _sftp):  # noqa: SLF001
            result = ssh.run(command, timeout=30)
        digest = result.stdout.strip()
        if result.exit_code != 0 or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.exit_code}"
            raise ConfFlowClientError(f"control workflow config digest failed: {detail}")
        return digest.lower()

    def _backend_for_state(self, run_id: str, state: dict[str, object] | None) -> str:
        if state is not None:
            backend = state.get("backend")
            if backend not in {LEGACY_BACKEND, CONTROL_BACKEND}:
                raise ConfFlowClientError(f"run {run_id} has invalid durable backend {backend!r}")
            return str(backend)
        return self._selected_backend or LEGACY_BACKEND

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
        save_state(service, snapshot.run_id, updated)
        self._project_control_state(snapshot.run_id, effective)
        return effective

    def _project_control_state(self, run_id: str, snapshot: ControlSnapshot) -> None:
        service = self._coordinator.service
        mapped = _task_status_for_control(snapshot.state)

        def mutation(tasks):
            result = []
            for task in tasks:
                if _is_local_terminal(task.status) and mapped not in {TaskStatus.downloaded, TaskStatus.analyzed}:
                    result.append(task)
                    continue
                result.append(task.model_copy(update={"status": mapped}, deep=True))
            return result

        service.repository.mutate_tasks(run_id, mutation)

    def _snapshot_for_run(self, run_id: str, producer_snapshot: ControlSnapshot) -> RemoteRunSnapshot:
        record = self._coordinator.service.load_run(run_id)
        tasks = self._coordinator.service.repository.load_tasks(run_id)
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

        service.repository.mutate_tasks(run_id, mutation)

    def _launcher_scheduler_details(self, record: Any, overrides: dict[str, object] | None):
        raw_resources = dict(getattr(record, "resources", {}) or {})
        scheduler_type = str(getattr(record, "scheduler_type", "nohup") or "nohup").lower()
        env_init_scripts = list(getattr(record, "env_init_scripts", []) or [])
        server = None
        try:
            server = self._coordinator._server_lookup(record.server_id)  # noqa: SLF001
        except AttributeError:
            pass
        if server is not None:
            scheduler_config = getattr(server, "scheduler", None)
            if not raw_resources:
                scheduler_type = str(getattr(scheduler_config, "type", scheduler_type) or scheduler_type).lower()
            if not env_init_scripts:
                env_init_scripts = list(getattr(server, "env_init_scripts", []) or [])
            if not raw_resources:
                raw_resources = {
                    "cpus": getattr(scheduler_config, "default_cpus", 1),
                    "memory_mb": getattr(scheduler_config, "default_memory_mb", 2048),
                    "walltime_minutes": getattr(scheduler_config, "default_walltime_minutes", 1440),
                    "partition": getattr(scheduler_config, "default_partition", ""),
                    "account": getattr(scheduler_config, "default_account", ""),
                    "gpus": getattr(scheduler_config, "default_gpus", 0),
                    "extra_directives": list(getattr(scheduler_config, "extra_directives", []) or []),
                }
        if overrides:
            raw_resources.update(overrides)
        scheduler_type = _canonical_scheduler_type(scheduler_type)
        resources = ResourceSpec.from_dict(raw_resources)
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
        self._coordinator.service.repository.update_run(record)
        return scheduler_type, resources, env_init_scripts

    def _launcher_executable(self, record: Any, state: dict[str, object], tasks: Iterable[Any]) -> str:
        try:
            server = self._coordinator._server_lookup(record.server_id)  # noqa: SLF001
        except AttributeError:
            server = None
        if server is not None:
            configured = str(getattr(server, "confflow_executable", "") or "")
            if configured:
                return configured
        capability = state.get("capability")
        if isinstance(capability, dict):
            executable = capability.get("executable")
            if isinstance(executable, dict):
                path = executable.get("path")
                if isinstance(path, str) and path:
                    return path
        for task in tasks:
            executable = getattr(task, "confflow_executable", "")
            if isinstance(executable, str) and executable:
                return executable
        return "confflow"

    def _reconcile_control_dispatch(self, record: Any, state: dict[str, object]) -> dict[str, object]:
        if state.get("backend") != CONTROL_BACKEND or state.get("dispatch_state") != "dispatching":
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
                return state
            try:
                if hasattr(sftp, "stat") and sftp.stat(metadata_path) is None:
                    return state
                raw = sftp.read_file_bytes(metadata_path, max_bytes=65536)
            except (FileNotFoundError, KeyError):
                return state
        try:
            marker = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfFlowClientError("control launcher metadata is malformed JSON") from exc
        if not isinstance(marker, dict):
            raise ConfFlowClientError("control launcher metadata must be a JSON object")
        if marker.get("content_schema") != "jobdesk.confflow.launcher.v1":
            raise ConfFlowClientError("control launcher metadata has an unsupported schema")
        if marker.get("run_id") != record.run_id:
            raise ConfFlowClientError("control launcher metadata run_id does not match durable state")
        if marker.get("state_root") != state_locator:
            raise ConfFlowClientError("control launcher metadata state root does not match durable state")
        if marker.get("command") != launcher.get("command"):
            raise ConfFlowClientError("control launcher metadata command does not match durable provenance")
        marker_scheduler = marker.get("scheduler_type")
        if _canonical_scheduler_type(str(marker_scheduler or "")) != _canonical_scheduler_type(
            str(state.get("scheduler_type", "nohup"))
        ):
            raise ConfFlowClientError("control launcher metadata scheduler type does not match durable state")
        scheduler_job_id = marker.get("scheduler_job_id") or marker.get("pid")
        if not isinstance(scheduler_job_id, str) or not scheduler_job_id:
            raise ConfFlowClientError("control launcher metadata has no scheduler job id or pid")
        updated = deepcopy(state)
        updated["dispatch_state"] = "submitted"
        updated["scheduler_job_id"] = scheduler_job_id
        updated_launcher = dict(launcher)
        updated_launcher["scheduler_job_id"] = scheduler_job_id
        updated["launcher"] = updated_launcher
        save_state(self._coordinator.service, record.run_id, updated)
        self._mark_control_submitted(record.run_id, _canonical_scheduler_type(str(marker_scheduler)), scheduler_job_id)
        return updated


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
                {"cursor": event.cursor, "revision": event.revision, "type": event.event_type}
                for event in page.events
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


class SSHRemoteRunHandle:
    def __init__(self, coordinator: RunCoordinator, reference: RemoteRunReference) -> None:
        self._coordinator = coordinator
        self._reference = reference

    @property
    def run_id(self) -> str:
        return self._reference.run_id

    def to_dict(self) -> dict[str, object]:
        return self._reference.to_dict()

    def status(self) -> RemoteRunSnapshot:
        record = self._coordinator.service.load_run(self.run_id)
        return _snapshot(record, self._coordinator.service.repository.load_tasks(self.run_id))

    def snapshot(self) -> RemoteRunSnapshot:
        return self.status()

    def events(self, *, after: str | None = None) -> EventPage:
        del after
        raise UnsupportedRemoteRunOperation("events")

    def cancel(self) -> RemoteRunSnapshot:
        outcome = self._coordinator.cancel(self.run_id)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        return self.status()

    def artifacts(self) -> ArtifactManifest:
        tasks = self._coordinator.service.repository.load_tasks(self.run_id)
        return ArtifactManifest(self.run_id, tuple(ArtifactEntry(t.task_id, tuple(t.remote_result_paths)) for t in tasks))

    def download(self, patterns: list[str]) -> RemoteRunSnapshot:
        outcome = self._coordinator.download(self.run_id, patterns)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        return self.status()

    def resume(self, *, checkpoint: str | None = None) -> RemoteRunSnapshot:
        del checkpoint
        raise UnsupportedRemoteRunOperation("resume")


LegacyConfFlowClient = SSHConfFlowClient
LegacyRemoteRunHandle = SSHRemoteRunHandle


def _reference_for(record: Any, provenance: dict[str, object] | None, state: dict[str, object] | None) -> RemoteRunReference:
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
    capability = identity.get("capability")
    protocol = "confflow.capabilities.v4" if isinstance(capability, dict) and capability.get("schema_version") == 4 else None
    return RemoteRunReference(record.server_id, record.run_id, protocol, identity)


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


def _state_identity(state: dict[str, object] | None) -> dict[str, object]:
    identity = state.get("producer_identity") if state else None
    if not isinstance(identity, dict):
        return {}
    return deepcopy(identity)


def _control_expected_identity(identity: dict[str, object]) -> dict[str, object]:
    digest = identity.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
        raise ConfFlowClientError("control producer identity has no SHA-256")
    expected: dict[str, object] = {"sha256": digest.lower()}
    realpath = identity.get("realpath")
    if isinstance(realpath, str) and realpath:
        expected["realpath"] = realpath
    device = identity.get("device")
    inode = identity.get("inode")
    if isinstance(device, int) and isinstance(inode, int):
        expected["device_inode"] = f"{device}:{inode}"
    return expected


def _state_locator(state: dict[str, object] | None) -> str | None:
    value = state.get("state_locator") if state else None
    return value if isinstance(value, str) and value else None


def _state_key(state: dict[str, object] | None, run_id: str) -> str:
    value = state.get("idempotency_key") if state else None
    return value if isinstance(value, str) and value else f"jobdesk.{run_id}"


def _optional_string(state: dict[str, object] | None, key: str) -> str | None:
    value = state.get(key) if state else None
    return value if isinstance(value, str) and value else None


def _canonical_scheduler_type(value: str) -> str:
    normalized = (value or "nohup").lower()
    if normalized in {"slurm", "sbatch"}:
        return "slurm"
    if normalized in {"pbs", "torque", "qsub"}:
        return "pbs"
    if normalized == "nohup":
        return "nohup"
    raise ValueError(f"Unknown scheduler type: {value}")


def _capability_payload(capabilities: ConfFlowCapabilities | None) -> dict[str, object] | None:
    if capabilities is None or not isinstance(capabilities.raw_payload, dict):
        return None
    return deepcopy(capabilities.raw_payload)


def _control_state(
    run_id: str,
    *,
    state_locator: str,
    capability: ConfFlowCapabilities | None,
    producer_identity: dict[str, object],
    request_frame: dict[str, object],
    snapshot: ControlSnapshot,
    previous: dict[str, object] | None,
    workflow_path: str,
    input_path: str,
) -> dict[str, object]:
    value: dict[str, object] = deepcopy(previous or {})
    value.update(
        {
            "content_schema": "jobdesk.confflow.backend.v1",
            "run_id": run_id,
            "backend": CONTROL_BACKEND,
            "protocol_schema": PROTOCOL_SCHEMA,
            "state_locator": state_locator,
            "idempotency_key": request_frame["idempotency_key"],
            "request_digest": request_frame["request_digest"],
            "request": deepcopy(request_frame),
            "capability": _capability_payload(capability) or value.get("capability", {}),
            "producer_identity": deepcopy(producer_identity),
            "workflow_config_path": workflow_path,
            "input_manifest_path": input_path,
            "revision": snapshot.revision,
            "state": snapshot.state,
        }
    )
    return value


def _input_manifest(record: Any, tasks: Iterable[Any]) -> dict[str, object]:
    return {
        "content_schema": "jobdesk.confflow.input-manifest.v1",
        "run_id": record.run_id,
        "inputs": [
            {
                "task_id": task.task_id,
                "paths": [task.remote_task_files, task.remote_config_path],
            }
            for task in tasks
        ],
    }


def _workflow_config_path(tasks: Iterable[Any]) -> str:
    paths = [task.remote_config_path for task in tasks if isinstance(task.remote_config_path, str) and task.remote_config_path]
    if not paths or not paths[0].startswith("/") or "\\" in paths[0]:
        raise ConfFlowClientError("control backend requires an absolute workflow config path")
    return posixpath.normpath(paths[0])


def _upload_input_manifest(sftp, remote_dir: str, content: bytes) -> None:
    target = posixpath.join(remote_dir.rstrip("/"), ".jobdesk-control", "input-manifest.json")
    sftp.mkdir_p(posixpath.dirname(target))
    with tempfile.TemporaryDirectory(prefix="jobdesk-control-input-") as temp_dir:
        local = Path(temp_dir) / "input-manifest.json"
        local.write_bytes(content)
        sftp.upload_file(local, target, overwrite=True)


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
    previous = -1
    for event in page.events:
        if event.revision <= previous:
            raise ControlProtocolError("events", "invalid_request", "event revisions are not strictly increasing")
        if cursor is not None and event.revision <= _cursor_revision(cursor):
            raise ControlProtocolError("events", "invalid_request", "event page repeats data before the cursor")
        previous = event.revision
    if page.next_cursor is not None and page.events and page.next_cursor != page.events[-1].cursor:
        raise ControlProtocolError("events", "invalid_request", "next_cursor does not match the final event")


def _cursor_revision(cursor: str) -> int:
    if not is_valid_cursor(cursor):
        raise ControlProtocolError("events", "invalid_request", "durable cursor is malformed")
    return int(cursor[1:])


def _artifact_entries(artifacts: Iterable[ControlArtifact]) -> tuple[ArtifactEntry, ...]:
    grouped: dict[str, list[str]] = {}
    for artifact in artifacts:
        grouped.setdefault(artifact.terminal, []).append(artifact.path)
    return tuple(ArtifactEntry(terminal, tuple(paths)) for terminal, paths in sorted(grouped.items()))


def _download_control_artifacts(service, run_id: str, artifacts: tuple[ControlArtifact, ...], patterns: list[str], sftp):
    tasks = service.repository.load_tasks(run_id)
    selected = [
        artifact for artifact in artifacts if not patterns or any(_pattern_matches(artifact.path, pattern) for pattern in patterns)
    ]
    if not selected:
        return [], []
    download_base = service.workspace_dir / "results" / run_id
    claimed: set[Path] = set()
    transfers = []
    failures: list[tuple[str, str]] = []
    for artifact in selected:
        try:
            _assert_safe_relative_artifact_path(artifact.path)
            work_dir = _work_dir_for_artifact(tasks, artifact.terminal)
            local_root = download_base / Path(work_dir).name
            remote_path = posixpath.join(work_dir.rstrip("/"), artifact.path)
            _assert_remote_not_symlink(sftp, work_dir, artifact.path)
            local_path = local_root / Path(*PurePosixPath(artifact.path).parts)
            if not local_path.is_relative_to(local_root):
                raise ValueError(f"artifact path escapes local results root: {artifact.path}")
            _assert_local_not_symlink(local_path)
            if local_path in claimed:
                raise ValueError(f"artifact target conflict: {artifact.path}")
            claimed.add(local_path)
            remote_stat = sftp.stat(remote_path)
            if remote_stat is None or int(remote_stat.st_size) != artifact.size:
                raise ValueError(f"artifact size mismatch: {artifact.path}")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="jobdesk-control-download-", dir=str(local_path.parent)) as temp_dir:
                staging = Path(temp_dir) / local_path.name
                transfer = sftp.download_file(remote_path, staging, overwrite=True, skip_if_same_size=False)
                transfers.append(transfer)
                if getattr(transfer.status, "value", transfer.status) == "failed":
                    raise ValueError(getattr(transfer, "reason", "artifact download failed"))
                if staging.stat().st_size != artifact.size or _sha256_file(staging) != artifact.sha256:
                    raise ValueError(f"artifact integrity mismatch: {artifact.path}")
                staging.replace(local_path)
        except Exception as exc:
            failures.append((artifact.terminal, f"{artifact.path}: {exc}"))
    if not failures and selected:
        selected_terminals = {artifact.terminal for artifact in selected}
        service.repository.mutate_tasks(
            run_id,
            lambda task_list: [
                task.model_copy(
                    update={"status": TaskStatus.downloaded, "downloaded_at": datetime.now()}
                    if _task_matches_terminal(task, selected_terminals)
                    else {},
                    deep=True,
                )
                for task in task_list
            ],
        )
    return transfers, failures


def _work_dir_for_artifact(tasks: Iterable[Any], terminal: str) -> str:
    candidates = [
        task.remote_workflow_dir
        for task in tasks
        if isinstance(task.remote_workflow_dir, str)
        and _is_safe_absolute_remote_path(task.remote_workflow_dir)
        and (task.task_id == terminal or PurePosixPath(task.remote_workflow_dir).name == terminal)
    ]
    if not candidates:
        all_work_dirs = [
            task.remote_workflow_dir
            for task in tasks
            if isinstance(task.remote_workflow_dir, str)
            and _is_safe_absolute_remote_path(task.remote_workflow_dir)
        ]
        if len(all_work_dirs) == 1:
            return all_work_dirs[0]
        raise ValueError(f"control artifact terminal has no unambiguous workflow directory: {terminal}")
    if len(set(candidates)) != 1:
        raise ValueError(f"control artifact terminal maps to multiple workflow directories: {terminal}")
    return candidates[0]


def _task_matches_terminal(task: Any, terminals: set[str]) -> bool:
    work_dir = getattr(task, "remote_workflow_dir", "")
    return task.task_id in terminals or (isinstance(work_dir, str) and PurePosixPath(work_dir).name in terminals)


def _pattern_matches(path: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(PurePosixPath(path).name, pattern)


def _assert_remote_not_symlink(sftp, work_dir: str, relative_path: str) -> None:
    current = "/"
    for part in (*PurePosixPath(work_dir).parts[1:], *PurePosixPath(relative_path).parts):
        current = posixpath.join(current, part)
        metadata = sftp.lstat(current)
        if metadata is None:
            raise ValueError(f"remote artifact path is missing: {current}")
        mode = getattr(metadata, "st_mode", None)
        if type(mode) is not int or stat.S_ISLNK(mode):
            raise ValueError(f"remote artifact path is a symlink or has invalid metadata: {current}")


def _assert_safe_relative_artifact_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or posixpath.normpath(path) != path
        or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
    ):
        raise ValueError(f"artifact path is unsafe: {path}")


def _is_safe_absolute_remote_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/")
        and "\\" not in path
        and posixpath.normpath(path) == path
        and all(part not in {"", ".", ".."} for part in PurePosixPath(path).parts[1:])
    )


def _assert_local_not_symlink(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError(f"local artifact target is a symlink: {component}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "LegacyConfFlowClient",
    "LegacyRemoteRunHandle",
    "SSHConfFlowClient",
    "SSHControlRunHandle",
    "SSHRemoteRunHandle",
]
