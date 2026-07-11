#!/usr/bin/env python3

"""DAG helpers for the workflow engine (Phase 3).

The engine used to dispatch steps in linear order (``for i, step in
enumerate(steps)``). Phase 3 turns that into a DAG: each step declares an
``inputs: list[str]`` of upstream step names, and the engine uses
``graphlib.TopologicalSorter`` to schedule steps in dependency order.

The helpers here stay free of side effects and IO so they are easy to
unit test.
"""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import Any

from ..core.exceptions import ConfFlowError

__all__ = [
    "build_step_graph",
    "topo_order",
    "resolve_step_outputs_map",
]


def _step_name(step: dict[str, Any], fallback_idx: int) -> str:
    """Resolve the canonical step name with a deterministic fallback."""
    raw = step.get("name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return f"step_{fallback_idx:02d}"


def build_step_graph(
    steps: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Build the predecessor map and lookups for a workflow.

    Returns
    -------
    predecessors : dict[step_name -> list[upstream step names]]
        Empty list means the step is a root. The legacy linear fallback
        (no step declares any inputs) is *not* applied here; that is the
        engine's job.
    by_name : dict[step_name -> step dict]
        Original step dict, indexed by canonical name.
    declared_inputs : dict[step_name -> list of upstream names actually
        declared in the YAML ``inputs`` field].
        Used by the engine to decide whether to fall back to the linear
        chain.
    """
    by_name: dict[str, dict[str, Any]] = {}
    declared_inputs: dict[str, list[str]] = {}
    predecessors: dict[str, list[str]] = {}

    for idx, step in enumerate(steps, start=1):
        name = _step_name(step, idx)
        if name in by_name:
            raise ConfFlowError(
                f"workflow step names must be unique; duplicate name: {name!r}"
            )
        by_name[name] = step

        raw_inputs = step.get("inputs")
        coerced: list[str] = []
        if raw_inputs is None:
            coerced = []
        elif isinstance(raw_inputs, str):
            coerced = [raw_inputs] if raw_inputs.strip() else []
        elif isinstance(raw_inputs, (list, tuple)):
            for item in raw_inputs:
                if item is None:
                    continue
                s = str(item).strip()
                if s:
                    coerced.append(s)
        else:
            coerced = [str(raw_inputs)]
        declared_inputs[name] = list(coerced)
        predecessors[name] = list(coerced)

    return predecessors, by_name, declared_inputs


def topo_order(predecessors: dict[str, list[str]]) -> list[list[str]]:
    """Compute wavefront-grouped topological order.

    Each inner list is one wavefront (steps whose predecessors are all
    in earlier waves). Within a wavefront, names are sorted so that the
    schedule is deterministic across runs and Python versions, regardless
    of dict insertion order.

    Raises
    ------
    ConfFlowError
        Wrapping ``graphlib.CycleError`` with a message naming the
        participating nodes (Python 3.11+ ``CycleError`` already lists the
        cycle; we re-raise to keep the engine's exception surface
        consistent).
    """
    if not predecessors:
        return []

    sorter = TopologicalSorter(predecessors)
    try:
        sorter.prepare()
    except CycleError as exc:
        raise ConfFlowError(
            f"workflow contains a dependency cycle: {exc}"
        ) from exc

    waves: list[list[str]] = []
    while sorter.is_active():
        ready = sorter.get_ready()
        if not ready:
            # Defensive: prepare() succeeded but get_ready() returned
            # nothing while is_active() is True. This should be
            # unreachable in CPython; if it ever fires, surface it
            # clearly rather than spinning.
            raise ConfFlowError(
                "topological sorter returned no ready nodes but is "
                "still active (graph state corruption)"
            )
        wave = sorted(ready)
        waves.append(wave)
        sorter.done(*wave)

    return waves


def resolve_step_outputs_map(
    by_name: dict[str, dict[str, Any]],
    declared_outputs: dict[str, list[str]] | None = None,
    step_dirnames: list[str] | None = None,
) -> dict[str, str]:
    """Return a map of step_name -> primary output path.

    The primary output is the first declared path under the step's
    directory. When the step has no ``outputs`` declaration the engine
    falls back to the standard filenames (``search.xyz`` for confgen,
    ``output.xyz`` for calc) so that the predecessor -> successor
    handoff keeps working without users having to spell out every file.

    The ``declared_outputs`` and ``step_dirnames`` arguments are
    optional; the call sites that already have them pass them in to avoid
    rebuilding.
    """
    declared_outputs = declared_outputs or {}
    step_dirnames = step_dirnames or []
    keys = list(by_name.keys())
    fallback_for_type = {"confgen": "search.xyz", "gen": "search.xyz", "calc": "output.xyz", "task": "output.xyz"}

    result: dict[str, str] = {}
    for idx, name in enumerate(keys):
        step = by_name[name]
        step_type = str(step.get("type", "")).strip().lower()
        outputs = declared_outputs.get(name) or []

        if outputs:
            primary = outputs[0]
        else:
            dirname = step_dirnames[idx] if idx < len(step_dirnames) else f"step_{idx + 1:02d}"
            fallback = fallback_for_type.get(step_type, "output.xyz")
            primary = f"{dirname}/{fallback}"

        result[name] = primary

    return result


def _backward_compat_predecessors(
    steps: list[dict[str, Any]],
    by_name: dict[str, dict[str, Any]],
    declared_inputs: dict[str, list[str]],
) -> dict[str, list[str]]:
    """Build a predecessor map that reproduces the legacy linear chain.

    Used only when *no* step declares any ``inputs`` field. The first
    step is a root, every other step has the previous step as its single
    predecessor.
    """
    keys = list(by_name.keys())
    preds: dict[str, list[str]] = {k: [] for k in keys}
    for i in range(1, len(keys)):
        preds[keys[i]] = [keys[i - 1]]
    return preds