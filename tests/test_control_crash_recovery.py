"""Restart-boundary crash matrix for the control journal and marker protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import jobdesk_app.infrastructure.persistence.sqlite_runs._control_decisions as decisions_module
import jobdesk_app.infrastructure.runtime.confflow_control_state as control_state
from jobdesk_app.application.confflow_client import SubmitRequest
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.infrastructure.runtime.run_service import RunService
from tests.test_confflow_launcher_integration import (
    FakeControlTransport,
    FakeScheduler,
    FakeSFTP,
    _client,
    _seed_control_state,
    _spec,
)


class SimulatedCrash(BaseException):
    """Model process loss without allowing cleanup code to run."""


def _restart(root: Path) -> RunService:
    """Open a fresh service/repository over the same durable directories."""
    return RunService(root, runs_dir=root / "runs")


def _desired_state() -> dict[str, object]:
    return {
        "content_schema": "jobdesk.confflow.backend.v1",
        "run_id": "run-1",
        "backend": "control",
        "protocol_schema": "confflow.control.v1",
        "state_locator": "/home/test/.local/state/confflow/control",
        "idempotency_key": "jobdesk.run-1",
        "producer_identity": {"sha256": "d" * 64},
        "capability": {"capabilities": {"control_worker": True}},
        "revision": 0,
        "state": "prepared",
        "dispatch_state": "dispatching",
        "dispatch_outcome": "pending",
        "dispatch_attempt": 1,
    }


def test_restart_after_crash_before_sqlite_commit_discards_uncommitted_decision(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    before = control_state.load_state(service, "run-1")
    assert before is not None
    projection_before = control_state.state_path(service, "run-1").read_bytes()

    with pytest.raises(SimulatedCrash):
        with service.repository._connection() as connection:  # noqa: SLF001 - crash boundary test
            decisions_module.commit_control_decision(
                connection,
                "run-1",
                _desired_state(),
                expected_previous_revision=1,
            )
            raise SimulatedCrash

    restarted = _restart(tmp_path)
    assert control_state.load_state(restarted, "run-1") == before
    assert control_state.state_path(restarted, "run-1").read_bytes() == projection_before
    decision = restarted.repository.load_confflow_control_decision("run-1")
    assert decision is not None and decision.decision_revision == 1


def test_restart_after_sqlite_commit_before_projection_write_regenerates_once(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    desired = _desired_state()

    def crash_after_commit(*_args, **_kwargs):
        raise SimulatedCrash

    with monkeypatch.context() as patcher:
        patcher.setattr(control_state, "atomic_write_text", crash_after_commit)
        with pytest.raises(SimulatedCrash):
            control_state.save_state(service, "run-1", desired)

    restarted = _restart(tmp_path)
    assert control_state.load_state(restarted, "run-1") == desired
    decision = restarted.repository.load_confflow_control_decision("run-1")
    assert decision is not None and decision.decision_revision == 2
    operation_id = decision.operation_id
    projection = control_state.state_path(restarted, "run-1").read_bytes()
    assert projection == control_state.projection_bytes(desired)

    # A second restart/read only verifies and does not append another journal
    # row or create a new decision revision.
    restarted_again = _restart(tmp_path)
    assert control_state.load_state(restarted_again, "run-1") == desired
    decision_again = restarted_again.repository.load_confflow_control_decision("run-1")
    assert decision_again is not None
    assert decision_again.operation_id == operation_id
    assert decision_again.decision_revision == 2


@pytest.mark.parametrize("failure_point", ["projection-temp-write", "projection-replace"])
def test_projection_crash_points_recover_from_sqlite_authority(tmp_path, monkeypatch, failure_point: str) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    path = control_state.state_path(service, "run-1")
    old_bytes = path.read_bytes()
    desired = _desired_state()

    if failure_point == "projection-temp-write":

        def crash_after_temp_write(target: Path, content: str, **_kwargs) -> None:
            temp = target.with_name(f".{target.name}.crash.tmp")
            temp.write_text(content, encoding="utf-8", newline="")
            raise SimulatedCrash

        failure = crash_after_temp_write
        patch_target = control_state
        patch_name = "atomic_write_text"
    else:
        original_replace = Path.replace

        def crash_before_replace(source: Path, target: Path):
            if target == path and source.name.startswith(f".{path.name}."):
                raise SimulatedCrash
            return original_replace(source, target)

        failure = crash_before_replace
        patch_target = Path
        patch_name = "replace"

    with monkeypatch.context() as patcher:
        patcher.setattr(patch_target, patch_name, failure)
        with pytest.raises(SimulatedCrash):
            control_state.save_state(service, "run-1", desired)

    # The compatibility projection is either still old or absent; the
    # journal has already committed the intended state.
    assert path.read_bytes() == old_bytes
    restarted = _restart(tmp_path)
    assert control_state.load_state(restarted, "run-1") == desired
    assert path.read_bytes() == control_state.projection_bytes(desired)
    assert restarted.repository.load_confflow_control_decision("run-1").decision_revision == 2


def test_restart_after_remote_marker_persisted_but_scheduler_response_lost_is_idempotent(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    first_transport = FakeControlTransport(sftp)
    first_scheduler = FakeScheduler(service=service, sftp=sftp, job_id="crash-1", lose_response=True)
    first_client = _client(service, first_transport, first_scheduler)
    monkeypatch.setattr(first_client, "_remote_digest", lambda *_args: "b" * 64)

    handle, outcome = first_client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is None and outcome.errors
    assert first_scheduler.calls and control_state.load_state(service, "run-1")["dispatch_state"] == "dispatching"

    restarted = _restart(tmp_path)
    second_transport = FakeControlTransport(sftp)
    second_scheduler = FakeScheduler(service=restarted, sftp=sftp, job_id="duplicate-must-not-run")
    second_client = _client(restarted, second_transport, second_scheduler)
    recovered = second_client.attach("run-1")
    assert recovered.run_id == "run-1"
    recovered_state = control_state.load_state(restarted, "run-1")
    assert recovered_state is not None
    assert recovered_state["dispatch_state"] == "submitted"
    assert recovered_state["scheduler_job_id"] == "crash-1"
    revision = restarted.repository.load_confflow_control_decision("run-1").decision_revision

    repeated, repeated_outcome = second_client.submit_with_outcome(SubmitRequest("run-1"))
    assert repeated is not None and not repeated_outcome.errors
    assert second_scheduler.calls == []
    assert restarted.repository.load_confflow_control_decision("run-1").decision_revision == revision


def test_restart_with_missing_remote_marker_remains_unresolved_and_never_terminal(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    first_transport = FakeControlTransport(sftp)

    class MarkerLostScheduler(FakeScheduler):
        def submit(self, ssh, script_path: str, resources):
            self.calls.append((ssh, script_path, resources))
            raise TimeoutError("crashed before marker persistence")

    first_scheduler = MarkerLostScheduler(service=service, sftp=sftp)
    first_client = _client(service, first_transport, first_scheduler)
    monkeypatch.setattr(first_client, "_remote_digest", lambda *_args: "b" * 64)
    _, outcome = first_client.submit_with_outcome(SubmitRequest("run-1"))
    assert outcome.errors

    restarted = _restart(tmp_path)
    second_transport = FakeControlTransport(sftp)
    second_scheduler = FakeScheduler(service=restarted, sftp=sftp)
    second_client = _client(restarted, second_transport, second_scheduler)
    recovered = second_client.attach("run-1")
    state = control_state.load_state(restarted, "run-1")
    assert recovered.run_id == "run-1"
    assert state is not None
    assert state["dispatch_state"] == "dispatching"
    assert state.get("dispatch_outcome") in {"pending", "unknown"}
    assert state.get("state") == "prepared"

    _, retry_outcome = second_client.submit_with_outcome(SubmitRequest("run-1"))
    assert retry_outcome.errors
    assert second_scheduler.calls == []
    assert control_state.load_state(restarted, "run-1")["dispatch_state"] == "dispatching"


def test_restart_with_truncated_remote_marker_fails_closed_without_terminal_or_duplicate_dispatch(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    first_transport = FakeControlTransport(sftp)
    first_scheduler = FakeScheduler(service=service, sftp=sftp, job_id="marker-1", lose_response=True)
    first_client = _client(service, first_transport, first_scheduler)
    monkeypatch.setattr(first_client, "_remote_digest", lambda *_args: "b" * 64)
    _, outcome = first_client.submit_with_outcome(SubmitRequest("run-1"))
    assert outcome.errors
    state = control_state.load_state(service, "run-1")
    assert state is not None
    launcher = state["launcher"]
    assert isinstance(launcher, dict)
    sftp.files[str(launcher["metadata_path"])] = b'{"content_schema":"jobdesk.confflow.launcher.v1"'

    restarted = _restart(tmp_path)
    second_transport = FakeControlTransport(sftp)
    second_scheduler = FakeScheduler(service=restarted, sftp=sftp)
    second_client = _client(restarted, second_transport, second_scheduler)
    with pytest.raises(Exception, match="metadata is malformed JSON"):
        second_client.attach("run-1")
    unchanged = control_state.load_state(restarted, "run-1")
    assert unchanged is not None
    assert unchanged["dispatch_state"] == "dispatching"
    assert unchanged.get("state") == "prepared"
    assert second_scheduler.calls == []


def test_completed_remote_marker_without_terminal_producer_state_never_reports_terminal_after_restart(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    _seed_control_state(service, "run-1")
    sftp = FakeSFTP()
    first_transport = FakeControlTransport(sftp)
    first_scheduler = FakeScheduler(service=service, sftp=sftp, job_id="worker-1")
    first_client = _client(service, first_transport, first_scheduler)
    monkeypatch.setattr(first_client, "_remote_digest", lambda *_args: "b" * 64)
    handle, outcome = first_client.submit_with_outcome(SubmitRequest("run-1"))
    assert handle is not None and not outcome.errors
    state = control_state.load_state(service, "run-1")
    assert state is not None
    launcher = state["launcher"]
    assert isinstance(launcher, dict)
    marker_path = str(launcher["metadata_path"])
    marker = json.loads(sftp.files[marker_path].decode("utf-8"))
    marker.update({"execution_state": "completed", "execute_rc": 0, "worker_started": True, "worker_rc": 17})
    sftp.files[marker_path] = json.dumps(marker).encode("utf-8")

    restarted = _restart(tmp_path)
    second_transport = FakeControlTransport(sftp, status_state="queued")
    second_scheduler = FakeScheduler(service=restarted, sftp=sftp)
    second_client = _client(restarted, second_transport, second_scheduler)
    second_client.attach("run-1")
    failed = control_state.load_state(restarted, "run-1")
    assert failed is not None
    assert failed["dispatch_state"] == "failed"
    assert failed["dispatch_outcome"] == "worker_failed"
    assert failed.get("state") == "prepared"
    assert restarted.repository.load_tasks("run-1")[0].status not in {
        TaskStatus.remote_completed,
        TaskStatus.downloaded,
        TaskStatus.analyzed,
    }
    assert second_scheduler.calls == []
