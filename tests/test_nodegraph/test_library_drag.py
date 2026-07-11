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


# ── Phase 10.3: tooltip content (port semantics for fan-out/fan-in) ─────


def test_tooltip_confgen_mentions_fan_out(qtbot):
    """CONF_GEN tooltip advertises the fan-out capability."""
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    tip = panel._buttons[NodeKind.CONF_GEN].toolTip()
    assert "STRUCTURES" in tip
    assert "fan" in tip.lower()


def test_tooltip_calc_kinds_advertise_input_structure(qtbot):
    """PRE_OPT/OPT/SP/FREQ/TS/REFINE tooltips all mention STRUCTURE input."""
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    for kind in (
        NodeKind.PRE_OPT,
        NodeKind.OPT,
        NodeKind.SINGLE_POINT,
        NodeKind.FREQUENCY,
        NodeKind.TS,
        NodeKind.REFINE,
    ):
        tip = panel._buttons[kind].toolTip()
        assert "STRUCTURE" in tip, f"kind={kind} tip={tip!r}"


def test_tooltip_output_mentions_aggregating_upstream(qtbot):
    """OUTPUT tooltip says it aggregates all upstream paths."""
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    tip = panel._buttons[NodeKind.OUTPUT].toolTip()
    assert "upstream" in tip.lower()
    assert "terminator" in tip.lower() or "workflow.yaml" in tip.lower()


def test_tooltips_translate_to_chinese(qtbot):
    """The Chinese tooltip strings exist and contain port names."""
    from jobdesk_app.gui.i18n import tr as _tr

    en_conf = _tr(
        "Generate a conformational ensemble (Output: STRUCTURES, fans out to multiple OPTs / SPs)",
        "en",
    )
    zh_conf = _tr(
        "Generate a conformational ensemble (Output: STRUCTURES, fans out to multiple OPTs / SPs)",
        "zh",
    )
    assert en_conf != zh_conf
    assert "STRUCTURES" in zh_conf
    en_opt = _tr(
        "DFT / ab-initio geometry optimization; Input: STRUCTURE",
        "en",
    )
    zh_opt = _tr(
        "DFT / ab-initio geometry optimization; Input: STRUCTURE",
        "zh",
    )
    assert en_opt != zh_opt
    assert "STRUCTURE" in zh_opt


def test_refresh_visibility_does_not_hide_calc_when_fanout(qtbot):
    """An OUTPUT plus an existing PRE_OPT must not hide the OTHER calc kinds.

    The library panel buttons stay visible/enabled for the user's
    next drop — only ``OUTPUT`` is unique in the graph (one per
    workflow).
    """
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.show()
    qtbot.waitUntil(lambda: panel.isVisible(), timeout=500)
    g = NodeGraph()
    xyz = default_node(NodeKind.XYZ_FILE, position=(0, 0))
    pre = default_node(NodeKind.PRE_OPT, position=(200, 0))
    out = default_node(NodeKind.OUTPUT, position=(600, 0))
    g.add_node(xyz)
    g.add_node(pre)
    g.add_node(out)
    from jobdesk_app.gui.nodegraph.model import Edge
    g.add_edge(Edge(id="e1", src_node=xyz.id, src_port="out",
                    dst_node=pre.id, dst_port="in"))
    g.add_edge(Edge(id="e2", src_node=pre.id, src_port="out",
                    dst_node=out.id, dst_port="in"))
    panel.refresh_visibility(g)
    # PRE_OPT/OPT/SP etc. are not hidden by an existing pre-opt.
    assert NodeKind.PRE_OPT in panel.shown_kinds()
    assert NodeKind.OPT in panel.shown_kinds()
    # Only OUTPUT is hidden because one is already in the graph.
    assert NodeKind.OUTPUT not in panel.shown_kinds()
