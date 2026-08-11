"""Application ports for durable local run projections."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from jobdesk_app.core.manifest import TaskRecord
    from jobdesk_app.services.run_repository import RunRecord


class RunProjectionStore(Protocol):
    """Persistence operations needed by the control-run projection."""

    def load_run(self, run_id: str) -> RunRecord:
        ...

    def load_tasks(self, run_id: str) -> list[TaskRecord]:
        ...

    def load_run_provenance(self, run_id: str) -> dict[str, object] | None:
        ...

    def mutate_tasks(
        self,
        run_id: str,
        mutation: Callable[[list[TaskRecord]], list[TaskRecord]],
    ) -> list[TaskRecord]:
        ...

    def update_run(self, record: RunRecord) -> None:
        ...


class ControlLauncher(Protocol):
    """Dispatch/reconcile one already-prepared control launch."""

    def dispatch(self, prepared_launch: object) -> object:
        ...

    def reconcile(self, run_id: str, state: Mapping[str, object]) -> dict[str, object]:
        ...


class WorkerHandoffStager(Protocol):
    """Build and stage a producer-bound worker handoff."""

    def stage(self, prepared_handoff: object, session: object) -> object:
        ...


class ControlArtifactDownloader(Protocol):
    """Transfer only producer-declared artifacts with integrity checks."""

    def download(
        self,
        run_id: str,
        artifacts: tuple[object, ...],
        patterns: list[str],
    ) -> tuple[list[object], list[tuple[str, str]]]:
        ...


__all__ = [
    "ControlArtifactDownloader",
    "ControlLauncher",
    "RunProjectionStore",
    "WorkerHandoffStager",
]
