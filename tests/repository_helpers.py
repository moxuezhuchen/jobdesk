from __future__ import annotations

from collections.abc import Sequence

from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.services.run_repository import RunRepository


def replace_tasks_for_test(
    repository: RunRepository,
    run_id: str,
    tasks: Sequence[TaskRecord],
) -> list[TaskRecord]:
    """Replace test fixture state through the repository's transactional mutation API."""
    replacements = [task.model_copy(deep=True) for task in tasks]
    return repository.mutate_tasks(run_id, lambda _current: replacements)
