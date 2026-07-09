"""Scene + view hosting the node graph visual registry.

The :class:`GraphScene` owns:

* a :class:`NodeGraph` model
* a :class:`QUndoStack` that records every mutation
* a registry that maps model-id → :class:`NodeItem` /
  :class:`EdgeItem`, refreshed on demand from the model

The :class:`GraphView` is a thin :class:`QGraphicsView` subclass that
adds panning with the middle mouse button and Ctrl+wheel zoom.

User gestures
-------------

* Drop a :class:`NodeKind` from the library → :class:`GraphScene`
  receives a ``dragEnter``/``drop`` event and pushes an
  :class:`AddNodeCommand` onto the undo stack.
* Click-and-drag a port → the scene shows a rubber-band
  :class:`EdgeItem`; releasing on a compatible port creates a real
  edge, releasing elsewhere cancels.
* Drag a :class:`NodeItem` → a :class:`MoveNodeCommand` is pushed on
  mouse release (not while dragging, to avoid flooding the stack).
* Delete/Backspace on a selected item → removes the underlying model
  entity with the right undo command.

The scene emits Qt signals so the surrounding panels (properties,
status bar) can react: ``selection_changed``, ``topology_changed``,
``validation_changed``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QKeyEvent,
    QPainter,
    QPen,
    QPixmap,
    QUndoStack,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSceneDragDropEvent,
    QGraphicsView,
)

from jobdesk_app.gui.nodegraph.model import (
    Edge,
    GraphIssue,
    Node,
    NodeGraph,
    NodeKind,
    PortType,
    default_node,
)
from jobdesk_app.gui.nodegraph.nodes import (
    EdgeItem,
    NodeItem,
    PortItem,
)
from jobdesk_app.gui.nodegraph.serialization import (
    AddEdgeCommand,
    AddNodeCommand,
    MoveNodeCommand,
    RemoveEdgeCommand,
    RemoveNodeCommand,
)


NODE_KIND_MIME = "application/x-jobdesk-node-kind"

GRID_SIZE = 20
GRID_COLOR = QColor("#e6e8eb")
GRID_BACKGROUND_COLOR = QColor("#fafbfc")

ZOOM_MIN = 0.25
ZOOM_MAX = 4.0


# ── in-progress wire drag state ────────────────────────────────────────


@dataclass
class _WireDragState:
    src_node_id: str
    src_port: str
    src_port_type: PortType
    src_direction: str  # "in" or "out"
    start_scene_pos: QPointF
    pending_edge: "EdgeItem"


class GraphScene(QGraphicsScene):
    """Owns the model, undo stack, and visual registry."""

    selection_changed = Signal()  # proxy for QGraphicsScene.selectionChanged
    topology_changed = Signal()
    validation_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._graph = NodeGraph()
        self._undo_stack = QUndoStack(self)
        self._node_items: dict[str, NodeItem] = {}
        self._edge_items: dict[str, EdgeItem] = {}
        self._last_known_positions: dict[str, tuple[float, float]] = {}
        self._wire_drag: _WireDragState | None = None
        self._suppress_move_command: bool = False
        self.setSceneRect(QRectF(-2000.0, -2000.0, 8000.0, 6000.0))
        self.setBackgroundBrush(_make_grid_brush())
        self.selectionChanged.connect(self.selection_changed)
        self._undo_stack.indexChanged.connect(self._on_undo_index_changed)
        self._refresh_validation()

    # ── public API ───────────────────────────────────────────────────

    def graph(self) -> NodeGraph:
        return self._graph

    def undo_stack(self) -> QUndoStack:
        return self._undo_stack

    def node_item(self, node_id: str) -> Optional[NodeItem]:
        return self._node_items.get(node_id)

    def edge_item(self, edge_id: str) -> Optional[EdgeItem]:
        return self._edge_items.get(edge_id)

    def selected_node(self) -> Optional[NodeItem]:
        items = self.selectedItems()
        for item in items:
            if isinstance(item, NodeItem):
                return item
        return None

    def set_graph(self, graph: NodeGraph) -> None:
        """Replace the current state wholesale (used for template load)."""
        self._undo_stack.clear()
        self._clear_registry()
        self._graph = graph
        self._last_known_positions = {
            node.id: (float(node.position[0]), float(node.position[1]))
            for node in graph.nodes.values()
        }
        self._rebuild_registry()
        self.topology_changed.emit()
        self._refresh_validation()

    def add_node(self, kind: NodeKind, position: tuple[float, float]) -> NodeItem:
        node = default_node(kind, position=position)
        cmd = AddNodeCommand(self._graph, node)
        self._undo_stack.push(cmd)
        return self._node_items[node.id]

    def add_edge_at(
        self,
        src_node_id: str,
        src_port: str,
        dst_node_id: str,
        dst_port: str,
    ) -> Optional[EdgeItem]:
        src_node = self._graph.nodes.get(src_node_id)
        dst_node = self._graph.nodes.get(dst_node_id)
        if src_node is None or dst_node is None:
            return None
        edge = Edge(
            id=Edge.new_id(),
            src_node=src_node_id,
            src_port=src_port,
            dst_node=dst_node_id,
            dst_port=dst_port,
        )
        cmd = AddEdgeCommand(self._graph, edge)
        self._undo_stack.push(cmd)
        return self._edge_items.get(edge.id)

    def remove_selected(self) -> None:
        # First pass: nodes (and their cascade edges). Second pass:
        # any remaining selected edges that weren't already removed
        # by the cascade.
        for item in list(self.selectedItems()):
            if isinstance(item, NodeItem):
                self._undo_stack.push(RemoveNodeCommand(self._graph, item.node_id))
        for item in list(self.selectedItems()):
            if isinstance(item, EdgeItem):
                self._undo_stack.push(RemoveEdgeCommand(self._graph, item.edge_id))
        self.clearSelection()

    def clear_graph(self) -> None:
        from jobdesk_app.gui.nodegraph.serialization import ClearGraphCommand
        if not self._graph.nodes:
            return
        self._undo_stack.push(ClearGraphCommand(self._graph))

    def validate(self) -> list[GraphIssue]:
        return self._graph.validate()

    # ── drag-from-library support ────────────────────────────────────

    @staticmethod
    def mime_data_for_node_kind(kind: NodeKind) -> QMimeData:
        """Build a :class:`QMimeData` carrying one node-kind MIME value.

        Exposed as a public helper so the library panel and tests
        share the same encoding.
        """
        mime = QMimeData()
        mime.setData(NODE_KIND_MIME, kind.value.encode("utf-8"))
        mime.setText(f"node-graph:{kind.value}")
        return mime

    def set_node_kind_mime(self, mime: QMimeData, kind: NodeKind) -> None:
        mime.setData(NODE_KIND_MIME, kind.value.encode("utf-8"))

    def decode_node_kind(self, mime: QMimeData) -> Optional[NodeKind]:
        if not mime.hasFormat(NODE_KIND_MIME):
            return None
        try:
            value = bytes(mime.data(NODE_KIND_MIME)).decode("utf-8")
            return NodeKind(value)
        except (ValueError, UnicodeDecodeError):
            return None

    def handle_drop(self, mime: QMimeData, scene_pos: QPointF) -> Optional[NodeItem]:
        """Process a drop with the given mime at scene_pos.

        Returns the new :class:`NodeItem` (or ``None`` if the mime
        didn't carry a node-kind MIME). Centralises the drop logic
        so :meth:`dropEvent` and tests can share it.
        """
        kind = self.decode_node_kind(mime)
        if kind is None:
            return None
        from jobdesk_app.gui.nodegraph.nodes import NODE_WIDTH
        centred = (scene_pos.x() - NODE_WIDTH / 2.0, scene_pos.y() - 12.0)
        return self.add_node(kind, centred)

    def dragEnterEvent(self, event: QGraphicsSceneDragDropEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(NODE_KIND_MIME):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event: QGraphicsSceneDragDropEvent) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(NODE_KIND_MIME):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QGraphicsSceneDragDropEvent) -> None:  # type: ignore[override]
        # Centralised in :meth:`handle_drop` so tests can invoke it
        # without synthesising a ``QGraphicsSceneDragDropEvent``.
        if self.handle_drop(event.mimeData(), event.scenePos()) is not None:
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    # ── port click handlers (called by PortItem) ──────────────────────

    def begin_wire_from(self, port: "PortItem") -> None:
        """Begin a wire-drag from a specific :class:`PortItem`."""
        node_item = port.parentItem()
        if not isinstance(node_item, NodeItem):
            return
        anchor = port.scenePos()
        pending = EdgeItem("__pending__")
        pen = QPen(QColor("#52606d"), 1.6)
        pen.setStyle(Qt.PenStyle.DashLine)
        pending.setPen(pen)
        pending.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.addItem(pending)
        self._wire_drag = _WireDragState(
            src_node_id=node_item.node_id,
            src_port=port.port_name,
            src_port_type=port.port_type,
            src_direction=port.direction,
            start_scene_pos=anchor,
            pending_edge=pending,
        )
        pending.set_src_anchor(anchor)
        pending.set_dst_anchor(anchor)
        self._refresh_compat_highlight()

    def port_clicked(self, port_name: str, port_type: PortType, direction: str) -> None:
        """Begin a wire-drag from the clicked port (legacy entrypoint)."""
        port = self._find_port(port_name, direction)
        if port is None:
            return
        self.begin_wire_from(port)

    def _find_port(self, port_name: str, direction: str) -> Optional["PortItem"]:
        for item in self.items():
            if isinstance(item, PortItem) and item.port_name == port_name and item.direction == direction:
                return item
        return None

    def port_dragged_to(self, port_name: str, direction: str, scene_pos: QPointF) -> None:
        if self._wire_drag is None:
            return
        self._wire_drag.pending_edge.set_dst_anchor(scene_pos)

    def port_released_at(
        self,
        port_name: str,
        port_type: PortType,
        direction: str,
        scene_pos: QPointF,
    ) -> None:
        if self._wire_drag is None:
            return
        pending = self._wire_drag.pending_edge
        src_node_id = self._wire_drag.src_node_id
        src_port = self._wire_drag.src_port
        src_port_type = self._wire_drag.src_port_type
        src_direction = self._wire_drag.src_direction
        # Find the port item under the cursor; we accept either a real
        # :class:`PortItem` hit or a manual scene-coordinate match.
        target = self._port_at(scene_pos)
        self._remove_wire_drag()
        if target is None:
            return
        target_node = target.parentItem()
        if not isinstance(target_node, NodeItem):
            return
        if target_node.node_id == src_node_id:
            return
        if not _ports_compatible(src_port_type, target.port_type):
            return
        # Wire direction: if user started from an output, the destination
        # must be an input; if they started from an input, swap.
        if src_direction == "out":
            src_node, src_p = src_node_id, src_port
            dst_node, dst_p = target_node.node_id, target.port_name
        else:
            src_node, src_p = target_node.node_id, target.port_name
            dst_node, dst_p = src_node_id, src_port
        self.add_edge_at(src_node, src_p, dst_node, dst_p)

    def _port_at(self, scene_pos: QPointF) -> Optional[PortItem]:
        for item in self.items(scene_pos):
            if isinstance(item, PortItem):
                return item
        return None

    def _remove_wire_drag(self) -> None:
        if self._wire_drag is None:
            return
        if self._wire_drag.pending_edge.scene() is self:
            self.removeItem(self._wire_drag.pending_edge)
        self._wire_drag = None
        self._refresh_compat_highlight()

    def _refresh_compat_highlight(self) -> None:
        # Reset all ports first.
        for item in self.items():
            if isinstance(item, PortItem):
                item.set_dimmed(False)
        if self._wire_drag is None:
            return
        src_type = self._wire_drag.src_port_type
        for item in self.items():
            if not isinstance(item, PortItem):
                continue
            # Dim anything that can't be a target — which is anything
            # on the source node and any port whose type is incompatible
            # with the source.
            parent_node = item.parentItem()
            same_node = isinstance(parent_node, NodeItem) and parent_node.node_id == self._wire_drag.src_node_id
            if same_node:
                item.set_dimmed(True)
                continue
            if not _ports_compatible(src_type, item.port_type):
                item.set_dimmed(True)
            elif self._wire_drag.src_direction == "out" and item.direction != "in":
                item.set_dimmed(True)
            elif self._wire_drag.src_direction == "in" and item.direction != "out":
                item.set_dimmed(True)

    # ── node movement bridge ─────────────────────────────────────────

    def node_moved(self, node_id: str, x: float, y: float) -> None:
        if self._suppress_move_command:
            return
        previous = self._last_known_positions.get(node_id)
        new_pos = (float(x), float(y))
        # We don't push a command per pixel: we only update the cached
        # position and rely on the undo stack's first redo to capture
        # the origin. The actual MoveNodeCommand is pushed in
        # mouseReleaseEvent of the scene.
        if previous is None or _distance(previous, new_pos) > 0.5:
            self._last_known_positions[node_id] = new_pos
            self._refresh_edges_for_node(node_id)

    def _refresh_edges_for_node(self, node_id: str) -> None:
        node_item = self._node_items.get(node_id)
        if node_item is None:
            return
        for edge in self._graph.edges.values():
            edge_item = self._edge_items.get(edge.id)
            if edge_item is None:
                continue
            if edge.src_node == node_id:
                src_center = node_item.port_center(edge.src_port)
                if src_center is not None:
                    edge_item.set_src_anchor(src_center)
            if edge.dst_node == node_id:
                dst_center = node_item.port_center(edge.dst_port)
                if dst_center is not None:
                    edge_item.set_dst_anchor(dst_center)

    # ── mouse handling for committing moves ──────────────────────────

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        # If a NodeItem was moved, push a MoveNodeCommand on the stack.
        moved_node_ids: list[str] = []
        for item in self.selectedItems():
            if isinstance(item, NodeItem):
                pos = item.pos()
                previous = self._last_known_positions.get(item.node_id, (pos.x(), pos.y()))
                if _distance(previous, (pos.x(), pos.y())) > 0.5:
                    moved_node_ids.append(item.node_id)
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        for nid in moved_node_ids:
            item = self._node_items.get(nid)
            if item is None:
                continue
            current = item.pos()
            new_pos = (current.x(), current.y())
            previous = self._last_known_positions.get(nid, new_pos)
            node = self._graph.nodes.get(nid)
            if node is not None:
                node.position = new_pos
            self._last_known_positions[nid] = new_pos
            if _distance(previous, new_pos) > 0.5:
                cmd = MoveNodeCommand(self._graph, nid, new_pos, old_position=previous)
                self._undo_stack.push(cmd)

    # ── keyboard ─────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    # ── registry rebuild ─────────────────────────────────────────────

    def _clear_registry(self) -> None:
        self.clear()
        self._node_items.clear()
        self._edge_items.clear()

    def _rebuild_registry(self) -> None:
        for node in self._graph.nodes.values():
            self._add_node_item(node)
        for edge in self._graph.edges.values():
            self._add_edge_item(edge)

    def _add_node_item(self, node: Node) -> NodeItem:
        item = NodeItem(node)
        item.setPos(node.position[0], node.position[1])
        self.addItem(item)
        self._node_items[node.id] = item
        self._last_known_positions[node.id] = (float(node.position[0]), float(node.position[1]))
        return item

    def _add_edge_item(self, edge: Edge) -> EdgeItem:
        item = EdgeItem(edge.id)
        self.addItem(item)
        self._edge_items[edge.id] = item
        self._refresh_edge_geometry(item)
        return item

    def _refresh_edge_geometry(self, item: EdgeItem) -> None:
        edge_id = item.edge_id
        edge = self._graph.edges.get(edge_id)
        if edge is None:
            return
        src_item = self._node_items.get(edge.src_node)
        dst_item = self._node_items.get(edge.dst_node)
        if src_item is None or dst_item is None:
            return
        src_center = src_item.port_center(edge.src_port)
        dst_center = dst_item.port_center(edge.dst_port)
        if src_center is None or dst_center is None:
            return
        item.attach(edge.src_node, edge.src_port, edge.dst_node, edge.dst_port,
                    src_center, dst_center)

    # ── undo/redo bridge ─────────────────────────────────────────────

    def _on_undo_index_changed(self, _idx: int) -> None:
        # The model mutated; rebuild the visual registry from scratch.
        self._resync_registry()

    def _resync_registry(self) -> None:
        """Make the visual registry match the current model state."""
        existing_node_ids = set(self._node_items.keys())
        existing_edge_ids = set(self._edge_items.keys())
        live_node_ids = set(self._graph.nodes.keys())
        live_edge_ids = set(self._graph.edges.keys())

        # Remove stale items.
        for stale in existing_node_ids - live_node_ids:
            item = self._node_items.pop(stale)
            self.removeItem(item)
        for stale in existing_edge_ids - live_edge_ids:
            item = self._edge_items.pop(stale)
            self.removeItem(item)

        # Refresh existing nodes (positions/ports may have changed).
        for nid, node in self._graph.nodes.items():
            item = self._node_items.get(nid)
            if item is None:
                self._add_node_item(node)
                continue
            item.update_model(node)
            item.setPos(node.position[0], node.position[1])
            self._last_known_positions[nid] = (float(node.position[0]), float(node.position[1]))

        # Recreate / refresh edges.
        for eid, edge in self._graph.edges.items():
            item = self._edge_items.get(eid)
            if item is None:
                self._add_edge_item(edge)
                continue
            self._refresh_edge_geometry(item)

        self.topology_changed.emit()
        self._refresh_validation()
        self.update()

    # ── status pill ──────────────────────────────────────────────────

    def compute_node_status(self) -> dict[str, str]:
        """Compute a per-node status key from the latest validation."""
        issues = self._graph.validate()
        status: dict[str, str] = {}
        for issue in issues:
            if issue.node_id is None:
                continue
            if issue.severity == "error":
                status[issue.node_id] = "error"
            elif issue.severity == "warning" and status.get(issue.node_id) != "error":
                status[issue.node_id] = "warning"
        for nid in self._graph.nodes:
            status.setdefault(nid, "ok" if nid in {i.node_id for i in issues} or True else "ok")
        for nid in self._graph.nodes:
            status.setdefault(nid, "ok")
        return status

    def apply_status_to_items(self) -> None:
        status = self.compute_node_status()
        for nid, item in self._node_items.items():
            item.set_status(status.get(nid, "ok"))

    def _refresh_validation(self) -> None:
        self.apply_status_to_items()
        self.validation_changed.emit()


# ── view ───────────────────────────────────────────────────────────────


class GraphView(QGraphicsView):
    """A :class:`QGraphicsView` with panning + zoom and no scrollbars."""

    def __init__(self, scene: GraphScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self._panning = False
        self._pan_anchor: QPointF | None = None

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self._zoom(factor)
            event.accept()
            return
        super().wheelEvent(event)

    def _zoom(self, factor: float) -> None:
        current = self.transform().m11()
        target = current * factor
        if target < ZOOM_MIN:
            factor = ZOOM_MIN / current
        elif target > ZOOM_MAX:
            factor = ZOOM_MAX / current
        self.scale(factor, factor)

    def fit_to_items(self) -> None:
        items_rect = self.scene().itemsBoundingRect()
        if items_rect.isEmpty():
            return
        margin = 40.0
        target = items_rect.adjusted(-margin, -margin, margin, margin)
        self.fitInView(target, Qt.AspectRatioMode.KeepAspectRatio)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_anchor = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._panning and self._pan_anchor is not None:
            delta = event.position() - self._pan_anchor
            self._pan_anchor = event.position()
            hbar = self.horizontalScrollBar()
            vbar = self.verticalScrollBar()
            hbar.setValue(hbar.value() - int(delta.x()))
            vbar.setValue(vbar.value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_anchor = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


# ── helpers ────────────────────────────────────────────────────────────


def _ports_compatible(src: PortType, dst: PortType) -> bool:
    if src is dst:
        return True
    if src is PortType.STRUCTURES and dst is PortType.STRUCTURE:
        return True
    return False


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return (dx * dx + dy * dy) ** 0.5


def _make_grid_brush() -> QBrush:
    pixmap = QPixmap(GRID_SIZE, GRID_SIZE)
    pixmap.fill(GRID_BACKGROUND_COLOR)
    painter = QPainter(pixmap)
    painter.setPen(QPen(GRID_COLOR, 1.0))
    painter.drawLine(0, 0, GRID_SIZE, 0)
    painter.drawLine(0, 0, 0, GRID_SIZE)
    painter.end()
    return QBrush(pixmap)


def make_grid_brush() -> QBrush:
    """Public factory for the default 20 px tiled grid brush."""
    return _make_grid_brush()


def make_blank_brush() -> QBrush:
    """Flat background brush (used when the grid is toggled off)."""
    return QBrush(GRID_BACKGROUND_COLOR)


__all__ = [
    "GRID_BACKGROUND_COLOR",
    "GRID_COLOR",
    "GRID_SIZE",
    "GraphScene",
    "GraphView",
    "NODE_KIND_MIME",
    "ZOOM_MAX",
    "ZOOM_MIN",
    "make_blank_brush",
    "make_grid_brush",
    "mime_data_for_node_kind",
]