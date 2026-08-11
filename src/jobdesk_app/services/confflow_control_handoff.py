"""Standalone construction and staging helpers for a ConfFlow worker handoff."""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from jobdesk_app.application.confflow_client import ConfFlowClientError


@dataclass(frozen=True, slots=True)
class WorkerHandoffResult:
    """The immutable consumer-side view of one canonical worker handoff."""

    run_id: str
    task_id: str
    attempt_root: str
    handoff_path: str
    workflow_path: str
    input_path: str
    work_dir: str
    workflow_digest: str
    input_digest: str
    envelope: dict[str, object]
    envelope_bytes: bytes
    envelope_digest: str


def build_worker_handoff_result(
    *,
    run_id: str,
    task_id: str,
    attempt_root: str,
    handoff_path: str,
    workflow_path: str,
    workflow_digest: str,
    input_path: str,
    input_digest: str,
    work_dir: str,
) -> WorkerHandoffResult:
    """Build the exact canonical envelope and retain all staging provenance."""
    envelope = _worker_handoff(
        run_id=run_id,
        workflow_path=workflow_path,
        workflow_digest=workflow_digest,
        input_path=input_path,
        input_digest=input_digest,
        work_dir=work_dir,
        task_id=task_id,
    )
    envelope_bytes = _canonical_json(envelope)
    return WorkerHandoffResult(
        run_id=run_id,
        task_id=task_id,
        attempt_root=attempt_root,
        handoff_path=handoff_path,
        workflow_path=workflow_path,
        input_path=input_path,
        work_dir=work_dir,
        workflow_digest=workflow_digest,
        input_digest=input_digest,
        envelope=envelope,
        envelope_bytes=envelope_bytes,
        envelope_digest=hashlib.sha256(envelope_bytes).hexdigest(),
    )


def _worker_handoff(
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


def _worker_handoff_digest(value: dict[str, object], key: str) -> str:
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
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in digest)
    ):
        raise ConfFlowClientError("control state worker handoff has an invalid digest")
    return digest.lower()


def _worker_task_digest(value: dict[str, object]) -> str:
    return _worker_handoff_digest(value, "tasks")


def _validate_safe_component(value: str, label: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
            for char in value
        )
    ):
        raise ConfFlowClientError(f"{label} contains an unsafe path component")


def _is_safe_absolute_remote_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/")
        and "\\" not in path
        and posixpath.normpath(path) == path
        and all(part not in {"", ".", ".."} for part in PurePosixPath(path).parts[1:])
    )


def _assert_path_under(root: str, candidate: str, label: str) -> None:
    try:
        root_path = PurePosixPath(root)
        candidate_path = PurePosixPath(candidate)
    except (TypeError, ValueError) as exc:
        raise ConfFlowClientError(f"{label} path is malformed") from exc
    if (
        not _is_safe_absolute_remote_path(root)
        or not _is_safe_absolute_remote_path(candidate)
        or candidate_path == root_path
        or not candidate_path.is_relative_to(root_path)
    ):
        raise ConfFlowClientError(f"{label} must remain below the worker attempt root")


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _upload_control_worker_handoff(
    sftp,
    ssh,
    *,
    handoff: WorkerHandoffResult,
    remote_workflow_path: str,
    remote_input_path: str,
) -> None:
    """Stage and upload one verified producer worker-handoff envelope."""
    _assert_path_under(handoff.attempt_root, handoff.handoff_path, "worker handoff")
    _assert_path_under(handoff.attempt_root, handoff.workflow_path, "worker workflow")
    _assert_path_under(handoff.attempt_root, handoff.input_path, "worker input")
    if _worker_handoff_digest(handoff.envelope, "workflow_config") != handoff.workflow_digest:
        raise ConfFlowClientError("worker workflow digest changed before staging")
    if _worker_task_digest(handoff.envelope) != handoff.input_digest:
        raise ConfFlowClientError("worker input digest changed before staging")
    handoff_tasks = handoff.envelope.get("tasks")
    if not isinstance(handoff_tasks, list) or len(handoff_tasks) != 1 or not isinstance(handoff_tasks[0], dict):
        raise ConfFlowClientError("worker handoff must contain exactly one task")
    workflow_locator = handoff.envelope.get("workflow_config")
    if not isinstance(workflow_locator, dict) or workflow_locator.get("path") != handoff.workflow_path:
        raise ConfFlowClientError("worker handoff workflow path does not match staged path")
    if handoff_tasks[0].get("input_xyz") != handoff.input_path:
        raise ConfFlowClientError("worker handoff input path does not match staged path")
    worker_work_dir = handoff_tasks[0].get("work_dir")
    if not isinstance(worker_work_dir, str) or not worker_work_dir.startswith("/"):
        raise ConfFlowClientError("worker handoff work directory is invalid")
    _assert_path_under(handoff.attempt_root, worker_work_dir, "worker work directory")
    results_dir = posixpath.dirname(worker_work_dir)
    _ensure_worker_remote_directories(
        sftp,
        ssh,
        handoff.attempt_root,
        posixpath.dirname(handoff.handoff_path),
        posixpath.dirname(handoff.workflow_path),
        posixpath.dirname(handoff.input_path),
        results_dir,
    )

    # In-memory control transports intentionally have no remote byte source;
    # they exercise request/state/launcher provenance only. The real SSH/SFTP
    # path below always verifies source type, downloads, hashes, and reuploads.
    if ssh is not None:
        if not hasattr(sftp, "download_file") or not hasattr(sftp, "lstat"):
            raise ConfFlowClientError("control worker staging requires full SFTP file primitives")
        with tempfile.TemporaryDirectory(prefix="jobdesk-control-worker-") as temp_dir:
            temp = Path(temp_dir)
            _stage_remote_file(
                sftp,
                remote_workflow_path,
                temp / "workflow.yaml",
                handoff.workflow_path,
                handoff.workflow_digest,
            )
            _stage_remote_file(
                sftp,
                remote_input_path,
                temp / Path(handoff.input_path).name,
                handoff.input_path,
                handoff.input_digest,
            )
            sftp.upload_file(temp / "workflow.yaml", handoff.workflow_path, overwrite=True, skip_if_same_size=False)
            sftp.upload_file(
                temp / Path(handoff.input_path).name,
                handoff.input_path,
                overwrite=True,
                skip_if_same_size=False,
            )
        for target_path in (handoff.workflow_path, handoff.input_path):
            metadata = sftp.lstat(target_path)
            mode = getattr(metadata, "st_mode", None) if metadata is not None else None
            if type(mode) is not int or not stat.S_ISREG(mode):
                raise ConfFlowClientError(f"control worker staged target is not a regular file: {target_path}")

    with tempfile.TemporaryDirectory(prefix="jobdesk-control-worker-handoff-") as temp_dir:
        local = Path(temp_dir) / "worker-handoff.json"
        local.write_bytes(handoff.envelope_bytes)
        sftp.upload_file(local, handoff.handoff_path, overwrite=True, skip_if_same_size=False)
    if ssh is not None:
        metadata = sftp.lstat(handoff.handoff_path)
        mode = getattr(metadata, "st_mode", None) if metadata is not None else None
        if type(mode) is not int or not stat.S_ISREG(mode):
            raise ConfFlowClientError(
                f"control worker staged handoff is not a regular file: {handoff.handoff_path}"
            )
        mode_result = ssh.run(
            "chmod 600 -- "
            + " ".join(shlex.quote(path) for path in (handoff.workflow_path, handoff.input_path, handoff.handoff_path)),
            timeout=30,
        )
        if mode_result.exit_code != 0:
            detail = mode_result.stderr.strip() or mode_result.stdout.strip() or f"exit {mode_result.exit_code}"
            raise ConfFlowClientError(f"control worker file permission setup failed: {detail}")


def _ensure_worker_remote_directories(
    sftp,
    ssh,
    attempt_root: str,
    handoff_dir: str,
    workflow_dir: str,
    input_dir: str,
    results_dir: str,
) -> None:
    directories = tuple(dict.fromkeys((attempt_root, handoff_dir, workflow_dir, input_dir, results_dir)))
    for directory in directories:
        sftp.mkdir_p(directory)
    if ssh is None:
        return
    mode_result = ssh.run(
        "chmod 700 -- " + " ".join(shlex.quote(directory) for directory in directories),
        timeout=30,
    )
    if mode_result.exit_code != 0:
        detail = mode_result.stderr.strip() or mode_result.stdout.strip() or f"exit {mode_result.exit_code}"
        raise ConfFlowClientError(f"control worker private directory setup failed: {detail}")


def _stage_remote_file(sftp, remote_path: str, local_path: Path, target_path: str, expected_digest: str) -> None:
    metadata = sftp.lstat(remote_path)
    mode = getattr(metadata, "st_mode", None) if metadata is not None else None
    if type(mode) is not int or not stat.S_ISREG(mode):
        raise ConfFlowClientError(f"control worker source is not a regular file: {remote_path}")
    transfer = sftp.download_file(
        remote_path,
        local_path,
        overwrite=True,
        skip_if_same_size=False,
    )
    if getattr(getattr(transfer, "status", None), "value", getattr(transfer, "status", None)) == "failed":
        raise ConfFlowClientError(
            f"control worker source download failed: {remote_path}: {getattr(transfer, 'reason', '')}"
        )
    digest = _sha256_file(local_path)
    if digest != expected_digest:
        raise ConfFlowClientError(
            f"control worker source digest mismatch for {target_path}: expected {expected_digest}, got {digest}"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "WorkerHandoffResult",
    "build_worker_handoff_result",
]
