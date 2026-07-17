"""Interaction-level tests for :class:`GraphScene` + :class:`GraphView`.

We avoid ``qtbot.mouseClick`` on the scene because PySide6 doesn't
deliver pointer events to :class:`QGraphicsScene` reliably in
offscreen mode. Instead we drive the scene's public API directly —
this still exercises the model mutation, undo/redo and cascade
behaviour we care about, without depending on the window manager.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QGraphicsSceneMouseEvent

from jobdesk_app.gui.nodegraph.model import (
    NodeKind,
)


def _scene_center(scene):
    return QPointF(300.0, 200.0)


def test_add_then_move_node_updates_model_position(graph_scene):
    scene, _view = graph_scene
    item = scene.add_node(NodeKind.OPT, (0.0, 0.0))
    node_id = item.node_id
    # Directly call the scene's move-commit path with a synthetic mouse
    # release. setPos alone doesn't fire ItemPositionHasChanged, so we
    # drive the mouseRelease handler the same way a real user drag would.
    item.setPos(150.0, 75.0)
    scene._last_known_positions[node_id] = (0.0, 0.0)  # force "moved"
    event = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    event.setScenePos(QPointF(150.0, 75.0))
    event.setButton(Qt.MouseButton.LeftButton)
    item.setSelected(True)
    scene.mouseReleaseEvent(event)
    node = scene.graph().nodes[node_id]
    assert node.position == (150.0, 75.0)


def test_port_click_compatible_creates_edge(graph_scene):
    scene, _view = graph_scene
    xyz = scene.add_node(NodeKind.XYZ_FILE, (40.0, 60.0))
    opt = scene.add_node(NodeKind.OPT, (260.0, 60.0))
    src_port = xyz.port_item("out")
    dst_port = opt.port_item("in")
    assert src_port is not None and dst_port is not None
    # Drive the scene's port-click/release flow directly.
    scene.begin_wire_from(src_port)
    assert scene._wire_drag is not None
    scene.port_released_at("in", dst_port.port_type, "in", dst_port.scenePos())
    assert any(edge.dst_node == opt.node_id for edge in scene.graph().edges.values())
    # Wire-drag state must have been cleared.
    assert scene._wire_drag is None


def test_port_click_incompatible_does_not_create_edge(graph_scene):
    scene, _view = graph_scene
    xyz = scene.add_node(NodeKind.XYZ_FILE, (40.0, 60.0))
    scene.add_node(NodeKind.OPT, (260.0, 60.0))
    src_port = xyz.port_item("out")  # STRUCTURE output
    scene.begin_wire_from(src_port)
    # Release over an empty scene position to force a cancel.
    scene.port_released_at(
        "__nope__",
        src_port.port_type,
        "in",
        QPointF(99999.0, 99999.0),
    )
    assert scene.graph().edges == {}


def test_port_compatibility_incompatible_types_do_not_connect(graph_scene):
    """The port-compatibility helper is direction-sensitive.

    Per :class:`NodeGraph.validate`, only ``STRUCTURES -> STRUCTURE``
    is allowed as a "downcast" (Refine picks one conformer from the
    ensemble). Anything else with mismatched types must be rejected.
    """
    from jobdesk_app.gui.nodegraph.canvas import _ports_compatible
    from jobdesk_app.gui.nodegraph.model import PortType

    # Identical types are fine.
    assert _ports_compatible(PortType.STRUCTURE, PortType.STRUCTURE) is True
    # Allowed downcast direction.
    assert _ports_compatible(PortType.STRUCTURES, PortType.STRUCTURE) is True
    # Reverse direction is not allowed — you can't widen a single
    # structure into a multi-conformer ensemble.
    assert _ports_compatible(PortType.STRUCTURE, PortType.STRUCTURES) is False
    # Unrelated types never connect.
    assert _ports_compatible(PortType.CONFIG, PortType.STRUCTURE) is False
    assert _ports_compatible(PortType.ENERGY, PortType.STRUCTURE) is False


def test_delete_key_removes_selected_node_and_cascades(graph_scene):
    scene, _view = graph_scene
    xyz = scene.add_node(NodeKind.XYZ_FILE, (40.0, 60.0))
    opt = scene.add_node(NodeKind.OPT, (260.0, 60.0))
    scene.add_edge_at(xyz.node_id, "out", opt.node_id, "in")
    assert len(scene.graph().nodes) == 2
    assert len(scene.graph().edges) == 1
    # Select the XYZ node and press Delete via the scene's key handler.
    xyz.setSelected(True)
    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    scene.keyPressEvent(event)
    assert len(scene.graph().nodes) == 1
    assert scene.graph().nodes[opt.node_id].kind is NodeKind.OPT
    # Cascade-removed edge.
    assert scene.graph().edges == {}


def test_delete_key_removes_selected_edge(graph_scene):
    scene, _view = graph_scene
    xyz = scene.add_node(NodeKind.XYZ_FILE, (40.0, 60.0))
    opt = scene.add_node(NodeKind.OPT, (260.0, 60.0))
    scene.add_edge_at(xyz.node_id, "out", opt.node_id, "in")
    edge_id = next(iter(scene.graph().edges))
    edge_item = scene.edge_item(edge_id)
    assert edge_item is not None
    edge_item.setSelected(True)
    event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    scene.keyPressEvent(event)
    assert scene.graph().edges == {}
