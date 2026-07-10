"""Tests for the Submit-page value types (Phase 14B).

The Submit page, the SubmitUseCase, and the page-level worker callback
all exchange data via :class:`SubmitPayload` / :class:`InputSource` /
:class:`WorkflowFields`. This file asserts their basic invariants
(immutability, defaults, repr-safety) so a future refactor can't
silently change the data contract.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jobdesk_app.core.submit_payload import (
    DagWorkflowFields,
    InputSource,
    SubmitPayload,
    WorkflowFields,
)

# --- InputSource ----------------------------------------------------------


def test_input_source_defaults():
    """A bare InputSource defaults to local/xyz (the common case)."""
    src = InputSource(path=Path("a.xyz"))
    assert src.side == "local"
    assert src.kind == "xyz"


def test_input_source_is_frozen():
    """InputSource is frozen — mutating after construction must raise."""
    src = InputSource(path=Path("a.xyz"))
    with pytest.raises(FrozenInstanceError):
        src.side = "remote"  # type: ignore[misc]


def test_input_source_equality():
    """Two InputSources with the same fields compare equal (frozen dataclass)."""
    a = InputSource(path=Path("a.xyz"), side="local", kind="xyz")
    b = InputSource(path=Path("a.xyz"), side="local", kind="xyz")
    assert a == b


def test_input_source_repr_includes_all_fields():
    src = InputSource(path=Path("a.gjf"), side="remote", kind="gjf")
    text = repr(src)
    assert "a.gjf" in text
    assert "remote" in text
    assert "gjf" in text


# --- WorkflowFields -------------------------------------------------------


def test_workflow_fields_defaults():
    """Empty steps / empty advanced_options by default."""
    wf = WorkflowFields(work_dir_name="run_1")
    assert wf.work_dir_name == "run_1"
    assert wf.steps == []
    assert wf.advanced_options == {}


def test_workflow_fields_is_mutable():
    """WorkflowFields is a regular dataclass (steps / advanced_options mutate)."""
    wf = WorkflowFields(work_dir_name="run_1")
    wf.steps.append("opt")
    wf.advanced_options["solvent"] = "water"
    assert wf.steps == ["opt"]
    assert wf.advanced_options == {"solvent": "water"}


# --- SubmitPayload --------------------------------------------------------


def test_submit_payload_minimal_construction():
    """A SubmitPayload with only required fields defaults the rest.

    Phase 10.6 retired the ``CalculationWidget`` module; this test mirrors
    the duck-typed contract by constructing a simple :class:`dataclasses`
    instance inline (any object with the right attribute surface will do
    for the :class:`SubmitPayload` constructor).
    """
    from dataclasses import dataclass

    @dataclass
    class _StubCalc:
        program: str
        preset_name: str | None
        method_basis: str
        job_keywords: list
        charge: int
        multiplicity: int
        nproc: int
        mem: str

    calc = _StubCalc(
        program="gaussian",
        preset_name=None,
        method_basis="B3LYP/6-31G(d)",
        job_keywords=[],
        charge=0,
        multiplicity=1,
        nproc=8,
        mem="4096MB",
    )
    payload = SubmitPayload(
        kind="single",
        inputs=[InputSource(path=Path("a.gjf"), side="local", kind="gjf")],
        program="gaussian",
        calc=calc,
        workflow=None,
        output_dir=Path("."),
    )
    assert payload.kind == "single"
    assert payload.program == "gaussian"
    assert payload.server_id == ""
    assert payload.remote_dir == "/"
    assert payload.max_parallel == 1
    assert payload.output_paths == []


def test_submit_payload_remote_sources_are_preserved():
    """Remote-side inputs must keep their side=remote marker."""
    src = InputSource(path=Path("/remote/a.xyz"), side="remote", kind="xyz")
    payload = SubmitPayload(
        kind="single",
        inputs=[src],
        program="gaussian",
        calc=None,
        workflow=None,
        output_dir=Path("."),
    )
    assert payload.inputs[0].side == "remote"
    assert payload.inputs[0].path == Path("/remote/a.xyz")


def test_submit_payload_distinguishes_kinds():
    """The kind field is the discriminator for the use case's branch."""
    src = InputSource(path=Path("a.xyz"))
    single = SubmitPayload(
        kind="single", inputs=[src], program="gaussian", calc=None,
        workflow=None, output_dir=Path("."),
    )
    confflow = SubmitPayload(
        kind="confflow", inputs=[src], program="gaussian", calc=None,
        workflow=WorkflowFields(work_dir_name="x"), output_dir=Path("."),
    )
    assert single.kind == "single"
    assert confflow.kind == "confflow"
    assert confflow.workflow is not None


# --- DagWorkflowFields (Phase 10.5) ---------------------------------------


def test_dag_workflow_fields_defaults():
    """Empty steps / empty advanced_options by default."""
    dag = DagWorkflowFields(work_dir_name="fanout_run")
    assert dag.work_dir_name == "fanout_run"
    assert dag.steps == []
    assert dag.advanced_options == {}


def test_dag_workflow_fields_accepts_serialised_steps():
    """Each step is a dict from to_workflow_spec() with name/type/inputs."""
    dag = DagWorkflowFields(
        work_dir_name="fanout_run",
        steps=[
            {"name": "confgen", "type": "confgen", "params": {}, "inputs": []},
            {"name": "sp", "type": "calc", "params": {"itask": "sp"}, "inputs": ["confgen"]},
            {"name": "freq", "type": "calc", "params": {"itask": "freq"}, "inputs": ["confgen"]},
        ],
    )
    assert len(dag.steps) == 3
    assert dag.steps[1]["inputs"] == ["confgen"]
    assert dag.steps[2]["inputs"] == ["confgen"]


def test_submit_payload_accepts_dag_kind():
    """A SubmitPayload for kind=dag carries the dag slot and may have workflow=None."""
    src = InputSource(path=Path("a.xyz"))
    dag = DagWorkflowFields(work_dir_name="fanout_run")
    payload = SubmitPayload(
        kind="dag", inputs=[src], program="orca", calc=None,
        workflow=None, output_dir=Path("."), dag=dag,
    )
    assert payload.kind == "dag"
    assert payload.dag is not None
    assert payload.workflow is None
    assert payload.dag.work_dir_name == "fanout_run"


def test_submit_payload_dag_defaults_to_none():
    """A legacy confflow/single payload keeps dag=None by default."""
    src = InputSource(path=Path("a.xyz"))
    payload = SubmitPayload(
        kind="confflow", inputs=[src], program="gaussian", calc=None,
        workflow=WorkflowFields(work_dir_name="x"), output_dir=Path("."),
    )
    assert payload.dag is None


def test_dag_workflow_payload_serializes_yaml():
    """``WorkflowGraphPayload.to_yaml()`` already includes the DAG ``inputs`` list.

    Phase 10.5 plumbing accepts that YAML verbatim on the submit side;
    this test is the contract: serialise a fan-out graph and assert
    every step's ``inputs`` field survives.
    """
    import yaml

    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    from jobdesk_app.gui.nodegraph.spec_bridge import to_workflow_spec
    from tests.test_nodegraph.test_spec_bridge import _make_fanout_graph

    graph = _make_fanout_graph()
    spec_payload = to_workflow_spec(graph)
    yaml_text = spec_payload.to_yaml()
    parsed = yaml.safe_load(yaml_text)
    by_name = {step["name"]: step for step in parsed["steps"]}
    # Both fan-out sinks must name "confgen" in their inputs list.
    assert by_name["sp"]["inputs"] == ["confgen"]
    assert by_name["freq"]["inputs"] == ["confgen"]
    # The root step still has an empty inputs list (it consumes global XYZ).
    assert by_name["confgen"]["inputs"] == []


def test_workflow_kind_dag_round_trip():
    """``WorkflowKind.dag`` survives the str round-trip and equals ``'dag'``."""
    from jobdesk_app.core.run import WorkflowKind

    assert WorkflowKind.dag.value == "dag"
    # String round-trip: ``RunSpec.workflow_kind`` accepts both forms.
    assert WorkflowKind("dag") is WorkflowKind.dag
    # The legacy kinds are untouched.
    assert WorkflowKind.confflow.value == "confflow"
    assert WorkflowKind.gaussian.value == "gaussian"
    assert WorkflowKind.orca.value == "orca"
