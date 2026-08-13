from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobdesk_app.application.confflow_client import SubmitRequest
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus
from jobdesk_app.services.confflow_control import (
    ControlArtifact,
    ControlArtifactManifest,
    ControlEvent,
    ControlEventPage,
    ControlProtocolError,
    ControlSnapshot,
    ControlUnsupported,
    build_prepare_request,
    parse_artifacts_response,
    parse_capabilities,
    parse_events_response,
    parse_snapshot_response,
)
from jobdesk_app.services.confflow_control_state import load_state, save_state
from jobdesk_app.services.run_service import RunService
from jobdesk_app.services.ssh_confflow_client import (
    SSHConfFlowClient,
    _canonical_json,
    _download_control_artifacts,
    _state_worker_executable,
    _upload_control_worker_handoff,
    _worker_handoff,
)
from jobdesk_app.services.ssh_confflow_control import (
    SSHControlTransport,
    build_control_execute_command,
)


def _response(operation: str, **fields: object) -> str:
    return json.dumps(
        {"protocol_schema": "confflow.control.v1", "operation": operation, "ok": True, **fields},
        separators=(",", ":"),
    )


def test_state_worker_executable_accepts_missing_durable_state() -> None:
    assert _state_worker_executable(None) is None
    assert _state_worker_executable({}) is None
    assert _state_worker_executable({"worker_executable": "/opt/confflow/bin/worker"}) == (
        "/opt/confflow/bin/worker"
    )
    assert _state_worker_executable({"worker_executable": 1}) is None


def test_prepare_digest_matches_phase_d_golden_frame() -> None:
    request = build_prepare_request(
        run_id="run-20260731-001",
        idempotency_key="submit-001",
        workflow_config={"path": "workflow.yaml", "sha256": "b" * 64},
        input_manifest={"path": "inputs/manifest.json", "sha256": "c" * 64},
        expected_executable_identity={
            "sha256": "d" * 64,
            "realpath": "/opt/confflow/bin/confflow",
            "device_inode": "8:1234",
        },
    )

    assert request["request_digest"] == "04c5bfe83012950203d2426420ab181c726ad497df75078b9af00af18ddaf78e"


def test_control_parsers_fail_closed_on_unsupported_or_malformed_wire_data() -> None:
    assert parse_capabilities(
        _response("capabilities", supported_protocols=["confflow.control.v1"])
    ) is True
    with pytest.raises(ControlUnsupported):
        parse_capabilities(_response("capabilities", supported_protocols=["confflow.control.v2"]))
    with pytest.raises(ControlProtocolError, match="exactly one JSON response"):
        parse_capabilities("{}\n{}\n")
    with pytest.raises(ControlProtocolError, match="cursor is malformed"):
        parse_events_response(
            "events",
            _response(
                "events",
                run_id="run-1",
                revision=1,
                state="running",
                events=[{"cursor": "bad", "revision": 1, "type": "running"}],
                next_cursor="bad/",
            ),
        )
    with pytest.raises(ControlProtocolError, match="out of order"):
        parse_events_response(
            "events",
            _response(
                "events",
                run_id="run-1",
                revision=2,
                state="running",
                events=[
                    {"cursor": "r00000000000000000001", "revision": 1, "type": "running"},
                    {"cursor": "r00000000000000000002", "revision": 1, "type": "running"},
                ],
                next_cursor="r00000000000000000002",
            ),
        )

    page = parse_events_response(
        "events",
        _response(
            "events",
            run_id="run-1",
            revision=2,
            state="running",
            events=[{"cursor": "cursor-2", "revision": 2, "type": "running"}],
            next_cursor="cursor-2",
        ),
    )
    assert page.next_cursor == "cursor-2"


def test_stable_cli_without_control_protocol_selects_legacy_fallback() -> None:
    """The stable CLI diagnostic must be classified as unsupported control."""

    class StableCli:
        def run(self, command: str, timeout: int):
            del command, timeout
            return SimpleNamespace(
                exit_code=2,
                stdout="",
                stderr="confflow: error: --json requires --capabilities",
            )

    with pytest.raises(ControlUnsupported):
        SSHControlTransport(
            StableCli(),
            None,
            executable="confflow",
            state_root="/tmp/jobdesk-control",
        ).capabilities()


@pytest.mark.parametrize(
    ("run_id", "idempotency_key"),
    [
        ("../escape", "jobdesk.run-1"),
        ("run-1", "jobdesk/escape"),
        ("run-1", ".."),
    ],
)
def test_control_prepare_rejects_request_path_components_before_sftp(
    run_id: str, idempotency_key: str
) -> None:
    class NoWriteSFTP:
        def mkdir_p(self, remote_dir: str) -> None:  # pragma: no cover - defensive
            raise AssertionError(f"unexpected remote mkdir: {remote_dir}")

    transport = SSHControlTransport(
        None,
        NoWriteSFTP(),
        executable="confflow",
        state_root="/tmp/jobdesk-control",
    )
    with pytest.raises(ValueError, match="request identifier pattern"):
        transport.prepare({"run_id": run_id, "idempotency_key": idempotency_key})


def test_control_launcher_accepts_producer_safe_dotted_run_id() -> None:
    command = build_control_execute_command(
        "confflow", "/tmp/jobdesk-control", "run.2026-08"
    )
    assert "--run-id run.2026-08" in command


def test_worker_handoff_digest_uses_canonical_envelope_bytes() -> None:
    handoff = _worker_handoff(
        run_id="run-1",
        workflow_path="/attempt/input/workflow.yaml",
        workflow_digest="b" * 64,
        input_path="/attempt/input/methane.xyz",
        input_digest="c" * 64,
        work_dir="/attempt/results/methane_confflow_work",
        task_id="methane",
    )

    expected = (
        b'{"content_schema":"confflow.control.worker-handoff.v1","run_id":"run-1","tasks":[{"input_xyz":"/attempt/input/methane.xyz","sha256":"'
        + b"c" * 64
        + b'","task_id":"methane","work_dir":"/attempt/results/methane_confflow_work"}],"workflow_config":{"path":"/attempt/input/workflow.yaml","sha256":"'
        + b"b" * 64
        + b'"}}'
    )
    assert _canonical_json(handoff) == expected
    assert handoff["workflow_config"] == {
        "path": "/attempt/input/workflow.yaml",
        "sha256": "b" * 64,
    }
    assert handoff["tasks"][0]["sha256"] == "c" * 64


class _WorkerSourceSFTP:
    def __init__(self, sources: dict[str, Path]) -> None:
        self.sources = sources
        self.files: dict[str, bytes] = {}

    def mkdir_p(self, remote_dir: str) -> None:
        del remote_dir

    def lstat(self, remote_path: str):
        if remote_path in self.sources:
            return self.sources[remote_path].lstat()
        if remote_path in self.files:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o600)
        return None

    def download_file(self, remote_path: str, local_path: Path, **kwargs):
        del kwargs
        local_path.write_bytes(self.sources[remote_path].read_bytes())
        return SimpleNamespace(status="transferred", reason="ok")

    def upload_file(self, local_path: Path, remote_path: str, **kwargs):
        del kwargs
        self.files[remote_path] = local_path.read_bytes()
        return SimpleNamespace(status="transferred", reason="ok")


def test_worker_handoff_stages_only_regular_digest_matched_sources(tmp_path) -> None:
    workflow = tmp_path / "workflow.yaml"
    input_xyz = tmp_path / "methane.xyz"
    workflow.write_text("steps: []\n", encoding="utf-8")
    input_xyz.write_text("1\n\nH 0 0 0\n", encoding="utf-8")
    attempt_root = "/attempt"
    workflow_target = f"{attempt_root}/input/workflow.yaml"
    input_target = f"{attempt_root}/input/methane.xyz"
    handoff_target = f"{attempt_root}/input/worker-handoff.json"
    handoff = _worker_handoff(
        run_id="run-1",
        workflow_path=workflow_target,
        workflow_digest=hashlib.sha256(workflow.read_bytes()).hexdigest(),
        input_path=input_target,
        input_digest=hashlib.sha256(input_xyz.read_bytes()).hexdigest(),
        work_dir=f"{attempt_root}/results/methane_confflow_work",
        task_id="methane",
    )
    sftp = _WorkerSourceSFTP({"/source/workflow.yaml": workflow, "/source/methane.xyz": input_xyz})
    ssh = SimpleNamespace(
        run=lambda command, timeout: SimpleNamespace(exit_code=0, stdout="", stderr="")
    )

    _upload_control_worker_handoff(
        sftp,
        ssh,
        worker_handoff=handoff,
        handoff_path=handoff_target,
        attempt_root=attempt_root,
        workflow_path=workflow_target,
        input_path=input_target,
        remote_workflow_path="/source/workflow.yaml",
        remote_input_path="/source/methane.xyz",
        workflow_digest=handoff["workflow_config"]["sha256"],
        input_digest=handoff["tasks"][0]["sha256"],
        handoff_bytes=_canonical_json(handoff),
    )

    assert sftp.files[workflow_target] == workflow.read_bytes()
    assert sftp.files[input_target] == input_xyz.read_bytes()
    assert json.loads(sftp.files[handoff_target]) == handoff


def test_control_prepare_rejects_non_string_request_component() -> None:
    transport = SSHControlTransport(
        None,
        object(),
        executable="confflow",
        state_root="/tmp/jobdesk-control",
    )
    with pytest.raises(ValueError, match="run_id must be a non-empty string"):
        transport.prepare({"run_id": 123, "idempotency_key": "jobdesk.run-1"})


def test_artifact_parser_rejects_traversal_and_nonterminal_manifest() -> None:
    response = _response(
        "artifacts",
        run_id="run-1",
        revision=4,
        state="completed",
        artifacts=[
            {
                "terminal": "task-1",
                "path": "../result.json",
                "sha256": "a" * 64,
                "size": 1,
                "content_schema": "application/json",
            }
        ],
    )
    with pytest.raises(ControlProtocolError, match="unsafe fields"):
        parse_artifacts_response("artifacts", response)

    with pytest.raises(ControlProtocolError, match="state is invalid for operation"):
        parse_artifacts_response(
            "artifacts",
            _response("artifacts", run_id="run-1", revision=1, state="running", artifacts=[]),
        )


def test_control_parser_enforces_producer_operation_states_and_run_binding() -> None:
    with pytest.raises(ControlProtocolError, match="state is invalid for operation"):
        parse_snapshot_response(
            "prepare",
            _response("prepare", run_id="run-1", revision=1, state="running"),
        )
    for state in ("queued", "running", "paused", "cancelled"):
        snapshot = parse_snapshot_response(
            "cancel",
            _response("cancel", run_id="run-1", revision=1, state=state),
            expected_run_id="run-1",
        )
        assert snapshot.state == state
    with pytest.raises(ControlProtocolError, match="state is invalid for operation"):
        parse_snapshot_response(
            "cancel",
            _response("cancel", run_id="run-1", revision=1, state="prepared"),
        )
    with pytest.raises(ControlProtocolError, match="snapshot fields are malformed"):
        parse_snapshot_response(
            "cancel",
            _response("cancel", run_id="run-1", revision=1, state="unknown"),
        )
    with pytest.raises(ControlProtocolError, match="snapshot fields are malformed"):
        parse_snapshot_response(
            "cancel",
            _response("cancel", run_id="run-1", revision=True, state="running"),
        )
    with pytest.raises(ControlProtocolError, match="state is invalid for operation"):
        parse_snapshot_response(
            "resume",
            _response("resume", run_id="run-1", revision=1, state="completed"),
        )
    with pytest.raises(ControlProtocolError, match="does not match requested run"):
        parse_snapshot_response(
            "status",
            _response("status", run_id="run-2", revision=1, state="running"),
            expected_run_id="run-1",
        )
    with pytest.raises(ControlProtocolError, match="snapshot fields are malformed"):
        parse_snapshot_response(
            "status",
            _response("status", run_id="../run", revision=1, state="running"),
        )


def test_artifact_parser_rejects_unsafe_terminal_names() -> None:
    response = _response(
        "artifacts",
        run_id="run-1",
        revision=4,
        state="completed",
        artifacts=[
            {
                "terminal": "bad/name",
                "path": "result.json",
                "sha256": "a" * 64,
                "size": 1,
                "content_schema": "application/json",
            }
        ],
    )
    with pytest.raises(ControlProtocolError, match="unsafe fields"):
        parse_artifacts_response("artifacts", response)


@dataclass
class FakeControlTransport:
    status_snapshots: list[ControlSnapshot] = field(default_factory=list)
    event_page: ControlEventPage | None = None
    prepared: list[dict[str, object]] = field(default_factory=list)
    cancelled: list[str] = field(default_factory=list)
    execute_snapshot: ControlSnapshot | None = None
    sftp: object | None = None

    def prepare(self, request: dict[str, object]) -> ControlSnapshot:
        self.prepared.append(request)
        return ControlSnapshot(str(request["run_id"]), 1, "prepared")

    def execute(self, run_id: str) -> ControlSnapshot:
        return self.execute_snapshot or ControlSnapshot(run_id, 2, "queued")

    def status(self, run_id: str) -> ControlSnapshot:
        return self.status_snapshots.pop(0)

    def events(self, run_id: str, *, after: str | None) -> ControlEventPage:
        assert self.event_page is not None
        self.last_after = after
        return self.event_page

    def cancel(self, run_id: str) -> ControlSnapshot:
        self.cancelled.append(run_id)
        return ControlSnapshot(run_id, 5, "cancelled")

    def resume(self, run_id: str, *, checkpoint: str | None) -> ControlSnapshot:
        return ControlSnapshot(run_id, 6, "queued")

    def artifacts(self, run_id: str) -> ControlArtifactManifest:
        return ControlArtifactManifest(ControlSnapshot(run_id, 5, "completed"), ())


class FakeLauncherSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def mkdir_p(self, remote_dir: str) -> None:
        del remote_dir

    def upload_file(self, local_path: Path, remote_path: str, **kwargs):
        del kwargs
        self.files[remote_path] = local_path.read_bytes()
        return SimpleNamespace(status="transferred", reason="ok")

    def stat(self, remote_path: str):
        return SimpleNamespace(st_size=len(self.files[remote_path])) if remote_path in self.files else None

    def read_file_bytes(self, remote_path: str, max_bytes: int = 65536) -> bytes:
        return self.files[remote_path][:max_bytes]


class FakeLauncherScheduler:
    def __init__(self, service: RunService, sftp: FakeLauncherSFTP, job_id: str = "control-test-job") -> None:
        self.service = service
        self.sftp = sftp
        self.job_id = job_id

    def submit(self, ssh, script_path: str, resources) -> str:
        del ssh, script_path, resources
        state = load_state(self.service, "run-1")
        assert state is not None
        launcher = state["launcher"]
        assert isinstance(launcher, dict)
        self.sftp.files[str(launcher["metadata_path"])] = json.dumps(
            {
                "content_schema": "jobdesk.confflow.launcher.v1",
                "run_id": "run-1",
                "scheduler_type": str(state["scheduler_type"]),
                "scheduler_job_id": self.job_id,
                "pid": self.job_id,
                "state_root": str(launcher["state_root"]),
                "command": str(launcher["command"]),
            }
        ).encode("utf-8")
        return self.job_id


def _control_spec() -> RunSpec:
    return RunSpec(
        server_id="server",
        remote_dir="/remote/project",
        command_template="confflow {name} -c {path}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/methane.xyz")],
        supporting_sources=[RunSource("/remote/project/workflow.yaml")],
        workflow_kind=WorkflowKind.confflow,
    )


def _seed_control_state(service: RunService, run_id: str, *, revision: int = 0, state: str = "prepared") -> None:
    attempt_root = "/home/test/.local/state/confflow/jobdesk-run-1"
    handoff = {
        "content_schema": "confflow.control.worker-handoff.v1",
        "run_id": run_id,
        "workflow_config": {
            "path": f"{attempt_root}/input/workflow.yaml",
            "sha256": "b" * 64,
        },
        "tasks": [
            {
                "task_id": "methane",
                "input_xyz": f"{attempt_root}/input/methane.xyz",
                "work_dir": f"{attempt_root}/results/methane_confflow_work",
                "sha256": "c" * 64,
            }
        ],
    }
    save_state(
        service,
        run_id,
        {
            "content_schema": "jobdesk.confflow.backend.v1",
            "run_id": run_id,
            "backend": "control",
            "protocol_schema": "confflow.control.v1",
            "state_locator": "/home/test/.local/state/confflow/control",
            "idempotency_key": f"jobdesk.{run_id}",
            "producer_identity": {"sha256": "d" * 64},
            "capability": {"capabilities": {"control_worker": True}},
            "input_manifest_path": f"{attempt_root}/input/worker-handoff.json",
            "worker_attempt_root": attempt_root,
            "worker_work_dir": f"{attempt_root}/results/methane_confflow_work",
            "worker_executable": "/opt/confflow/bin/confflow-control-worker",
            "worker_handoff": handoff,
            "revision": revision,
            "state": state,
        },
    )


def test_control_handle_persists_cursor_and_prevents_terminal_regression(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_control_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    transport = FakeControlTransport(
        status_snapshots=[
            ControlSnapshot("run-1", 5, "completed"),
            ControlSnapshot("run-1", 6, "running"),
        ],
        event_page=ControlEventPage(
            ControlSnapshot("run-1", 1, "running"),
            (ControlEvent("cursor-1", 1, "running"),),
            "cursor-1",
        ),
    )
    coordinator = type("Coordinator", (), {"service": service})()
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        backend_mode="control",
    )
    handle = client.attach("run-1")

    page = handle.events()
    assert page.next_cursor == "cursor-1"
    assert transport.last_after is None
    assert load_state(service, "run-1")["cursor"] == "cursor-1"

    first = handle.status()
    second = handle.status()
    assert first.producer_state == "completed"
    assert second.producer_state == "completed"
    assert second.revision == 6


def test_control_status_does_not_change_state_at_the_same_revision(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_control_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    transport = FakeControlTransport(
        status_snapshots=[
            ControlSnapshot("run-1", 1, "running"),
            ControlSnapshot("run-1", 1, "queued"),
        ]
    )
    coordinator = type("Coordinator", (), {"service": service})()
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        backend_mode="control",
    )
    handle = client.attach("run-1")

    assert handle.status().producer_state == "running"
    assert handle.status().producer_state == "running"


def test_control_submit_uses_stable_idempotency_and_persists_backend(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_control_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeLauncherSFTP()
    transport = FakeControlTransport(sftp=sftp)
    scheduler = FakeLauncherScheduler(service, sftp)
    coordinator = type("Coordinator", (), {"service": service})()
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        scheduler_factory=lambda scheduler_type: scheduler,
        backend_mode="control",
    )
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is not None
    assert not outcome.errors
    assert len(transport.prepared) == 1
    assert transport.prepared[0]["idempotency_key"] == "jobdesk.run-1"
    saved = load_state(service, "run-1")
    assert saved["backend"] == "control"
    assert saved["request_digest"] == transport.prepared[0]["request_digest"]
    assert service.repository.load_tasks("run-1")[0].scheduler_type == "nohup"


def test_control_submit_leaves_producer_lifecycle_to_launcher(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_control_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeLauncherSFTP()
    transport = FakeControlTransport(sftp=sftp)
    scheduler = FakeLauncherScheduler(service, sftp)
    coordinator = type("Coordinator", (), {"service": service})()
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        scheduler_factory=lambda scheduler_type: scheduler,
        backend_mode="control",
    )
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is not None
    assert not outcome.errors
    assert service.repository.load_tasks("run-1")[0].status == TaskStatus.uploaded
    assert service.repository.load_tasks("run-1")[0].remote_job_id == "control-test-job"


def test_control_download_uses_manifest_paths_and_matching_task_directory(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_control_spec(), run_id="run-1")
    task = service.repository.load_tasks("run-1")[0]
    content = b"manifest output\n"

    class FakeSFTP:
        def lstat(self, remote_path: str):
            return SimpleNamespace(st_mode=stat.S_IFREG)

        def stat(self, remote_path: str):
            return SimpleNamespace(st_size=len(content))

        def download_file(self, remote_path: str, local_path: Path, **kwargs):
            local_path.write_bytes(content)
            return TransferRecord(
                TransferDirection.download,
                str(local_path),
                remote_path,
                size_bytes=len(content),
                status=TransferStatus.transferred,
            )

    transfers, failures = _download_control_artifacts(
        service,
        "run-1",
        (
            ControlArtifact(
                task.task_id,
                "result.json",
                hashlib.sha256(content).hexdigest(),
                len(content),
                "application/json",
            ),
        ),
        ["*.json"],
        FakeSFTP(),
    )

    assert len(transfers) == 1
    assert failures == []
    assert (tmp_path / "results" / "run-1" / Path(task.remote_workflow_dir).name / "result.json").read_bytes() == content
