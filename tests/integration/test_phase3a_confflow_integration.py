"""Phase 3A — JobDesk → ConfFlow Python API integration tests.

Covers the library surface that JobDesk calls to drive ConfFlow:
- ``confflow.run_workflow`` as a Python library (no CLI subprocess)
- ``confflow_results.load_summary`` / ``load_step_progress`` on real artifact shapes
- ``ConfFlowAdapter.build_spec`` / ``build_dag_spec`` round-trips
- DAG workflow execution with explicit ``inputs`` edges

These tests run on Windows (no WSL required) and inject a fake ConfFlow
calculation executor so they are fast and hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from confflow.calc.executor import CalcHandle, CalcStatus

# ---------------------------------------------------------------------------
# Artifact fixtures
# ---------------------------------------------------------------------------

METANE_XYZ = """5
methane
C   0.000000   0.000000   0.000000
H   0.629118   0.629118   0.629118
H  -0.629118  -0.629118   0.629118
H  -0.629118   0.629118  -0.629118
H   0.629118  -0.629118  -0.629118
"""


def _coords_snapshot(coords: Any) -> Any:
    """Convert executor coordinates into a comparable immutable snapshot."""
    if hasattr(coords, "tolist"):
        coords = coords.tolist()
    if isinstance(coords, (list, tuple)):
        return tuple(_coords_snapshot(item) for item in coords)
    return coords


@pytest.fixture
def methane_xyz(tmp_path: Path) -> Path:
    p = tmp_path / "methane.xyz"
    p.write_text(METANE_XYZ, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# run_workflow as a library call
# ---------------------------------------------------------------------------


class FakeCalcExecutor:
    """In-memory v1.4.0 executor that never launches a calculation process."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.calc_submissions: list[dict[str, Any]] = []
        self.polls: list[str] = []
        self._handles: dict[str, CalcHandle] = {}

    def submit(
        self,
        work_dir: str,
        job_name: str,
        policy: Any,
        coords: Any,
        config: dict[str, Any],
        cmd: list[str],
        env: dict[str, str] | None,
    ) -> CalcHandle:
        del config, cmd, env
        step_dir = Path(work_dir)
        step_dir.mkdir(parents=True, exist_ok=True)
        (step_dir / f"{job_name}.{policy.log_ext}").write_text(
            " Normal termination of Gaussian 16\n",
            encoding="utf-8",
        )
        (step_dir / f"{job_name}.err").write_text("", encoding="utf-8")
        handle = CalcHandle(
            job_name=job_name,
            work_dir=work_dir,
            submitted_at=0.0,
            executor_data={"fake": True},
        )
        self.submitted.append(job_name)
        self.calc_submissions.append(
            {
                "job_name": job_name,
                "work_dir": step_dir,
                "coords": _coords_snapshot(coords),
            }
        )
        self._handles[job_name] = handle
        return handle

    def is_terminal(self, handle: CalcHandle) -> bool:
        self.polls.append(handle.job_name)
        return True

    def succeeded(self, handle: CalcHandle) -> bool:
        return handle.job_name in self._handles

    def error(self, handle: CalcHandle) -> str | None:
        del handle
        return None

    def cancel(self, handle: CalcHandle) -> None:
        del handle

    def fetch_output(
        self,
        handle: CalcHandle,
        log: str,
        config: dict[str, Any],
        is_sp_task: bool = False,
    ) -> dict[str, Any]:
        del handle, log, config, is_sp_task
        return {
            "e_low": -40.51838331,
            "g_low": None,
            "g_corr": None,
            "final_coords": [
                "C 0.000000 0.000000 0.000000",
                "H 0.629118 0.629118 0.629118",
                "H -0.629118 -0.629118 0.629118",
                "H -0.629118 0.629118 -0.629118",
                "H 0.629118 -0.629118 -0.629118",
            ],
            "num_imag_freqs": 0,
            "lowest_freq": None,
        }

    def poll(self, handle: CalcHandle) -> CalcStatus:
        return CalcStatus(is_terminal=True, succeeded=self.succeeded(handle), exit_code=0)


def test_run_workflow_library_call_opt_calc(methane_xyz: Path, tmp_path: Path) -> None:
    """JobDesk can call confflow.run_workflow() directly as a Python library."""
    from confflow.workflow.engine import run_workflow

    config_file = tmp_path / "confflow.yaml"
    config_file.write_text(
        "global:\n"
        "  cores_per_task: 1\n"
        "  total_memory: 1GB\n"
        "  charge: 0\n"
        "  multiplicity: 1\n"
        "steps:\n"
        "  - name: g16_opt\n"
        "    type: calc\n"
        "    params:\n"
        "      iprog: g16\n"
        "      itask: opt\n"
        "      keyword: b3lyp/6-31g(d)\n"
        "      cores_per_task: 1\n"
        "      total_memory: 1GB\n"
    )

    work_dir = tmp_path / "work"

    executor = FakeCalcExecutor()
    with patch("confflow.blocks.viz.generate_text_report", return_value=""):
        stats = run_workflow(
            input_xyz=[str(methane_xyz)],
            config_file=str(config_file),
            work_dir=str(work_dir),
            resume=False,
            verbose=False,
            calc_executor=executor,
        )

    assert isinstance(stats, dict)
    assert "steps" in stats
    assert len(stats["steps"]) == 1
    assert stats["steps"][0]["name"] == "g16_opt"
    assert stats["steps"][0]["status"] == "completed"
    assert stats["steps"][0]["input_conformers"] == 1

    assert (work_dir / "g16_opt" / "output.xyz").exists()
    assert (work_dir / "workflow_stats.json").exists()

    raw_stats = json.loads((work_dir / "workflow_stats.json").read_text(encoding="utf-8"))
    assert "steps" in raw_stats
    assert raw_stats["steps"][0]["name"] == "g16_opt"
    assert raw_stats["steps"][0]["status"] == "completed"


def test_run_workflow_resume_skips_completed_step(methane_xyz: Path, tmp_path: Path) -> None:
    """Resume re-runs only unfinished steps; completed ones are skipped."""
    from confflow.workflow.engine import run_workflow

    config_file = tmp_path / "confflow.yaml"
    config_file.write_text(
        "global:\n"
        "  cores_per_task: 1\n"
        "  total_memory: 1GB\n"
        "  charge: 0\n"
        "  multiplicity: 1\n"
        "steps:\n"
        "  - name: s1\n"
        "    type: calc\n"
        "    params:\n"
        "      iprog: g16\n"
        "      itask: opt\n"
        "      keyword: b3lyp/6-31g(d)\n"
    )

    work_dir = tmp_path / "work"

    executor = FakeCalcExecutor()
    with patch("confflow.blocks.viz.generate_text_report", return_value=""):
        first = run_workflow(
            [str(methane_xyz)],
            str(config_file),
            str(work_dir),
            resume=False,
            verbose=False,
            calc_executor=executor,
        )
        assert first["steps"][0]["status"] == "completed"

    with patch("confflow.blocks.viz.generate_text_report", return_value=""):
        second = run_workflow(
            [str(methane_xyz)],
            str(config_file),
            str(work_dir),
            resume=True,
            verbose=False,
            calc_executor=executor,
        )
        assert second.get("steps") is None or second["steps"] == []
        assert executor.submitted == ["A000001"]


# ---------------------------------------------------------------------------
# Results parsing
# ---------------------------------------------------------------------------

RUN_SUMMARY_FIXTURE = {
    "initial_conformers": 1,
    "final_conformers": 1,
    "total_duration_seconds": 12.5,
    "step_status_counts": {"completed": 1, "running": 0, "failed": 0, "skipped": 0},
    "lowest_conformer": {"cid": "A000001", "energy": -40.51838331},
}

WORKFLOW_STATS_FIXTURE = {
    "steps": [
        {"name": "g16_opt", "status": "completed", "input_conformers": 1, "output_conformers": 1},
        {"name": "g16_freq", "status": "running", "input_conformers": 1, "output_conformers": 0},
    ],
    "last_updated": "2026-07-17T10:30:00",
}


def test_load_summary_round_trip(tmp_path: Path) -> None:
    """load_summary parses run_summary.json and returns a frozen dataclass."""
    from jobdesk_app.services.confflow_results import ConfFlowSummary, load_summary

    summary_path = tmp_path / "run_summary.json"
    summary_path.write_text(json.dumps(RUN_SUMMARY_FIXTURE), encoding="utf-8")

    summary = load_summary(summary_path)
    assert isinstance(summary, ConfFlowSummary)
    assert summary.initial_conformers == 1
    assert summary.final_conformers == 1
    assert summary.total_duration_seconds == 12.5
    assert summary.step_status_counts == {"completed": 1, "running": 0, "failed": 0, "skipped": 0}
    assert summary.lowest_conformer == {"cid": "A000001", "energy": -40.51838331}


def test_load_summary_missing_file_returns_defaults(tmp_path: Path) -> None:
    """load_summary returns zeroed summary when the file does not exist."""
    from jobdesk_app.services.confflow_results import ConfFlowSummary, load_summary

    summary = load_summary(tmp_path / "nonexistent.json")
    assert isinstance(summary, ConfFlowSummary)
    assert summary.initial_conformers == 0
    assert summary.final_conformers == 0


def test_load_step_progress_completed_steps(tmp_path: Path) -> None:
    """load_step_progress extracts completed step names from workflow_stats.json."""
    from jobdesk_app.services.confflow_results import (
        ConfFlowStepProgress,
        load_step_progress,
    )

    stats_path = tmp_path / "workflow_stats.json"
    stats_path.write_text(json.dumps(WORKFLOW_STATS_FIXTURE), encoding="utf-8")

    progress = load_step_progress(stats_path)
    assert isinstance(progress, ConfFlowStepProgress)
    assert "g16_opt" in progress.completed
    assert "g16_freq" not in progress.completed
    assert progress.current == "g16_freq"
    assert progress.last_updated == "2026-07-17T10:30:00"


def test_load_step_progress_empty_when_missing(tmp_path: Path) -> None:
    """load_step_progress returns empty progress for non-existent file."""
    from jobdesk_app.services.confflow_results import ConfFlowStepProgress, load_step_progress

    progress = load_step_progress(tmp_path / "nonexistent.json")
    assert isinstance(progress, ConfFlowStepProgress)
    assert progress.completed == ()
    assert progress.current == ""


def test_format_summary_lines(tmp_path: Path) -> None:
    """format_summary renders a human-readable one-line summary."""
    from jobdesk_app.services.confflow_results import format_summary, load_summary

    summary_path = tmp_path / "run_summary.json"
    summary_path.write_text(json.dumps(RUN_SUMMARY_FIXTURE), encoding="utf-8")
    summary = load_summary(summary_path)
    text = format_summary(summary)
    assert "1" in text
    assert "-40.518" in text


def test_format_step_progress_done_only(tmp_path: Path) -> None:
    """format_step_progress renders done-only progress cleanly."""
    from jobdesk_app.services.confflow_results import (
        ConfFlowStepProgress,
        format_step_progress,
    )

    progress = ConfFlowStepProgress(completed=("s1", "s2"), current="", last_updated="")
    text = format_step_progress(progress)
    assert "s1" in text
    assert "s2" in text


# ---------------------------------------------------------------------------
# ConfFlowAdapter round-trips
# ---------------------------------------------------------------------------

def test_conf_flow_adapter_build_spec_single() -> None:
    """ConfFlowAdapter.build_spec builds a correct RunSpec for a single molecule."""
    from jobdesk_app.services.program_adapters import ConfFlowAdapter

    spec = ConfFlowAdapter.build_spec(
        server_id="ws1",
        remote_dir="/tmp/cf",
        xyz_paths="/tmp/cf/butane.xyz",
        config_path="/tmp/cf/confflow.yaml",
        max_parallel=1,
        resume=False,
    )

    assert spec.server_id == "ws1"
    assert spec.max_parallel == 1
    assert len(spec.sources) == 1
    assert spec.sources[0].path == "/tmp/cf/butane.xyz"
    assert len(spec.supporting_sources) == 1
    assert "confflow.yaml" in spec.command_template
    assert "{basename}_confflow_work" in spec.command_template
    assert "{name}" in spec.command_template
    assert "--resume" not in spec.command_template
    assert spec.workflow_kind.value == "confflow"


def test_conf_flow_adapter_build_spec_batch() -> None:
    """ConfFlowAdapter.build_spec handles a list of xyz paths (batch)."""
    from jobdesk_app.services.program_adapters import ConfFlowAdapter

    spec = ConfFlowAdapter.build_spec(
        server_id="ws1",
        remote_dir="/tmp/cf",
        xyz_paths=["/tmp/cf/a.xyz", "/tmp/cf/b.xyz"],
        config_path="/tmp/cf/confflow.yaml",
        max_parallel=2,
        resume=True,
    )

    assert len(spec.sources) == 2
    assert spec.max_parallel == 2
    assert "--resume" in spec.command_template


def test_conf_flow_adapter_build_dag_spec() -> None:
    """ConfFlowAdapter.build_dag_spec flips workflow_kind to dag."""
    from jobdesk_app.core.run import WorkflowKind
    from jobdesk_app.services.program_adapters import ConfFlowAdapter

    spec = ConfFlowAdapter.build_dag_spec(
        server_id="ws1",
        remote_dir="/tmp/cf",
        xyz_paths="/tmp/cf/butane.xyz",
        config_path="/tmp/cf/confflow.yaml",
        max_parallel=1,
    )

    assert spec.workflow_kind == WorkflowKind.dag
    assert "confflow.yaml" in spec.command_template


# ---------------------------------------------------------------------------
# DAG workflow execution (explicit inputs edges)
# ---------------------------------------------------------------------------

def test_run_workflow_dag_fan_out(methane_xyz: Path, tmp_path: Path) -> None:
    """DAG fan-out: two calc steps each depend on confgen, run in parallel."""
    from confflow.workflow.engine import run_workflow

    config_file = tmp_path / "confflow.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "global": {
                    "cores_per_task": 1,
                    "total_memory": "1GB",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "steps": [
                    {
                        "name": "confgen",
                        "type": "confgen",
                        "params": {"chains": ["1-2-3-4-5"]},
                    },
                    {
                        "name": "opt_a",
                        "type": "calc",
                        "inputs": ["confgen"],
                        "params": {
                            "iprog": "g16",
                            "itask": "opt",
                            "keyword": "b3lyp/6-31g(d)",
                        },
                    },
                    {
                        "name": "opt_b",
                        "type": "calc",
                        "inputs": ["confgen"],
                        "params": {
                            "iprog": "g16",
                            "itask": "opt",
                            "keyword": "b3lyp/6-31g(d)",
                        },
                    },
                ],
            },
            sort_keys=False,
        )
    )

    work_dir = tmp_path / "work"

    def fake_confgen(*args, **kwargs):
        Path("search.xyz").write_text(
            "5\nconfgen\n"
            "C   0.123456   0.000000   0.000000\n"
            "H   0.629118   0.629118   0.629118\n"
            "H  -0.629118  -0.629118   0.629118\n"
            "H  -0.629118   0.629118  -0.629118\n"
            "H   0.629118  -0.629118  -0.629118\n",
            encoding="utf-8",
        )

    executor = FakeCalcExecutor()
    with (
        patch("confflow.blocks.confgen.run_generation", side_effect=fake_confgen),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow(
            [str(methane_xyz)],
            str(config_file),
            str(work_dir),
            resume=False,
            verbose=False,
            calc_executor=executor,
        )

    assert (work_dir / "confgen" / "search.xyz").exists()
    assert (work_dir / "opt_a" / "output.xyz").exists()
    assert (work_dir / "opt_b" / "output.xyz").exists()
    submissions_by_step = {record["work_dir"].parent.name: record for record in executor.calc_submissions}
    assert set(submissions_by_step) == {"opt_a", "opt_b"}
    assert submissions_by_step["opt_a"]["coords"] == submissions_by_step["opt_b"]["coords"]
    assert "0.123456" in repr(submissions_by_step["opt_a"]["coords"])
    step_names = {s["name"] for s in stats["steps"]}
    assert step_names == {"confgen", "opt_a", "opt_b"}


def test_run_workflow_dag_linear_backward_compatibility(methane_xyz: Path, tmp_path: Path) -> None:
    """Legacy linear workflow (no inputs) still works exactly as before."""
    from confflow.workflow.engine import run_workflow

    config_file = tmp_path / "confflow.yaml"
    config_file.write_text(
        yaml.safe_dump(
            {
                "global": {
                    "cores_per_task": 1,
                    "total_memory": "1GB",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "steps": [
                    {
                        "name": "step1",
                        "type": "calc",
                        "params": {
                            "iprog": "g16",
                            "itask": "opt",
                            "keyword": "b3lyp/6-31g(d)",
                        },
                    },
                    {
                        "name": "step2",
                        "type": "calc",
                        "params": {
                            "iprog": "g16",
                            "itask": "freq",
                            "keyword": "b3lyp/6-31g(d)",
                        },
                    },
                ],
            },
            sort_keys=False,
        )
    )

    work_dir = tmp_path / "work"

    executor = FakeCalcExecutor()
    with patch("confflow.blocks.viz.generate_text_report", return_value=""):
        stats = run_workflow(
            [str(methane_xyz)],
            str(config_file),
            str(work_dir),
            resume=False,
            verbose=False,
            calc_executor=executor,
        )

    assert (work_dir / "step1" / "output.xyz").exists()
    assert (work_dir / "step2" / "output.xyz").exists()
    step_names = {s["name"] for s in stats["steps"]}
    assert step_names == {"step1", "step2"}


# Config validation is covered by tests/test_workflow_spec.py and
# jobdesk_app.core._confflow_validation.

# ---------------------------------------------------------------------------
# ConfFlow CLI build_parser and stop_all_confflow_processes
# ---------------------------------------------------------------------------

def test_cli_build_parser_routes_stop() -> None:
    """cli.build_parser returns an ArgumentParser with expected arguments."""
    from confflow.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["--stop"])
    assert args.stop is True

    args2 = parser.parse_args(["--verbose"])
    assert args2.verbose is True

    args3 = parser.parse_args(["mol.xyz", "-c", "cfg.yaml", "-w", "work"])
    assert args3.input_xyz == ["mol.xyz"]
    assert args3.config == "cfg.yaml"
    assert args3.work_dir == "work"


def test_cli_main_rejects_missing_input_xyz() -> None:
    """cli.main calls parser.error() when input_xyz is missing (no --stop)."""
    from confflow.cli import main

    with pytest.raises(SystemExit):
        main([])


# ---------------------------------------------------------------------------
# Monitor / state-file refresh integration
# ---------------------------------------------------------------------------


def test_workflow_state_json_is_parseable_by_confflow_results(tmp_path: Path):
    """End-to-end: .workflow_state.json written by v1.4.0 is parseable.

    This validates the integration between:
    - ConfFlow v1.4.0 writing state files
    - confflow_results.load_workflow_state_progress parsing them
    - The Runs page being able to render step progress

    The fixture mimics the shape written by the v1.4.0 state tracker.
    """
    from jobdesk_app.services.confflow_results import (
        ConfFlowStepProgress,
        format_step_progress,
        load_workflow_state_progress,
    )

    state_path = tmp_path / ".workflow_state.json"
    state_path.write_text(
        json.dumps(
            {
                "run_id": "v13-state-test",
                "work_dir": "/tmp/cf_work",
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
                        "status": "submitted",
                        "submitted_at": 1011.0,
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
    assert isinstance(progress, ConfFlowStepProgress)
    assert progress.completed == ("confgen",)
    assert progress.current == "opt"
    assert progress.final_status == ""

    # Verify the progress can be rendered (as the Runs page would)
    rendered = format_step_progress(progress)
    assert "confgen" in rendered
    assert "opt" in rendered
