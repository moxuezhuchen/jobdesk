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
    """A SubmitPayload with only required fields defaults the rest."""
    from jobdesk_app.gui.widgets.calculation_widget import CalculationFields

    calc = CalculationFields(
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
