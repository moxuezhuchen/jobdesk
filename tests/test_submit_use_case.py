"""Tests for :class:`SubmitUseCase` (Phase 14B).

The use case is the single entry point that turns a :class:`SubmitPayload`
into a :class:`PreparedBatch` (local paths to upload + remote targets +
``RunSpec`` list + optional workflow YAML path). It does **not** talk
to the network or to ``RunCoordinator`` directly — the page-level
worker callback does that. The use case is therefore pure
validation + spec-building logic, easy to test in isolation.

Phase 10.6 removed the legacy ``CalculationWidget`` module; the local
``_StubCalcFields`` dataclass below mirrors the attribute surface the
use case reads (``charge`` / ``multiplicity`` / ``nproc`` / ``mem``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from jobdesk_app.core.run import RunMode, RunSpec, WorkflowKind
from jobdesk_app.core.submit_payload import (
    DagWorkflowFields,
    InputSource,
    SubmitPayload,
    WorkflowFields,
)
from jobdesk_app.services.submit_use_case import PreparedBatch, SubmitUseCase, remote_child_path


@dataclass
class _StubCalcFields:
    """Minimal shape ``SubmitUseCase`` reads off ``payload.calc``.

    Phase 10.6 dropped the legacy ``CalculationWidget.CalculationFields``
    dataclass; tests now build this stub locally so they stay aligned
    with the duck-typed contract without depending on the retired module.
    """

    program: str
    preset_name: str | None
    method_basis: str
    job_keywords: list
    charge: int
    multiplicity: int
    nproc: int
    mem: str


def _calc_fields() -> _StubCalcFields:
    return _StubCalcFields(
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


def _payload_dag(
    *,
    inputs=None,
    steps=None,
    server_id: str = "test-server",
    remote_dir: str = "/work",
    max_parallel: int = 1,
    work_dir_name: str = "dag_run",
) -> SubmitPayload:
    if inputs is None:
        inputs = [InputSource(path=Path("a.xyz"), side="local", kind="xyz")]
    if steps is None:
        # Mirror the small fan-out from ``tests/test_nodegraph/test_spec_bridge.py``
        # so we exercise the same shape the bridge would produce.
        steps = [
            {"name": "confgen", "type": "confgen", "params": {"nconf": 3}, "inputs": []},
            {"name": "sp", "type": "calc", "params": {"itask": "sp"}, "inputs": ["confgen"]},
            {"name": "freq", "type": "calc", "params": {"itask": "freq"}, "inputs": ["confgen"]},
        ]
    dag = DagWorkflowFields(work_dir_name=work_dir_name, steps=steps)
    return SubmitPayload(
        kind="dag",
        inputs=inputs,
        program="gaussian",
        calc=_calc_fields(),
        workflow=None,
        dag=dag,
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


def test_dag_without_dag_yields_error():
    """kind=dag requires a DagWorkflowFields value — without it we fail fast."""
    payload = SubmitPayload(
        kind="dag",
        inputs=[InputSource(path=Path("a.xyz"))],
        program="gaussian",
        calc=_calc_fields(),
        workflow=None,
        output_dir=Path("."),
        server_id="srv",
    )
    batch = SubmitUseCase().execute(payload)
    assert not batch.ok
    assert any("DAG" in e for e in batch.errors)


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


def test_confflow_yaml_lands_next_to_first_input_even_when_output_dir_is_cwd(tmp_path):
    """Regression: the legacy ``_payload_confflow`` helper passes
    ``output_dir=Path(".")`` so the YAML used to land in the repo root
    during tests. ``_resolve_yaml_dir`` must fall back to the first
    input's parent instead, so the YAML always lives next to the
    user's first XYZ file regardless of what ``output_dir`` says.
    """
    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    src_dir = tmp_path / "molecules"
    src_dir.mkdir()
    inputs = [InputSource(path=src_dir / "water.xyz", side="local", kind="xyz")]
    # output_dir explicitly the cwd — the broken pre-fix behaviour
    # would write workflow.yaml into the repo root.
    payload = _payload_confflow(inputs=inputs, remote_dir="/work")
    payload_dict = payload.__dict__
    payload_dict["output_dir"] = Path(".")
    payload = type(payload)(**payload_dict)
    batch = SubmitUseCase().execute(payload)
    assert batch.ok, batch.errors
    assert batch.yaml_local_path is not None
    assert batch.yaml_local_path.parent == src_dir, f"yaml landed in {batch.yaml_local_path.parent}, expected {src_dir}"


# --- PreparedBatch.ok property --------------------------------------------


def test_prepared_batch_ok_requires_no_errors_and_specs():
    batch = PreparedBatch(errors=["x"], specs=[])
    assert batch.ok is False

    batch = PreparedBatch(errors=[], specs=[])
    assert batch.ok is False  # no specs -> not ok

    batch = PreparedBatch(
        errors=[],
        specs=[
            RunSpec(server_id="s", remote_dir="/", command_template="x", max_parallel=1, mode=RunMode.selected_files)
        ],
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


# --- dag kind (Phase 10.5) ------------------------------------------------


def test_dag_kind_builds_single_spec_and_writes_yaml(tmp_path):
    """For kind=dag, exactly one RunSpec is emitted and a workflow.yaml
    containing the editor's per-step dict list (including ``inputs: [...]``)
    is written next to the first XYZ.
    """
    import yaml

    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    inputs = [InputSource(path=src_dir / "a.xyz")]
    payload = _payload_dag(inputs=inputs, remote_dir="/work")
    batch = SubmitUseCase().execute(payload)

    assert batch.ok, batch.errors
    assert len(batch.specs) == 1
    spec = batch.specs[0]
    assert spec.workflow_kind == WorkflowKind.dag
    # DAG adapter uses the same confflow command template; supporting
    # sources carry the workflow.yaml path.
    assert "confflow" in spec.command_template
    assert any("workflow.yaml" in s.path for s in spec.supporting_sources)
    assert batch.yaml_local_path is not None
    assert batch.yaml_local_path.name == "workflow.yaml"
    assert batch.yaml_local_path.exists()

    # The YAML body must contain every editor-emitted step dict, including
    # the per-step ``inputs`` arrays that mark the DAG topology.
    parsed = yaml.safe_load(batch.yaml_local_path.read_text(encoding="utf-8"))
    by_name = {step["name"]: step for step in parsed["steps"]}
    assert by_name["confgen"]["inputs"] == []
    assert by_name["sp"]["inputs"] == ["confgen"]
    assert by_name["freq"]["inputs"] == ["confgen"]


def test_dag_kind_writes_yaml_at_output_dir(tmp_path):
    """The DAG YAML is dropped at ``payload.output_dir / "workflow.yaml"``.

    Mirrors the linear ``confflow`` helper: the wizard passes the
    first-XYZ's parent directory as ``output_dir`` so the worker's SFTP
    upload picks up both files in one walk.
    """
    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    payload = _payload_dag(
        inputs=[InputSource(path=src_dir / "a.xyz")],
        remote_dir="/work",
    )
    # The page worker likewise sets output_dir to the first XYZ's parent
    # directory; we replicate that explicitly here so the assertion reads
    # against the actual on-disk location of the YAML.
    payload.output_dir = src_dir
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert batch.yaml_local_path is not None
    assert batch.yaml_local_path.parent == src_dir
    assert batch.yaml_local_path.name == "workflow.yaml"


def test_dag_kind_remote_targets_pair_with_inputs(tmp_path):
    """DAG kind pairs local inputs to remote_targets the same way confflow does."""

    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    local = tmp_path / "work_dir" / "a.xyz"
    payload = _payload_dag(
        inputs=[InputSource(path=local)],
        remote_dir="/remote",
    )
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert any(t.endswith("a.xyz") for t in batch.remote_targets)
    # The workflow.yaml is listed as a supporting source on the single spec.
    spec = batch.specs[0]
    assert spec.supporting_sources, "expected workflow.yaml in supporting sources"
    assert any("workflow.yaml" in s.path for s in spec.supporting_sources)


def test_dag_kind_max_parallel_propagates(tmp_path):
    """DAG payload's max_parallel flows onto the RunSpec like confflow."""
    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    local = tmp_path / "a.xyz"
    payload = _payload_dag(inputs=[InputSource(path=local)], max_parallel=4)
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert all(s.max_parallel == 4 for s in batch.specs)


def test_dag_kind_supports_fan_in_inputs(tmp_path):
    """Multiple inputs fed by the editor still produce a single DAG spec.

    The fan-in is modelled inside the YAML steps (``inputs: [...]``); the
    prepare-batch layer sees only the toplevel XYZ inputs uploaded by the
    worker callback.  We just confirm the use case emits one spec with all
    sources.
    """
    from jobdesk_app.core import workflow_spec

    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    local_a = tmp_path / "a.xyz"
    local_b = tmp_path / "b.xyz"
    payload = _payload_dag(
        inputs=[InputSource(path=local_a), InputSource(path=local_b)],
        remote_dir="/work",
    )
    batch = SubmitUseCase().execute(payload)
    assert batch.ok
    assert len(batch.specs) == 1
    spec = batch.specs[0]
    # Sources are the remote paths the SFTP helper will use for the run.
    assert {s.path for s in spec.sources} == {"/work/a.xyz", "/work/b.xyz"}
