"""Submit page value types.

Phase 14B: the new :class:`SubmitPage` builds a single :class:`SubmitPayload`
value type when the user clicks Submit.  The payload is the only thing that
crosses the page → main-window boundary; the use case and the worker callback
both consume it.

Two supporting types live here too:

* :class:`InputSource` — one input file (XYZ / gjf / inp) selected by the
  user, with the side (``"local"`` / ``"remote"``) it came from so the worker
  knows whether to upload or treat it as already on the server.
* :class:`WorkflowFields` — plain value type for the workflow form (work_dir
  name + step list + advanced options dict).  We mirror the dataclass the
  wizard already used internally so the use case can construct a
  :class:`WorkflowSpec` without re-reading Qt widgets.

We keep these in ``core/`` rather than ``gui/`` because they're plain
dataclasses — the GUI layer is the only consumer today, but tests and the
future CLI use case can pick them up without pulling in PySide6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SubmitKind = Literal["single", "confflow", "dag"]
"""How the remote program should interpret this submission.

* ``"single"`` — one quantum-chemistry binary invocation per input
  (``.gjf`` / ``.inp``).  Produces a flat :class:`RunSpec` whose
  ``workflow_kind`` is gaussian / orca.
* ``"confflow"`` — ConfFlow workflow engine runs over a YAML config plus
  one or more XYZ inputs.  Produces a :class:`RunSpec` whose
  ``workflow_kind`` is ``confflow`` plus the supporting workflow.yaml.
* ``"dag"`` — Phase 10.5: same remote command / engine as ``"confflow"``,
  but the YAML's per-step ``inputs`` lists (Phase 10.1–10.4) declare a
  DAG topology (fan-in / fan-out).  Produces a :class:`RunSpec` whose
  ``workflow_kind`` is ``dag`` plus the supporting workflow.yaml.
"""


InputKind = Literal["xyz", "gjf", "inp"]
InputSide = Literal["local", "remote"]


@dataclass(frozen=True)
class InputSource:
    """One input file the user picked on the Submit page.

    ``side`` distinguishes local files (must be uploaded before the run)
    from remote files (already on the server).  ``kind`` is inferred from
    the file extension; it steers the use-case logic — XYZ files need
    workflow generation, ``.gjf`` / ``.inp`` already carry their inputs.
    """

    path: Path
    side: InputSide = "local"
    kind: InputKind = "xyz"


@dataclass
class WorkflowFields:
    """Plain value type for the workflow form.

    Mirrors the public shape the workflow side of the legacy ConfFlow
    wizard used to expose via ``work_dir_name`` / ``steps`` /
    ``advanced_options``. We keep it as a dataclass so the use case can
    pass it through without touching Qt widgets.
    """

    work_dir_name: str
    steps: list[str] = field(default_factory=list)
    advanced_options: dict[str, Any] = field(default_factory=dict)
    # When a workflow was authored in the workflow page, this is the
    # validated final document (global YAML plus ordered step YAML).  Keeping
    # it here prevents the submit boundary from reducing rich step fragments
    # to legacy form tokens and reconstructing a different workflow.
    yaml_text: str | None = None


@dataclass
class DagWorkflowFields:
    """Plain value type for the Phase 10.5 ``kind="dag"`` submit path.

    The editor's :class:`NodeGraph` already serialises itself into a list
    of per-step dicts via :func:`jobdesk_app.gui.nodegraph.spec_bridge.to_workflow_spec`
    (each dict carries ``name``, ``type``, ``params``, ``inputs``).  The
    use case hands that list straight to the confflow workflow YAML —
    no per-step type re-definition is required, the engine reads the
    same schema it already understood for linear workflows, just with
    non-empty ``inputs`` arrays.

    ``work_dir_name`` mirrors :class:`WorkflowFields.work_dir_name` so the
    YAML's ``work_dir:`` key is populated; ``steps`` is the serialised
    graph and ``advanced_options`` is merged into the workflow-level
    config the same way the legacy wizard did.
    """

    work_dir_name: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    advanced_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmitPayload:
    """The single value type for "the user wants to submit this".

    The :class:`SubmitPage` builds one when Submit is clicked and emits it
    on :pyattr:`SubmitPage.submit_requested`.  The main window drives the
    background worker off this payload.

    ``calc`` carries the calculation-field values used by :class:`SubmitUseCase`
    (program / method_basis / charge / multiplicity / nproc / mem).  ``workflow``
    is ``None`` for :pyattr:`SubmitKind` ``"single"`` and a :class:`WorkflowFields`
    for ``"confflow"``.

    ``dag`` is the Phase 10.5 parallel slot for ``"dag"`` payloads — it is
    populated from the editor's :class:`NodeGraph` via the bridge's
    :func:`to_workflow_spec` and carries the full per-step dict list
    (including ``inputs: [...]`` for fan-out / fan-in) along with a
    ``work_dir_name`` that mirrors :class:`WorkflowFields`.

    ``output_paths`` is a convenience list the page pre-resolves (e.g. a
    freshly generated ``.gjf`` written next to the input XYZ).  Workers
    may use it to skip re-rendering.

    ``server_id`` / ``remote_dir`` mirror the Files-page connection state
    so the worker doesn't have to re-derive them from AppState.  ``state``
    is the same ``AppState`` instance shared across pages; the worker
    reads ``current_project_root`` from it.
    """

    kind: SubmitKind
    inputs: list[InputSource]
    program: str  # "gaussian" | "orca"
    calc: Any  # CalculationFields (avoid circular import)
    workflow: WorkflowFields | None
    output_dir: Path
    output_paths: list[Path] = field(default_factory=list)
    server_id: str = ""
    remote_dir: str = "/"
    max_parallel: int = 1
    dag: DagWorkflowFields | None = None


__all__ = [
    "DagWorkflowFields",
    "InputKind",
    "InputSide",
    "InputSource",
    "SubmitKind",
    "SubmitPayload",
    "WorkflowFields",
]
