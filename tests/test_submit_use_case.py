"""Tests for :class:`SubmitUseCase` (Phase 14B).

The use case is the single entry point that turns a :class:`SubmitPayload`
into a :class:`PreparedBatch` (local paths to upload + remote targets +
``RunSpec`` list + optional workflow YAML path). It does **not** talk
to the network or to ``RunCoordinator`` directly — the page-level
worker callback does that. The use case is therefore pure
validation + spec-building logic, easy to test in isolation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jobdesk_app.core.run import RunMode, RunSpec, WorkflowKind
from jobdesk_app.core.submit_payload import InputSource, SubmitPayload, WorkflowFields
from jobdesk_app.gui.widgets.calculation_widget import CalculationFields
from jobdesk_app.services.submit_use_case import PreparedBatch, SubmitUseCase, remote_child_path


def _calc_fields() -> CalculationFields:
    return CalculationFields(
        program="gaussian",
        preset_name=None,
        method_basis="B3LYP/6-31G(d)",
        job_keywords=[],
        charge=0,
        multiplicity=1,
        nproc=8,
        mem="4096MB",
    )


def _payload_single(
    *,
    inputs=None,
    program: str = "gaussian",
    server_id: str = "test-server",
    remote_dir: str = "/work",
    max_parallel: int = 1,
) -> SubmitPayload:
    if inputs is None:
        inputs = [InputSource(path=Path("a.gjf"), side="local", kind="gjf")]
    return SubmitPayload(
        kind="single",
        inputs=inputs,
        program=program,
        calc=_calc_fields(),
        workflow=None,
        output_dir=Path("."),
        server_id=server_id,
        remote_dir=remote_dir,
        max_parallel=max_parallel,
    )


def _payload_confflow(
    *,
    inputs=None,
    server_id: str = "test-server",
    remote_dir: str = "/work",
    max_parallel: int = 1,
) -> SubmitPayload:
    if inputs is None:
        inputs = [InputSource(path=Path("a.xyz"), side="local", kind="xyz")]
    return SubmitPayload(
        kind="confflow",
        inputs=inputs,
        program="gaussian",
        calc=_calc_fields(),
        workflow=WorkflowFields(work_dir_name="confflow_run"),
        output_dir=Path("."),
        server_id=server_id,
        remote_dir=remote_dir,
        max_parallel=max_parallel,
    )


# --- error paths ----------------------------------------------------------


def test_empty_inputs_yields_error():
    payload = _payload_single(inputs=[])
    batch = SubmitUseCase().execute(payload)
    assert not batch.ok
    assert any("No inputs" in e for e in batch.errors)
    assert batch.specs == []


def test_confflow_without_workflow_yields_error():
    """kind=confflow requires a WorkflowFields value — without it we fail fast."""
    payload = SubmitPayload(
        kind="confflow",
        inputs=[InputSource(path=Path("a.xyz"))],
        program="gaussian",
        calc=_calc_fields(),
        workflow=None,
        output_dir=Path("."),
        server_id="srv",
    )
    batch = SubmitUseCase().execute(payload)
    assert not batch.ok
    assert any("Workflow" in e for e in batch.errors)


def test_single_with_unsupported_program_yields_error():
    payload = _payload_single(program="not_a_real_program")
    batch = SubmitUseCase().execute(payload)
    assert not batch.ok
    assert any("Unsupported program" in e for e in batch.errors)


def test_missing_server_id_yields_error():
    payload = _payload_single(server_id="")
    batch = SubmitUseCase().execute(payload)
    assert not batch.ok
    assert any("server" in e.lower() for e in batch.errors)


# --- single kind ----------------------------------------------------------


def test_single_kind_builds_one_run_spec_per_input():
    """For kind=single, one RunSpec is emitted per chunk of inputs."""
    payload = _payload_single(
        inputs=[
            InputSource(path=Path("a.gjf")),
            InputSource(path=Path("b.gjf")),
        ]
    )
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert batch.yaml_local_path is None
    assert len(batch.specs) >= 1
    # Each spec has mode=selected_files and command_template referencing g16.
    for spec in batch.specs:
        assert isinstance(spec, RunSpec)
        assert spec.mode == RunMode.selected_files
        assert spec.workflow_kind == WorkflowKind.gaussian
        assert "{name}" in spec.command_template


def test_single_local_paths_and_remote_targets_pair():
    """Local inputs pair 1:1 with remote targets in the prepared batch."""
    payload = _payload_single(
        inputs=[InputSource(path=Path("local_dir/a.gjf"))],
        remote_dir="/remote",
    )
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert Path("local_dir/a.gjf") in batch.local_paths
    assert any(t.endswith("a.gjf") for t in batch.remote_targets)


def test_single_remote_sources_skip_upload():
    """A remote-side input is recorded but not added to local_paths."""
    payload = SubmitPayload(
        kind="single",
        inputs=[InputSource(path=Path("remote_dir/already/a.gjf"), side="remote", kind="gjf")],
        program="gaussian",
        calc=_calc_fields(),
        workflow=None,
        output_dir=Path("."),
        server_id="srv",
        remote_dir="/work",
    )
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert batch.local_paths == []  # nothing to upload
    # Remote targets preserve the source path's name; on Windows that means
    # backslashes, on POSIX forward slashes — match the filename.
    assert any("a.gjf" in t for t in batch.remote_targets)
    assert len(batch.remote_targets) == 1


def test_single_orca_uses_orca_command_template():
    payload = _payload_single(program="orca")
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert any("orca" in s.command_template for s in batch.specs)
    assert any(s.workflow_kind == WorkflowKind.orca for s in batch.specs)


def test_max_parallel_propagates_to_spec():
    """SubmitPayload.max_parallel ends up on every emitted RunSpec."""
    payload = _payload_single(max_parallel=7)
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert all(s.max_parallel == 7 for s in batch.specs)


# --- confflow kind --------------------------------------------------------


def test_confflow_kind_builds_single_spec_and_writes_yaml(tmp_path):
    """For kind=confflow, exactly one RunSpec is emitted and a workflow.yaml
    is written next to the first input's parent directory."""
    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    inputs = [
        InputSource(path=src_dir / "a.xyz"),
        InputSource(path=src_dir / "b.xyz"),
    ]
    payload = _payload_confflow(inputs=inputs, remote_dir="/work")
    batch = SubmitUseCase().execute(payload)

    assert batch.ok, batch.errors
    assert len(batch.specs) == 1
    spec = batch.specs[0]
    assert spec.workflow_kind == WorkflowKind.confflow
    # Two sources packed into the single spec's source list.
    assert len(spec.sources) == 2
    assert batch.yaml_local_path is not None
    assert batch.yaml_local_path.exists()
    assert batch.yaml_local_path.name == "workflow.yaml"
    assert batch.yaml_local_path.parent == src_dir


# --- PreparedBatch.ok property --------------------------------------------


def test_prepared_batch_ok_requires_no_errors_and_specs():
    batch = PreparedBatch(errors=["x"], specs=[])
    assert batch.ok is False

    batch = PreparedBatch(errors=[], specs=[])
    assert batch.ok is False  # no specs -> not ok


    batch = PreparedBatch(
        errors=[], specs=[RunSpec(server_id="s", remote_dir="/", command_template="x",
                                   max_parallel=1, mode=RunMode.selected_files)]
    )
    assert batch.ok is True


# --- remote_child_path helper ---------------------------------------------


def test_remote_child_path_handles_root():
    assert remote_child_path("/", "a.gjf") == "/a.gjf"


def test_remote_child_path_preserves_directory():
    assert remote_child_path("/work", "a.gjf") == "/work/a.gjf"


def test_remote_child_path_compacts_double_slashes():
    assert remote_child_path("/work/", "a.gjf") == "/work/a.gjf"
    assert remote_child_path("work/", "a.gjf") == "/work/a.gjf"


def test_remote_child_path_strips_name_slashes():
    """If the file name contains slashes, the path is compacted."""
    assert remote_child_path("/work", "a/b.gjf") == "/work/a/b.gjf"


# --- constructor: coordinator_factory is accepted but unused --------------


def test_coordinator_factory_is_optional():
    """The use case accepts a coordinator_factory but doesn't call it."""
    factory = object()  # anything non-None
    use_case = SubmitUseCase(coordinator_factory=factory)
    payload = _payload_single()
    batch = use_case.execute(payload)
    assert batch.ok
