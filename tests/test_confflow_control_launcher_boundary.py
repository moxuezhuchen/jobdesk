from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobdesk_app.remote.scheduler import ResourceSpec
from jobdesk_app.services.confflow_control_launcher import (
    ControlLauncher,
    PreparedControlLaunch,
    SchedulerResourceInput,
)


class FakeSFTP:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.uploads: list[str] = []

    def mkdir_p(self, remote_dir: str) -> None:
        del remote_dir

    def upload_file(self, local_path: Path, remote_path: str, **kwargs: object) -> object:
        del kwargs
        self.files[remote_path] = local_path.read_bytes()
        self.uploads.append(remote_path)
        return SimpleNamespace(status="transferred")

    def stat(self, remote_path: str) -> object | None:
        return SimpleNamespace() if remote_path in self.files else None

    def read_file_bytes(self, remote_path: str, max_bytes: int = 65536) -> bytes:
        return self.files[remote_path][:max_bytes]


@dataclass
class FakeStateStore:
    saves: list[dict[str, object]] = field(default_factory=list)

    def save(self, run_id: str, state: dict[str, object]) -> None:
        assert state["run_id"] == run_id
        self.saves.append(json.loads(json.dumps(state)))


class FakeScheduler:
    def __init__(self, job_id: str = "12345", error: Exception | None = None) -> None:
        self.job_id = job_id
        self.error = error
        self.calls: list[tuple[object, str, ResourceSpec]] = []

    def submit(self, ssh: object, script_path: str, resources: ResourceSpec) -> str:
        self.calls.append((ssh, script_path, resources))
        if self.error is not None:
            raise self.error
        return self.job_id


class NoPrepareTransport:
    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare(self, request: object) -> None:
        del request
        self.prepare_calls += 1
        raise AssertionError("prepared launches must not call prepare")


def _prepared(ssh: object | None = None) -> PreparedControlLaunch:
    return PreparedControlLaunch(
        run_id="run-1",
        remote_dir="/remote/project",
        state_root="/home/test/.local/state/confflow/control",
        handoff_path="/home/test/.local/state/confflow/run-1/input/worker-handoff.json",
        producer_executable="/opt/confflow/bin/confflow",
        worker_executable="/opt/confflow/bin/confflow-control-worker",
        scheduler=SchedulerResourceInput(
            resources={"cpus": 2},
            default_resources={"cpus": 1},
            overrides={"memory_mb": 4096},
        ),
        ssh=ssh or object(),
        prepared_state={
            "content_schema": "jobdesk.confflow.backend.v1",
            "run_id": "run-1",
            "backend": "control",
            "state_locator": "/home/test/.local/state/confflow/control",
            "state": "prepared",
        },
    )


def test_dispatch_uses_prepared_inputs_and_never_calls_prepare() -> None:
    sftp = FakeSFTP()
    store = FakeStateStore()
    scheduler = FakeScheduler()
    transport = NoPrepareTransport()
    launcher = ControlLauncher(
        sftp=sftp,
        state_store=store,
        scheduler_factory=lambda scheduler_type: scheduler,
    )

    result = launcher.dispatch(_prepared(transport))

    assert transport.prepare_calls == 0
    assert scheduler.calls[0][2].cpus == 2
    assert scheduler.calls[0][2].memory_mb == 4096
    assert result.scheduler_job_id == "12345"
    assert len(store.saves) == 2
    assert store.saves[0]["dispatch_state"] == "dispatching"
    assert store.saves[1]["dispatch_state"] == "submitted"
    assert result.script_path in sftp.files
    assert b"control execute" in sftp.files[result.script_path]


def test_ambiguous_scheduler_response_stays_unresolved_without_prepare() -> None:
    sftp = FakeSFTP()
    store = FakeStateStore()
    scheduler = FakeScheduler(error=TimeoutError("response lost after dispatch"))
    launcher = ControlLauncher(
        sftp=sftp,
        state_store=store,
        scheduler_factory=lambda scheduler_type: scheduler,
    )

    with pytest.raises(TimeoutError):
        launcher.dispatch(_prepared())
    dispatching = store.saves[-1]
    metadata_path = str(dispatching["launcher"]["metadata_path"])
    launcher_info = dispatching["launcher"]
    sftp.files[metadata_path] = json.dumps(
        {
            "content_schema": "jobdesk.confflow.launcher.v1",
            "run_id": "run-1",
            "scheduler_type": "nohup",
            "scheduler_job_id": "12345",
            "state_root": launcher_info["state_root"],
            "command": launcher_info["command"],
            "execution_state": "started",
            "execute_rc": None,
            "worker_started": False,
        }
    ).encode("utf-8")

    reconciled = launcher.reconcile("run-1", dispatching)

    assert reconciled["dispatch_state"] == "dispatching"
    assert len(store.saves) == 1


def test_success_marker_reconciliation_persists_submitted_state() -> None:
    sftp = FakeSFTP()
    store = FakeStateStore()
    launcher = ControlLauncher(
        sftp=sftp,
        state_store=store,
        scheduler_factory=lambda scheduler_type: FakeScheduler(),
    )
    state = {
        "run_id": "run-1",
        "backend": "control",
        "state_locator": "/home/test/.local/state/confflow/control",
        "scheduler_type": "nohup",
        "dispatch_state": "dispatching",
        "launcher": {
            "metadata_path": "/remote/project/.jobdesk-control/launcher/run-1.json",
            "state_root": "/home/test/.local/state/confflow/control",
            "command": "confflow control execute",
        },
    }
    path = state["launcher"]["metadata_path"]
    sftp.files[path] = json.dumps(
        {
            "content_schema": "jobdesk.confflow.launcher.v1",
            "run_id": "run-1",
            "scheduler_type": "nohup",
            "scheduler_job_id": "98765",
            "state_root": state["state_locator"],
            "command": state["launcher"]["command"],
            "execution_state": "started",
            "execute_rc": 0,
            "worker_started": True,
        }
    ).encode("utf-8")

    reconciled = launcher.reconcile("run-1", state)

    assert reconciled["dispatch_state"] == "submitted"
    assert reconciled["scheduler_job_id"] == "98765"
    assert store.saves[-1]["launcher"]["scheduler_job_id"] == "98765"
