"""Dependency-injected launcher for one already-prepared control request.

The boundary is deliberately narrower than ``SSHConfFlowClient``: the caller
supplies all producer-owned paths and state produced by prepare.  This object
only builds and dispatches the launcher, persists JobDesk-side dispatch state,
and reconciles a response that may have been lost after remote submission.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import tempfile
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from jobdesk_app.remote.scheduler import ResourceSpec, make_adapter
from jobdesk_app.services.ssh_confflow_control import (
    build_control_execute_command,
    build_control_launcher_script,
    build_control_worker_command,
)


class LauncherSFTP(Protocol):
    """The small SFTP surface needed after prepare has completed."""

    def mkdir_p(self, remote_dir: str) -> None: ...

    def upload_file(self, local_path: Path, remote_path: str, **kwargs: object) -> object: ...

    def stat(self, remote_path: str) -> object | None: ...

    def read_file_bytes(self, remote_path: str, max_bytes: int = 65536) -> bytes: ...


class LauncherScheduler(Protocol):
    """Scheduler port for one already-assembled launcher script."""

    def submit(self, ssh: object, script_path: str, resources: ResourceSpec) -> str: ...


class LauncherStateStore(Protocol):
    """JobDesk-owned persistence; this is not the producer control store."""

    def save(self, run_id: str, state: Mapping[str, object]) -> None: ...


SchedulerFactory = Callable[[str], LauncherScheduler]
LauncherScriptBuilder = Callable[..., str]


@dataclass(frozen=True)
class SchedulerResourceInput:
    """Record/server scheduler values before pure selection and validation."""

    scheduler_type: str = "nohup"
    resources: Mapping[str, object] = field(default_factory=dict)
    default_scheduler_type: str = "nohup"
    default_resources: Mapping[str, object] = field(default_factory=dict)
    overrides: Mapping[str, object] = field(default_factory=dict)
    env_init_scripts: tuple[str, ...] = ()
    default_env_init_scripts: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchedulerResourceSelection:
    scheduler_type: str
    resources: ResourceSpec
    env_init_scripts: tuple[str, ...]


def select_scheduler_resources(request: SchedulerResourceInput) -> SchedulerResourceSelection:
    """Resolve scheduler defaults without mutating a run record or producer state."""

    configured_resources = dict(request.resources)
    if configured_resources:
        scheduler_type = request.scheduler_type or "nohup"
        raw_resources = configured_resources
        env_init_scripts = request.env_init_scripts
    else:
        scheduler_type = request.default_scheduler_type or request.scheduler_type or "nohup"
        raw_resources = dict(request.default_resources)
        env_init_scripts = request.env_init_scripts or request.default_env_init_scripts
    raw_resources.update(request.overrides)
    return SchedulerResourceSelection(
        scheduler_type=_canonical_scheduler_type(scheduler_type),
        resources=ResourceSpec.from_dict(raw_resources),
        env_init_scripts=tuple(env_init_scripts),
    )


@dataclass(frozen=True)
class PreparedControlLaunch:
    """All inputs required to launch after the producer has been prepared."""

    run_id: str
    remote_dir: str
    state_root: str
    handoff_path: str
    producer_executable: str | None
    worker_executable: str | None
    scheduler: SchedulerResourceInput
    ssh: object
    prepared_state: Mapping[str, object]


@dataclass(frozen=True)
class ControlLaunchResult:
    scheduler_type: str
    scheduler_job_id: str
    script_path: str
    metadata_path: str
    log_path: str
    command: str
    state: dict[str, object]


class ControlLauncher:
    """Launch and reconcile one prepared control request.

    No control transport is accepted by this collaborator.  In particular,
    there is no path here that can call producer ``prepare`` or write its
    state; ``prepared_state`` is treated as an immutable input snapshot.
    """

    def __init__(
        self,
        *,
        sftp: LauncherSFTP,
        state_store: LauncherStateStore,
        scheduler_factory: SchedulerFactory = make_adapter,
        script_builder: LauncherScriptBuilder = build_control_launcher_script,
    ) -> None:
        self._sftp = sftp
        self._state_store = state_store
        self._scheduler_factory = scheduler_factory
        self._script_builder = script_builder

    def dispatch(self, launch: PreparedControlLaunch) -> ControlLaunchResult:
        """Upload and submit the launcher, leaving dispatching state on ambiguity."""

        selection = select_scheduler_resources(launch.scheduler)
        launcher_dir = posixpath.join(launch.remote_dir.rstrip("/"), ".jobdesk-control", "launcher")
        script_path = posixpath.join(launcher_dir, f"{launch.run_id}.sh")
        metadata_path = posixpath.join(launcher_dir, f"{launch.run_id}.json")
        log_path = posixpath.join(launcher_dir, ".jobdesk_submit.log")
        command = (
            f"{build_control_execute_command(launch.producer_executable, launch.state_root, launch.run_id)}"
            f" && setsid --wait {build_control_worker_command(launch.worker_executable, launch.state_root, launch.run_id, launch.handoff_path)}"
        )
        script = self._script_builder(
            executable=launch.producer_executable,
            worker_executable=launch.worker_executable,
            handoff_path=launch.handoff_path,
            state_root=launch.state_root,
            run_id=launch.run_id,
            metadata_path=metadata_path,
            scheduler_type=selection.scheduler_type,
            resources=selection.resources,
            env_init_scripts=selection.env_init_scripts,
        )
        script_bytes = script.encode("utf-8")
        self._sftp.mkdir_p(launcher_dir)
        with tempfile.TemporaryDirectory(prefix="jobdesk-control-launcher-") as temp_dir:
            local_script = Path(temp_dir) / f"{launch.run_id}.sh"
            local_script.write_bytes(script_bytes)
            self._sftp.upload_file(local_script, script_path, overwrite=True)

        launcher = {
            "content_schema": "jobdesk.confflow.launcher.v1",
            "run_id": launch.run_id,
            "scheduler_type": selection.scheduler_type,
            "script_path": script_path,
            "metadata_path": metadata_path,
            "log_path": log_path,
            "state_root": launch.state_root,
            "command": command,
            "script_sha256": hashlib.sha256(script_bytes).hexdigest(),
            "script_size": len(script_bytes),
        }
        dispatching = deepcopy(dict(launch.prepared_state))
        dispatching.update(
            {
                "dispatch_state": "dispatching",
                "scheduler_type": selection.scheduler_type,
                "launcher": launcher,
            }
        )
        self._state_store.save(launch.run_id, dispatching)

        scheduler = self._scheduler_factory(selection.scheduler_type)
        scheduler_job_id = scheduler.submit(launch.ssh, script_path, selection.resources)
        if not isinstance(scheduler_job_id, str) or not scheduler_job_id:
            raise ValueError("scheduler adapter returned an empty control launcher job id")
        submitted = deepcopy(dispatching)
        submitted.update({"dispatch_state": "submitted", "scheduler_job_id": scheduler_job_id})
        submitted_launcher = dict(launcher)
        submitted_launcher["scheduler_job_id"] = scheduler_job_id
        submitted["launcher"] = submitted_launcher
        self._state_store.save(launch.run_id, submitted)
        return ControlLaunchResult(
            selection.scheduler_type,
            scheduler_job_id,
            script_path,
            metadata_path,
            log_path,
            command,
            submitted,
        )

    def reconcile(self, run_id: str, state: Mapping[str, object]) -> dict[str, object]:
        """Reconcile a lost scheduler response, or preserve unresolved ambiguity."""

        current = deepcopy(dict(state))
        if current.get("dispatch_state") != "dispatching":
            return current
        launcher = current.get("launcher")
        if not isinstance(launcher, dict):
            raise ValueError("control launcher dispatch has no durable launcher provenance")
        metadata_path = launcher.get("metadata_path")
        if not isinstance(metadata_path, str) or not metadata_path.startswith("/"):
            raise ValueError("control launcher dispatch has an invalid metadata locator")
        try:
            if hasattr(self._sftp, "stat") and self._sftp.stat(metadata_path) is None:
                return current
            raw = self._sftp.read_file_bytes(metadata_path, max_bytes=65536)
        except (FileNotFoundError, KeyError):
            return current
        marker = _read_marker(raw)
        if not _validate_marker(marker, run_id, current, launcher):
            return current
        execution_state = marker.get("execution_state")
        execute_rc = marker.get("execute_rc")
        scheduler_job_id = marker.get("scheduler_job_id") or marker.get("pid")
        if not isinstance(scheduler_job_id, str) or not scheduler_job_id:
            raise ValueError("control launcher metadata has no scheduler job id or pid")
        updated_launcher = dict(launcher)
        updated_launcher["scheduler_job_id"] = scheduler_job_id
        if execution_state is not None:
            updated_launcher["execution_state"] = execution_state
            updated_launcher["execute_rc"] = execute_rc
        updated = deepcopy(current)
        updated["launcher"] = updated_launcher
        updated["dispatch_state"] = "failed" if execution_state == "failed" else "submitted"
        if execution_state != "failed":
            updated["scheduler_job_id"] = scheduler_job_id
        self._state_store.save(run_id, updated)
        return updated


def _read_marker(raw: bytes) -> dict[str, object]:
    try:
        marker = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("control launcher metadata is malformed JSON") from exc
    if not isinstance(marker, dict):
        raise ValueError("control launcher metadata must be a JSON object")
    return marker


def _validate_marker(
    marker: Mapping[str, object],
    run_id: str,
    state: Mapping[str, object],
    launcher: Mapping[str, object],
) -> bool:
    if marker.get("content_schema") != "jobdesk.confflow.launcher.v1":
        raise ValueError("control launcher metadata has an unsupported schema")
    if marker.get("run_id") != run_id:
        raise ValueError("control launcher metadata run_id does not match durable state")
    expected_root = state.get("state_locator")
    if not isinstance(expected_root, str) or not expected_root:
        raise ValueError("control launcher dispatch has no producer state locator")
    if marker.get("state_root") != expected_root:
        raise ValueError("control launcher metadata state root does not match durable state")
    if marker.get("command") != launcher.get("command"):
        raise ValueError("control launcher metadata command does not match durable provenance")
    if _canonical_scheduler_type(str(marker.get("scheduler_type") or "")) != _canonical_scheduler_type(
        str(state.get("scheduler_type", "nohup"))
    ):
        raise ValueError("control launcher metadata scheduler type does not match durable state")
    execution_state = marker.get("execution_state")
    if execution_state is not None and (
        not isinstance(execution_state, str)
        or execution_state not in {"started", "completed", "failed"}
    ):
        raise ValueError("control launcher metadata has an invalid execution state")
    worker_started = marker.get("worker_started")
    if execution_state is not None and worker_started is not None and type(worker_started) is not bool:
        raise ValueError("control launcher metadata has an invalid worker_started flag")
    worker_rc = marker.get("worker_rc")
    if execution_state is not None and worker_rc is not None and (type(worker_rc) is not int or worker_rc < 0):
        raise ValueError("control launcher metadata has an invalid worker return code")
    execute_rc = marker.get("execute_rc")
    if execution_state == "started" and not _marker_proves_handoff(marker, execution_state, execute_rc):
        return False
    if execution_state is not None and (type(execute_rc) is not int or execute_rc < 0):
        raise ValueError("control launcher metadata has no execution return code")
    if execution_state == "completed" and execute_rc != 0:
        raise ValueError("control launcher metadata has inconsistent completion status")
    if execution_state == "failed" and execute_rc == 0:
        raise ValueError("control launcher metadata has inconsistent failure status")
    return True


def _marker_proves_handoff(marker: Mapping[str, object], execution_state: object, execute_rc: object) -> bool:
    if execution_state == "started":
        return marker.get("worker_started") is True and execute_rc == 0
    return True


def _canonical_scheduler_type(value: str) -> str:
    scheduler = (value or "nohup").lower()
    if scheduler in {"slurm", "sbatch"}:
        return "slurm"
    if scheduler in {"pbs", "torque", "qsub"}:
        return "pbs"
    if scheduler == "nohup":
        return "nohup"
    raise ValueError(f"Unknown scheduler type: {value}")


__all__ = [
    "ControlLaunchResult",
    "ControlLauncher",
    "LauncherSFTP",
    "LauncherStateStore",
    "PreparedControlLaunch",
    "SchedulerResourceInput",
    "SchedulerResourceSelection",
    "select_scheduler_resources",
]
