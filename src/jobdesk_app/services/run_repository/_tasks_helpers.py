"""Task read/write helpers used by multiple modules."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from jobdesk_app.core.lifecycle import TaskStatus

if TYPE_CHECKING:
    from jobdesk_app.core.manifest import TaskRecord
    from ._operations_types import OperationRecord


def _validated_operation_task_ids(
    operation: "OperationRecord",
    current: list,
    expected_status: TaskStatus,
) -> set | None:
    payload_task_ids = operation.payload.get("task_ids")
    if not isinstance(payload_task_ids, list) or not payload_task_ids:
        return None
    if not all(
        isinstance(task_id, str) and bool(task_id) for task_id in payload_task_ids
    ):
        return None
    typed_task_ids = cast("list[str]", payload_task_ids)
    selected = set(typed_task_ids)
    if len(selected) != len(typed_task_ids):
        return None
    current_by_id = {task.task_id: task for task in current}
    if any(
        task_id not in current_by_id
        or current_by_id[task_id].status != expected_status
        for task_id in selected
    ):
        return None
    return selected


def _load_tasks(connection, run_id: str) -> list:
    # Import at runtime to avoid circular dependency at module load time.
    from jobdesk_app.core.manifest import TaskRecord
    rows = connection.execute(
        "SELECT payload_json FROM tasks WHERE run_id = ? ORDER BY position",
        (run_id,),
    ).fetchall()
    return [TaskRecord.model_validate(json.loads(row["payload_json"])) for row in rows]


def _replace_tasks(connection, run_id: str, tasks: list) -> None:
    mismatched = [task.task_id for task in tasks if task.batch_id != run_id]
    if mismatched:
        raise ValueError(
            f"task batch_id does not match run_id {run_id!r}: "
            + ", ".join(mismatched)
        )
    connection.execute("DELETE FROM tasks WHERE run_id = ?", (run_id,))
    connection.executemany(
        """
        INSERT INTO tasks(run_id, task_id, status, position, payload_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                task.task_id,
                task.status.value,
                position,
                json.dumps(task.model_dump(mode="json"), ensure_ascii=False),
            )
            for position, task in enumerate(tasks)
        ],
    )
