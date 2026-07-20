#!/usr/bin/env python3

"""DAG dispatch tests for the workflow engine (Phase 3).

Covers:
- fan-out: one confgen step feeding two calc steps
- fan-in: two confgen steps feeding one calc step (verifies fan-in warning)
- cycle: a 3-step graph with a cycle -> ConfFlowError
- backward compat: a linear chain (no ``inputs`` declared) runs through
  the DAG dispatcher unchanged
- deterministic tie-breaking
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from confflow.core.exceptions import ConfFlowError
from confflow.workflow.dag import build_step_graph, topo_order
from confflow.workflow.engine import DagCycleError, run_workflow

# ---------------------------------------------------------------------------
# Pure DAG helper tests
# ---------------------------------------------------------------------------


def test_topo_order_simple_chain():
    predecessors = {"a": [], "b": ["a"], "c": ["b"]}
    waves = topo_order(predecessors)
    assert waves == [["a"], ["b"], ["c"]]


def test_topo_order_diamond():
    predecessors = {
        "root": [],
        "left": ["root"],
        "right": ["root"],
        "merge": ["left", "right"],
    }
    waves = topo_order(predecessors)
    assert waves[0] == ["root"]
    # left and right are independent -> same wave, sorted
    assert sorted(waves[1]) == ["left", "right"]
    assert waves[2] == ["merge"]


def test_topo_order_cycle_raises_conf_flow_error():
    predecessors = {"a": ["b"], "b": ["c"], "c": ["a"]}
    with pytest.raises(ConfFlowError, match="cycle"):
        topo_order(predecessors)


def test_topo_order_deterministic_when_multiple_ready():
    """Two independent roots -> same wave; ordering inside wave is sorted."""
    predecessors = {"b": [], "a": [], "c": []}
    waves = topo_order(predecessors)
    assert waves == [["a", "b", "c"]]


def test_build_step_graph_rejects_duplicate_names():
    steps = [{"name": "s1", "type": "calc"}, {"name": "s1", "type": "calc"}]
    with pytest.raises(ConfFlowError, match="must be unique"):
        build_step_graph(steps)


def test_build_step_graph_coerces_inputs_from_string():
    steps = [
        {"name": "a", "type": "calc"},
        {"name": "b", "type": "calc", "inputs": "a"},
        {"name": "c", "type": "calc", "inputs": ["b"]},
    ]
    _, by_name, declared = build_step_graph(steps)
    assert declared["b"] == ["a"]
    assert declared["c"] == ["b"]


# ---------------------------------------------------------------------------
# Engine-level DAG tests (run_workflow with mocks)
# ---------------------------------------------------------------------------


def _make_mock_step(step_dir: str, output_name: str = "output.xyz"):
    """Create a fake ChemTaskManager.run side-effect that writes ``output_name``."""

    def side_effect(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        out_path = os.path.join(self.work_dir, output_name)
        with open(out_path, "w") as f:
            f.write("2\nCID=1 E=-1.0\nC 0 0 0\nH 0 0 1.1\n")
        db_path = os.path.join(self.work_dir, "results.db")
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE task_results (status TEXT)")
        con.execute("INSERT INTO task_results VALUES ('success')")
        con.commit()
        con.close()
        return out_path

    return side_effect


def _make_confgen_mock(step_dir_marker: str = ""):
    def fake_run_generation(input_files, **kwargs):
        out_path = os.path.join(os.getcwd(), "search.xyz")
        with open(out_path, "w") as f:
            f.write("2\ngenerated\nC 0 0 0\nH 0 0 1.1\n")
        return out_path

    return fake_run_generation


def test_dag_fan_out_one_conformer_two_optimizers(tmp_path: Path):
    """One confgen step feeds two parallel calc steps (fan-out).

    The graph:
        confgen
          |
          +--> opt1
          +--> opt2
    """
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\nin\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")

    cfg = {
        "global": {
            "gaussian_path": "g16",
            "orca_path": "orca",
            "iprog": "orca",
            "itask": "sp",
            "keyword": "B3LYP",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "max_parallel_jobs": 1,
        },
        "steps": [
            {"name": "confgen_step", "type": "confgen", "params": {"chains": ["1-2"]}},
            {
                "name": "opt1",
                "type": "calc",
                "inputs": ["confgen_step"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
            {
                "name": "opt2",
                "type": "calc",
                "inputs": ["confgen_step"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
        ],
    }
    cfg_path = tmp_path / "wf.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    work_dir = tmp_path / "work"

    with (
        patch(
            "confflow.workflow.engine.confgen.run_generation",
            side_effect=_make_confgen_mock(),
        ),
        patch(
            "confflow.workflow.engine.calc.ChemTaskManager.run",
            autospec=True,
            side_effect=_make_mock_step(""),
        ),
        patch("confflow.workflow.engine.viz.parse_xyz_file", return_value=[]),
        patch("confflow.workflow.engine.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow([str(input_xyz)], str(cfg_path), str(work_dir))

    step_names = [s["name"] for s in stats["steps"]]
    assert "confgen_step" in step_names
    assert "opt1" in step_names
    assert "opt2" in step_names

    # All three output files exist.
    assert (work_dir / "confgen_step" / "search.xyz").exists()
    assert (work_dir / "opt1" / "output.xyz").exists()
    assert (work_dir / "opt2" / "output.xyz").exists()


def test_dag_fan_in_two_conformers_one_optimizer(tmp_path: Path, caplog):
    """Two confgen steps feed one calc step (fan-in -> warning logged).

    The graph (ascii):
        confgen1   confgen2
              merge into calc_merge
    """
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\nin\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")

    cfg = {
        "global": {
            "gaussian_path": "g16",
            "orca_path": "orca",
            "iprog": "orca",
            "itask": "sp",
            "keyword": "B3LYP",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "max_parallel_jobs": 1,
        },
        "steps": [
            {"name": "confgen1", "type": "confgen", "params": {"chains": ["1-2"]}},
            {"name": "confgen2", "type": "confgen", "params": {"chains": ["1-2"]}},
            {
                "name": "calc_merge",
                "type": "calc",
                "inputs": ["confgen1", "confgen2"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
        ],
    }
    cfg_path = tmp_path / "wf.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    work_dir = tmp_path / "work"

    # Track which inputs calc_merge actually consumes.
    seen_inputs: dict[str, object] = {}

    def fake_calc_run(self, input_xyz_file):
        seen_inputs["value"] = input_xyz_file
        os.makedirs(self.work_dir, exist_ok=True)
        out_path = os.path.join(self.work_dir, "output.xyz")
        with open(out_path, "w") as f:
            f.write("2\nout\nC 0 0 0\nH 0 0 1.1\n")
        db_path = os.path.join(self.work_dir, "results.db")
        con = sqlite3.connect(db_path)
        con.execute("CREATE TABLE task_results (status TEXT)")
        con.execute("INSERT INTO task_results VALUES ('success')")
        con.commit()
        con.close()

    with (
        patch(
            "confflow.workflow.engine.confgen.run_generation",
            side_effect=_make_confgen_mock(),
        ),
        patch(
            "confflow.workflow.engine.calc.ChemTaskManager.run",
            autospec=True,
            side_effect=fake_calc_run,
        ),
        patch("confflow.workflow.engine.viz.parse_xyz_file", return_value=[]),
        patch("confflow.workflow.engine.viz.generate_text_report", return_value=""),
        caplog.at_level(logging.WARNING),
    ):
        run_workflow([str(input_xyz)], str(cfg_path), str(work_dir))

    # The calc step ran.
    assert (work_dir / "calc_merge" / "output.xyz").exists()
    # It received the primary predecessor (confgen1) only.
    assert "value" in seen_inputs
    # Either the primary .xyz string or the resolved primary path.
    # Most importantly: NOT a merged list.
    assert seen_inputs["value"] != [
        str(work_dir / "confgen1" / "search.xyz"),
        str(work_dir / "confgen2" / "search.xyz"),
    ]
    # Fan-in warning was emitted somewhere in the logs.
    assert any(
        "fan-in" in record.getMessage() or "fan-in is partially supported" in record.getMessage()
        for record in caplog.records
    )


def test_dag_cycle_raises_conf_flow_error(tmp_path: Path):
    """A 3-step graph with a cycle should raise a ConfFlowError."""
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\nin\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")

    cfg = {
        "global": {
            "iprog": "orca",
            "itask": "sp",
            "keyword": "B3LYP",
            "gaussian_path": "g16",
            "orca_path": "orca",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "max_parallel_jobs": 1,
        },
        "steps": [
            {
                "name": "a",
                "type": "calc",
                "inputs": ["c"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
            {
                "name": "b",
                "type": "calc",
                "inputs": ["a"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
            {
                "name": "c",
                "type": "calc",
                "inputs": ["b"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
        ],
    }
    cfg_path = tmp_path / "wf.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    work_dir = tmp_path / "work"

    with (
        patch(
            "confflow.workflow.engine.calc.ChemTaskManager.run",
            autospec=True,
            side_effect=_make_mock_step(""),
        ),
        patch("confflow.workflow.engine.viz.parse_xyz_file", return_value=[]),
        patch("confflow.workflow.engine.viz.generate_text_report", return_value=""),
    ):
        with pytest.raises((DagCycleError, ConfFlowError)) as exc_info:
            run_workflow([str(input_xyz)], str(cfg_path), str(work_dir))
    assert "cycle" in str(exc_info.value).lower()


def test_dag_linear_backward_compat(tmp_path: Path):
    """A 3-step linear chain with no ``inputs`` fields works through the DAG dispatcher.

    Same shape as the legacy ``test_run_workflow_full_and_resume`` flow.
    """
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\nin\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")

    cfg = {
        "global": {
            "gaussian_path": "g16",
            "orca_path": "orca",
            "iprog": "orca",
            "itask": "sp",
            "keyword": "B3LYP",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "max_parallel_jobs": 1,
        },
        "steps": [
            {"name": "s1", "type": "confgen", "params": {"chains": ["1-2"]}},
            {
                "name": "s2",
                "type": "calc",
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
            {
                "name": "s3",
                "type": "calc",
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
        ],
    }
    cfg_path = tmp_path / "wf.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    work_dir = tmp_path / "work"

    with (
        patch(
            "confflow.workflow.engine.confgen.run_generation",
            side_effect=_make_confgen_mock(),
        ),
        patch(
            "confflow.workflow.engine.calc.ChemTaskManager.run",
            autospec=True,
            side_effect=_make_mock_step(""),
        ),
        patch("confflow.workflow.engine.viz.parse_xyz_file", return_value=[]),
        patch("confflow.workflow.engine.viz.generate_text_report", return_value=""),
    ):
        stats = run_workflow([str(input_xyz)], str(cfg_path), str(work_dir))

    step_names = [s["name"] for s in stats["steps"]]
    assert step_names == ["s1", "s2", "s3"]
    assert (work_dir / "s1" / "search.xyz").exists()
    assert (work_dir / "s2" / "output.xyz").exists()
    assert (work_dir / "s3" / "output.xyz").exists()


def test_dag_deterministic_tie_breaking(tmp_path: Path):
    """DAG dispatch is deterministic.

    When a step explicitly declares ``inputs``, the engine builds a DAG
    and dispatches within each wave in alphabetical order (defending
    against Python dict-order surprises across runs).
    """
    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\nin\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")

    # A small DAG with an explicit edge forces the DAG path. zeta is a
    # root, alpha + mu both depend on zeta -> dispatch order in the
    # second wave should be alphabetic.
    cfg = {
        "global": {
            "gaussian_path": "g16",
            "orca_path": "orca",
            "iprog": "orca",
            "itask": "sp",
            "keyword": "B3LYP",
            "cores_per_task": 1,
            "total_memory": "1GB",
            "max_parallel_jobs": 1,
        },
        "steps": [
            {
                "name": "zeta",
                "type": "calc",
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
            {
                "name": "alpha",
                "type": "calc",
                "inputs": ["zeta"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
            {
                "name": "mu",
                "type": "calc",
                "inputs": ["zeta"],
                "params": {"iprog": "orca", "itask": "sp", "keyword": "B3LYP"},
            },
        ],
    }
    cfg_path = tmp_path / "wf.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    orders: list[list[str]] = []
    for run_idx in range(2):
        work_dir = tmp_path / f"work_{run_idx}"
        with (
            patch(
                "confflow.workflow.engine.calc.ChemTaskManager.run",
                autospec=True,
                side_effect=_make_mock_step(""),
            ),
            patch("confflow.workflow.engine.viz.parse_xyz_file", return_value=[]),
            patch("confflow.workflow.engine.viz.generate_text_report", return_value=""),
        ):
            stats = run_workflow([str(input_xyz)], str(cfg_path), str(work_dir))
        orders.append([s["name"] for s in stats["steps"]])

    # zeta first, then alpha and mu in alphabetical order.
    assert orders[0] == orders[1] == ["zeta", "alpha", "mu"]


def test_dag_stepconfig_inputs_outputs_round_trip():
    """The StepConfig Pydantic model exposes inputs/outputs as list[str]."""
    from confflow.core.models import StepConfig

    sc = StepConfig(
        name="s1",
        type="calc",
        inputs=["a", "b"],
        outputs=["s1/output.xyz"],
    )
    assert sc.inputs == ["a", "b"]
    assert sc.outputs == ["s1/output.xyz"]
    # List form is the canonical shape; users build the list explicitly
    # rather than relying on string coercion at model level. The DAG
    # helper (build_step_graph) handles the string/list coercion when
    # parsing raw YAML dicts.
    sc2 = StepConfig.model_validate(
        {"name": "s2", "type": "calc", "inputs": ["a", "b"], "outputs": ["o"]}
    )
    assert sc2.inputs == ["a", "b"]


def test_dag_acceptance_run(tmp_path: Path):
    """End-to-end acceptance: run tests/data/wf_dag.yaml with mock executors.

    Drives the real ``run_workflow`` entry point with the fixture
    workflow. No g16/orca invocation; ChemTaskManager.run and
    confgen.run_generation are patched to write valid output files.
    """
    import confflow.workflow.engine as engine

    fixture = Path(__file__).resolve().parent / "data" / "wf_dag.yaml"
    assert fixture.exists(), f"missing acceptance fixture: {fixture}"

    input_xyz = tmp_path / "input.xyz"
    input_xyz.write_text("2\nin\nC 0 0 0\nH 0 0 1\n", encoding="utf-8")

    work_dir = tmp_path / "tmp_dag_run"

    def fake_confgen(input_files, **kwargs):
        out = os.path.join(os.getcwd(), "search.xyz")
        with open(out, "w") as f:
            f.write("2\ngen\nC 0 0 0\nH 0 0 1.1\n")
        return out

    def fake_calc(self, input_xyz_file):
        os.makedirs(self.work_dir, exist_ok=True)
        out = os.path.join(self.work_dir, "output.xyz")
        with open(out, "w") as f:
            f.write("2\ncalc\nC 0 0 0\nH 0 0 1.1\n")
        db = os.path.join(self.work_dir, "results.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE task_results (status TEXT)")
        con.execute("INSERT INTO task_results VALUES ('success')")
        con.commit()
        con.close()

    with (
        patch("confflow.workflow.engine.confgen.run_generation", side_effect=fake_confgen),
        patch(
            "confflow.workflow.engine.calc.ChemTaskManager.run",
            autospec=True,
            side_effect=fake_calc,
        ),
        patch("confflow.workflow.engine.viz.parse_xyz_file", return_value=[]),
        patch("confflow.workflow.engine.viz.generate_text_report", return_value=""),
    ):
        stats = engine.run_workflow([str(input_xyz)], str(fixture), str(work_dir))

    # The dispatch should have touched all four steps.
    step_names = [s["name"] for s in stats["steps"]]
    assert "confgen" in step_names
    assert "opt_a" in step_names
    assert "opt_b" in step_names
    assert "merge" in step_names

    # Each step's standard output file exists in tmp_dag_run/.
    assert (work_dir / "confgen" / "search.xyz").exists()
    assert (work_dir / "opt_a" / "output.xyz").exists()
    assert (work_dir / "opt_b" / "output.xyz").exists()
    assert (work_dir / "merge" / "output.xyz").exists()

    # workflow_stats.json got written.
    assert (work_dir / "workflow_stats.json").exists()
