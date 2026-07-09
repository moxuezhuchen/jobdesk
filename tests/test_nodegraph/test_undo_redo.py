"""Undo / redo behaviour for node / edge operations.

We verify that adding then undoing a node brings the model back to its
prior state, and that redoing restores it. We also confirm that
deleting a node cascades edge removal and that undoing the deletion
restores the edges too.
"""
from __future__ import annotations

from jobdesk_app.gui.nodegraph.model import NodeKind
from jobdesk_app.gui.nodegraph.serialization import (
    AddEdgeCommand,
    AddNodeCommand,
    RemoveNodeCommand,
)


def test_add_node_undo_redo(graph_scene):
    scene, _view = graph_scene
    item = scene.add_node(NodeKind.OPT, (10.0, 10.0))
    node_id = item.node_id
    assert node_id in scene.graph().nodes
    scene.undo_stack().undo()
    assert node_id not in scene.graph().nodes
    scene.undo_stack().redo()
    assert node_id in scene.graph().nodes
    assert scene.graph().nodes[node_id].kind is NodeKind.OPT


def test_remove_node_undo_restores_cascade(graph_scene):
    scene, _view = graph_scene
    xyz = scene.add_node(NodeKind.XYZ_FILE, (40.0, 60.0))
    opt = scene.add_node(NodeKind.OPT, (260.0, 60.0))
    scene.add_edge_at(xyz.node_id, "out", opt.node_id, "in")
    edge_id = next(iter(scene.graph().edges))
    # Push a manual RemoveNodeCommand so we can verify the cascade
    # behaviour independently of the keyboard path.
    scene.undo_stack().push(RemoveNodeCommand(scene.graph(), xyz.node_id))
    assert xyz.node_id not in scene.graph().nodes
    assert edge_id not in scene.graph().edges
    scene.undo_stack().undo()
    assert xyz.node_id in scene.graph().nodes
    assert edge_id in scene.graph().edges


def test_add_edge_then_undo(graph_scene):
    scene, _view = graph_scene
    xyz = scene.add_node(NodeKind.XYZ_FILE, (40.0, 60.0))
    opt = scene.add_node(NodeKind.OPT, (260.0, 60.0))
    edge_id = scene.add_edge_at(xyz.node_id, "out", opt.node_id, "in").edge_id
    assert edge_id in scene.graph().edges
    scene.undo_stack().undo()
    assert edge_id not in scene.graph().edges


def test_undo_stack_index_changes_emit_topology_signal(graph_scene, qtbot):
    scene, _view = graph_scene
    fired: list[int] = []

    def on_change():
        fired.append(1)

    scene.topology_changed.connect(on_change)
    scene.add_node(NodeKind.OPT, (10.0, 10.0))
    scene.undo_stack().undo()
    scene.undo_stack().redo()
    assert len(fired) >= 2  # at least once for the add and once for redo