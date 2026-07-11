"""Shared fixtures for the node-graph editor tests.

The tests need a deterministic ``QApplication`` instance plus a few
helpers for constructing small graphs. Keeping them in a conftest
avoids each test file re-implementing the boilerplate.
"""
from __future__ import annotations

import os

# The PySide6 tests need an offscreen QPA so they can run in CI on
# Windows runners that have no DISPLAY/X server. Setting this before
# any Qt import is critical.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6", reason="PySide6 not installed")


from jobdesk_app.gui.nodegraph.canvas import GraphScene, GraphView  # noqa: E402
from jobdesk_app.gui.nodegraph.model import (  # noqa: E402
    Edge,
    NodeGraph,
    NodeKind,
    default_node,
)
from jobdesk_app.gui.nodegraph.serialization import to_json  # noqa: E402


@pytest.fixture
def graph_scene(qtbot):
    """A fresh :class:`GraphScene` with a view attached for hit-testing."""
    scene = GraphScene()
    view = GraphView(scene)
    view.resize(640, 480)
    qtbot.addWidget(view)
    return scene, view


def make_linear_graph() -> NodeGraph:
    """Build a deterministic XYZ_FILE -> OPT 2-node linear graph."""
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(40.0, 60.0))
    opt = default_node(NodeKind.OPT, position=(260.0, 60.0))
    g.add_node(xyz)
    g.add_node(opt)
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out",
                    dst_node=opt.id, dst_port="in"))
    return g


@pytest.fixture
def linear_graph():
    return make_linear_graph()


__all__ = ["graph_scene", "linear_graph", "make_linear_graph", "to_json"]
