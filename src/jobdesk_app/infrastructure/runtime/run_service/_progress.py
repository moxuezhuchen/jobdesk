"""Strict live-progress synchronization for active workflow tasks."""

from __future__ import annotations

import errno
import json
from pathlib import Path, PurePosixPath
from uuid import uuid4

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.transfer import TransferStatus

_ACTIVE_PROGRESS_STATUSES = {
    TaskStatus.submitting,
    TaskStatus.uncertain,
    TaskStatus.submitted,
    TaskStatus.running,
}
_MAX_PROGRESS_FILE_BYTES = 2 * 1024 * 1024


def sync_progress(service, run_id: str, sftp):
    """Fetch only declared state/statistics JSON for active tasks.

    Missing remote files are normal while ConfFlow is starting. Every other
    failure is returned to the caller and an existing local checkpoint is
    preserved until a complete, valid JSON object has been downloaded.
    """
    service.repository.load_run(run_id)
    tasks = service.repository.load_tasks(run_id)
    # Checkpoints are transient run metadata, not downloaded results.  Keep
    # them under the managed run directory so two submissions with matching
    # molecule names cannot overwrite each other's live state.
    progress_base = service._run_dir(run_id) / "progress"
    records = []
    failures: list[tuple[str, str]] = []

    for task in tasks:
        if task.status not in _ACTIVE_PROGRESS_STATUSES:
            continue
        for remote_path in dict.fromkeys((task.remote_state_path, task.remote_stats_path)):
            if not remote_path:
                continue
            try:
                local_path = _progress_local_path(task.remote_work_dir, remote_path, progress_base)
                remote_stat = sftp.stat(remote_path)
                if remote_stat is None:
                    continue
                remote_size = getattr(remote_stat, "st_size", None)
                if remote_size is not None and remote_size > _MAX_PROGRESS_FILE_BYTES:
                    raise ValueError(f"declared progress file exceeds {_MAX_PROGRESS_FILE_BYTES} bytes: {remote_path}")
                local_path.parent.mkdir(parents=True, exist_ok=True)
                staging_path = local_path.with_name(f".{local_path.name}.{uuid4().hex}.progress")
                try:
                    transfer = sftp.download_file(
                        remote_path,
                        staging_path,
                        overwrite=True,
                        skip_if_same_size=False,
                    )
                    transfer.local_path = str(local_path)
                    if transfer.status == TransferStatus.failed:
                        if sftp.stat(remote_path) is None:
                            continue
                        raise OSError(transfer.reason or f"progress download failed: {remote_path}")
                    _validate_progress_json(staging_path, remote_path)
                    staging_path.replace(local_path)
                    records.append(transfer)
                finally:
                    staging_path.unlink(missing_ok=True)
            except OSError as exc:
                if _is_missing_remote_error(exc):
                    continue
                failures.append((task.task_id, f"{remote_path}: {exc}"))
            except Exception as exc:
                failures.append((task.task_id, f"{remote_path}: {exc}"))

    return records, failures


def _progress_local_path(remote_root: str, remote_path: str, progress_base: Path) -> Path:
    """Map a declared path below one run's owned root into its progress cache."""
    if "\\" in remote_root or "\\" in remote_path or "\x00" in remote_path:
        raise ValueError(f"unsafe declared progress path: {remote_path}")
    root = PurePosixPath(remote_root)
    candidate = PurePosixPath(remote_path)
    if not root.is_absolute() or not candidate.is_absolute():
        raise ValueError(f"declared progress path must be absolute: {remote_path}")
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"declared progress path escapes remote work dir: {remote_path}") from exc
    if not relative.parts or ".." in relative.parts:
        raise ValueError(f"unsafe declared progress path: {remote_path}")
    progress_base = progress_base.resolve()
    local_path = progress_base.joinpath(*relative.parts)
    if not local_path.resolve().is_relative_to(progress_base):
        raise ValueError(f"declared progress path escapes local dir: {remote_path}")
    return local_path


def _validate_progress_json(path: Path, remote_path: str) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed progress JSON at {remote_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"malformed progress JSON at {remote_path}: expected an object")


def _is_missing_remote_error(exc: OSError) -> bool:
    return isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) in {
        errno.ENOENT,
        errno.ENOTDIR,
    }
