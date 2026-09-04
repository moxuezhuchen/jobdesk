"""P-H1 (R-H1) ParseState / ParseResult / load_summary_result tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobdesk_app.core.confflow_results import (
    ConfFlowSummary,
    ParseResult,
    ParseState,
    format_summary,
    load_summary,
    load_summary_result,
)


def test_load_summary_result_missing(tmp_path: Path) -> None:
    """Missing file → ``state=MISSING``, ``summary=None``."""
    result = load_summary_result(tmp_path / "absent.json")
    assert result.state is ParseState.MISSING
    assert result.summary is None


def test_load_summary_result_malformed_json(tmp_path: Path) -> None:
    """Invalid JSON → ``state=MALFORMED``, ``summary=None``."""
    target = tmp_path / "broken.json"
    target.write_text("{not json", encoding="utf-8")
    result = load_summary_result(target)
    assert result.state is ParseState.MALFORMED
    assert result.summary is None


def test_load_summary_result_not_a_dict(tmp_path: Path) -> None:
    """JSON list instead of object → ``state=MALFORMED``."""
    target = tmp_path / "list.json"
    target.write_text("[1, 2, 3]", encoding="utf-8")
    result = load_summary_result(target)
    assert result.state is ParseState.MALFORMED
    assert result.summary is None


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {"initial_conformers": 1, "final_conformers": 1, "total_duration_seconds": 1.0, "step_status_counts": []},
        {"initial_conformers": 1, "final_conformers": 1, "total_duration_seconds": 1.0, "step_status_counts": {"opt": "1"}},
        {"initial_conformers": 1, "final_conformers": 1, "total_duration_seconds": 1.0, "step_status_counts": {}, "lowest_conformer": "bad"},
    ),
)
def test_load_summary_result_rejects_incompatible_current_shapes(tmp_path: Path, payload: object) -> None:
    target = tmp_path / "summary.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert load_summary_result(target).state is ParseState.MALFORMED


def test_load_summary_result_ok(tmp_path: Path) -> None:
    """Clean payload → ``state=OK`` with the parsed summary."""
    target = tmp_path / "summary.json"
    target.write_text(
        json.dumps(
            {
                "initial_conformers": 5,
                "final_conformers": 3,
                "total_duration_seconds": 12.5,
                "step_status_counts": {"opt": 3},
                "lowest_conformer": {"energy": -42.0},
            }
        ),
        encoding="utf-8",
    )
    result = load_summary_result(target)
    assert result.state is ParseState.OK
    assert isinstance(result.summary, ConfFlowSummary)
    assert result.summary.initial_conformers == 5
    assert result.summary.final_conformers == 3
    assert result.summary.total_duration_seconds == 12.5
    assert result.summary.step_status_counts == {"opt": 3}
    assert result.summary.lowest_conformer == {"energy": -42.0}


def test_load_summary_result_wrong_field_type(tmp_path: Path) -> None:
    """Wrong field type → ``state=MALFORMED``."""
    target = tmp_path / "summary.json"
    target.write_text(
        json.dumps({"initial_conformers": "not-an-int"}),
        encoding="utf-8",
    )
    result = load_summary_result(target)
    assert result.state is ParseState.MALFORMED
    assert result.summary is None


def test_legacy_load_summary_returns_zero_value_for_missing(tmp_path: Path) -> None:
    """Old ``load_summary`` keeps the never-raise contract."""
    summary = load_summary(tmp_path / "absent.json")
    assert summary.initial_conformers == 0
    assert summary.final_conformers == 0
    assert summary.total_duration_seconds == 0.0


def test_legacy_load_summary_forwards_to_new_api_on_ok(tmp_path: Path) -> None:
    """Old ``load_summary`` forwards to the new API for an OK payload."""
    target = tmp_path / "summary.json"
    target.write_text(json.dumps({"initial_conformers": 7}), encoding="utf-8")
    summary = load_summary(target)
    assert summary.initial_conformers == 0


def test_format_summary_tolerates_none() -> None:
    """R-H1: ``format_summary(None)`` returns empty string instead of crashing."""
    assert format_summary(None) == ""


def test_parse_result_summary_field_independent_of_state() -> None:
    """``summary`` is None for non-OK states, populated for OK."""
    ok = ParseResult(state=ParseState.OK, summary=ConfFlowSummary(initial_conformers=1, final_conformers=1, total_duration_seconds=0.0))
    missing = ParseResult(state=ParseState.MISSING, summary=None)
    malformed = ParseResult(state=ParseState.MALFORMED, summary=None)
    assert ok.summary is not None
    assert missing.summary is None
    assert malformed.summary is None
