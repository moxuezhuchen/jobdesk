"""Confirm and abandon operations for run_service."""

from __future__ import annotations

from collections.abc import Iterable


def confirm_submitted(
    service,
    run_id: str,
    task_ids: Iterable[str],
    remote_job_ids: dict[str, str] | None = None,
) -> list[str]:
    """Mark submitted tasks as confirmed with known job IDs.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    selected = _require_task_ids(task_ids)
    accepted, _tasks = service.repository.resolve_uncertain_tasks(
        run_id,
        selected,
        action="confirm",
        remote_job_ids=remote_job_ids,
    )
    return accepted


def abandon_submit(service, run_id: str, task_ids: Iterable[str]) -> list[str]:
    """Abandon previously submitted tasks, resetting them to uploaded state.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    selected = _require_task_ids(task_ids)
    accepted, _tasks = service.repository.resolve_uncertain_tasks(
        run_id,
        selected,
        action="abandon",
    )
    return accepted


def _require_task_ids(task_ids: Iterable[str]) -> list[str]:
    """Normalize and validate a collection of task IDs."""
    selected = list(dict.fromkeys(task_id for task_id in task_ids if task_id.strip()))
    if not selected:
        raise ValueError("selected task IDs required")
    return selected
