"""Stable application facade over JobDesk's existing ConfFlow lifecycle.

This module defines the control-protocol boundary used by JobDesk's ConfFlow
lifecycle.  The Phase F owner exception retired the legacy SSH backend.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Protocol, runtime_checkable

from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities


class ConfFlowClientError(RuntimeError):
    """Base error raised by the application-level ConfFlow facade."""


class UnsupportedRemoteRunOperation(ConfFlowClientError):
    """The current SSH lifecycle has no truthful implementation for an operation."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"remote run operation is not supported by the control protocol: {operation}")


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
    revision: int | None = None
    backend: str = "control"
    producer_state: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status_summary", deepcopy(self.status_summary))

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["status_summary"] = deepcopy(self.status_summary)
        return data


@dataclass(frozen=True)
class RemoteRunReference:
    """The complete serializable state needed to reattach a control handle.

    ``identity_snapshot`` is copied from the durable provenance record accepted
    during submit.  It deliberately contains data only: neither a coordinator
    nor an SSH/SFTP client can be serialized here.
    """

    server_id: str
    run_id: str
    accepted_protocol: str | None
    identity_snapshot: dict[str, object]
    backend: str = "control"
    state_locator: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity_snapshot", deepcopy(self.identity_snapshot))

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "server_id": self.server_id,
            "run_id": self.run_id,
            "accepted_protocol": self.accepted_protocol,
            "identity_snapshot": deepcopy(self.identity_snapshot),
        }
        data["backend"] = self.backend
        data["state_locator"] = self.state_locator
        return data

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RemoteRunReference:
        protocol = value.get("accepted_protocol")
        identity = value.get("identity_snapshot")
        if protocol is not None and not isinstance(protocol, str):
            raise ValueError("accepted_protocol must be a string or null")
        if not isinstance(identity, dict):
            raise ValueError("identity_snapshot must be an object")
        backend = value.get("backend")
        if backend != "control":
            raise ValueError("backend must be 'control'; legacy serialized handles are no longer supported")
        state_locator = value.get("state_locator")
        if state_locator is not None and (not isinstance(state_locator, str) or not state_locator):
            raise ValueError("state_locator must be a non-empty string or null")
        return cls(
            server_id=_required_string(value, "server_id"),
            run_id=_required_string(value, "run_id"),
            accepted_protocol=protocol,
            identity_snapshot=deepcopy(identity),
            backend=backend,
            state_locator=state_locator,
        )


@dataclass(frozen=True)
class EventPage:
    """Future-compatible event page shape for the control protocol."""

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
    """Projection of persisted declared result paths.

    It is intentionally not a replacement for ConfFlow's versioned
    ``output_manifest.json``.  Parsing that producer artifact remains in the
    existing result-download path.
    """

    run_id: str
    entries: tuple[ArtifactEntry, ...]
    source: str = "control-manifest"

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


def _required_string(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{key} must be a non-empty string")
    return result
