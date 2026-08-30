"""Producer-worker handoff construction and containment checks.

This module owns the byte-independent parts of staging a one-task ConfFlow
control-worker invocation.  It deliberately depends only on application value
types, so the SSH client can orchestrate it without owning these invariants.
"""

from __future__ import annotations

import hashlib
import posixpath
import shlex
import stat
import tempfile
from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

from jobdesk_app.application.confflow_client import ConfFlowClientError
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities


def is_safe_absolute_remote_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/")
        and "\\" not in path
        and posixpath.normpath(path) == path
        and all(part not in {"", ".", ".."} for part in PurePosixPath(path).parts[1:])
    )


def worker_handoff(
    *,
    run_id: str,
    workflow_path: str,
    workflow_digest: str,
    input_path: str,
    input_digest: str,
    work_dir: str,
    task_id: str,
) -> dict[str, object]:
    """Build the exact producer-owned one-task worker handoff envelope."""
    return {
        "content_schema": "confflow.control.worker-handoff.v1",
        "run_id": run_id,
        "workflow_config": {"path": workflow_path, "sha256": workflow_digest},
        "tasks": [
            {
                "task_id": task_id,
                "input_xyz": input_path,
                "work_dir": work_dir,
                "sha256": input_digest,
            }
        ],
    }


def worker_handoff_digest(value: dict[str, object], key: str) -> str:
    """Return a validated digest from a persisted worker handoff."""
    if key == "workflow_config":
        locator = value.get("workflow_config")
        if not isinstance(locator, dict):
            raise ConfFlowClientError("control state worker handoff has no workflow configuration")
    else:
        tasks = value.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
            raise ConfFlowClientError("control state worker handoff must contain exactly one task")
        locator = tasks[0]
    digest = locator.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
        raise ConfFlowClientError("control state worker handoff has an invalid digest")
    return digest.lower()


def worker_task_digest(value: dict[str, object]) -> str:
    return worker_handoff_digest(value, "tasks")


def state_worker_handoff(state: dict[str, object]) -> dict[str, object]:
    value = state.get("worker_handoff")
    if not isinstance(value, dict):
        raise ConfFlowClientError("control state has no producer worker-handoff envelope")
    return deepcopy(value)


def state_worker_handoff_path(state: dict[str, object]) -> str:
    value = state.get("input_manifest_path")
    if not isinstance(value, str) or not is_safe_absolute_remote_path(value):
        raise ConfFlowClientError("control state has no absolute worker-handoff path")
    return value


def state_worker_attempt_root(state: dict[str, object]) -> str:
    value = state.get("worker_attempt_root")
    if not isinstance(value, str):
        value = posixpath.dirname(posixpath.dirname(state_worker_handoff_path(state)))
    if not is_safe_absolute_remote_path(value):
        raise ConfFlowClientError("control state has an unsafe worker attempt root")
    return value


def state_worker_work_dir(state: dict[str, object]) -> str:
    value = state.get("worker_work_dir")
    if not isinstance(value, str) or not is_safe_absolute_remote_path(value):
        raise ConfFlowClientError("control state has no absolute worker work directory")
    return value


def state_worker_executable(state: dict[str, object] | None) -> str | None:
    value = state.get("worker_executable") if state is not None else None
    return value if isinstance(value, str) and value else None


def state_worker_input_path(handoff: dict[str, object]) -> str:
    tasks = handoff.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
        raise ConfFlowClientError("control state worker handoff must contain exactly one task")
    value = tasks[0].get("input_xyz")
    if not isinstance(value, str) or not is_safe_absolute_remote_path(value):
        raise ConfFlowClientError("control state worker handoff has no absolute input path")
    return value


def control_worker_enabled(capability: ConfFlowCapabilities | None, state: dict[str, object] | None) -> bool:
    if capability is not None:
        return capability.control_worker
    raw = state.get("capability") if state else None
    if not isinstance(raw, dict):
        return False
    values = raw.get("capabilities")
    return isinstance(values, dict) and values.get("control_worker") is True


def validate_safe_component(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in value)
    ):
        raise ConfFlowClientError(f"{label} contains an unsafe path component")


def worker_state_root(base_state_root: str, run_id: str) -> str:
    if not isinstance(base_state_root, str) or not base_state_root.startswith("/"):
        raise ConfFlowClientError("control backend state locator must be an absolute POSIX path")
    validate_safe_component(run_id, "run_id")
    return posixpath.join(posixpath.dirname(base_state_root.rstrip("/")), f"jobdesk-{run_id}", "state")


def worker_executable_for(executable: str) -> str:
    value = (executable or "confflow").strip()
    if value.startswith("/"):
        return posixpath.join(posixpath.dirname(value), "confflow-control-worker")
    return "confflow-control-worker"


def worker_work_dir_name(task: Any) -> str:
    value = PurePosixPath(str(getattr(task, "remote_workflow_dir", ""))).name
    if not value:
        value = f"{task.task_id}_confflow_work"
    # Unlike protocol identifiers, a staged scientific filename/work folder
    # may legitimately contain spaces.  It remains one POSIX component and is
    # later containment-checked below the private attempt root.
    if value in {".", ".."} or "\\" in value or "\x00" in value or "/" in value:
        raise ConfFlowClientError("worker work directory contains an unsafe path component")
    return value


def remote_input_path(task: Any) -> str:
    exact = getattr(task, "remote_source_path", "")
    if exact:
        if not is_safe_absolute_remote_path(exact) or not exact.lower().endswith(".xyz"):
            raise ConfFlowClientError("control worker exact input source path is unsafe")
        return exact

    # Backward compatibility for task payloads and TSV manifests written
    # before the exact source locator was persisted.
    names = getattr(task, "remote_task_files", None)
    base = getattr(task, "remote_work_dir", "")
    if not isinstance(names, list) or not names or not isinstance(base, str) or not base.startswith("/"):
        raise ConfFlowClientError("control worker task has no absolute remote input path")
    name = names[0]
    if (
        not isinstance(name, str)
        or PurePosixPath(name).name != name
        or name in {"", ".", ".."}
        or not name.lower().endswith(".xyz")
    ):
        raise ConfFlowClientError("control worker input filename is unsafe")
    return posixpath.join(base.rstrip("/"), name)


def workflow_config_path(tasks: Iterable[Any]) -> str:
    paths = [
        task.remote_config_path
        for task in tasks
        if isinstance(task.remote_config_path, str) and task.remote_config_path
    ]
    if not paths or not paths[0].startswith("/") or "\\" in paths[0]:
        raise ConfFlowClientError("control backend requires an absolute workflow config path")
    return posixpath.normpath(paths[0])


def assert_path_under(root: str, candidate: str, label: str) -> None:
    try:
        root_path = PurePosixPath(root)
        candidate_path = PurePosixPath(candidate)
    except (TypeError, ValueError) as exc:
        raise ConfFlowClientError(f"{label} path is malformed") from exc
    if (
        not is_safe_absolute_remote_path(root)
        or not is_safe_absolute_remote_path(candidate)
        or candidate_path == root_path
        or not candidate_path.is_relative_to(root_path)
    ):
        raise ConfFlowClientError(f"{label} must remain below the worker attempt root")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_control_worker_handoff(
    sftp,
    ssh,
    *,
    worker_handoff: dict[str, object],
    handoff_path: str,
    attempt_root: str,
    workflow_path: str,
    input_path: str,
    remote_workflow_path: str,
    remote_input_path: str,
    workflow_digest: str,
    input_digest: str,
    handoff_bytes: bytes,
) -> None:
    """Stage and upload one verified producer worker-handoff envelope."""
    assert_path_under(attempt_root, handoff_path, "worker handoff")
    assert_path_under(attempt_root, workflow_path, "worker workflow")
    assert_path_under(attempt_root, input_path, "worker input")
    if worker_handoff_digest(worker_handoff, "workflow_config") != workflow_digest:
        raise ConfFlowClientError("worker workflow digest changed before staging")
    if worker_task_digest(worker_handoff) != input_digest:
        raise ConfFlowClientError("worker input digest changed before staging")
    handoff_tasks = worker_handoff.get("tasks")
    if not isinstance(handoff_tasks, list) or len(handoff_tasks) != 1 or not isinstance(handoff_tasks[0], dict):
        raise ConfFlowClientError("worker handoff must contain exactly one task")
    workflow_locator = worker_handoff.get("workflow_config")
    if not isinstance(workflow_locator, dict) or workflow_locator.get("path") != workflow_path:
        raise ConfFlowClientError("worker handoff workflow path does not match staged path")
    if handoff_tasks[0].get("input_xyz") != input_path:
        raise ConfFlowClientError("worker handoff input path does not match staged path")
    worker_work_dir = handoff_tasks[0].get("work_dir")
    if not isinstance(worker_work_dir, str) or not worker_work_dir.startswith("/"):
        raise ConfFlowClientError("worker handoff work directory is invalid")
    assert_path_under(attempt_root, worker_work_dir, "worker work directory")
    ensure_worker_remote_directories(
        sftp,
        ssh,
        attempt_root,
        posixpath.dirname(handoff_path),
        posixpath.dirname(workflow_path),
        posixpath.dirname(input_path),
        posixpath.dirname(worker_work_dir),
    )
    if ssh is not None:
        if not hasattr(sftp, "download_file") or not hasattr(sftp, "lstat"):
            raise ConfFlowClientError("control worker staging requires full SFTP file primitives")
        with tempfile.TemporaryDirectory(prefix="jobdesk-control-worker-") as temp_dir:
            temp = Path(temp_dir)
            stage_remote_file(sftp, remote_workflow_path, temp / "workflow.yaml", workflow_path, workflow_digest)
            stage_remote_file(sftp, remote_input_path, temp / Path(input_path).name, input_path, input_digest)
            sftp.upload_file(temp / "workflow.yaml", workflow_path, overwrite=True, skip_if_same_size=False)
            sftp.upload_file(temp / Path(input_path).name, input_path, overwrite=True, skip_if_same_size=False)
        for target_path in (workflow_path, input_path):
            metadata = sftp.lstat(target_path)
            mode = getattr(metadata, "st_mode", None) if metadata is not None else None
            if type(mode) is not int or not stat.S_ISREG(mode):
                raise ConfFlowClientError(f"control worker staged target is not a regular file: {target_path}")
    with tempfile.TemporaryDirectory(prefix="jobdesk-control-worker-handoff-") as temp_dir:
        local = Path(temp_dir) / "worker-handoff.json"
        local.write_bytes(handoff_bytes)
        sftp.upload_file(local, handoff_path, overwrite=True, skip_if_same_size=False)
    if ssh is not None:
        metadata = sftp.lstat(handoff_path)
        mode = getattr(metadata, "st_mode", None) if metadata is not None else None
        if type(mode) is not int or not stat.S_ISREG(mode):
            raise ConfFlowClientError(f"control worker staged handoff is not a regular file: {handoff_path}")
        mode_result = ssh.run(
            "chmod 600 -- " + " ".join(shlex.quote(path) for path in (workflow_path, input_path, handoff_path)),
            timeout=30,
        )
        if mode_result.exit_code != 0:
            detail = mode_result.stderr.strip() or mode_result.stdout.strip() or f"exit {mode_result.exit_code}"
            raise ConfFlowClientError(f"control worker file permission setup failed: {detail}")


def ensure_worker_remote_directories(
    sftp, ssh, attempt_root: str, handoff_dir: str, workflow_dir: str, input_dir: str, results_dir: str
) -> None:
    directories = tuple(dict.fromkeys((attempt_root, handoff_dir, workflow_dir, input_dir, results_dir)))
    for directory in directories:
        sftp.mkdir_p(directory)
    if ssh is None:
        return
    mode_result = ssh.run("chmod 700 -- " + " ".join(shlex.quote(directory) for directory in directories), timeout=30)
    if mode_result.exit_code != 0:
        detail = mode_result.stderr.strip() or mode_result.stdout.strip() or f"exit {mode_result.exit_code}"
        raise ConfFlowClientError(f"control worker private directory setup failed: {detail}")


def stage_remote_file(sftp, remote_path: str, local_path: Path, target_path: str, expected_digest: str) -> None:
    metadata = sftp.lstat(remote_path)
    mode = getattr(metadata, "st_mode", None) if metadata is not None else None
    if type(mode) is not int or not stat.S_ISREG(mode):
        raise ConfFlowClientError(f"control worker source is not a regular file: {remote_path}")
    transfer = sftp.download_file(remote_path, local_path, overwrite=True, skip_if_same_size=False)
    if getattr(getattr(transfer, "status", None), "value", getattr(transfer, "status", None)) == "failed":
        raise ConfFlowClientError(
            f"control worker source download failed: {remote_path}: {getattr(transfer, 'reason', '')}"
        )
    digest = sha256_file(local_path)
    if digest != expected_digest:
        raise ConfFlowClientError(
            f"control worker source digest mismatch for {target_path}: expected {expected_digest}, got {digest}"
        )
