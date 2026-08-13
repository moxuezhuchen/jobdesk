from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import jobdesk_app.services.ssh_confflow_client as ssh_confflow_client_module
from jobdesk_app.application.confflow_client import SubmitRequest
from jobdesk_app.core.confflow_executable import ConfFlowExecutableIdentity
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.remote.scheduler import ResourceSpec
from jobdesk_app.services.confflow_control import (
    CONTROL_BACKEND,
    ControlArtifactManifest,
    ControlSnapshot,
)
from jobdesk_app.services.confflow_control_state import load_state, save_state
from jobdesk_app.services.run_service import RunService
from jobdesk_app.services.ssh_confflow_client import ConfFlowClientError, SSHConfFlowClient


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


def _spec(*, confflow_executable: str = "") -> RunSpec:
    return RunSpec(
        server_id="server",
        remote_dir="/remote/project",
        command_template="confflow {name} -c {path}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/methane.xyz")],
        supporting_sources=[RunSource("/remote/project/workflow.yaml")],
        workflow_kind=WorkflowKind.confflow,
        confflow_executable=confflow_executable,
    )


def _seed_control_state(service: RunService, run_id: str) -> None:
    attempt_root = "/home/test/.local/state/confflow/jobdesk-run-1"
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
            "worker_handoff": {
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
            },
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
        worker_executable="/opt/confflow/bin/confflow-control-worker",
        handoff_path="/tmp/jobdesk-control/input/worker-handoff.json",
        state_root="/tmp/jobdesk-control/state",
        run_id="run-1",
        metadata_path="/tmp/jobdesk-control/launcher.json",
        scheduler_type=scheduler_type,
        resources=ResourceSpec(cpus=2),
        env_init_scripts=["/etc/profile.d/confflow.sh"],
    )

    assert "control execute" in script
    assert "confflow-control-worker" in script
    assert "setsid --wait" in script
    assert "--state-root /tmp/jobdesk-control/state" in script
    assert "--run-id run-1" in script
    assert "/tmp/jobdesk-control/launcher.json" in script
    assert '"execute_rc":null' in script
    assert "completed_marker=\"${marker//$old_fragment/$new_fragment}\"" in script
    assert "sed" not in script
    assert header in script
    assert "gaussian" not in script.lower()
    assert "g16" not in script.lower()


def test_control_launcher_script_is_shell_valid() -> None:
    if os.name == "nt":
        pytest.skip("POSIX shell validation runs in the Linux contract environment")
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to validate the POSIX launcher script")
    from jobdesk_app.services.ssh_confflow_control import build_control_launcher_script

    script = build_control_launcher_script(
        executable="/opt/confflow/bin/confflow",
        worker_executable="/opt/confflow/bin/confflow-control-worker",
        handoff_path="/tmp/jobdesk-control/input/worker-handoff.json",
        state_root="/tmp/jobdesk-control/state",
        run_id="run-1",
        metadata_path="/tmp/jobdesk-control/launcher.json",
        scheduler_type="nohup",
        resources=ResourceSpec(cpus=2),
    )
    result = subprocess.run(
        [bash, "-n"],
        input=script.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


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


def test_worker_started_marker_reconciles_after_execute_response_loss(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp, job_id="worker-123", lose_response=True)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is None
    assert outcome.errors
    state = load_state(service, "run-1")
    assert state is not None
    launcher = state["launcher"]
    assert isinstance(launcher, dict)
    marker_path = str(launcher["metadata_path"])
    marker = json.loads(sftp.files[marker_path].decode("utf-8"))
    marker.update({"execution_state": "started", "execute_rc": 0, "worker_started": True, "worker_rc": None})
    sftp.files[marker_path] = json.dumps(marker).encode("utf-8")

    recovered = client.attach("run-1")
    assert recovered.run_id == "run-1"
    recovered_state = load_state(service, "run-1")
    assert recovered_state is not None
    assert recovered_state["dispatch_state"] == "submitted"
    assert recovered_state["scheduler_job_id"] == "worker-123"


def test_failed_launcher_marker_is_not_reconciled_as_submitted(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp, job_id="54321", lose_response=True)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is None
    assert outcome.errors
    state = load_state(service, "run-1")
    assert state is not None
    launcher = state["launcher"]
    assert isinstance(launcher, dict)
    marker_path = str(launcher["metadata_path"])
    marker = json.loads(sftp.files[marker_path].decode("utf-8"))
    marker["execution_state"] = []
    sftp.files[marker_path] = json.dumps(marker).encode("utf-8")
    with pytest.raises(ConfFlowClientError, match="invalid execution state"):
        client.attach("run-1")
    marker.update({"execution_state": "failed", "execute_rc": 17})
    sftp.files[marker_path] = json.dumps(marker).encode("utf-8")

    recovered = client.attach("run-1")
    assert recovered.run_id == "run-1"
    failed = load_state(service, "run-1")
    assert failed is not None
    assert failed["dispatch_state"] == "failed"
    assert failed["launcher"]["execute_rc"] == 17
    assert "scheduler_job_id" not in failed

    retry_handle, retry_outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert retry_handle is None
    assert any("refusing duplicate prepare" in error for error in retry_outcome.errors)
    assert len(scheduler.calls) == 1


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


def test_probe_failure_clears_selection_for_same_client_retry(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    capability = ConfFlowCapabilities(
        schema_version=4,
        version="2.1.2",
        workflow_state=True,
        resume=True,
        dag=True,
        control_worker=True,
        raw_payload={"version": "2.1.2"},
    )
    coordinator = SimpleNamespace(
        service=service,
        probe_capabilities=lambda server_id, require_dag=False: capability,
    )
    client = SSHConfFlowClient(coordinator, "server", backend_mode="control")
    monkeypatch.setattr(
        client,
        "_negotiate_backend",
        lambda capabilities: (
            setattr(client, "_selected_backend", CONTROL_BACKEND),
            setattr(client, "_selected_state_locator", "/tmp/control"),
        ),
    )
    identity = ConfFlowExecutableIdentity(
        path="/opt/confflow/bin/confflow",
        realpath="/opt/confflow/bin/confflow",
        sha256="a" * 64,
        python="/opt/confflow/bin/python3.12",
        size=10,
        mtime_ns=20,
        device=30,
        inode=40,
    )
    contract = SimpleNamespace(
        accepted=True,
        as_dict=lambda: {"accepted": True},
        remote_identity=SimpleNamespace(as_dict=lambda: {"cache_key": "cache"}),
    )
    measurements = iter([OSError("temporary SSH failure"), identity])
    monkeypatch.setattr(client, "_measure_producer_identity", lambda capabilities: _next_probe(measurements))
    monkeypatch.setattr(client, "_resolve_config_contract", lambda capabilities, identity: contract)

    with pytest.raises(ConfFlowClientError, match="temporary SSH failure"):
        client.probe()
    assert client._selected_backend is None
    assert client._selected_capability is None

    assert client.probe() is capability
    assert client._selected_backend == CONTROL_BACKEND
    assert client._selected_config_contract is contract


def test_submit_reprobes_after_probe_failure_before_dispatch(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(confflow_executable="/opt/confflow/bin/confflow"), run_id="run-1")
    capability = ConfFlowCapabilities(
        schema_version=4,
        version="2.1.2",
        workflow_state=True,
        resume=True,
        dag=True,
        control_worker=True,
        executable={
            "path": "/opt/confflow/bin/confflow",
            "python": "/opt/confflow/bin/python3.12",
            "sha256": "a" * 64,
        },
        raw_payload={"version": "2.1.2", "capabilities": {"control_worker": True}},
    )
    probe_calls = 0

    def probe_capabilities(server_id, require_dag=False):
        nonlocal probe_calls
        probe_calls += 1
        return capability

    coordinator = SimpleNamespace(service=service, probe_capabilities=probe_capabilities)
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        scheduler_factory=lambda scheduler_type: scheduler,
        backend_mode="control",
    )
    monkeypatch.setattr(
        client,
        "_negotiate_backend",
        lambda capabilities: (
            setattr(client, "_selected_backend", CONTROL_BACKEND),
            setattr(client, "_selected_state_locator", "/home/test/.local/state/confflow/control"),
        ),
    )
    identity = ConfFlowExecutableIdentity(
        path="/opt/confflow/bin/confflow",
        realpath="/opt/confflow/bin/confflow",
        sha256="a" * 64,
        python="/opt/confflow/bin/python3.12",
        size=10,
        mtime_ns=20,
        device=30,
        inode=40,
    )
    contract = SimpleNamespace(
        accepted=True,
        as_dict=lambda: {"accepted": True, "producer": {"version": "2.1.2"}},
        remote_identity=SimpleNamespace(as_dict=lambda: {"cache_key": "cache"}),
    )
    measurements = iter([OSError("temporary SSH failure"), identity])
    monkeypatch.setattr(client, "_measure_producer_identity", lambda capabilities: _next_probe(measurements))
    monkeypatch.setattr(client, "_resolve_config_contract", lambda capabilities, identity: contract)
    monkeypatch.setattr(client, "_measure_control_identity", lambda capabilities: {"sha256": "d" * 64})
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    first_handle, first_outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert first_handle is None
    assert first_outcome.errors
    assert not transport.prepared
    assert not scheduler.calls

    second_handle, second_outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert second_handle is not None
    assert not second_outcome.errors
    assert probe_calls == 2
    assert len(transport.prepared) == 1
    assert len(scheduler.calls) == 1


def _next_probe(values):
    value = next(values)
    if isinstance(value, BaseException):
        raise value
    return value


def _fresh_client_with_selected_provenance(
    service: RunService,
    transport: FakeControlTransport,
    scheduler: FakeScheduler,
) -> SSHConfFlowClient:
    coordinator = type("Coordinator", (), {"service": service})()
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_transport_factory=lambda run_id, locator: transport,
        scheduler_factory=lambda scheduler_type: scheduler,
        backend_mode="control",
    )
    executable = "/opt/confflow/bin/confflow"
    capability_payload = {
        "schema_version": 4,
        "version": "2.1.2",
        "capabilities": {"control_worker": True, "dag": True, "resume": True, "workflow_state": True},
        "executable": {"path": executable, "python": "/opt/confflow/bin/python3.12", "sha256": "a" * 64},
        "producer": {"package": "confflow", "version": "2.1.2"},
    }
    client._selected_backend = CONTROL_BACKEND
    client._selected_state_locator = "/home/test/.local/state/confflow/control"
    client._selected_capability = ConfFlowCapabilities(
        schema_version=4,
        version="2.1.2",
        workflow_state=True,
        resume=True,
        dag=True,
        control_worker=True,
        executable=capability_payload["executable"],
        raw_payload=capability_payload,
    )
    identity = ConfFlowExecutableIdentity(
        path=executable,
        realpath=executable,
        sha256="a" * 64,
        python="/opt/confflow/bin/python3.12",
        size=10,
        mtime_ns=20,
        device=30,
        inode=40,
    )
    client._selected_executable_identity = identity
    client._selected_config_contract = SimpleNamespace(
        accepted=True,
        as_dict=lambda: {
            "accepted": True,
            "producer": {"package": "confflow", "version": "2.1.2"},
        },
        remote_identity=SimpleNamespace(as_dict=lambda: {"server_id": "server", "cache_key": "cache"}),
    )
    return client


def test_fresh_control_submit_persists_provenance_before_dispatch(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(confflow_executable="/opt/confflow/bin/confflow"), run_id="run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = _fresh_client_with_selected_provenance(service, transport, scheduler)
    monkeypatch.setattr(client, "_measure_control_identity", lambda capabilities: {"sha256": "d" * 64})
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is not None
    assert not outcome.errors
    assert len(transport.prepared) == 1
    assert len(scheduler.calls) == 1
    provenance = service.load_run_provenance("run-1")
    assert provenance is not None
    assert provenance["producer_version"] == "2.1.2"
    assert provenance["resolved_executable"] == "/opt/confflow/bin/confflow"
    assert provenance["config_contract"]["producer"]["version"] == "2.1.2"
    manifest = json.loads((tmp_path / "runs" / "run-1" / "provenance.json").read_text(encoding="utf-8"))
    assert manifest["executable_identity"]["sha256"] == "a" * 64


def test_control_submit_fails_closed_when_provenance_persistence_fails(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = _fresh_client_with_selected_provenance(service, transport, scheduler)
    monkeypatch.setattr(client, "_measure_control_identity", lambda capabilities: {"sha256": "d" * 64})
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    def fail_persistence(*args, **kwargs):
        raise OSError("provenance database is unavailable")

    monkeypatch.setattr(client._projection_store, "persist_confflow_provenance", fail_persistence)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is None
    assert outcome.errors
    assert "provenance database is unavailable" in outcome.errors[0].message
    assert not transport.prepared
    assert not scheduler.calls
