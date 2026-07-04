"""Parse and validate --job-id task=id overrides for run confirm-submitted."""
from __future__ import annotations

from collections.abc import Iterable


class JobIdOverridesError(ValueError):
    """Raised when --job-id arguments are malformed or reference unknown tasks."""


def parse_job_id_overrides(
    values: Iterable[str], selected_task_ids: Iterable[str]
) -> dict[str, str]:
    """Parse --job-id task=id arguments.

    Each value must contain "=" with non-empty task and id (after stripping).
    All task IDs must be present in selected_task_ids, with no duplicates.

    Returns a dict mapping task_id -> job_id.
    """
    selected = set(selected_task_ids)
    parsed: dict[str, str] = {}

    for value in values:
        if "=" not in value:
            raise JobIdOverridesError(f"job ID must use task=id syntax: {value!r}")
        task_id, job_id = value.split("=", 1)
        task_id = task_id.strip()
        job_id = job_id.strip()
        if not task_id or not job_id:
            raise JobIdOverridesError("job ID task and id must be non-empty")
        if task_id not in selected:
            raise JobIdOverridesError(f"job ID references unknown task: {task_id}")
        if task_id in parsed:
            raise JobIdOverridesError(f"duplicate job ID for task: {task_id}")
        parsed[task_id] = job_id

    return parsed
