"""Cancel operations for run_service."""
from __future__ import annotations

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import remote_run_dir
from jobdesk_app.services.run_repository import RunRecord, RunRepository


def cancel_run(service, run_id: str, ssh) -> tuple[int, list[str]]:
    """Cancel remote jobs, recording cancellation only after the remote action succeeds.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    record = service.load_run(run_id)
    return _cancel_run_locked(
        service.repository,
        service.workspace_dir,
        record,
        run_id,
        ssh,
    )


def _cancel_run_locked(
    repository: RunRepository,
    workspace_dir,  # Path | NoneType - type omitted intentionally
    record: RunRecord,
    run_id: str,
    ssh,
) -> tuple[int, list[str]]:
    """Internal cancel implementation."""
    from jobdesk_app.remote.scheduler import make_adapter

    tasks = repository.load_tasks(run_id)
    expected = {task.task_id: task.model_copy(deep=True) for task in tasks}
    changed = 0
    errors: list[str] = []
    terminal = {
        TaskStatus.remote_completed,
        TaskStatus.downloaded,
        TaskStatus.analyzed,
        TaskStatus.failed,
        TaskStatus.cancelled,
    }
    cancelled_jobs: set[tuple[str, str]] = set()
    for task in tasks:
        if task.status in terminal:
            continue
        if task.status in {TaskStatus.local_ready, TaskStatus.uploaded}:
            task.status = TaskStatus.cancelled
            task.error_message = "cancelled before remote execution"
            changed += 1
            continue
        if not task.remote_job_id:
            errors.append(f"{task.task_id}: no remote job id available for cancellation")
            continue
        job_key = (task.scheduler_type or record.scheduler_type, task.remote_job_id)
        if job_key not in cancelled_jobs:
            try:
                make_adapter(job_key[0]).cancel(ssh, job_key[1])
                cancelled_jobs.add(job_key)
            except Exception as exc:
                errors.append(f"{task.task_id}: remote cancellation failed: {exc}")
                continue
        task.status = TaskStatus.cancelled
        task.error_message = "cancelled after remote termination request"
        changed += 1
    if not changed:
        return 0, errors
    merged = repository.merge_tasks(run_id, tasks, expected_tasks=expected)
    merged_by_id = {task.task_id: task for task in merged.tasks}
    rejected_cancellations = sorted(
        task.task_id
        for task in tasks
        if task.status == TaskStatus.cancelled
        and task.task_id not in merged.accepted_task_ids
        and (
            task.task_id not in merged_by_id
            or merged_by_id[task.task_id].status != TaskStatus.cancelled
        )
    )
    errors.extend(
        f"{task_id}: task state changed during cancellation; "
        "cancellation status was not committed"
        for task_id in rejected_cancellations
    )
    confirmed = sum(
        1
        for task in tasks
        if task.status == TaskStatus.cancelled
        and task.task_id in merged.accepted_task_ids
    )
    return confirmed, errors
