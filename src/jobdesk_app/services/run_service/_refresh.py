"""Refresh operations for run_service."""

from __future__ import annotations

from jobdesk_app.core.run import remote_run_dir
from jobdesk_app.core.status import StatusRefreshResult


def refresh_run(service, run_id: str, ssh) -> StatusRefreshResult:
    """Refresh run task statuses from the remote cluster.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    from jobdesk_app.remote.status_refresh import refresh_task_statuses

    record = service.load_run(run_id)
    tasks = service.repository.load_tasks(run_id)
    expected = {task.task_id: task.model_copy(deep=True) for task in tasks}
    result, updated = refresh_task_statuses(
        ssh,
        tasks,
        remote_run_dir(record.remote_dir, record.run_id),
        record.run_id,
    )
    merged = service.repository.merge_tasks(run_id, updated, expected_tasks=expected)
    original_by_id = {task.task_id: task for task in tasks}
    accepted_task_ids = merged.accepted_task_ids
    accepted_transitions = {
        task.task_id
        for task in updated
        if task.task_id in accepted_task_ids
        and task.task_id in original_by_id
        and original_by_id[task.task_id].status != task.status
    }
    result.snapshots = [snapshot for snapshot in result.snapshots if snapshot.task_id in accepted_task_ids]
    result.failures = [failure for failure in result.failures if failure.task_id in accepted_task_ids]
    result.changed_count = len(accepted_transitions)
    return result
