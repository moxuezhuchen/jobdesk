from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

import jobdesk_app.infrastructure.runtime.ssh_confflow_client as ssh_confflow_client_module
from jobdesk_app.application.confflow_client import SubmitRequest
from jobdesk_app.core.confflow_contract import (
    CAPABILITY_SCHEMA_VERSION,
    EXPECTED_ARTIFACTS,
    REQUIRED_COMMANDS,
)
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.infrastructure.remote.errors import SSHCommandError, SSHConnectionError
from jobdesk_app.infrastructure.remote.scheduler import ResourceSpec, SchedulerSubmitRejected
from jobdesk_app.infrastructure.runtime.confflow_control import ControlArtifactManifest, ControlSnapshot
from jobdesk_app.infrastructure.runtime.confflow_control_state import load_state, save_state
from jobdesk_app.infrastructure.runtime.run_service import RunService
from jobdesk_app.infrastructure.runtime.ssh_confflow_client import ConfFlowClientError, SSHConfFlowClient


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
    cancel_state: str = "cancelled"
    status_state: str = "queued"

    def prepare(self, request: dict[str, object]) -> ControlSnapshot:
        self.prepared.append(request)
        return ControlSnapshot(str(request["run_id"]), 1, "prepared")

    def execute(self, run_id: str) -> ControlSnapshot:
        self.execute_calls += 1
        raise AssertionError(f"direct control execute is not allowed during submit: {run_id}")

    def status(self, run_id: str) -> ControlSnapshot:
        return ControlSnapshot(run_id, 4, self.status_state)

    def events(self, run_id: str, *, after: str | None):
        del after
        raise AssertionError("events are outside submit")

    def cancel(self, run_id: str) -> ControlSnapshot:
        return ControlSnapshot(run_id, 3, self.cancel_state)

    def resume(self, run_id: str, *, checkpoint: str | None):
        del checkpoint
        return ControlSnapshot(run_id, 4, "queued")

    def artifacts(self, run_id: str) -> ControlArtifactManifest:
        return ControlArtifactManifest(ControlSnapshot(run_id, 2, "completed"), ())


class FakeScheduler:
    def __init__(
        self, *, service: RunService, sftp: FakeSFTP, job_id: str = "98765", lose_response: bool = False
    ) -> None:
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


class RejectingScheduler(FakeScheduler):
    def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
        self.calls.append((ssh, script_path, resources))
        raise SchedulerSubmitRejected("scheduler refused before accepting launcher")


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
    service.load_configuration_binding = lambda run_id: object()  # type: ignore[method-assign]
    coordinator = type(
        "Coordinator",
        (),
        {
            "service": service,
            "verify_configuration_binding": lambda self, server_id, binding, **kwargs: None,
        },
    )()
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
    from jobdesk_app.infrastructure.runtime.ssh_confflow_control import build_control_launcher_script

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
    assert 'completed_marker="${marker//$old_fragment/$new_fragment}"' in script
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
    from jobdesk_app.infrastructure.runtime.ssh_confflow_control import build_control_launcher_script

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


def test_missing_exact_remote_source_fails_before_prepare_launcher_or_marker(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    spec = RunSpec(
        server_id="server",
        remote_dir="/remote/submission workspace",
        command_template="confflow {name} -c {path}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/shared/source files/methane one.xyz")],
        supporting_sources=[RunSource("/remote/submission workspace/workflow.yaml")],
        workflow_kind=WorkflowKind.confflow,
    )
    service.create_run(spec, run_id="run-1")
    task = service.load_tasks("run-1")[0]
    assert task.remote_source_path == "/shared/source files/methane one.xyz"

    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    client._selected_backend = "control"
    client._selected_state_locator = "/home/test/.local/state/confflow/control"
    client._selected_capability = SimpleNamespace(control_worker=True)
    monkeypatch.setattr(client, "_measure_control_identity", lambda capability: {"sha256": "d" * 64})
    digested: list[str] = []

    def digest(run_id: str, locator: str, path: str) -> str:
        del run_id, locator
        digested.append(path)
        if path == task.remote_source_path:
            raise ConfFlowClientError("control remote source digest failed: missing")
        return "b" * 64

    monkeypatch.setattr(client, "_remote_digest", digest)
    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is None
    assert outcome.errors == ["control remote source digest failed: missing"]
    assert digested == [task.remote_config_path, task.remote_source_path]
    assert transport.prepared == []
    assert scheduler.calls == []
    assert sftp.files == {}
    assert load_state(service, "run-1") is None


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


def test_definitive_scheduler_rejection_is_durable_auditable_and_retryable(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    rejected = RejectingScheduler(service=service, sftp=sftp)
    client = _client(service, transport, rejected)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is None
    assert outcome.errors == ["scheduler refused before accepting launcher"]
    failed = load_state(service, "run-1")
    assert failed is not None
    assert failed["dispatch_state"] == "failed"
    assert failed["dispatch_outcome"] == "rejected"
    assert failed["dispatch_attempt"] == 1
    assert failed["dispatch_error"] == "scheduler refused before accepting launcher"
    assert isinstance(failed["dispatch_updated_at"], str)

    accepted = FakeScheduler(service=service, sftp=sftp, job_id="retry-123")
    client._scheduler_factory = lambda scheduler_type: accepted
    retry, retry_outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert retry is not None
    assert not retry_outcome.errors
    assert len(accepted.calls) == 1
    saved = load_state(service, "run-1")
    assert saved is not None
    assert saved["dispatch_state"] == "submitted"
    assert saved["dispatch_attempt"] == 2
    assert len(transport.prepared) == 2
    assert transport.prepared[0]["idempotency_key"] == transport.prepared[1]["idempotency_key"]
    assert transport.prepared[0]["request_digest"] == transport.prepared[1]["request_digest"]


def test_unknown_submit_result_records_bounded_reconciliation_without_retry(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)

    class UnknownScheduler(FakeScheduler):
        def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
            self.calls.append((ssh, script_path, resources))
            raise OSError("connection lost while awaiting scheduler response")

    scheduler = UnknownScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    _, first = client.submit_with_outcome(SubmitRequest("run-1"))
    assert first.errors
    state = load_state(service, "run-1")
    assert state is not None
    assert state["dispatch_state"] == "dispatching"
    assert state["dispatch_outcome"] == "unknown"
    assert state["reconcile_attempts"] == 0

    for expected_attempt in range(1, 5):
        _, repeated = client.submit_with_outcome(SubmitRequest("run-1"))
        assert any("unresolved" in error for error in repeated.errors)
        state = load_state(service, "run-1")
        assert state is not None
        assert state["reconcile_attempts"] == min(expected_attempt, 3)
    assert len(scheduler.calls) == 1

    client.confirm_unresolved_dispatch_not_accepted(
        "run-1", evidence="sacct and squeue show no job for the recorded launcher window"
    )
    resolved = load_state(service, "run-1")
    assert resolved is not None
    assert resolved["dispatch_state"] == "failed"
    assert resolved["recovery_state"] == "retry_authorized"
    assert resolved["dispatch_resolution"]["kind"] == "scheduler_non_acceptance"

    accepted = FakeScheduler(service=service, sftp=sftp, job_id="after-review")
    client._scheduler_factory = lambda scheduler_type: accepted
    retry, retry_outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert retry is not None
    assert not retry_outcome.errors
    assert len(accepted.calls) == 1


@pytest.mark.parametrize(
    "remote_error",
    [
        SSHCommandError("scheduler SSH command failed", command="nohup", stderr="connection reset"),
        SSHConnectionError("scheduler SSH connection dropped", host="scheduler.example"),
    ],
)
def test_scheduler_remote_error_is_durable_unknown_and_fail_closed(tmp_path, monkeypatch, remote_error) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)

    class RemoteErrorScheduler(FakeScheduler):
        def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
            self.calls.append((ssh, script_path, resources))
            raise remote_error

    scheduler = RemoteErrorScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is None
    assert outcome.errors
    state = load_state(service, "run-1")
    assert state is not None
    assert state["dispatch_state"] == "dispatching"
    assert state["dispatch_outcome"] == "unknown"
    assert state["dispatch_error"] == str(remote_error)
    assert isinstance(state["dispatch_updated_at"], str)

    _, repeated = client.submit_with_outcome(SubmitRequest("run-1"))
    assert any("unresolved" in error for error in repeated.errors)
    assert len(scheduler.calls) == 1


@pytest.mark.parametrize("job_id", ["", "   ", " 98765 "])
def test_invalid_scheduler_job_id_is_durable_unknown(tmp_path, monkeypatch, job_id: str) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)

    class EmptyJobIdScheduler(FakeScheduler):
        def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
            self.calls.append((ssh, script_path, resources))
            return job_id

    scheduler = EmptyJobIdScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert handle is None
    assert outcome.errors
    state = load_state(service, "run-1")
    assert state is not None
    assert state["dispatch_state"] == "dispatching"
    assert state["dispatch_outcome"] == "unknown"
    assert state["dispatch_error"] == "scheduler adapter returned an empty control launcher job id"
    assert isinstance(state["dispatch_updated_at"], str)


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


def test_completed_worker_without_producer_terminal_state_can_be_restarted(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    first_scheduler = FakeScheduler(service=service, sftp=sftp, job_id="worker-1")
    client = _client(service, transport, first_scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)
    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is not None and not outcome.errors

    state = load_state(service, "run-1")
    assert state is not None
    launcher = state["launcher"]
    assert isinstance(launcher, dict)
    marker_path = str(launcher["metadata_path"])
    marker = json.loads(sftp.files[marker_path].decode("utf-8"))
    marker.update(
        {
            "execution_state": "completed",
            "execute_rc": 0,
            "worker_started": True,
            "worker_rc": 17,
        }
    )
    sftp.files[marker_path] = json.dumps(marker).encode("utf-8")

    client.attach("run-1")
    failed = load_state(service, "run-1")
    assert failed is not None
    assert failed["dispatch_state"] == "failed"
    assert failed["dispatch_outcome"] == "worker_failed"
    assert failed["recovery_state"] == "worker_restart_required"

    recovery_scheduler = FakeScheduler(service=service, sftp=sftp, job_id="worker-2")
    client._scheduler_factory = lambda scheduler_type: recovery_scheduler
    recovered, recovery_outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert recovered is not None
    assert not recovery_outcome.errors
    assert len(transport.prepared) == 1
    assert len(recovery_scheduler.calls) == 1
    recovered_state = load_state(service, "run-1")
    assert recovered_state is not None
    assert recovered_state["dispatch_state"] == "submitted"
    assert recovered_state["dispatch_attempt"] == 2
    recovery_script = sftp.files[str(recovered_state["launcher"]["script_path"])]
    assert b"control execute" not in recovery_script
    assert b"confflow-control-worker" in recovery_script


def test_worker_recovery_remote_error_is_durable_unknown(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    first_scheduler = FakeScheduler(service=service, sftp=sftp, job_id="worker-1")
    client = _client(service, transport, first_scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)
    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is not None and not outcome.errors

    state = load_state(service, "run-1")
    assert state is not None
    launcher = state["launcher"]
    assert isinstance(launcher, dict)
    marker_path = str(launcher["metadata_path"])
    marker = json.loads(sftp.files[marker_path].decode("utf-8"))
    marker.update(
        {
            "execution_state": "completed",
            "execute_rc": 0,
            "worker_started": True,
            "worker_rc": 17,
        }
    )
    sftp.files[marker_path] = json.dumps(marker).encode("utf-8")
    client.attach("run-1")
    failed = load_state(service, "run-1")
    assert failed is not None
    assert failed["dispatch_outcome"] == "worker_failed"

    recovery_error = SSHCommandError("worker recovery SSH command failed", command="sbatch")

    class RemoteErrorRecoveryScheduler(FakeScheduler):
        def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
            self.calls.append((ssh, script_path, resources))
            raise recovery_error

    recovery_scheduler = RemoteErrorRecoveryScheduler(service=service, sftp=sftp)
    client._scheduler_factory = lambda scheduler_type: recovery_scheduler
    recovered, recovery_outcome = client.submit_with_outcome(SubmitRequest("run-1"))

    assert recovered is None
    assert recovery_outcome.errors
    recovered_state = load_state(service, "run-1")
    assert recovered_state is not None
    assert recovered_state["dispatch_state"] == "dispatching"
    assert recovered_state["dispatch_outcome"] == "unknown"
    assert recovered_state["dispatch_error"] == str(recovery_error)
    assert len(recovery_scheduler.calls) == 1


def test_dispatch_reconciliation_does_not_reuse_transport_after_session_exit(tmp_path, monkeypatch) -> None:
    """Status reconciliation must acquire a live transport after metadata I/O.

    The metadata read leases an SFTP/SSH session.  Once that context exits the
    underlying SSH channel may be closed or returned to the pool, so the
    authoritative producer status read must not call the old transport.
    """

    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp)
    scheduler = FakeScheduler(service=service, sftp=sftp, job_id="worker-1")
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)

    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is not None and not outcome.errors
    state = load_state(service, "run-1")
    assert state is not None
    launcher = state["launcher"]
    assert isinstance(launcher, dict)
    marker_path = str(launcher["metadata_path"])
    marker = json.loads(sftp.files[marker_path].decode("utf-8"))
    marker.update(
        {
            "execution_state": "completed",
            "execute_rc": 0,
            "worker_started": True,
            "worker_rc": 0,
        }
    )
    sftp.files[marker_path] = json.dumps(marker).encode("utf-8")

    class ClosingTransport(FakeControlTransport):
        closed: bool = False

        def status(self, run_id: str) -> ControlSnapshot:
            if self.closed:
                raise AssertionError("status called on a transport after session exit")
            return super().status(run_id)

    metadata_transport = ClosingTransport(sftp, status_state="running")
    status_transport = ClosingTransport(sftp, status_state="completed")

    @contextmanager
    def sessions(_run_id: str, _locator: str, *, need_sftp: bool):
        selected = metadata_transport if need_sftp else status_transport
        try:
            yield selected, sftp if need_sftp else None, None
        finally:
            selected.closed = True

    monkeypatch.setattr(client, "_control_session", sessions)

    recovered = client.attach("run-1")

    assert recovered.run_id == "run-1"
    assert metadata_transport.closed is True
    assert status_transport.closed is True


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


def test_cancel_projects_nonterminal_producer_acknowledgement_as_request_only(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp, cancel_state="running")
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)
    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is not None and not outcome.errors

    snapshot = handle.cancel()

    assert snapshot.producer_state == "running"
    assert service.repository.load_tasks("run-1")[0].status.value == "running"


def test_status_after_cancel_uses_same_authoritative_producer_snapshot_boundary(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    transport = FakeControlTransport(sftp, cancel_state="running", status_state="cancelled")
    scheduler = FakeScheduler(service=service, sftp=sftp)
    client = _client(service, transport, scheduler)
    monkeypatch.setattr(client, "_remote_digest", lambda run_id, locator, path: "b" * 64)
    handle, outcome = client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is not None and not outcome.errors

    requested = handle.cancel()
    completed = handle.status()

    assert requested.producer_state == "running"
    assert completed.producer_state == "cancelled"
    assert load_state(service, "run-1")["state"] == "cancelled"
    assert service.repository.load_tasks("run-1")[0].status.value == "cancelled"


def test_old_producer_is_rejected_before_control_backend_admission(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    coordinator = type(
        "Coordinator",
        (),
        {
            "service": service,
            "server_config": lambda self, server_id: SimpleNamespace(confflow_executable="/opt/confflow/bin/confflow"),
        },
    )()
    client = SSHConfFlowClient(coordinator, "server", backend_mode="control")
    old = ConfFlowCapabilities(
        CAPABILITY_SCHEMA_VERSION,
        "1.5.3",
        True,
        True,
        True,
        artifacts=EXPECTED_ARTIFACTS,
        commands={name: True for name in REQUIRED_COMMANDS},
        build={"commit": "old", "dirty": False},
        producer={
            "package": "confflow",
            "version": "1.5.3",
            "build": {"commit": "old", "dirty": False},
            "wheel": {"filename": "old.whl", "sha256": "0" * 64},
        },
        executable={
            "path": "/opt/confflow/bin/confflow",
            "sha256": "0" * 64,
            "python": "/opt/confflow/bin/python3.12",
        },
        raw_payload={},
        control_worker=True,
    )

    with pytest.raises(ValueError, match="incompatible ConfFlow version"):
        client._negotiate_backend(old)
