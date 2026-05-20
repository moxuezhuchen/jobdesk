"""Manifest state operations - atomic read-modify-write helpers."""
from __future__ import annotations

from pathlib import Path

from .lifecycle import TaskStatus
from .manifest import Manifest


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
