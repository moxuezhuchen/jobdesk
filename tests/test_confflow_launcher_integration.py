from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import jobdesk_app.services.ssh_confflow_client as ssh_confflow_client_module
from jobdesk_app.application.confflow_client import SubmitRequest
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.remote.scheduler import ResourceSpec
from jobdesk_app.services.confflow_control import ControlArtifactManifest, ControlSnapshot
from jobdesk_app.services.confflow_control_state import load_state, save_state
from jobdesk_app.services.run_service import RunService
from jobdesk_app.services.ssh_confflow_client import SSHConfFlowClient


class FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.uploads: list[str] = []

    def mkdir_p(self, remote_dir: str) -> None:
        del remote_dir

    def upload_file(self, local_path: Path, remote_path: str, **kwargs):
        del kwargs
        self.files[remote_path] = local_path.read_bytes()
        self.uploads.append(remote_path)
        return SimpleNamespace(status="transferred", reason="ok")

    def read_file_bytes(self, remote_path: str, max_bytes: int = 65536) -> bytes:
        return self.files[remote_path][:max_bytes]


@dataclass
class FakeControlTransport:
    sftp: FakeSFTP
    prepared: list[dict[str, object]] = field(default_factory=list)
    execute_calls: int = 0

    def prepare(self, request: dict[str, object]) -> ControlSnapshot:
        self.prepared.append(request)
        return ControlSnapshot(str(request["run_id"]), 1, "prepared")

    def execute(self, run_id: str) -> ControlSnapshot:
        self.execute_calls += 1
        raise AssertionError(f"direct control execute is not allowed during submit: {run_id}")

    def status(self, run_id: str) -> ControlSnapshot:
        return ControlSnapshot(run_id, 2, "queued")

    def events(self, run_id: str, *, after: str | None):
        del after
        raise AssertionError("events are outside submit")

    def cancel(self, run_id: str) -> ControlSnapshot:
        return ControlSnapshot(run_id, 3, "cancelled")

    def resume(self, run_id: str, *, checkpoint: str | None):
        del checkpoint
        return ControlSnapshot(run_id, 4, "queued")

    def artifacts(self, run_id: str) -> ControlArtifactManifest:
        return ControlArtifactManifest(ControlSnapshot(run_id, 2, "completed"), ())


class FakeScheduler:
    def __init__(self, *, service: RunService, sftp: FakeSFTP, job_id: str = "98765", lose_response: bool = False) -> None:
        self.service = service
        self.sftp = sftp
        self.job_id = job_id
        self.lose_response = lose_response
        self.calls: list[tuple[object, str, ResourceSpec]] = []
        self.state_at_dispatch: dict[str, object] | None = None

    def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
        self.calls.append((ssh, script_path, resources))
        state = load_state(self.service, "run-1")
        assert state is not None
        self.state_at_dispatch = state
        launcher = state["launcher"]
        assert isinstance(launcher, dict)
        marker_path = str(launcher["metadata_path"])
        self.sftp.files[marker_path] = json.dumps(
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
        if self.lose_response:
            raise TimeoutError("scheduler response lost after dispatch")
        return self.job_id

    def poll(self, ssh, job_id: str):
        del ssh, job_id
        return "running"

    def cancel(self, ssh, job_id: str) -> None:
        del ssh, job_id


def _spec() -> RunSpec:
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


def _seed_control_state(service: RunService, run_id: str) -> None:
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
            "revision": 0,
            "state": "prepared",
        },
    )


def _client(service: RunService, transport: FakeControlTransport, scheduler: FakeScheduler) -> SSHConfFlowClient:
    coordinator = type("Coordinator", (), {"service": service})()
    return SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        scheduler_factory=lambda scheduler_type: scheduler,
        backend_mode="control",
    )


@pytest.mark.parametrize(
    ("scheduler_type", "header"),
    [("nohup", ""), ("slurm", "#SBATCH --cpus-per-task=2"), ("pbs", "#PBS -l nodes=1:ppn=2")],
)
def test_control_launcher_script_uses_existing_scheduler_headers(scheduler_type: str, header: str) -> None:
    from jobdesk_app.services.ssh_confflow_control import build_control_launcher_script

    script = build_control_launcher_script(
        executable="/opt/confflow/bin/confflow",
        state_root="/tmp/jobdesk-control/state",
        run_id="run-1",
        metadata_path="/tmp/jobdesk-control/launcher.json",
        scheduler_type=scheduler_type,
        resources=ResourceSpec(cpus=2),
        env_init_scripts=["/etc/profile.d/confflow.sh"],
    )

    assert "control execute" in script
    assert "--state-root /tmp/jobdesk-control/state" in script
    assert "--run-id run-1" in script
    assert "/tmp/jobdesk-control/launcher.json" in script
    assert header in script
    assert "gaussian" not in script.lower()
    assert "g16" not in script.lower()


def test_control_submit_dispatches_scheduler_and_persists_provenance(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is not None
    assert not outcome.errors
    assert transport.execute_calls == 0
    assert len(transport.prepared) == 1
    assert len(scheduler.calls) == 1
    assert scheduler.state_at_dispatch is not None
    assert scheduler.state_at_dispatch["dispatch_state"] == "dispatching"
    saved = load_state(service, "run-1")
    assert saved is not None
    assert saved["dispatch_state"] == "submitted"
    assert saved["scheduler_job_id"] == "98765"
    assert saved["scheduler_type"] == "nohup"
    launcher = saved["launcher"]
    assert isinstance(launcher, dict)
    script_path = str(launcher["script_path"])
    assert script_path in sftp.files
    assert b"control execute" in sftp.files[script_path]
    task = service.repository.load_tasks("run-1")[0]
    assert task.scheduler_type == "nohup"
    assert task.remote_job_id == "98765"


def test_dispatch_response_loss_reconciles_from_remote_launcher_marker(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp, job_id="12345", lose_response=True)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is None
    assert outcome.errors
    assert load_state(service, "run-1")["dispatch_state"] == "dispatching"

    recovered = client.attach("run-1")
    assert recovered.run_id == "run-1"
    saved = load_state(service, "run-1")
    assert saved is not None
    assert saved["dispatch_state"] == "submitted"
    assert saved["scheduler_job_id"] == "12345"


def test_local_provenance_save_failure_reconciles_without_duplicate_dispatch(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp, job_id="54321")
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    real_save_state = ssh_confflow_client_module.save_state
    failed = False

    def fail_once(service_arg, run_id: str, value: dict[str, object]) -> None:
        nonlocal failed
        if value.get("dispatch_state") == "submitted" and not failed:
            failed = True
            raise OSError("local provenance write failed")
        real_save_state(service_arg, run_id, value)

    monkeypatch.setattr(ssh_confflow_client_module, "save_state", fail_once)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is None
    assert outcome.errors
    assert len(scheduler.calls) == 1
    assert load_state(service, "run-1")["dispatch_state"] == "dispatching"

    recovered = client.attach("run-1")
    assert recovered.run_id == "run-1"
    assert load_state(service, "run-1")["scheduler_job_id"] == "54321"
    assert len(scheduler.calls) == 1


def test_duplicate_control_submit_does_not_dispatch_second_launcher(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    first, first_outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    second, second_outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert first is not None and second is not None
    assert not first_outcome.errors and not second_outcome.errors
    assert len(scheduler.calls) == 1
    assert len(transport.prepared) == 1
