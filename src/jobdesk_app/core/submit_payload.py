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


SubmitKind = Literal["single", "confflow"]
"""How the remote program should interpret this submission.

* ``"single"`` — one quantum-chemistry binary invocation per input
  (``.gjf`` / ``.inp``).  Produces a flat :class:`RunSpec` whose
  ``workflow_kind`` is gaussian / orca.
* ``"confflow"`` — ConfFlow workflow engine runs over a YAML config plus
  one or more XYZ inputs.  Produces a :class:`RunSpec` whose
  ``workflow_kind`` is ``confflow`` plus the supporting workflow.yaml.
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

    Mirrors the dict the embedded :class:`WorkflowWidget` already exposes
    via its public API (``work_dir_name``, ``steps``, ``advanced_options``).
    We keep it as a dataclass so the use case can pass it through without
    touching Qt widgets.
    """

    work_dir_name: str
    steps: list[str] = field(default_factory=list)
    advanced_options: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmitPayload:
    """The single value type for "the user wants to submit this".

    The :class:`SubmitPage` builds one when Submit is clicked and emits it
    on :pyattr:`SubmitPage.submit_requested`.  The main window drives the
    background worker off this payload.

    ``calc`` carries the :class:`CalculationFields` value type from the
    embedded :class:`CalculationWidget`.  ``workflow`` is ``None`` for
    :pyattr:`SubmitKind` ``"single"`` and a :class:`WorkflowFields` for
    ``"confflow"``.

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


__all__ = [
    "InputKind",
    "InputSide",
    "InputSource",
    "SubmitKind",
    "SubmitPayload",
    "WorkflowFields",
]