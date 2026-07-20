#!/usr/bin/env python3
"""Backward-compatibility shim for ``confflow.workflow.dag``.

The topological-scheduling helpers (``build_step_graph``,
``topo_order``, ``resolve_step_outputs_map``) previously lived in this
module. The Phase 1b DAG-introspection work added a sibling
``dag/__init__.py`` package, which shadows this module under PEP 328/338
import resolution: ``from confflow.workflow.dag import …`` now resolves to
the package, not this file.

The implementations were relocated to
``confflow.workflow.dag._legacy`` and are re-exported here for the
narrow set of code paths that still import the module by file path
(e.g. ``tests/test_dag_engine.py`` previously did so). New code should
import from ``confflow.workflow.dag`` (the package) directly.
"""

from __future__ import annotations

from .dag._legacy import (  # noqa: F401  -- re-export shim
    build_step_graph,
    resolve_step_outputs_map,
    topo_order,
)

__all__ = [
    "build_step_graph",
    "topo_order",
    "resolve_step_outputs_map",
]