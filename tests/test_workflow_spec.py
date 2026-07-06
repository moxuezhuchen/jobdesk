"""Tests for ``jobdesk_app.core.workflow_spec``.

The ConfFlow Pydantic models are an optional dependency. These tests verify
the wrapper behaves correctly regardless: graceful degradation when the
package is missing, round-trip serialization when it is available.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobdesk_app.core import workflow_spec
from jobdesk_app.core.workflow_spec import (
    ConfFlowUnavailableError,
    DryRunReport,
    WorkflowSpec,
    require_confflow,
    write_workflow_yaml,
)


def test_require_confflow_raises_when_unavailable():
    """require_confflow raises ConfFlowUnavailableError if confflow missing."""
    if workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package is installed; behavior is covered by other tests")
    with pytest.raises(ConfFlowUnavailableError):
        require_confflow()


def test_from_yaml_round_trip_when_confflow_available(tmp_path: Path):
    """Round-trip serialization preserves shape when confflow is installed."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    yaml_text = (
        "work_dir: hexane_work\n"
        "calc:\n"
        "  program: gaussian\n"
        "  method: B3LYP\n"
        "  basis: 6-31G(d)\n"
        "  charge: 0\n"
        "  multiplicity: 1\n"
        "  nproc: 8\n"
        "  memory_mb: 4096\n"
        "  steps:\n"
        "    - confgen\n"
        "    - preopt\n"
        "    - opt\n"
        "    - refine\n"
        "    - sp\n"
    )
    spec = WorkflowSpec.from_yaml(yaml_text)
    serialized = spec.to_yaml()
    # Round-trip should produce identical payload on the second pass.
    again = WorkflowSpec.from_yaml(serialized)
    assert again.to_yaml() == serialized


def test_to_form_includes_known_fields_when_available():
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="hexane_work",
        program="gaussian",
        method="B3LYP",
        basis="6-31G(d)",
        charge=0,
        multiplicity=1,
        nproc=8,
        memory_mb=4096,
        steps=("confgen", "opt"),
    )
    form = spec.to_form()
    assert form["program"] == "gaussian"
    assert form["method"] == "B3LYP"
    assert form["nproc"] == 8
    assert form["steps"] == ["confgen", "opt"]


def test_dry_run_ok_when_round_trip_works(tmp_path: Path):
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    spec = WorkflowSpec.from_form(
        work_dir_name="water_work",
        program="orca",
        method="B3LYP",
        basis="def2-TZVP",
        charge=0,
        multiplicity=1,
        nproc=4,
        memory_mb=2048,
    )
    report = spec.dry_run()
    assert isinstance(report, DryRunReport)
    assert report.ok is True
    assert report.error == ""


def test_write_workflow_yaml_is_atomic(tmp_path: Path):
    """write_workflow_yaml replaces the target atomically."""
    target = tmp_path / "nested" / "workflow.yaml"
    if workflow_spec._CONFFLOW_AVAILABLE:
        spec = WorkflowSpec.from_form(
            work_dir_name="x",
            program="gaussian",
            method="HF",
            basis="3-21G",
            charge=0,
            multiplicity=1,
            nproc=1,
            memory_mb=1024,
        )
        write_workflow_yaml(spec, target)
        # Final file must exist; .tmp sidecar must not leak.
        assert target.exists()
        assert not target.with_suffix(target.suffix + ".tmp").exists()
        assert json.loads(target.read_text(encoding="utf-8")) != {}
    else:
        # Graceful path: missing confflow raises a typed error.
        with pytest.raises(ConfFlowUnavailableError):
            write_workflow_yaml(WorkflowSpec(global_config=None), target)
