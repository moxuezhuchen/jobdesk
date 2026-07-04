"""Manifest state operations - atomic read-modify-write helpers."""
from __future__ import annotations

from pathlib import Path

from .lifecycle import TaskStatus
from .manifest import Manifest

_ACTIVE_REMOTE_STATUSES = {
    TaskStatus.submitting,
    TaskStatus.uncertain,
    TaskStatus.submitted,
    TaskStatus.running,
}


def reset_failed_to_uploaded(manifest_path: Path) -> int:
    tasks = Manifest.read(manifest_path)
    changed = 0
    for t in tasks:
        if t.status == TaskStatus.failed:
            t.status = TaskStatus.uploaded
            t.error_message = None
            changed += 1
    if changed:
        Manifest.write(manifest_path, tasks)
    return changed


def reset_all_to_uploaded(manifest_path: Path) -> int:
    tasks = Manifest.read(manifest_path)
    active = [t.task_id for t in tasks if t.status in _ACTIVE_REMOTE_STATUSES]
    if active:
        raise ValueError(f"cannot rerun active remote tasks: {', '.join(active)}")
    for t in tasks:
        t.status = TaskStatus.uploaded
        t.submitted_at = None
        t.started_at = None
        t.completed_at = None
        t.downloaded_at = None
        t.analyzed_at = None
        t.remote_job_id = None
        t.scheduler_type = "nohup"
        t.error_message = None
    Manifest.write(manifest_path, tasks)
    return len(tasks)
