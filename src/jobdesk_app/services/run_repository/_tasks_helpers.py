"""Task helpers shared across modules — validated_operation_task_ids only."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._operations_types import OperationRecord


def _validated_operation_task_ids(
    operation: "OperationRecord",
    current: list,
    expected_status,  # noqa: ARG001 — resolved at runtime via TaskStatus enum
) -> set | None:
    """Validate that an operation's task_ids match current task state."""
    from jobdesk_app.core.lifecycle import TaskStatus

    payload_task_ids = operation.payload.get("task_ids")
    if not isinstance(payload_task_ids, list) or not payload_task_ids:
        return None
    if not all(
        isinstance(task_id, str) and bool(task_id) for task_id in payload_task_ids
    ):
        return None
    selected = set(payload_task_ids)
    if len(selected) != len(payload_task_ids):
        return None
    current_by_id = {task.task_id: task for task in current}
    if any(
        task_id not in current_by_id
        or current_by_id[task_id].status != expected_status
        for task_id in selected
    ):
        return None
    return selected
