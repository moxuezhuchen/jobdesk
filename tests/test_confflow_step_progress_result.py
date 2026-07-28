"""P-L2 (R-L2) load_step_progress_result API tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobdesk_app.services.confflow_results import (
    ConfFlowStepProgress,
    ParseState,
    load_step_progress,
    load_step_progress_result,
)


def test_load_step_progress_result_missing(tmp_path: Path) -> None:
    """Missing file → ``state=MISSING``, ``summary`` is an empty progress."""
    result = load_step_progress_result(tmp_path / "absent.json")
    assert result.state is ParseState.MISSING
    assert isinstance(result.summary, ConfFlowStepProgress)
    assert result.summary.completed == ()


def test_load_step_progress_result_malformed_json(tmp_path: Path) -> None:
    """Invalid JSON → ``state=MALFORMED``, ``summary`` is an empty progress."""
    target = tmp_path / "broken.json"
    target.write_text("{not json", encoding="utf-8")
    result = load_step_progress_result(target)
    assert result.state is ParseState.MALFORMED
    assert isinstance(result.summary, ConfFlowStepProgress)
    assert result.summary.completed == ()


def test_load_step_progress_result_not_a_dict(tmp_path: Path) -> None:
    """JSON list (not a dict) → ``state=MALFORMED``."""
    target = tmp_path / "list.json"
    target.write_text("[1, 2]", encoding="utf-8")
    result = load_step_progress_result(target)
    assert result.state is ParseState.MALFORMED


@pytest.mark.parametrize(
    "payload",
    (
        {"steps": {}},
        {"steps": ["broken"]},
        {"steps": [{"name": "opt"}]},
        {"steps": [{"name": " ", "status": "completed"}]},
        {"steps": [{"name": "opt", "status": " "}]},
    ),
)
def test_load_step_progress_result_rejects_incompatible_steps(tmp_path: Path, payload: object) -> None:
    target = tmp_path / "stats.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    assert load_step_progress_result(target).state is ParseState.MALFORMED


def test_load_step_progress_result_ok(tmp_path: Path) -> None:
    """Clean payload → ``state=OK`` with the parsed steps."""
    target = tmp_path / "stats.json"
    target.write_text(
        json.dumps(
            {
                "steps": [
                    {"name": "confgen", "status": "completed"},
                    {"name": "opt", "status": "running"},
                ],
                "last_updated": "2026-07-10T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    result = load_step_progress_result(target)
    assert result.state is ParseState.OK
    assert isinstance(result.summary, ConfFlowStepProgress)
    assert result.summary.completed == ("confgen",)
    assert result.summary.current == "opt"
    assert result.summary.last_updated == "2026-07-10T00:00:00Z"


def test_load_step_progress_result_ok_with_extra_keys(tmp_path: Path) -> None:
    """Forward-compatible: extra keys allowed when the file is valid."""
    target = tmp_path / "stats.json"
    target.write_text(
        json.dumps(
            {
                "steps": [{"name": "opt", "status": "completed"}],
                "last_updated": "...",
                "future_field": "ignored",
            }
        ),
        encoding="utf-8",
    )
    result = load_step_progress_result(target)
    assert result.state is ParseState.OK
    assert result.summary.completed == ("opt",)


def test_legacy_load_step_progress_returns_empty_for_missing(tmp_path: Path) -> None:
    """Old ``load_step_progress`` keeps the never-raise contract."""
    progress = load_step_progress(tmp_path / "absent.json")
    assert isinstance(progress, ConfFlowStepProgress)
    assert progress.completed == ()


def test_legacy_load_step_progress_forwards_to_inner(tmp_path: Path) -> None:
    """Old ``load_step_progress`` parses a clean payload the same way."""
    target = tmp_path / "stats.json"
    target.write_text(
        json.dumps({"steps": [{"name": "opt", "status": "completed"}]}),
        encoding="utf-8",
    )
    progress = load_step_progress(target)
    assert progress.completed == ("opt",)
