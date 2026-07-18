import json

from jobdesk_app.services.confflow_results import (
    ConfFlowStepProgress,
    format_step_progress,
    format_summary,
    load_step_progress,
    load_summary,
    load_workflow_state_progress,
)


def test_load_and_format_confflow_run_summary(tmp_path):
    path = tmp_path / "run_summary.json"
    path.write_text(
        json.dumps(
            {
                "initial_conformers": 12,
                "final_conformers": 3,
                "total_duration_seconds": 42.5,
                "step_status_counts": {"completed": 4},
                "lowest_conformer": {"cid": "water_0001", "energy": -76.4},
            }
        ),
        encoding="utf-8",
    )

    summary = load_summary(path)
    text = format_summary(summary)

    assert summary.initial_conformers == 12
    assert summary.final_conformers == 3
    assert "Final conformers: 3" in text
    assert "water_0001" in text


def test_load_step_progress_completed_and_running(tmp_path):
    """Workflow-stats file yields (completed, current) for the Runs page."""
    path = tmp_path / "workflow_stats.json"
    path.write_text(
        json.dumps(
            {
                "steps": [
                    {"name": "confgen", "status": "completed"},
                    {"name": "preopt", "status": "completed"},
                    {"name": "opt", "status": "running"},
                    {"name": "refine", "status": "pending"},
                ],
                "last_updated": "2026-07-06T22:00:00",
            }
        ),
        encoding="utf-8",
    )

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
    assert format_step_progress(ConfFlowStepProgress()) == ""


def test_load_workflow_state_progress_basic(tmp_path):
    """Parse v1.3.0 .workflow_state.json with completed steps."""
    state_path = tmp_path / ".workflow_state.json"
    state_path.write_text(
        json.dumps(
            {
                "run_id": "abc-123",
                "work_dir": "/tmp/work",
                "input_files": ["mol.xyz"],
                "original_inputs": ["mol.xyz"],
                "config_file": "workflow.yaml",
                "steps": {
                    "step_01_confgen": {
                        "name": "confgen",
                        "type": "confgen",
                        "status": "completed",
                        "submitted_at": 1000.0,
                        "completed_at": 1010.0,
                        "output_xyz": "search.xyz",
                        "error": None,
                        "executor_handle_data": None,
                        "fail_count": 0,
                    },
                    "step_02_opt": {
                        "name": "opt",
                        "type": "calc",
                        "status": "completed",
                        "submitted_at": 1011.0,
                        "completed_at": 1020.0,
                        "output_xyz": "output.xyz",
                        "error": None,
                        "executor_handle_data": None,
                        "fail_count": 0,
                    },
                    "step_03_sp": {
                        "name": "sp",
                        "type": "calc",
                        "status": "submitted",
                        "submitted_at": 1021.0,
                        "completed_at": None,
                        "output_xyz": None,
                        "error": None,
                        "executor_handle_data": {"job_id": 42},
                        "fail_count": 0,
                    },
                },
                "wavefront_index": 2,
                "started_at": 1000.0,
                "last_updated_at": 1021.5,
                "final_status": "",
            }
        ),
        encoding="utf-8",
    )

    progress = load_workflow_state_progress(state_path)
    assert progress.completed == ("confgen", "opt")
    assert progress.current == "sp"
    assert progress.final_status == ""
    assert progress.step_statuses["confgen"] == "completed"
    assert progress.step_statuses["opt"] == "completed"
    assert progress.step_statuses["sp"] == "submitted"


def test_load_workflow_state_progress_with_final_status(tmp_path):
    """Parse completed workflow with final_status."""
    state_path = tmp_path / ".workflow_state.json"
    state_path.write_text(
        json.dumps(
            {
                "run_id": "done-456",
                "work_dir": "/tmp/work",
                "input_files": ["mol.xyz"],
                "original_inputs": ["mol.xyz"],
                "config_file": "workflow.yaml",
                "steps": {
                    "step_01_confgen": {
                        "name": "confgen",
                        "type": "confgen",
                        "status": "completed",
                        "submitted_at": 1000.0,
                        "completed_at": 1010.0,
                        "output_xyz": "search.xyz",
                        "error": None,
                        "executor_handle_data": None,
                        "fail_count": 0,
                    },
                },
                "wavefront_index": 1,
                "started_at": 1000.0,
                "last_updated_at": 1010.5,
                "final_status": "completed",
            }
        ),
        encoding="utf-8",
    )

    progress = load_workflow_state_progress(state_path)
    assert progress.completed == ("confgen",)
    assert progress.current == ""
    assert progress.final_status == "completed"


def test_load_workflow_state_progress_missing_file(tmp_path):
    """Missing .workflow_state.json returns empty progress."""
    progress = load_workflow_state_progress(tmp_path / "nope.json")
    assert progress.completed == ()
    assert progress.current == ""
    assert progress.final_status == ""


def test_load_workflow_state_progress_malformed_json(tmp_path):
    """Malformed JSON degrades to empty progress."""
    state_path = tmp_path / ".workflow_state.json"
    state_path.write_text("not valid json {[[", encoding="utf-8")
    progress = load_workflow_state_progress(state_path)
    assert progress.completed == ()


def test_load_workflow_state_progress_partial_state(tmp_path):
    """Incomplete/half-written state file handled gracefully."""
    state_path = tmp_path / ".workflow_state.json"
    # Missing steps dict - partial write scenario
    state_path.write_text(
        json.dumps(
            {
                "run_id": "partial-789",
                "work_dir": "/tmp/work",
                # steps key missing - mid-write scenario
                "started_at": 1000.0,
                "last_updated_at": 1001.0,
            }
        ),
        encoding="utf-8",
    )
    progress = load_workflow_state_progress(state_path)
    assert progress.completed == ()
    assert progress.current == ""


def test_load_workflow_state_progress_failed_step(tmp_path):
    """Failed step reflected in step_statuses."""
    state_path = tmp_path / ".workflow_state.json"
    state_path.write_text(
        json.dumps(
            {
                "run_id": "fail-123",
                "work_dir": "/tmp/work",
                "input_files": ["mol.xyz"],
                "original_inputs": ["mol.xyz"],
                "config_file": "workflow.yaml",
                "steps": {
                    "step_01_confgen": {
                        "name": "confgen",
                        "type": "confgen",
                        "status": "completed",
                        "submitted_at": 1000.0,
                        "completed_at": 1010.0,
                        "output_xyz": "search.xyz",
                        "error": None,
                        "executor_handle_data": None,
                        "fail_count": 0,
                    },
                    "step_02_opt": {
                        "name": "opt",
                        "type": "calc",
                        "status": "failed",
                        "submitted_at": 1011.0,
                        "completed_at": 1015.0,
                        "output_xyz": None,
                        "error": "Segmentation fault",
                        "executor_handle_data": None,
                        "fail_count": 1,
                    },
                },
                "wavefront_index": 1,
                "started_at": 1000.0,
                "last_updated_at": 1015.5,
                "final_status": "failed",
            }
        ),
        encoding="utf-8",
    )
    progress = load_workflow_state_progress(state_path)
    assert progress.completed == ("confgen",)
    assert progress.final_status == "failed"
    assert progress.step_statuses["opt"] == "failed"
