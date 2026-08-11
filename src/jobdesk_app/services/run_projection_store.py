"""RunService adapter for the application run projection port."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .run_service import RunService

if TYPE_CHECKING:
    from jobdesk_app.core.manifest import TaskRecord
    from jobdesk_app.services.run_repository import RunRecord


class RunProjectionStoreAdapter:
    """Expose the projection subset of ``RunService`` without new logic."""

    def __init__(self, service: RunService) -> None:
        self._service = service

    def load_run(self, run_id: str) -> RunRecord:
        return self._service.load_run(run_id)

    def load_tasks(self, run_id: str) -> list[TaskRecord]:
        return self._service.load_tasks(run_id)

    def load_run_provenance(self, run_id: str) -> dict[str, object] | None:
        return self._service.load_run_provenance(run_id)

    def mutate_tasks(
        self,
        run_id: str,
        mutation: Callable[[list[TaskRecord]], list[TaskRecord]],
    ) -> list[TaskRecord]:
        return self._service.mutate_tasks(run_id, mutation)

    def update_run(self, record: RunRecord) -> None:
        self._service.update_run(record)


__all__ = ["RunProjectionStoreAdapter"]
