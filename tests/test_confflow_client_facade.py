"""Contract tests for the post-M2 application facade."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from jobdesk_app.application.confflow_client import (
    ArtifactEntry,
    ArtifactManifest,
    ConfFlowClient,
    ConfFlowClientError,
    EventPage,
    RemoteRunHandle,
    RemoteRunReference,
    RemoteRunSnapshot,
    SubmitRequest,
    UnsupportedRemoteRunOperation,
)
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec
from jobdesk_app.services.run_service import RunService
from jobdesk_app.services.ssh_confflow_client import LegacyConfFlowClient


@dataclass
class FakeHandle:
    run_id: str = "run-1"

    def status(self) -> RemoteRunSnapshot:
        return RemoteRunSnapshot("run-1", "server", "/remote", "confflow", {}, ())

    def snapshot(self) -> RemoteRunSnapshot:
        return self.status()

    def events(self, *, after: str | None = None) -> EventPage:
        del after
        return EventPage((), None)

    def cancel(self) -> RemoteRunSnapshot:
        return self.status()

    def artifacts(self) -> ArtifactManifest:
        return ArtifactManifest("run-1", ())

    def download(self, patterns: list[str]) -> RemoteRunSnapshot:
        del patterns
        return self.status()

    def resume(self, *, checkpoint: str | None = None) -> RemoteRunSnapshot:
        del checkpoint
        return self.status()

    def to_dict(self) -> dict[str, object]:
        return RemoteRunReference("server", self.run_id, "confflow.capabilities.v4", {}).to_dict()


class FakeClient:
    def probe(self, *, require_dag: bool = False):
        del require_dag
        return None

    def attach(self, run_id: str) -> FakeHandle:
        return FakeHandle(run_id)

    def submit(self, request: SubmitRequest) -> FakeHandle:
        return FakeHandle(request.run_id)


def _spec() -> RunSpec:
    return RunSpec(
        server_id="server",
        remote_dir="/remote/project",
        command_template="confflow {name} -c config.yaml",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/methane.xyz")],
        result_templates=["{basename}.out"],
    )


def test_fake_client_and_handle_satisfy_contract() -> None:
    client: ConfFlowClient = FakeClient()
    handle: RemoteRunHandle = client.submit(SubmitRequest("run-1"))

    assert isinstance(client, ConfFlowClient)
    assert isinstance(handle, RemoteRunHandle)
    assert handle.snapshot().to_dict()["run_id"] == "run-1"


def test_models_are_plain_serializable_data() -> None:
    request = SubmitRequest.from_dict({"run_id": "run-1", "resource_overrides": {"cores": 2}})
    manifest = ArtifactManifest("run-1", (ArtifactEntry("task-1", ("result.out",)),))

    assert request.to_dict() == {"run_id": "run-1", "resource_overrides": {"cores": 2}}
    assert manifest.to_dict() == {
        "run_id": "run-1",
        "entries": ({"task_id": "task-1", "remote_paths": ("result.out",)},),
        "source": "legacy-task-records",
    }
    assert json.loads(json.dumps(manifest.to_dict()))["run_id"] == "run-1"


def test_legacy_handle_attaches_and_projects_persisted_records(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    coordinator = type("Coordinator", (), {"service": service})()

    handle = LegacyConfFlowClient(coordinator, "server").attach("run-1")
    snapshot = handle.status()

    assert snapshot.run_id == "run-1"
    assert snapshot.tasks[0].status == TaskStatus.uploaded.value
    assert handle.artifacts().entries[0].remote_paths
    assert handle.artifacts().source == "legacy-task-records"
    assert handle.to_dict() == {
        "server_id": "server",
        "run_id": "run-1",
        "accepted_protocol": None,
        "identity_snapshot": {},
    }
    with pytest.raises(UnsupportedRemoteRunOperation, match="events"):
        handle.events()
    with pytest.raises(UnsupportedRemoteRunOperation, match="resume"):
        handle.resume()


def test_legacy_client_delegates_submit_and_download_to_coordinator(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")

    class Coordinator:
        def __init__(self) -> None:
            self.service = service
            self.submissions: list[tuple[str, dict[str, object] | None]] = []
            self.downloads: list[tuple[str, list[str]]] = []
            self.cancellations: list[str] = []

        def submit(self, run_id: str, *, resource_overrides: dict[str, object] | None):
            self.submissions.append((run_id, resource_overrides))
            result = type("Submit", (), {"warnings": ["resource advisory"]})()
            return type("Outcome", (), {"errors": [], "submit_results": [result]})()

        def download(self, run_id: str, patterns: list[str]):
            self.downloads.append((run_id, patterns))
            return type("Outcome", (), {"errors": []})()

        def probe_capabilities(self, server_id: str, *, require_dag: bool):
            return (server_id, require_dag)

        def cancel(self, run_id: str):
            self.cancellations.append(run_id)
            return type("Outcome", (), {"errors": []})()

    coordinator = Coordinator()
    client = LegacyConfFlowClient(coordinator, "server")
    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1", {"cores": 2}))

    assert coordinator.submissions == [("run-1", {"cores": 2})]
    assert handle is not None
    assert outcome.submit_results[0].warnings == ["resource advisory"]
    assert LegacyConfFlowClient(coordinator, "server").probe(require_dag=True) == ("server", True)
    assert handle.cancel().run_id == "run-1"
    assert coordinator.cancellations == ["run-1"]
    handle.download(["*.json"])
    assert coordinator.downloads == [("run-1", ["*.json"])]


def test_submit_with_outcome_preserves_errors_without_attaching(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")

    class Coordinator:
        def __init__(self) -> None:
            self.service = service

        def submit(self, run_id: str, *, resource_overrides: dict[str, object] | None):
            del run_id, resource_overrides
            return type("Outcome", (), {"errors": ["remote rejected"]})()

    handle, outcome = LegacyConfFlowClient(Coordinator(), "server").submit_with_outcome(SubmitRequest("run-1"))

    assert handle is None
    assert outcome.errors == ["remote rejected"]


def test_legacy_client_restores_only_matching_serialized_identity(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    service.persist_confflow_provenance(
        "run-1",
        {"schema_version": 4, "producer": {"version": "1.4.6"}},
        resolved_executable="/opt/confflow/bin/confflow",
        resolved_realpath="/opt/confflow/bin/confflow",
    )
    coordinator = type("Coordinator", (), {"service": service})()
    client = LegacyConfFlowClient(coordinator, "server")

    serialized = client.attach("run-1").to_dict()

    assert serialized["accepted_protocol"] == "confflow.capabilities.v4"
    assert serialized["identity_snapshot"]["resolved_realpath"] == "/opt/confflow/bin/confflow"
    assert client.restore_handle(serialized).run_id == "run-1"
    serialized["server_id"] = "other"
    with pytest.raises(ConfFlowClientError, match="serialized handle belongs"):
        client.restore_handle(serialized)


def test_cancel_error_and_serialization_dicts_are_defensively_copied(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")

    class Coordinator:
        def __init__(self) -> None:
            self.service = service

        def cancel(self, run_id: str):
            assert run_id == "run-1"
            return type("Outcome", (), {"errors": ["remote cancel rejected"]})()

    source_identity: dict[str, object] = {"producer": {"version": "1.4.6"}}
    reference = RemoteRunReference("server", "run-1", "confflow.capabilities.v4", source_identity)
    source_identity["producer"] = {"version": "mutated"}
    serialized = reference.to_dict()
    serialized["identity_snapshot"]["producer"] = {"version": "mutated-again"}
    snapshot = RemoteRunSnapshot("run-1", "server", "/remote", None, {"running": 1}, ())
    status = snapshot.to_dict()["status_summary"]
    assert isinstance(status, dict)
    status["running"] = 9

    assert reference.identity_snapshot == {"producer": {"version": "1.4.6"}}
    assert snapshot.status_summary == {"running": 1}
    with pytest.raises(ConfFlowClientError, match="remote cancel rejected"):
        LegacyConfFlowClient(Coordinator(), "server").attach("run-1").cancel()
