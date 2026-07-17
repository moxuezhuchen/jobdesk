"""State management and shared data structures for the workflow page.

This module contains the WorkflowDraft dataclass, node kind mappings, and
helper functions for YAML serialization and step parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from ...nodegraph.model import Node

__all__ = ["WorkflowDraft", "_STEP_KINDS", "_dump_yaml", "_step_kind", "_node_fragment"]


# ---- node kind mappings ---------------------------------------------------

from ...nodegraph.model import NodeKind

_STEP_KINDS: frozenset = frozenset(
    {
        NodeKind.CONF_GEN,
        NodeKind.PRE_OPT,
        NodeKind.OPT,
        NodeKind.SINGLE_POINT,
        NodeKind.FREQUENCY,
        NodeKind.TS,
        NodeKind.REFINE,
    }
)

_ITASK_TO_KIND: dict[str, NodeKind] = {
    "preopt": NodeKind.PRE_OPT,
    "opt": NodeKind.OPT,
    "sp": NodeKind.SINGLE_POINT,
    "freq": NodeKind.FREQUENCY,
    "ts": NodeKind.TS,
    "refine": NodeKind.REFINE,
}

_KIND_TO_ITASK: dict[NodeKind, str] = {value: key for key, value in _ITASK_TO_KIND.items()}


# ---- YAML helpers ---------------------------------------------------------


def _dump_yaml(value: dict[str, Any]) -> str:
    """Dump a dictionary as formatted YAML."""
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _step_kind(fragment: dict[str, Any]) -> NodeKind:
    """Determine the step kind from a YAML fragment."""
    if fragment.get("type") == "confgen":
        return NodeKind.CONF_GEN
    if fragment.get("type") != "calc":
        raise ValueError("Step type must be 'calc' or 'confgen'.")
    params = fragment.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("Step params must be a mapping.")
    return _ITASK_TO_KIND.get(str(params.get("itask") or "opt").lower(), NodeKind.OPT)


def _node_fragment(node: Node) -> dict[str, Any]:
    """Convert a Node into a workflow YAML fragment."""
    if node.kind is NodeKind.CONF_GEN:
        return {"name": node.title, "type": "confgen", "params": dict(node.params)}
    params = dict(node.params)
    params.setdefault("itask", _KIND_TO_ITASK.get(node.kind, "opt"))
    return {"name": node.title, "type": "calc", "params": params}


# ---- draft state ----------------------------------------------------------


@dataclass
class WorkflowDraft:
    """The single in-memory source for page controls and YAML output."""

    graph: Any  # NodeGraph
    global_config: dict[str, Any]
    preset: Any = None
    dirty: bool = False
