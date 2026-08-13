"""Application ports for durable local run projections."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from jobdesk_app.application.confflow_config_contract import (
        ConfigContractResult,
        RemoteIdentityCacheKey,
    )
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

    def persist_confflow_provenance(
        self,
        run_id: str,
        capability: dict[str, object],
        *,
        resolved_executable: str,
        resolved_realpath: str = "",
        executable_identity: dict[str, object] | None = None,
        config_contract: dict[str, object] | None = None,
        remote_identity: dict[str, object] | None = None,
    ) -> None:
        """Persist accepted producer identity before a control dispatch."""
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


class ConfigContractResolver(Protocol):
    """Resolve the producer-owned ConfFlow configuration contract."""

    def resolve(
        self,
        ssh: object,
        *,
        server_id: str,
        executable: str | None,
        capabilities: object,
        executable_identity: object,
        env_init_scripts: list[str] | None = None,
    ) -> ConfigContractResult:
        ...


class ConfigContractProvenance(Protocol):
    """Serializable identity selected for config-contract caching."""

    @property
    def remote_identity(self) -> RemoteIdentityCacheKey:
        ...


__all__ = [
    "ControlArtifactDownloader",
    "ControlLauncher",
    "ConfigContractProvenance",
    "ConfigContractResolver",
    "RunProjectionStore",
    "WorkerHandoffStager",
]
