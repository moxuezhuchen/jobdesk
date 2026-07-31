"""Stable application facade over JobDesk's existing ConfFlow lifecycle.

This module deliberately does not introduce a second remote protocol.  The
legacy adapter delegates to :class:`RunCoordinator`, which remains the owner
of SSH/SFTP leases, durable submit operations, monitoring, and downloads.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities

if TYPE_CHECKING:
    from jobdesk_app.core.manifest import TaskRecord
    from jobdesk_app.services.run_coordinator import RunCoordinator
    from jobdesk_app.services.run_repository import RunRecord


class ConfFlowClientError(RuntimeError):
    """Base error raised by the application-level ConfFlow facade."""


class UnsupportedRemoteRunOperation(ConfFlowClientError):
    """The current SSH lifecycle has no truthful implementation for an operation."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"remote run operation is not supported by the legacy adapter: {operation}")


@dataclass(frozen=True)
class SubmitRequest:
    """Request submission of an already-created durable JobDesk run."""

    run_id: str
    resource_overrides: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.resource_overrides is not None:
            object.__setattr__(self, "resource_overrides", deepcopy(self.resource_overrides))

    def to_dict(self) -> dict[str, object]:
        return {"run_id": self.run_id, "resource_overrides": deepcopy(self.resource_overrides)}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SubmitRequest:
        overrides = value.get("resource_overrides")
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("resource_overrides must be an object or null")
        return cls(run_id=_required_string(value, "run_id"), resource_overrides=overrides)


@dataclass(frozen=True)
class TaskSnapshot:
    """Serializable view of fields already persisted on ``TaskRecord``."""

    task_id: str
    status: str
    remote_job_dir: str
    remote_workflow_dir: str
    remote_state_path: str
    remote_result_paths: tuple[str, ...]
    remote_job_id: str | None
    error_message: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RemoteRunSnapshot:
    """Serializable local projection of one durable JobDesk run."""

    run_id: str
    server_id: str
    remote_dir: str
    workflow_kind: str | None
    status_summary: dict[str, object]
    tasks: tuple[TaskSnapshot, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "status_summary", deepcopy(self.status_summary))

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["status_summary"] = deepcopy(self.status_summary)
        return data


@dataclass(frozen=True)
class RemoteRunReference:
    """The complete serializable state needed to reattach a legacy handle.

    ``identity_snapshot`` is copied from the durable provenance record accepted
    during submit.  It deliberately contains data only: neither a coordinator
    nor an SSH/SFTP client can be serialized here.
    """

    server_id: str
    run_id: str
    accepted_protocol: str | None
    identity_snapshot: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity_snapshot", deepcopy(self.identity_snapshot))

    def to_dict(self) -> dict[str, object]:
        return {
            "server_id": self.server_id,
            "run_id": self.run_id,
            "accepted_protocol": self.accepted_protocol,
            "identity_snapshot": deepcopy(self.identity_snapshot),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RemoteRunReference:
        protocol = value.get("accepted_protocol")
        identity = value.get("identity_snapshot")
        if protocol is not None and not isinstance(protocol, str):
            raise ValueError("accepted_protocol must be a string or null")
        if not isinstance(identity, dict):
            raise ValueError("identity_snapshot must be an object")
        return cls(
            server_id=_required_string(value, "server_id"),
            run_id=_required_string(value, "run_id"),
            accepted_protocol=protocol,
            identity_snapshot=deepcopy(identity),
        )


@dataclass(frozen=True)
class EventPage:
    """Future-compatible event page shape; legacy SSH monitoring has no page API."""

    events: tuple[dict[str, object], ...]
    next_cursor: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ArtifactEntry:
    """Task-declared remote result paths, preserving the existing manifest contract."""

    task_id: str
    remote_paths: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactManifest:
    """Legacy projection of persisted declared result paths.

    It is intentionally not a replacement for ConfFlow's versioned
    ``output_manifest.json``.  Parsing that producer artifact remains in the
    existing result-download path.
    """

    run_id: str
    entries: tuple[ArtifactEntry, ...]
    source: str = "legacy-task-records"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@runtime_checkable
class RemoteRunHandle(Protocol):
    """A reattachable, synchronous handle; it never owns an SSH session."""

    @property
    def run_id(self) -> str: ...

    def status(self) -> RemoteRunSnapshot: ...

    def snapshot(self) -> RemoteRunSnapshot: ...

    def events(self, *, after: str | None = None) -> EventPage: ...

    def cancel(self) -> RemoteRunSnapshot: ...

    def artifacts(self) -> ArtifactManifest: ...

    def download(self, patterns: list[str]) -> RemoteRunSnapshot: ...

    def resume(self, *, checkpoint: str | None = None) -> RemoteRunSnapshot: ...

    def to_dict(self) -> dict[str, object]: ...


@runtime_checkable
class ConfFlowClient(Protocol):
    """Application boundary for capability probing and durable remote runs."""

    def probe(self, *, require_dag: bool = False) -> ConfFlowCapabilities: ...

    def attach(self, run_id: str) -> RemoteRunHandle: ...

    def submit(self, request: SubmitRequest) -> RemoteRunHandle: ...


class LegacyConfFlowClient:
    """Adapter for the pre-control-protocol JobDesk SSH lifecycle."""

    def __init__(self, coordinator: RunCoordinator, server_id: str) -> None:
        self._coordinator = coordinator
        self._server_id = server_id

    def probe(self, *, require_dag: bool = False) -> ConfFlowCapabilities:
        return self._coordinator.probe_capabilities(self._server_id, require_dag=require_dag)

    def probe_capabilities(self, server_id: str, *, require_dag: bool = False) -> ConfFlowCapabilities:
        """Compatibility alias for callers that still select a server per probe."""
        return self._coordinator.probe_capabilities(server_id, require_dag=require_dag)

    def attach(self, run_id: str) -> LegacyRemoteRunHandle:
        # Loading is deliberate: a handle is only valid for a durable run.
        record = self._coordinator.service.load_run(run_id)
        if record.server_id != self._server_id:
            raise ConfFlowClientError(f"run {run_id!r} belongs to server {record.server_id!r}, not {self._server_id!r}")
        reference = _reference_for(record, self._coordinator.service.repository.load_run_provenance(run_id))
        return LegacyRemoteRunHandle(self._coordinator, reference)

    def restore_handle(self, value: dict[str, object]) -> LegacyRemoteRunHandle:
        """Reattach serialized data after validating its durable identity snapshot."""
        saved = RemoteRunReference.from_dict(value)
        if saved.server_id != self._server_id:
            raise ConfFlowClientError(f"serialized handle belongs to server {saved.server_id!r}")
        handle = self.attach(saved.run_id)
        if handle.to_dict() != saved.to_dict():
            raise ConfFlowClientError("serialized handle identity no longer matches durable provenance")
        return handle

    def submit(self, request: SubmitRequest) -> LegacyRemoteRunHandle:
        outcome = self._coordinator.submit(request.run_id, resource_overrides=request.resource_overrides)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        return self.attach(request.run_id)


class LegacyRemoteRunHandle:
    """Reattachable handle that borrows coordinator resources per operation."""

    def __init__(self, coordinator: RunCoordinator, reference: RemoteRunReference) -> None:
        self._coordinator = coordinator
        self._reference = reference

    @property
    def run_id(self) -> str:
        return self._reference.run_id

    def to_dict(self) -> dict[str, object]:
        return self._reference.to_dict()

    def status(self) -> RemoteRunSnapshot:
        record = self._coordinator.service.load_run(self.run_id)
        tasks = self._coordinator.service.repository.load_tasks(self.run_id)
        return _snapshot(record, tasks)

    def snapshot(self) -> RemoteRunSnapshot:
        """Convenience alias retained for callers written before the facade contract."""
        return self.status()

    def events(self, *, after: str | None = None) -> EventPage:
        del after
        raise UnsupportedRemoteRunOperation("events")

    def cancel(self) -> RemoteRunSnapshot:
        outcome = self._coordinator.cancel(self.run_id)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        return self.status()

    def artifacts(self) -> ArtifactManifest:
        tasks = self._coordinator.service.repository.load_tasks(self.run_id)
        return ArtifactManifest(
            run_id=self.run_id,
            entries=tuple(ArtifactEntry(task_id=task.task_id, remote_paths=tuple(task.remote_result_paths)) for task in tasks),
        )

    def download(self, patterns: list[str]) -> RemoteRunSnapshot:
        outcome = self._coordinator.download(self.run_id, patterns)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        return self.snapshot()

    def resume(self, *, checkpoint: str | None = None) -> RemoteRunSnapshot:
        del checkpoint
        raise UnsupportedRemoteRunOperation("resume")


def _snapshot(record: RunRecord, tasks: list[TaskRecord]) -> RemoteRunSnapshot:
    workflow_kind = record.workflow_kind.value if record.workflow_kind is not None else None
    return RemoteRunSnapshot(
        run_id=record.run_id,
        server_id=record.server_id,
        remote_dir=record.remote_dir,
        workflow_kind=workflow_kind,
        status_summary=dict(record.status_summary),
        tasks=tuple(
            TaskSnapshot(
                task_id=task.task_id,
                status=task.status.value,
                remote_job_dir=task.remote_job_dir,
                remote_workflow_dir=task.remote_workflow_dir,
                remote_state_path=task.remote_state_path,
                remote_result_paths=tuple(task.remote_result_paths),
                remote_job_id=task.remote_job_id,
                error_message=task.error_message,
            )
            for task in tasks
        ),
    )


def _required_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result


def _reference_for(record: RunRecord, provenance: dict[str, object] | None) -> RemoteRunReference:
    identity = dict(provenance or {})
    capability = identity.get("capability")
    protocol = "confflow.capabilities.v4" if isinstance(capability, dict) and capability.get("schema_version") == 4 else None
    return RemoteRunReference(
        server_id=record.server_id,
        run_id=record.run_id,
        accepted_protocol=protocol,
        identity_snapshot=identity,
    )
