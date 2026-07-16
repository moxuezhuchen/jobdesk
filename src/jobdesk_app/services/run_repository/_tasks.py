"""Task mutation and merge operations."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING

from ._operations_types import MergeResult
from ._runs import _load_tasks, _replace_tasks

if TYPE_CHECKING:
    pass


def mutate_tasks(
    connection: sqlite3.Connection,
    run_id: str,
    mutation: Callable[[list], list],
) -> list:
    connection.execute("BEGIN IMMEDIATE")
    tasks = _load_tasks(connection, run_id)
    updated = mutation(tasks)
    _replace_tasks(connection, run_id, updated)
    return updated


def merge_tasks(
    connection: sqlite3.Connection,
    run_id: str,
    updates: list,
    *,
    expected_tasks: dict | None = None,
) -> MergeResult:
    """Merge task updates when the complete previously-read row still matches."""
    update_by_id = {task.task_id: task for task in updates}
    accepted_task_ids: set = set()
    connection.execute("BEGIN IMMEDIATE")
    current = _load_tasks(connection, run_id)
    merged: list = []
    for task in current:
        update = update_by_id.get(task.task_id)
        expected_matches = expected_tasks is None or (
            task.task_id in expected_tasks and task == expected_tasks[task.task_id]
        )
        if update is not None and expected_matches:
            merged.append(update.model_copy(deep=True))
            accepted_task_ids.add(task.task_id)
        else:
            merged.append(task)
    _replace_tasks(connection, run_id, merged)
    return MergeResult(tasks=merged, accepted_task_ids=accepted_task_ids)
