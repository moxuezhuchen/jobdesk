"""Manifest state operations — atomic read-modify-write helpers.

These avoid repeating the pattern of Manifest.read → loop → Manifest.write
in service code. Each function performs a single logical state transition.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .lifecycle import TaskStatus
from .manifest import Manifest, TaskRecord


def mark_uploaded(manifest_path: Path, task_ids: set[str]) -> int:
    tasks = Manifest.read(manifest_path)
    changed = 0
    now = datetime.now()
    for t in tasks:
        if t.task_id in task_ids and t.status == TaskStatus.local_ready:
            t.status = TaskStatus.uploaded
            t.uploaded_at = now
            changed += 1
    if changed:
        Manifest.write(manifest_path, tasks)
    return changed


def mark_downloaded(manifest_path: Path, task_ids: set[str]) -> int:
    tasks = Manifest.read(manifest_path)
    changed = 0
    now = datetime.now()
    for t in tasks:
        if t.task_id in task_ids and t.status == TaskStatus.remote_completed:
            t.status = TaskStatus.downloaded
            t.downloaded_at = now
            changed += 1
    if changed:
        Manifest.write(manifest_path, tasks)
    return changed


def mark_failed(manifest_path: Path, task_ids: set[str], reason: str) -> int:
    tasks = Manifest.read(manifest_path)
    changed = 0
    for t in tasks:
        if t.task_id in task_ids:
            t.status = TaskStatus.failed
            t.error_message = reason
            changed += 1
    if changed:
        Manifest.write(manifest_path, tasks)
    return changed


def mark_cancelled(manifest_path: Path) -> int:
    """Mark all non-terminal tasks as failed with reason 'cancelled'."""
    tasks = Manifest.read(manifest_path)
    changed = 0
    cancellable = (TaskStatus.uploaded, TaskStatus.submitted, TaskStatus.running)
    for t in tasks:
        if t.status in cancellable:
            t.status = TaskStatus.failed
            t.error_message = "cancelled"
            changed += 1
    if changed:
        Manifest.write(manifest_path, tasks)
    return changed


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
    for t in tasks:
        t.status = TaskStatus.uploaded
        t.error_message = None
    Manifest.write(manifest_path, tasks)
    return len(tasks)


def status_summary(manifest_path: Path) -> dict[str, int]:
    tasks = Manifest.read(manifest_path)
    summary: dict[str, int] = {}
    for t in tasks:
        summary[t.status.value] = summary.get(t.status.value, 0) + 1
    return summary
