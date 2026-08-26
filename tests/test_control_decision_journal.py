"""JD2b persistence tests for the SQLite-authoritative control decision."""

from __future__ import annotations

import json

import pytest

import jobdesk_app.services.confflow_control_state as control_state
import jobdesk_app.services.run_repository._control_decisions as decisions_module
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec
from jobdesk_app.services.run_repository import RunRepository
from jobdesk_app.services.run_repository._control_decisions import (
    CONTROL_DECISION_KIND,
    commit_control_decision,
    commit_control_decision_and_replace_tasks,
    import_legacy_control_decision,
    load_control_decision,
)


def _state(run_id: str = "control-run", **extra: object) -> dict[str, object]:
    return {"backend": "control", "run_id": run_id, "revision": 0, **extra}


def _service(tmp_path):
    from jobdesk_app.services.run_service import RunService

    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote",
            command_template="echo {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/input.xyz")],
        ),
        run_id="control-run",
    )
    return service


def test_commit_control_decision_is_single_cas_journal(tmp_path) -> None:
    repository = RunRepository(tmp_path / "runs")
    with repository._connection() as connection:  # noqa: SLF001 - connection-level journal contract
        first = commit_control_decision(connection, "control-run", _state(), expected_previous_revision=0)
        second = commit_control_decision(
            connection,
            "control-run",
            _state(revision=1, dispatch_state="dispatching"),
            expected_previous_revision=1,
        )
        assert first.operation_id == second.operation_id
        assert second.decision_revision == 2
        assert second.expected_previous_revision == 1
        rows = connection.execute(
            "SELECT kind, payload_json FROM operations WHERE run_id = ?", ("control-run",)
        ).fetchall()
        assert len(rows) == 1 and rows[0]["kind"] == CONTROL_DECISION_KIND
        payload = json.loads(rows[0]["payload_json"])
        assert payload["control_state"] == _state(revision=1, dispatch_state="dispatching")


def test_commit_control_decision_rejects_stale_revision(tmp_path) -> None:
    repository = RunRepository(tmp_path / "runs")
    with repository._connection() as connection:  # noqa: SLF001 - connection-level journal contract
        commit_control_decision(connection, "control-run", _state(), expected_previous_revision=0)
        with pytest.raises(ValueError, match="stale ConfFlow control decision"):
            commit_control_decision(connection, "control-run", _state(revision=1), expected_previous_revision=0)


def test_import_legacy_control_decision_is_idempotent_and_tamper_fails_closed(tmp_path) -> None:
    repository = RunRepository(tmp_path / "runs")
    state = _state(launcher={"script_path": "/remote/run.sh"})
    with repository._connection() as connection:  # noqa: SLF001 - connection-level journal contract
        imported = import_legacy_control_decision(connection, "control-run", state)
        assert import_legacy_control_decision(connection, "control-run", dict(state)) == imported
        connection.execute(
            "UPDATE operations SET payload_json = json_set(payload_json, '$.projection_sha256', ?) "
            "WHERE operation_id = ?",
            ("0" * 64, imported.operation_id),
        )
        with pytest.raises(ValueError, match="projection digest"):
            load_control_decision(connection, "control-run")


def test_failed_projection_write_retains_sqlite_decision_and_later_regenerates(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    control_state.save_state(service, "control-run", _state(dispatch_state="prepared"))
    desired = _state(revision=1, dispatch_state="dispatching")

    with monkeypatch.context() as patcher:
        patcher.setattr(
            control_state, "atomic_write_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))
        )
        with pytest.raises(OSError, match="disk full"):
            control_state.save_state(service, "control-run", desired)

    decision = service.repository.load_confflow_control_decision("control-run")
    assert decision is not None and decision.control_state == desired
    assert control_state.load_state(service, "control-run") == desired
    assert control_state.state_path(service, "control-run").read_bytes() == control_state.projection_bytes(desired)


def test_first_legacy_load_imports_once_without_rewriting_json(tmp_path) -> None:
    service = _service(tmp_path)
    path = control_state.state_path(service, "control-run")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_state(dispatch_state="prepared"), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    original = path.read_bytes()

    assert control_state.load_state(service, "control-run") == _state(dispatch_state="prepared")
    decision = service.repository.load_confflow_control_decision("control-run")
    assert decision is not None and decision.decision_revision == 1
    assert path.read_bytes() == original


def test_task_projection_failure_rolls_back_control_decision(tmp_path, monkeypatch) -> None:
    repository = RunRepository(tmp_path / "runs")
    monkeypatch.setattr(
        decisions_module,
        "_replace_tasks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("task projection failed")),
    )
    with pytest.raises(RuntimeError, match="task projection failed"):
        with repository._connection() as connection:  # noqa: SLF001 - connection-level journal contract
            commit_control_decision_and_replace_tasks(
                connection,
                "control-run",
                _state(),
                [],
                expected_previous_revision=0,
            )
    with repository._connection() as connection:  # noqa: SLF001 - connection-level journal contract
        assert load_control_decision(connection, "control-run") is None


def test_failed_projection_after_atomic_task_projection_recovers_from_sqlite(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    task = service.repository.load_tasks("control-run")[0]
    projected = [task.model_copy(update={"status": TaskStatus.running}, deep=True)]
    desired = _state(revision=2, state="running")

    with monkeypatch.context() as patcher:
        patcher.setattr(
            control_state, "atomic_write_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full"))
        )
        with pytest.raises(OSError, match="disk full"):
            control_state.save_state_with_task_projection(service, "control-run", desired, projected)

    assert service.repository.load_tasks("control-run")[0].status == TaskStatus.running
    assert control_state.load_state(service, "control-run") == desired


def test_rollback_projection_gate_rejects_stale_json_until_regenerated(tmp_path) -> None:
    service = _service(tmp_path)
    control_state.save_state(service, "control-run", _state(dispatch_state="prepared"))
    path = control_state.state_path(service, "control-run")
    path.write_text('{"backend":"control","run_id":"control-run"}\n', encoding="utf-8")

    assert not control_state.projection_matches_authority(service, "control-run")
    with pytest.raises(ValueError, match="regenerate it before rollback"):
        control_state.require_projection_matches_authority(service, "control-run")

    control_state.load_state(service, "control-run")
    assert control_state.projection_matches_authority(service, "control-run")


def test_rollback_gate_scans_all_projections_and_is_read_only(tmp_path) -> None:
    service = _service(tmp_path)
    control_state.save_state(service, "control-run", _state(dispatch_state="prepared"))
    path = control_state.state_path(service, "control-run")
    original = path.read_bytes()

    assert control_state.rollback_projection_errors(service) == []
    control_state.require_all_projections_match_authority(service)
    assert path.read_bytes() == original

    path.write_bytes(b'{"backend":"control","run_id":"control-run"}\n')
    errors = control_state.rollback_projection_errors(service)
    assert len(errors) == 1
    assert "stale" in errors[0]
    with pytest.raises(ValueError, match="stale"):
        control_state.require_all_projections_match_authority(service)
    assert path.read_bytes() != original


def test_rollback_gate_rejects_projection_without_authoritative_decision(tmp_path) -> None:
    service = _service(tmp_path)
    orphan = service.runs_dir / "orphan-run"
    orphan.mkdir()
    orphan_state = orphan / control_state.CONTROL_STATE_FILENAME
    orphan_state.write_text(
        json.dumps(_state(run_id="orphan-run"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    errors = control_state.rollback_projection_errors(service)
    assert errors == ["orphan-run: control JSON has no authoritative SQLite decision"]
