"""Library → scene drag-and-drop via the custom MIME type.

We do not try to drive a real ``QDrag`` (the platform's drag manager
isn't always available in offscreen mode). The drop path is exercised
two ways:

1. Direct call to :meth:`GraphScene.handle_drop` with a mime we built
   via :func:`GraphScene.mime_data_for_node_kind`. This is the exact
   code path ``dropEvent`` uses, minus the Qt internal event plumbing.
2. ``QGraphicsSceneDragDropEvent`` is wrapped in a test-local subclass
   that overrides ``mimeData()`` so the full Qt event loop runs
   without us having to construct one via private API.
"""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QPointF
from PySide6.QtWidgets import QGraphicsSceneDragDropEvent

from jobdesk_app.gui.nodegraph.canvas import (
    NODE_KIND_MIME,
    GraphScene,
)
from jobdesk_app.gui.nodegraph.library import NodeLibraryPanel
from jobdesk_app.gui.nodegraph.model import (
    NodeGraph,
    NodeKind,
    default_node,
)
from jobdesk_app.gui.nodegraph.nodes import NODE_WIDTH


class _MimeDropEvent(QGraphicsSceneDragDropEvent):
    """A drop event carrying a caller-supplied :class:`QMimeData`."""

    def __init__(self, kind, mime: QMimeData, scene_pos: QPointF) -> None:
        super().__init__(kind)
        self._mime = mime
        self.setScenePos(scene_pos)
        # Use Qt.DropAction since QMimeData.Action doesn't exist in PySide6.
        from PySide6.QtCore import Qt
        self.setDropAction(Qt.DropAction.CopyAction)

    def mimeData(self) -> QMimeData:  # type: ignore[override]
        return self._mime


def test_drop_node_kind_adds_matching_node(graph_scene):
    scene, _view = graph_scene
    mime = GraphScene.mime_data_for_node_kind(NodeKind.OPT)
    result = scene.handle_drop(mime, QPointF(120.0, 80.0))
    assert result is not None
    kinds = [n.kind for n in scene.graph().nodes.values()]
    assert NodeKind.OPT in kinds
    assert len(scene.graph().nodes) == 1


def test_drop_position_centres_node_on_cursor(graph_scene):
    scene, _view = graph_scene
    mime = GraphScene.mime_data_for_node_kind(NodeKind.XYZ_FILE)
    scene.handle_drop(mime, QPointF(300.0, 200.0))
    assert len(scene.graph().nodes) == 1
    node = next(iter(scene.graph().nodes.values()))
    assert node.position[0] == 300.0 - NODE_WIDTH / 2.0


def test_drop_without_mime_is_ignored(graph_scene):
    scene, _view = graph_scene
    mime = QMimeData()
    mime.setText("plain text")
    result = scene.handle_drop(mime, QPointF(50.0, 50.0))
    assert result is None
    assert scene.graph().nodes == {}


def test_drop_qgraphicsevent_dispatch_reaches_scene(graph_scene):
    """End-to-end: the full QGraphicsSceneDragDropEvent path is exercised.

    The MIME used here is the one the library panel would attach, so
    this covers the real drag-pipeline without relying on the OS
    drag manager.
    """
    scene, _view = graph_scene
    mime = GraphScene.mime_data_for_node_kind(NodeKind.OPT)
    event = _MimeDropEvent(QGraphicsSceneDragDropEvent.Type.Drop, mime, QPointF(50.0, 50.0))
    scene.dropEvent(event)
    assert len(scene.graph().nodes) == 1


def test_mime_data_for_node_kind_round_trip():
    """Direct test of the MIME encoding helper."""
    mime = GraphScene.mime_data_for_node_kind(NodeKind.CONF_GEN)
    assert mime.hasFormat(NODE_KIND_MIME)
    decoded = bytes(mime.data(NODE_KIND_MIME)).decode("utf-8")
    assert decoded == NodeKind.CONF_GEN.value


def test_library_panel_exposes_buttons_for_every_kind(qtbot):
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitUntil(lambda: panel.isVisible(), timeout=500)
    kinds = set(panel._buttons.keys())
    assert NodeKind.XYZ_FILE in kinds
    assert NodeKind.OPT in kinds
    assert NodeKind.OUTPUT in kinds


def test_library_panel_hides_output_when_present(qtbot):
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitUntil(lambda: panel.isVisible(), timeout=500)
    graph = NodeGraph()
    graph.add_node(default_node(NodeKind.OUTPUT))
    panel.refresh_visibility(graph)
    # The model is the source of truth — ``visible_kinds()`` is the
    # stable query that doesn't depend on Qt's parent layout.
    assert NodeKind.OUTPUT not in panel.shown_kinds()


def test_library_panel_greyed_output_without_calc_nodes(qtbot):
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitUntil(lambda: panel.isVisible(), timeout=500)
    graph = NodeGraph()
    panel.refresh_visibility(graph)
    # Without calc nodes, OUTPUT is still shown but disabled.
    assert NodeKind.OUTPUT in panel.shown_kinds()
    assert panel.is_kind_enabled(NodeKind.OUTPUT) is False