"""Phase 3A — JobDesk → ConFlow Python API integration tests.

Covers the library surface that JobDesk calls to drive ConfFlow:
- ``confflow.run_workflow`` as a Python library (no CLI subprocess)
- ``confflow.ChemTaskManager`` initialized from a dict config
- ``confflow_results.load_summary`` / ``load_step_progress`` on real artifact shapes
- ``ConfFlowAdapter.build_spec`` / ``build_dag_spec`` round-trips
- DAG workflow execution with explicit ``inputs`` edges

These tests run on Windows (no WSL required) and mock the Gaussian
executor so they are fast and hermetic.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


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


@pytest.fixture
def methane_xyz(tmp_path: Path) -> Path:
    p = tmp_path / "methane.xyz"
    p.write_text(METANE_XYZ, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# run_workflow as a library call
# ---------------------------------------------------------------------------


def _mock_calc_run(self, input_xyz_file: str) -> None:
    os.makedirs(self.work_dir, exist_ok=True)
    out = os.path.join(self.work_dir, "output.xyz")
    with open(out, "w") as fh:
        fh.write(METANE_XYZ)

    db = os.path.join(self.work_dir, "results.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_results "
        "(cid TEXT, status TEXT, energy_au REAL, final_xyz TEXT)"
    )
    conn.execute(
        "INSERT INTO task_results VALUES ('A000001', 'success', -40.51838331, ?)",
        (METANE_XYZ,),
    )
    conn.commit()
    conn.close()


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

    with (
        patch("confflow.calc.ChemTaskManager.run", new=_mock_calc_run),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow(
            input_xyz=[str(methane_xyz)],
            config_file=str(config_file),
            work_dir=str(work_dir),
            resume=False,
            verbose=False,
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

    with (
        patch("confflow.calc.ChemTaskManager.run", new=_mock_calc_run),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        first = run_workflow(
            [str(methane_xyz)], str(config_file), str(work_dir), resume=False, verbose=False
        )
        assert first["steps"][0]["status"] == "completed"

    with (
        patch("confflow.calc.ChemTaskManager.run", new=_mock_calc_run),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        second = run_workflow(
            [str(methane_xyz)], str(config_file), str(work_dir), resume=True, verbose=False
        )
        # Completed step is skipped; second run may return empty steps
        # depending on checkpoint state.
        assert second.get("steps") is None or second["steps"] == []


# ---------------------------------------------------------------------------
# ChemTaskManager from dict config
# ---------------------------------------------------------------------------


def test_chem_task_manager_from_dict_config(tmp_path: Path) -> None:
    """ChemTaskManager can be initialized from a plain dict (no INI file)."""
    from confflow.calc import ChemTaskManager

    cfg: dict[str, Any] = {
        "iprog": "g16",
        "itask": "sp",
        "keyword": "b3lyp/6-31g(d)",
        "cores_per_task": 1,
        "total_memory": "1GB",
        "charge": 0,
        "multiplicity": 1,
    }

    manager = ChemTaskManager(settings=cfg)
    assert manager.config["iprog"] == "g16"
    assert manager.config["itask"] == "sp"
    assert manager.config["keyword"] == "b3lyp/6-31g(d)"
    assert manager.config["cores_per_task"] == 1


def test_chem_task_manager_work_dir_from_resume_dir(tmp_path: Path) -> None:
    """ChemTaskManager honours resume_dir for work_dir on re-run."""
    from confflow.calc import ChemTaskManager

    resume = tmp_path / "resume_wd"
    resume.mkdir()

    manager = ChemTaskManager(resume_dir=str(resume))
    assert manager.work_dir == str(resume)


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
    from jobdesk_app.services.confflow_results import ConfFlowSummary, format_summary, load_summary

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
        load_step_progress,
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
    import yaml

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
            "C   0.000000   0.000000   0.000000\n"
            "H   0.629118   0.629118   0.629118\n"
            "H  -0.629118  -0.629118   0.629118\n"
            "H  -0.629118   0.629118  -0.629118\n"
            "H   0.629118  -0.629118  -0.629118\n",
            encoding="utf-8",
        )

    def fake_calc_run(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        out = os.path.join(self.work_dir, "output.xyz")
        Path(out).write_text(METANE_XYZ, encoding="utf-8")
        db = os.path.join(self.work_dir, "results.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_results "
            "(cid TEXT, status TEXT, energy_au REAL)"
        )
        conn.execute("INSERT INTO task_results VALUES ('A000001', 'success', -40.5)")
        conn.commit()
        conn.close()

    with (
        patch("confflow.blocks.confgen.run_generation", side_effect=fake_confgen),
        patch("confflow.calc.ChemTaskManager.run", new=fake_calc_run),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow(
            [str(methane_xyz)],
            str(config_file),
            str(work_dir),
            resume=False,
            verbose=False,
        )

    assert (work_dir / "confgen" / "search.xyz").exists()
    assert (work_dir / "opt_a" / "output.xyz").exists()
    assert (work_dir / "opt_b" / "output.xyz").exists()
    step_names = {s["name"] for s in stats["steps"]}
    assert step_names == {"confgen", "opt_a", "opt_b"}


def test_run_workflow_dag_linear_backward_compatibility(methane_xyz: Path, tmp_path: Path) -> None:
    """Legacy linear workflow (no inputs) still works exactly as before."""
    import yaml

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

    def fake_calc_run(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        out = os.path.join(self.work_dir, "output.xyz")
        Path(out).write_text(METANE_XYZ, encoding="utf-8")
        db = os.path.join(self.work_dir, "results.db")
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_results "
            "(cid TEXT, status TEXT, energy_au REAL)"
        )
        conn.execute("INSERT INTO task_results VALUES ('A000001', 'success', -40.5)")
        conn.commit()
        conn.close()

    with (
        patch("confflow.calc.ChemTaskManager.run", new=fake_calc_run),
        patch("confflow.blocks.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow(
            [str(methane_xyz)],
            str(config_file),
            str(work_dir),
            resume=False,
            verbose=False,
        )

    assert (work_dir / "step1" / "output.xyz").exists()
    assert (work_dir / "step2" / "output.xyz").exists()
    step_names = {s["name"] for s in stats["steps"]}
    assert step_names == {"step1", "step2"}


# ---------------------------------------------------------------------------
# ConfFlow config schema validation
# ---------------------------------------------------------------------------

def test_config_schema_validates_calc_config() -> None:
    """ConfigSchema.validate_calc_config accepts a well-formed calc config."""
    from confflow.config.schema import ConfigSchema

    cfg = {
        "iprog": "g16",
        "itask": "opt",
        "keyword": "b3lyp/6-31g(d)",
        "cores_per_task": 4,
        "total_memory": "8GB",
        "max_parallel_jobs": 2,
    }
    ConfigSchema.validate_calc_config(cfg)


def test_config_schema_rejects_missing_keyword() -> None:
    """ConfigSchema.validate_calc_config raises on missing keyword."""
    from confflow.config.schema import ConfigSchema

    cfg = {"iprog": "g16", "itask": "opt"}
    with pytest.raises(ValueError, match="missing required parameter"):
        ConfigSchema.validate_calc_config(cfg)


def test_config_schema_rejects_invalid_itask() -> None:
    """ConfigSchema.validate_calc_config raises on invalid itask."""
    from confflow.config.schema import ConfigSchema

    cfg = {"iprog": "g16", "itask": "invalid_task", "keyword": "hf"}
    with pytest.raises(ValueError, match="invalid itask"):
        ConfigSchema.validate_calc_config(cfg)


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
