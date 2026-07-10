"""SubmitUseCase — single entry point for submitting a batch of molecules.

Phase 14B: replaces the three run entry points that previously lived on
``FileTransferPage`` (``_run_selected``, ``_run_confflow``,
``_open_confflow_wizard``).  Both ``kind == "single"`` and
``kind == "confflow"`` go through this use case.

The use case does **not** perform file uploads or remote interactions —
those still happen in the page-level worker callback, which knows about
the live ``FileTransferService`` connection.  The use case is a thin
wrapper that:

1. Validates the payload (kinds + inputs).
2. Builds the ``RunSpec`` (or list of ``RunSpec``) for the batch.
3. Returns a :class:`PreparedBatch` the page can hand to the worker.

For ``confflow`` we also render the ``workflow.yaml`` to disk next to
the first XYZ so the existing SFTP upload helper can ship it.  We do
**not** upload it here — the worker callback does that, mirroring how
``_on_confflow_done`` worked before the refactor.

Public API:

* :class:`SubmitUseCase` — main entry point.
* :class:`PreparedBatch` — return value (local files, remote targets,
  spec, optional yaml path).

The class is intentionally framework-free: no Qt, no asyncio, no I/O.
The coordinator factory is passed in (defaults to ``RunCoordinator``)
so tests can substitute a fake without monkeypatching.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..core.run import RunMode, RunSource, RunSpec, WorkflowKind, chunk_sources
from ..core.submit_payload import DagWorkflowFields, SubmitPayload
from ..core.workflow_spec import (
    ConfFlowUnavailableError,
    WorkflowSpec,
    require_confflow,
    write_workflow_yaml,
)
from .program_adapters import ConfFlowAdapter


@dataclass
class PreparedBatch:
    """Outcome of :meth:`SubmitUseCase.execute`.

    The page worker iterates ``local_paths`` paired with ``remote_targets``
    and uploads each via the existing ``FileTransferService`` helper.

    * ``specs`` — list of :class:`RunSpec` (one per chunk).  ``single``
      produces ``len(local_paths)`` specs; ``confflow`` always produces
      a single spec (the workflow wraps them).
    * ``yaml_local_path`` — set only for ``confflow``; the page worker
      uploads this alongside the XYZ files.
    * ``errors`` — non-empty if validation failed; the page surfaces
      them on the activity log without raising.
    """

    local_paths: list[Path] = field(default_factory=list)
    remote_targets: list[str] = field(default_factory=list)
    specs: list[RunSpec] = field(default_factory=list)
    yaml_local_path: Path | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.specs)


class SubmitUseCase:
    """Build a :class:`PreparedBatch` from a :class:`SubmitPayload`.

    Constructor takes a ``coordinator_factory`` that produces something
    with a ``create_run`` method.  We don't actually call the
    coordinator from inside ``execute`` — the worker callback does —
    but accepting the factory now keeps the signature stable for when
    we tighten the boundary in Phase 14D.
    """

    def __init__(
        self,
        coordinator_factory: Callable[..., object] | None = None,
    ) -> None:
        self._coordinator_factory = coordinator_factory

    def execute(self, payload: SubmitPayload) -> PreparedBatch:
        """Validate ``payload`` and build the run specs.

        Returns a :class:`PreparedBatch` with local paths and remote
        targets the page worker uploads before calling the coordinator.
        Validation errors are reported via ``batch.errors`` rather than
        raised so the page can render them in the activity log.
        """
        errors: list[str] = []
        if not payload.inputs:
            errors.append("No inputs selected for submission")
            return PreparedBatch(errors=errors)
        if payload.kind == "confflow" and payload.workflow is None:
            errors.append("Workflow fields are required for ConfFlow submission")
            return PreparedBatch(errors=errors)
        if payload.kind == "dag" and payload.dag is None:
            errors.append("DAG workflow fields are required for DAG submission")
            return PreparedBatch(errors=errors)
        if payload.kind == "single" and payload.program not in ("gaussian", "orca"):
            errors.append(f"Unsupported program: {payload.program!r}")
            return PreparedBatch(errors=errors)
        if not payload.server_id:
            errors.append("No server selected")
            return PreparedBatch(errors=errors)

        local_paths: list[Path] = []
        remote_targets: list[str] = []
        for source in payload.inputs:
            if source.side == "remote":
                # Remote sources are already on the server; nothing to
                # upload.  The worker still records the path so the
                # result table can surface them later.
                remote_targets.append(str(source.path))
            else:
                local_paths.append(source.path)
                remote_targets.append(
                    remote_child_path(payload.remote_dir, source.path.name)
                )

        if not remote_targets:
            errors.append("No remote targets resolved from inputs")
            return PreparedBatch(errors=errors)

        try:
            if payload.kind == "confflow":
                specs, yaml_path = self._build_confflow_specs(payload, remote_targets)
            elif payload.kind == "dag":
                specs, yaml_path = self._build_dag_specs(payload, remote_targets)
            else:
                specs = self._build_single_specs(payload, remote_targets)
                yaml_path = None
        except ConfFlowUnavailableError as exc:
            errors.append(f"ConfFlow unavailable: {exc}")
            return PreparedBatch(errors=errors)
        except ValueError as exc:
            errors.append(str(exc))
            return PreparedBatch(errors=errors)

        return PreparedBatch(
            local_paths=local_paths,
            remote_targets=remote_targets,
            specs=specs,
            yaml_local_path=yaml_path,
        )

    # ── internal helpers ────────────────────────────────────────────────

    def _build_single_specs(
        self,
        payload: SubmitPayload,
        remote_targets: list[str],
    ) -> list[RunSpec]:
        """One :class:`RunSpec` per chunk, mirroring the legacy _create_specs.

        For each remote target we build a ``RunSource``; if the payload
        was generated from a ``.gjf`` / ``.inp`` the corresponding
        ``input_builder`` would already have rendered the file under
        ``payload.output_paths`` — we don't touch that here, the spec
        simply points at the remote path.
        """
        sources = [RunSource(path=p) for p in remote_targets]
        chunks = chunk_sources(sources, batch_size=None)
        specs: list[RunSpec] = []
        workflow_kind = (
            WorkflowKind.orca if payload.program == "orca" else WorkflowKind.gaussian
        )
        for chunk in chunks:
            specs.append(
                RunSpec(
                    server_id=payload.server_id,
                    remote_dir=payload.remote_dir,
                    command_template=_command_template_for(payload.program),
                    max_parallel=payload.max_parallel,
                    mode=RunMode.selected_files,
                    sources=chunk,
                    workflow_kind=workflow_kind,
                )
            )
        return specs

    def _build_confflow_specs(
        self,
        payload: SubmitPayload,
        remote_targets: list[str],
    ) -> tuple[list[RunSpec], Path]:
        """Render ``workflow.yaml`` next to the first XYZ and build the spec.

        The YAML path mirrors the legacy behaviour: the wizard wrote it
        next to the first XYZ file (so the SFTP uploader could ship it).
        We do the same here — callers shouldn't have to know.
        """
        assert payload.workflow is not None  # checked in execute()
        workflow = payload.workflow
        first_xyz = _resolve_yaml_dir(payload)
        yaml_local = first_xyz / "workflow.yaml"

        calc = payload.calc
        method, basis = _split_method_basis(getattr(calc, "method_basis", ""))
        spec = WorkflowSpec.from_form(
            work_dir_name=workflow.work_dir_name,
            program=payload.program,
            method=method,
            basis=basis,
            charge=calc.charge,
            multiplicity=calc.multiplicity,
            nproc=calc.nproc,
            memory_mb=_parse_mem_mb(calc.mem),
            steps=tuple(workflow.steps),
            extra_options=workflow.advanced_options or None,
        )
        write_workflow_yaml(spec, yaml_local)
        yaml_target = remote_child_path(payload.remote_dir, yaml_local.name)
        run_spec = ConfFlowAdapter.build_spec(
            server_id=payload.server_id,
            remote_dir=payload.remote_dir,
            xyz_paths=remote_targets,
            config_path=yaml_target,
            max_parallel=payload.max_parallel,
            resume=False,
        )
        return [run_spec], yaml_local

    def _build_dag_specs(
        self,
        payload: SubmitPayload,
        remote_targets: list[str],
    ) -> tuple[list[RunSpec], Path]:
        """Phase 10.5: render the editor's DAG workflow to YAML and submit.

        The :class:`DagWorkflowFields` carries the already-serialised step
        list produced by :func:`jobdesk_app.gui.nodegraph.spec_bridge.to_workflow_spec`
        — each step dict has ``name`` / ``type`` / ``params`` / ``inputs``.
        We rebuild a fresh :class:`WorkflowSpec` from the calc-side fields
        so the workflow-level config (program / method / basis / charge /
        multiplicity / nproc / memory_mb) stays validated, then drop the
        bridge-produced ``steps`` list verbatim onto it before serialising.

        The remote command is the same ``confflow {name} -c yaml ...`` the
        legacy ``confflow`` adapter uses; the engine reads ``StepConfig.inputs``
        via ``graphlib.TopologicalSorter`` since Phase 3.
        """
        assert payload.dag is not None  # checked in execute()
        dag = payload.dag
        first_xyz = _resolve_yaml_dir(payload)
        yaml_local = first_xyz / "workflow.yaml"

        calc = payload.calc
        method, basis = _split_method_basis(getattr(calc, "method_basis", ""))
        spec = WorkflowSpec.from_form(
            work_dir_name=dag.work_dir_name,
            program=payload.program,
            method=method,
            basis=basis,
            charge=calc.charge,
            multiplicity=calc.multiplicity,
            nproc=calc.nproc,
            memory_mb=_parse_mem_mb(calc.mem),
            steps=("dag",),  # placeholder; we overwrite via to_yaml_payload below
            extra_options=dag.advanced_options or None,
        )
        yaml_text = _render_dag_yaml(spec, dag.steps)
        yaml_local.parent.mkdir(parents=True, exist_ok=True)
        tmp = yaml_local.with_suffix(yaml_local.suffix + ".tmp")
        tmp.write_text(yaml_text, encoding="utf-8")
        tmp.replace(yaml_local)
        yaml_target = remote_child_path(payload.remote_dir, yaml_local.name)
        run_spec = ConfFlowAdapter.build_dag_spec(
            server_id=payload.server_id,
            remote_dir=payload.remote_dir,
            xyz_paths=remote_targets,
            config_path=yaml_target,
            max_parallel=payload.max_parallel,
            resume=False,
        )
        return [run_spec], yaml_local


def remote_child_path(remote_dir: str, name: str) -> str:
    """Mirror :func:`file_transfer_helpers.remote_child_path` (no GUI dep)."""
    base = (remote_dir or "/").rstrip("/") or "/"
    child = name.strip("/")
    if not child:
        return base
    joined = f"{base}/{child}"
    # Compact the path the same way the GUI helper does (handles "//").
    parts = [p for p in joined.split("/") if p != ""]
    return "/" + "/".join(parts) if parts else "/"


def _command_template_for(program: str) -> str:
    """Pick a sensible remote command template for ``program``.

    The legacy code reused whatever the user typed into the
    ``command_edit`` field.  For now we pick a sane default; the
    :class:`SubmitPage` may surface a free-form override later.
    """
    if program == "orca":
        return "orca {name} > {basename}.out"
    return "g16 {name} {basename}.log"


def _parse_mem_mb(mem: str) -> int:
    """Best-effort parse of a memory string like ``"4096MB"`` → ``4096``.

    Mirrors the legacy behaviour from ``_CalcPage`` which kept
    ``memory_mb`` as an int.  Falls back to ``1024`` for any
    unparseable string so the YAML still validates.
    """
    if not mem:
        return 1024
    text = mem.strip().upper().replace("MB", "").replace("GB", "000")
    try:
        return max(1024, int(text))
    except ValueError:
        return 1024


def _split_method_basis(method_basis: str) -> tuple[str, str]:
    """Split ``"B3LYP/6-31G(d)"`` into ``("B3LYP", "6-31G(d)")``.

    ConfFlow wants ``method`` and ``basis`` as separate form fields;
    the calc widget produces a single ``"method/basis"`` string.  When
    there is no slash we treat the whole string as the method.
    """
    text = (method_basis or "").strip()
    if "/" not in text:
        return text, ""
    method, basis = text.split("/", 1)
    return method.strip(), basis.strip()


def _render_dag_yaml(spec: WorkflowSpec, steps: list[dict]) -> str:
    """Serialise ``spec`` plus an editor-derived ``steps`` list to YAML.

    The :class:`WorkflowSpec` owns the workflow-level config; the
    per-step list is the bridge's output and must be written verbatim so
    each step's ``inputs: [...]`` field (Phase 10.1) survives. The
    confflow engine reads ``StepConfig.inputs`` since Phase 3 to
    walk the DAG via ``graphlib.TopologicalSorter``.
    """
    import yaml

    require_confflow()
    data = spec.global_config.model_dump(mode="json", exclude_none=True)
    data["steps"] = list(steps)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _resolve_yaml_dir(payload: SubmitPayload) -> Path:
    """Pick the directory where ``workflow.yaml`` should land on disk.

    The legacy wizard wrote the YAML next to the first input XYZ, so the
    SFTP uploader could ship it as a sibling file. The submit page
    mirrors this through ``payload.output_dir`` but only sets it from
    the first input's parent when the path is absolute — relative
    inputs leave it as ``Path(".")``, which then drops ``workflow.yaml``
    into the repository root during tests.

    Phase 11.1 — fix the contract: the YAML must live next to the first
    *local* input's parent directory. ``payload.output_dir`` is only
    used as a fallback for legacy callers that pre-set it explicitly
    (e.g. jobs originating from the Files page where output_dir is a
    user-chosen project root).
    """
    local_inputs = [s for s in payload.inputs if s.side == "local"]
    if local_inputs:
        candidate = local_inputs[0].path.parent
        # ``Path(".")`` (or any empty / non-absolute path that is just
        # the cwd placeholder) is unreliable; prefer the explicit input
        # parent so the YAML lands where the user expects it.
        if candidate and candidate != Path("."):
            return candidate
    if payload.output_dir and payload.output_dir != Path("."):
        return payload.output_dir
    # Last resort — keep cwd so we don't regress the original behaviour
    # for callers that legitimately want the YAML in the workspace root.
    return payload.output_dir


__all__ = ["PreparedBatch", "SubmitUseCase", "remote_child_path"]
