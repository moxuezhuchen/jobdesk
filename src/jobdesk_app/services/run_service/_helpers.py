"""Helpers for run_service subpackage."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.core.run import RunPlan


def _declared_outputs(task: TaskRecord, patterns: list[str]) -> list[str]:
    """Determine declared output filenames from task and pattern list."""
    if task.remote_result_files:
        return list(task.remote_result_files)
    input_name = task.remote_task_files[0] if task.remote_task_files else task.task_id
    stem = input_name.rsplit(".", 1)[0] if "." in input_name else input_name
    results = []
    for pattern in patterns:
        if pattern.startswith("."):
            results.append(f"{stem}{pattern}")
        elif "*" in pattern:
            results.append(f"{stem}{pattern.lstrip('*')}")
        else:
            results.append(pattern)
    return results


def _safe_declared_result_path(value: str) -> PurePosixPath:
    """Validate and convert a declared result path to a safe PurePosixPath."""
    if "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe declared result path: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe declared result path: {value}")
    return path


def _tasks_from_plan(plan: RunPlan) -> list[TaskRecord]:
    """Convert a RunPlan into a list of TaskRecords."""
    return [
        TaskRecord(
            task_id=task.task_id,
            batch_id=plan.run_id,
            remote_job_dir=task.remote_job_dir,
            task_files=[],
            remote_task_files=[task.source_name, *[Path(path).name for path in task.supporting_paths]],
            remote_result_files=list(task.remote_result_files),
            execution_profile="quick_run",
            discovery_name="files",
            server_id=plan.spec.server_id,
            remote_work_dir=plan.spec.remote_dir,
            max_parallel=plan.spec.max_parallel,
            rendered_command=task.command,
            status=TaskStatus.uploaded,
        )
        for task in plan.tasks
    ]


def _status_summary(tasks: list[TaskRecord]) -> dict[str, int]:
    """Build a status-count summary dict from a task list."""
    summary: dict[str, int] = {}
    for task in tasks:
        summary[task.status.value] = summary.get(task.status.value, 0) + 1
    return summary


def _scheduler_type(scheduler) -> str:
    """Infer scheduler type string from a scheduler adapter instance."""
    from jobdesk_app.remote.scheduler import PBSAdapter, SlurmAdapter

    if isinstance(scheduler, SlurmAdapter):
        return "slurm"
    if isinstance(scheduler, PBSAdapter):
        return "pbs"
    return "nohup"
