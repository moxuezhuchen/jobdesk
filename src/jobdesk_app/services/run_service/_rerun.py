"""Rerun operations for run_service."""
from __future__ import annotations

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.services.run_repository import RunRepository


def prepare_rerun(service, run_id: str) -> int:
    """Reset all tasks to uploaded state for re-submission.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    def mutation(tasks: list[TaskRecord]) -> list[TaskRecord]:
        active = [
            task.task_id
            for task in tasks
            if task.status
            in {
                TaskStatus.submitting,
                TaskStatus.uncertain,
                TaskStatus.submitted,
                TaskStatus.running,
            }
        ]
        if active:
            raise ValueError(f"cannot rerun active remote tasks: {', '.join(active)}")
        for task in tasks:
            task.status = TaskStatus.uploaded
            task.submitted_at = None
            task.started_at = None
            task.completed_at = None
            task.downloaded_at = None
            task.analyzed_at = None
            task.remote_job_id = None
            task.scheduler_type = "nohup"
            task.error_message = None
        return tasks

    return len(service.repository.mutate_tasks(run_id, mutation))


def prepare_retry_failed(service, run_id: str) -> int:
    """Re-submit only the failed tasks.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    changed = 0

    def mutation(tasks: list[TaskRecord]) -> list[TaskRecord]:
        nonlocal changed
        for task in tasks:
            if task.status == TaskStatus.failed:
                task.status = TaskStatus.uploaded
                task.error_message = None
                changed += 1
        return tasks

    service.repository.mutate_tasks(run_id, mutation)
    return changed
