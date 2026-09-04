"""Unit tests for the Runs application query and selection boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobdesk_app.application.facades import RunSummary
from jobdesk_app.application.runs_query import (
    FacadeRunQueryService,
    RunFilterSpec,
    RunQueryController,
    RunQuerySnapshot,
    RunSelectionState,
    filter_run_snapshots,
)


def test_facade_query_adapter_reads_public_run_summaries_without_workspace_leakage():
    expected = RunSummary(
        run_id="run-facade",
        server_id="wsl",
        workflow_kind="dag",
        created_at="2026-09-04T00:00:00",
        status_counts=(("running", 1),),
    )

    class Facade:
        def list_runs(self, query=None):
            assert query is None
            return (expected,)

    assert FacadeRunQueryService(Facade()).list_runs() == [expected]  # type: ignore[arg-type]


def _record(run_id: str, *, status: dict[str, int] | None = None):
    return SimpleNamespace(
        run_id=run_id,
        server_id="wsl",
        remote_dir=f"/runs/{run_id}",
        command_template="g16 {name}",
        created_at="2026-08-24T00:00:00",
        status_summary=status or {"running": 1},
        workflow_kind=None,
    )


def test_query_controller_keeps_service_boundary_and_freezes_projection(tmp_path: Path):
    records = [_record("run-1", status={"running": 2})]
    created: list[Path] = []

    class Service:
        def list_runs(self):
            return records

    def factory(workspace: Path):
        created.append(workspace)
        return Service()

    result = RunQueryController(factory).list_runs(tmp_path)

    assert created == [tmp_path]
    assert result.records == tuple(records)
    snapshot = result.snapshots[0]
    assert snapshot.run_id == "run-1"
    assert dict(snapshot.status_summary) == {"running": 2}
    with pytest.raises(TypeError):
        snapshot.status_summary["failed"] = 1  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        snapshot.run_id = "mutated"  # type: ignore[misc]


def test_selection_state_prunes_ids_and_applies_new_batch_once():
    state = RunSelectionState()
    state.remember(["old", "missing"], "old")

    assert state.reconcile(["old", "new"], "new") == "new"
    first = state.snapshot()
    assert first.selected_ids == frozenset({"old"})
    assert first.current_id == "new"
    assert first.applied_batch_id == "new"

    state.remember(["old", "new"], "old")
    assert state.reconcile(["old", "new"], "new") == "old"
    assert state.snapshot().current_id == "old"


def _snapshot(
    run_id: str,
    *,
    server_id: str,
    status: dict[str, int],
    workflow_kind: str | None,
    command_template: str,
    created_at: str,
) -> RunQuerySnapshot:
    return RunQuerySnapshot(
        run_id=run_id,
        server_id=server_id,
        remote_dir=f"/runs/{run_id}",
        command_template=command_template,
        created_at=created_at,
        status_summary=status,
        workflow_kind=workflow_kind,
    )


def test_filter_predicate_covers_all_fields_and_date_windows():
    snapshots = (
        _snapshot(
            "RUN-ALPHA",
            server_id="Luna",
            status={"running": 1},
            workflow_kind="dag",
            command_template="Confflow water",
            created_at="2026-08-24T08:00:00",
        ),
        _snapshot(
            "run-beta",
            server_id="Luna",
            status={"downloaded": 1},
            workflow_kind="dag",
            command_template="confflow methane",
            created_at="2026-08-18T08:00:00",
        ),
        _snapshot(
            "run-gamma",
            server_id="Other",
            status={"failed": 1},
            workflow_kind="gaussian",
            command_template="g16 water",
            created_at="2026-07-01T08:00:00",
        ),
    )
    today = date(2026, 8, 24)

    assert filter_run_snapshots(snapshots, RunFilterSpec(), today=today) == snapshots
    assert filter_run_snapshots(
        snapshots,
        RunFilterSpec(
            search="ALPHA",
            status="active",
            server_id="Luna",
            workflow_kind="dag",
            date_range="today",
        ),
        today=today,
    ) == (snapshots[0],)
    assert filter_run_snapshots(
        snapshots,
        RunFilterSpec(status="completed", date_range="7d"),
        today=today,
    ) == (snapshots[1],)
    assert filter_run_snapshots(
        snapshots,
        RunFilterSpec(status="failed", server_id="Other", workflow_kind="gaussian"),
        today=today,
    ) == (snapshots[2],)
    assert (
        filter_run_snapshots(
            snapshots,
            RunFilterSpec(search="does-not-match"),
            today=today,
        )
        == ()
    )


def test_filter_spec_is_frozen_and_invalid_date_is_excluded_from_date_filter():
    spec = RunFilterSpec(date_range="today")
    with pytest.raises(FrozenInstanceError):
        spec.date_range = "all"  # type: ignore[misc]

    invalid = _snapshot(
        "invalid-date",
        server_id="wsl",
        status={"running": 1},
        workflow_kind=None,
        command_template="g16 water",
        created_at="not-a-date",
    )
    assert filter_run_snapshots((invalid,), spec, today=date(2026, 8, 24)) == ()
