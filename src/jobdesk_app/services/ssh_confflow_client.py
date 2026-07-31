"""SSH-backed implementation of the application ConfFlow client contract."""

from __future__ import annotations

from typing import Any

from jobdesk_app.application.confflow_client import (
    ArtifactEntry,
    ArtifactManifest,
    ConfFlowClientError,
    EventPage,
    RemoteRunReference,
    RemoteRunSnapshot,
    SubmitRequest,
    TaskSnapshot,
    UnsupportedRemoteRunOperation,
)
from jobdesk_app.remote.confflow_probe import ConfFlowCapabilityPreflightError
from jobdesk_app.services.run_coordinator import RunCoordinator


class SSHConfFlowClient:
    """Adapt the existing coordinator without retaining a transport lease."""

    def __init__(self, coordinator: RunCoordinator, server_id: str) -> None:
        self._coordinator = coordinator
        self._server_id = server_id

    def probe(self, *, require_dag: bool = False):
        try:
            return self._coordinator.probe_capabilities(self._server_id, require_dag=require_dag)
        except ConfFlowCapabilityPreflightError as exc:
            raise ConfFlowClientError(str(exc)) from exc

    def probe_capabilities(self, server_id: str, *, require_dag: bool = False):
        try:
            return self._coordinator.probe_capabilities(server_id, require_dag=require_dag)
        except ConfFlowCapabilityPreflightError as exc:
            raise ConfFlowClientError(str(exc)) from exc

    def attach(self, run_id: str) -> SSHRemoteRunHandle:
        record = self._coordinator.service.load_run(run_id)
        if record.server_id != self._server_id:
            raise ConfFlowClientError(f"run {run_id!r} belongs to server {record.server_id!r}, not {self._server_id!r}")
        return SSHRemoteRunHandle(self._coordinator, _reference_for(record, self._coordinator.service.repository.load_run_provenance(run_id)))

    def restore_handle(self, value: dict[str, object]) -> SSHRemoteRunHandle:
        saved = RemoteRunReference.from_dict(value)
        if saved.server_id != self._server_id:
            raise ConfFlowClientError(f"serialized handle belongs to server {saved.server_id!r}")
        handle = self.attach(saved.run_id)
        if handle.to_dict() != saved.to_dict():
            raise ConfFlowClientError("serialized handle identity no longer matches durable provenance")
        return handle

    def submit(self, request: SubmitRequest) -> SSHRemoteRunHandle:
        handle, outcome = self.submit_with_outcome(request)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        if handle is None:
            raise ConfFlowClientError("submit returned no handle")
        return handle

    def submit_with_outcome(self, request: SubmitRequest) -> tuple[SSHRemoteRunHandle | None, Any]:
        """Preserve legacy GUI outcome details while submitting exactly once."""
        outcome = self._coordinator.submit(request.run_id, resource_overrides=request.resource_overrides)
        if outcome.errors:
            return None, outcome
        return self.attach(request.run_id), outcome

    def refresh_outcome(self, handle: SSHRemoteRunHandle, patterns: list[str], *, download: bool):
        """Compatibility bridge retaining the existing GUI outcome shape."""
        if download:
            return self._coordinator.refresh_and_download(handle.run_id, patterns)
        return self._coordinator.refresh(handle.run_id)

    def download_outcome(self, handle: SSHRemoteRunHandle, patterns: list[str]):
        """Compatibility bridge retaining transfer records and per-file failures."""
        return self._coordinator.download(handle.run_id, patterns)


class SSHRemoteRunHandle:
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
        return _snapshot(record, self._coordinator.service.repository.load_tasks(self.run_id))

    def snapshot(self) -> RemoteRunSnapshot:
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
        return ArtifactManifest(self.run_id, tuple(ArtifactEntry(t.task_id, tuple(t.remote_result_paths)) for t in tasks))

    def download(self, patterns: list[str]) -> RemoteRunSnapshot:
        outcome = self._coordinator.download(self.run_id, patterns)
        if outcome.errors:
            raise ConfFlowClientError("; ".join(outcome.errors))
        return self.status()

    def resume(self, *, checkpoint: str | None = None) -> RemoteRunSnapshot:
        del checkpoint
        raise UnsupportedRemoteRunOperation("resume")


LegacyConfFlowClient = SSHConfFlowClient
LegacyRemoteRunHandle = SSHRemoteRunHandle


def _snapshot(record: Any, tasks: list[Any]) -> RemoteRunSnapshot:
    kind = record.workflow_kind.value if record.workflow_kind is not None else None
    return RemoteRunSnapshot(record.run_id, record.server_id, record.remote_dir, kind, dict(record.status_summary), tuple(
        TaskSnapshot(t.task_id, t.status.value, t.remote_job_dir, t.remote_workflow_dir, t.remote_state_path,
                     tuple(t.remote_result_paths), t.remote_job_id, t.error_message) for t in tasks))


def _reference_for(record: Any, provenance: dict[str, object] | None) -> RemoteRunReference:
    identity = dict(provenance or {})
    capability = identity.get("capability")
    protocol = "confflow.capabilities.v4" if isinstance(capability, dict) and capability.get("schema_version") == 4 else None
    return RemoteRunReference(record.server_id, record.run_id, protocol, identity)
