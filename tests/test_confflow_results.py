import json

from jobdesk_app.services.confflow_results import (
    format_step_progress,
    format_summary,
    load_step_progress,
    load_summary,
)


def test_load_and_format_confflow_run_summary(tmp_path):
    path = tmp_path / "run_summary.json"
    path.write_text(json.dumps({
        "initial_conformers": 12,
        "final_conformers": 3,
        "total_duration_seconds": 42.5,
        "step_status_counts": {"completed": 4},
        "lowest_conformer": {"cid": "water_0001", "energy": -76.4},
    }), encoding="utf-8")

    summary = load_summary(path)
    text = format_summary(summary)

    assert summary.initial_conformers == 12
    assert summary.final_conformers == 3
    assert "Final conformers: 3" in text
    assert "water_0001" in text


def test_load_step_progress_completed_and_running(tmp_path):
    """Workflow-stats file yields (completed, current) for the Runs page."""
    path = tmp_path / "workflow_stats.json"
    path.write_text(json.dumps({
        "steps": [
            {"name": "confgen", "status": "completed"},
            {"name": "preopt",  "status": "completed"},
            {"name": "opt",     "status": "running"},
            {"name": "refine",  "status": "pending"},
        ],
        "last_updated": "2026-07-06T22:00:00",
    }), encoding="utf-8")

    progress = load_step_progress(path)
    assert progress.completed == ("confgen", "preopt")
    assert progress.current == "opt"
    assert progress.last_updated == "2026-07-06T22:00:00"

    rendered = format_step_progress(progress)
    assert "confgen" in rendered
    assert "opt" in rendered


def test_load_step_progress_missing_file_returns_empty(tmp_path):
    """Missing file never raises — caller decides how to render empty."""
    progress = load_step_progress(tmp_path / "nope.json")
    assert progress.completed == ()
    assert progress.current == ""
    assert progress.last_updated == ""


def test_load_step_progress_malformed_returns_empty(tmp_path):
    """JSON-parse errors degrade to empty progress, never raise."""
    path = tmp_path / "broken.json"
    path.write_text("not valid json {[", encoding="utf-8")
    progress = load_step_progress(path)
    assert progress.completed == ()


def test_format_step_progress_no_progress_returns_empty():
    """Empty progress renders as empty string so the GUI shows a blank cell."""
    from jobdesk_app.services.confflow_results import ConfFlowStepProgress

    assert format_step_progress(ConfFlowStepProgress()) == ""
