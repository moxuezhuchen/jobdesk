"""Pure launcher configuration decisions for the control backend."""

from __future__ import annotations

import hashlib
import posixpath
import tempfile
from collections.abc import Callable, Iterable
from copy import deepcopy
from pathlib import Path
from typing import Any

from jobdesk_app.remote.errors import RemoteError
from jobdesk_app.remote.scheduler import ResourceSpec, SchedulerAdapter, SchedulerSubmitRejected
from jobdesk_app.services.ssh_confflow_control import (
    build_control_launcher_script,
    build_control_worker_command,
)


def canonical_scheduler_type(value: str) -> str:
    normalized = (value or "nohup").lower()
    if normalized in {"slurm", "sbatch"}:
        return "slurm"
    if normalized in {"pbs", "torque", "qsub"}:
        return "pbs"
    if normalized == "nohup":
        return "nohup"
    raise ValueError(f"Unknown scheduler type: {value}")


def launcher_scheduler_details(record: Any, server: Any | None, overrides: dict[str, object] | None):
    """Resolve launcher resources without mutating a run or opening a session."""
    raw_resources = dict(getattr(record, "resources", {}) or {})
    scheduler_type = str(getattr(record, "scheduler_type", "nohup") or "nohup").lower()
    env_init_scripts = list(getattr(record, "env_init_scripts", []) or [])
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
    return canonical_scheduler_type(scheduler_type), ResourceSpec.from_dict(raw_resources), env_init_scripts


def launcher_executable(record: Any, state: dict[str, object], tasks: Iterable[Any], server: Any | None) -> str:
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


def launcher_paths(remote_dir: str, run_id: str, *, recovery_attempt: int | None = None) -> tuple[str, str, str, str]:
    """Return the byte-compatible durable paths for one launcher attempt."""
    launcher_dir = posixpath.join(remote_dir.rstrip("/"), ".jobdesk-control", "launcher")
    suffix = "" if recovery_attempt is None else f".recovery-{recovery_attempt}"
    return (
        launcher_dir,
        posixpath.join(launcher_dir, f"{run_id}{suffix}.sh"),
        posixpath.join(launcher_dir, f"{run_id}.json"),
        posixpath.join(launcher_dir, ".jobdesk_submit.log"),
    )


def stage_launcher_script(
    sftp: Any, launcher_dir: str, script_path: str, run_id: str, script: str, *, prefix: str
) -> tuple[bytes, str, int]:
    """Stage a launcher script and return its immutable provenance fields."""
    sftp.mkdir_p(launcher_dir)
    script_bytes = script.encode("utf-8")
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        local_script = Path(temp_dir) / f"{run_id}.sh"
        local_script.write_bytes(script_bytes)
        sftp.upload_file(local_script, script_path, overwrite=True)
    return script_bytes, hashlib.sha256(script_bytes).hexdigest(), len(script_bytes)


def recovery_launcher_plan(
    *,
    remote_dir: str,
    run_id: str,
    attempt: int,
    state_locator: str,
    worker_executable: str | None,
    handoff_path: str,
    scheduler_type: str,
    resources: ResourceSpec,
    env_init_scripts: list[str],
) -> tuple[str, str, str, str, str, str]:
    """Build the byte-compatible worker-only recovery launcher inputs."""
    launcher_dir, script_path, metadata_path, log_path = launcher_paths(remote_dir, run_id, recovery_attempt=attempt)
    worker_command = build_control_worker_command(worker_executable, state_locator, run_id, handoff_path)
    script = build_control_launcher_script(
        executable=None,
        worker_executable=worker_executable,
        handoff_path=handoff_path,
        state_root=state_locator,
        run_id=run_id,
        metadata_path=metadata_path,
        scheduler_type=scheduler_type,
        resources=resources,
        env_init_scripts=env_init_scripts,
        worker_only=True,
    )
    return launcher_dir, script_path, metadata_path, log_path, f"setsid --wait {worker_command}", script


def dispatching_state(
    state: dict[str, object], *, scheduler_type: str, launcher: dict[str, object], timestamp: str
) -> dict[str, object]:
    """Create the next durable dispatch intent before calling a scheduler."""
    previous_attempt = state.get("dispatch_attempt", 0)
    if type(previous_attempt) is not int or previous_attempt < 0:
        raise ValueError("control launcher has an invalid durable dispatch attempt")
    updated = deepcopy(state)
    updated.update(
        {
            "dispatch_state": "dispatching",
            "dispatch_outcome": "pending",
            "dispatch_attempt": previous_attempt + 1,
            "dispatch_updated_at": timestamp,
            "reconcile_attempts": 0,
            "scheduler_type": scheduler_type,
            "launcher": deepcopy(launcher),
        }
    )
    return updated


def submitted_state(dispatching: dict[str, object], *, scheduler_job_id: str, timestamp: str) -> dict[str, object]:
    """Commit a scheduler acceptance without changing launcher provenance."""
    if not isinstance(scheduler_job_id, str) or not scheduler_job_id or scheduler_job_id != scheduler_job_id.strip():
        raise ValueError("scheduler adapter returned an empty control launcher job id")
    launcher = dispatching.get("launcher")
    if not isinstance(launcher, dict):
        raise ValueError("control launcher dispatch has no durable launcher provenance")
    updated = deepcopy(dispatching)
    updated.update(
        {
            "dispatch_state": "submitted",
            "dispatch_outcome": "accepted",
            "dispatch_updated_at": timestamp,
            "scheduler_job_id": scheduler_job_id,
        }
    )
    updated_launcher = dict(launcher)
    updated_launcher["scheduler_job_id"] = scheduler_job_id
    updated["launcher"] = updated_launcher
    return updated


def rejected_state(dispatching: dict[str, object], *, error: str, timestamp: str) -> dict[str, object]:
    """Persist a definitive scheduler rejection that may be safely retried."""
    updated = deepcopy(dispatching)
    updated.update(
        {
            "dispatch_state": "failed",
            "dispatch_outcome": "rejected",
            "dispatch_error": error,
            "dispatch_updated_at": timestamp,
        }
    )
    return updated


def submit_scheduler(
    scheduler_factory: Callable[[str], SchedulerAdapter],
    *,
    scheduler_type: str,
    ssh: Any,
    script_path: str,
    resources: ResourceSpec,
    on_rejected: Callable[[SchedulerSubmitRejected], None],
    on_unknown: Callable[[object], None],
    empty_job_error: str,
) -> str:
    """Submit one durable launcher intent without translating its outcome."""
    scheduler = scheduler_factory(scheduler_type)
    try:
        scheduler_job_id = scheduler.submit(ssh, script_path, resources)
    except SchedulerSubmitRejected as exc:
        on_rejected(exc)
        raise
    except (RemoteError, OSError, RuntimeError, TimeoutError) as exc:
        on_unknown(exc)
        raise
    if not isinstance(scheduler_job_id, str) or not scheduler_job_id or scheduler_job_id != scheduler_job_id.strip():
        on_unknown(empty_job_error)
        raise ValueError(empty_job_error)
    return scheduler_job_id
