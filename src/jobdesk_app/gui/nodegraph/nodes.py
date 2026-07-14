"""QGraphicsItem subclasses that render the node graph.

The three item types here are dumb on purpose: they hold geometry, draw
themselves, and emit Qt signals for user gestures. All policy (model
mutation, undo/redo, selection fan-out) lives in
:mod:`jobdesk_app.gui.nodegraph.canvas`.

Visual contract
---------------

* :class:`NodeItem` is a 180 px wide rounded rectangle. Height auto-fits
  the number of ports (one row per port + a title row).
* :class:`PortItem` is a 10 px circle whose colour is derived from
  :class:`PortType`. Inputs go on the left edge, outputs on the right.
* :class:`EdgeItem` is a cubic Bézier between the centres of two
  :class:`PortItem` instances. The two endpoints can move
  independently and the curve updates.

All items use ``ItemIsMovable`` / ``ItemIsSelectable`` /
``ItemSendsGeometryChanges`` selectively — only :class:`NodeItem` is
movable. Ports are clickable but not movable. Edges are
selection-clickable for the Delete key flow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsSceneMouseEvent,
    QStyleOptionGraphicsItem,
    QWidget,
)

from jobdesk_app.gui.nodegraph.model import Node, PortType

# Geometry constants — kept here so the whole scene shares them.
NODE_WIDTH = 180.0
NODE_TITLE_HEIGHT = 24.0
NODE_PORT_ROW = 22.0
NODE_BORDER_RADIUS = 6.0
PORT_RADIUS = 5.0
EDGE_HOVER_PADDING = 6.0

# Status colours applied to the border. ``NONE`` falls back to the
# neutral grey defined by the QSS palette.
STATUS_BORDER_COLOR = {
    "ok": QColor("#3fa856"),
    "warning": QColor("#d99424"),
    "error": QColor("#c0392b"),
    "none": QColor("#6e6e6e"),
}

PORT_FILL_COLOR = {
    PortType.STRUCTURE: QColor("#3a78d8"),
    PortType.STRUCTURES: QColor("#1aa6b7"),
    PortType.ENERGY: QColor("#e08a1f"),
    PortType.CONFIG: QColor("#8a8a8a"),
    PortType.ANY: QColor("#6b7280"),
}


@dataclass(frozen=True)
class PortGeometry:
    """Local (item-relative) centre of a :class:`PortItem` on its node."""

    name: str
    port_type: PortType
    direction: str
    x: float
    y: float

    @property
    def is_input(self) -> bool:
        return self.direction == "in"


class NodeItem(QGraphicsRectItem):
    """A rounded rectangle representing one :class:`Node` in the graph."""

    def __init__(self, node: Node, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self._node = node
        self._ports: dict[str, "PortItem"] = {}
        self._status: str = "none"
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(False)
        self._rebuild_layout()
        # Sit between edges (low z) and selection handles (high z).
        self.setZValue(1.0)

    # ── public API used by GraphScene ────────────────────────────────

    @property
    def node_id(self) -> str:
        return self._node.id

    @property
    def model(self) -> Node:
        return self._node

    def update_model(self, node: Node) -> None:
        """Refresh visual state after the model changed underneath us."""
        self._node = node
        self._rebuild_layout()
        self.update()

    def set_status(self, status: str) -> None:
        """Border colour key — one of ``ok`` / ``warning`` / ``error`` / ``none``."""
        self._status = status
        self.update()

    def port_item(self, port_name: str) -> Optional["PortItem"]:
        return self._ports.get(port_name)

    def port_center(self, port_name: str) -> Optional[QPointF]:
        item = self._ports.get(port_name)
        if item is None:
            return None
        return item.scenePos()

    def itemChange(self, change, value):  # type: ignore[override]
        # Persist the new position to the model so a later serialise()
        # captures it. The owning scene listens for this and pushes a
        # MoveNodeCommand on the undo stack.
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None and hasattr(scene, "node_moved"):
                scene.node_moved(self._node.id, value.x(), value.y())
        return super().itemChange(change, value)

    # ── layout ───────────────────────────────────────────────────────

    def _rebuild_layout(self) -> None:
        n_inputs = len(self._node.inputs)
        n_outputs = len(self._node.outputs)
        height = NODE_TITLE_HEIGHT + max(n_inputs, n_outputs, 1) * NODE_PORT_ROW + 8.0
        self.setRect(QRectF(0.0, 0.0, NODE_WIDTH, height))
        self._ports.clear()
        # Remove previously-attached port items; they were children.
        for child in list(self.childItems()):
            child.setParentItem(None)  # type: ignore[arg-type]  # runtime accepts None
        # Input ports along the left edge.
        for idx, port in enumerate(self._node.inputs):
            y = NODE_TITLE_HEIGHT + idx * NODE_PORT_ROW + NODE_PORT_ROW / 2.0
            port_item = PortItem(port.name, port.type, "in", self)
            port_item.setPos(-PORT_RADIUS, y)
            self._ports[port.name] = port_item
        # Output ports along the right edge.
        for idx, port in enumerate(self._node.outputs):
            y = NODE_TITLE_HEIGHT + idx * NODE_PORT_ROW + NODE_PORT_ROW / 2.0
            port_item = PortItem(port.name, port.type, "out", self)
            port_item.setPos(NODE_WIDTH + PORT_RADIUS, y)
            self._ports[port.name] = port_item

    # ── painting ─────────────────────────────────────────────────────

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        rect = self.rect()
        body = QColor("#ffffff")
        border = STATUS_BORDER_COLOR.get(self._status, STATUS_BORDER_COLOR["none"])
        title_band = QColor("#eef1f5")
        radius = NODE_BORDER_RADIUS
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(QPen(border, 2.0))
        painter.setBrush(body)
        painter.drawPath(path)
        # Title band.
        title_rect = QRectF(rect.left(), rect.top(), rect.width(), NODE_TITLE_HEIGHT)
        title_path = QPainterPath()
        title_path.addRoundedRect(title_rect, radius, radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(title_band)
        painter.drawPath(title_path)
        # The bottom corners of the title band should be flat — cover them
        # with a rectangle so the rounded corners don't show through.
        painter.drawRect(QRectF(rect.left(), rect.top() + NODE_TITLE_HEIGHT - radius,
                                rect.width(), radius))
        # Title text.
        painter.setPen(QPen(QColor("#1f2933"), 1.0))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(title_rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self._node.title)
        # Selection highlight ring.
        if self.isSelected():
            ring_pen = QPen(QColor("#2563eb"), 2.0, Qt.PenStyle.DashLine)
            painter.setPen(ring_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring_rect = rect.adjusted(-3, -3, 3, 3)
            ring_path = QPainterPath()
            ring_path.addRoundedRect(ring_rect, radius + 2, radius + 2)
            painter.drawPath(ring_path)


class PortItem(QGraphicsEllipseItem):
    """A clickable socket on a :class:`NodeItem`."""

    def __init__(
        self,
        port_name: str,
        port_type: PortType,
        direction: str,
        parent: NodeItem,
    ) -> None:
        size = PORT_RADIUS * 2.0
        super().__init__(-PORT_RADIUS, -PORT_RADIUS, size, size, parent)
        self._port_name = port_name
        self._port_type = port_type
        self._direction = direction
        self._dimmed = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(2.0)
        self.setBrush(QBrush(PORT_FILL_COLOR[port_type]))
        self.setPen(QPen(QColor("#1f2933"), 1.2))

    @property
    def port_name(self) -> str:
        return self._port_name

    @property
    def port_type(self) -> PortType:
        return self._port_type

    @property
    def direction(self) -> str:
        return self._direction

    def set_dimmed(self, dimmed: bool) -> None:
        if dimmed == self._dimmed:
            return
        self._dimmed = dimmed
        if dimmed:
            self.setOpacity(0.25)
            self.setZValue(0.0)
        else:
            self.setOpacity(1.0)
            self.setZValue(2.0)
        self.update()

    def is_dimmed(self) -> bool:
        return self._dimmed

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        scene = self.scene()
        if scene is not None and hasattr(scene, "begin_wire_from"):
            scene.begin_wire_from(self)
        event.accept()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # type: ignore[override]
        # Forward mid-drag motion to the scene so it can grow the
        # rubber-band edge.
        scene = self.scene()
        if scene is not None and hasattr(scene, "port_dragged_to"):
            scene.port_dragged_to(self._port_name, self._direction, event.scenePos())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # type: ignore[override]
        scene = self.scene()
        if scene is not None and hasattr(scene, "port_released_at"):
            scene.port_released_at(self._port_name, self._port_type, self._direction, event.scenePos())
        super().mouseReleaseEvent(event)


class EdgeItem(QGraphicsPathItem):
    """A cubic Bézier curve between two :class:`PortItem` instances."""

    def __init__(self, edge_id: str, parent: QGraphicsItem | None = None) -> None:
        super().__init__(parent)
        self._edge_id = edge_id
        self._src_node_id: str | None = None
        self._src_port: str | None = None
        self._dst_node_id: str | None = None
        self._dst_port: str | None = None
        self._src_anchor: QPointF | None = None
        self._dst_anchor: QPointF | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setZValue(0.5)
        pen = QPen(QColor("#52606d"), 1.8)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(Qt.BrushStyle.NoBrush)

    @property
    def edge_id(self) -> str:
        return self._edge_id

    def attach(
        self,
        src_node_id: str,
        src_port: str,
        dst_node_id: str,
        dst_port: str,
        src_anchor: QPointF,
        dst_anchor: QPointF,
    ) -> None:
        self._src_node_id = src_node_id
        self._src_port = src_port
        self._dst_node_id = dst_node_id
        self._dst_port = dst_port
        self._src_anchor = QPointF(src_anchor)
        self._dst_anchor = QPointF(dst_anchor)
        self._rebuild_path()

    def set_src_anchor(self, anchor: QPointF | None) -> None:
        if anchor is None:
            self._src_anchor = None
        else:
            self._src_anchor = QPointF(anchor)
        self._rebuild_path()

    def set_dst_anchor(self, anchor: QPointF | None) -> None:
        if anchor is None:
            self._dst_anchor = None
        else:
            self._dst_anchor = QPointF(anchor)
        self._rebuild_path()

    def endpoints(self) -> tuple[str, str, str, str]:
        return (
            self._src_node_id or "",
            self._src_port or "",
            self._dst_node_id or "",
            self._dst_port or "",
        )

    def shape(self) -> QPainterPath:  # type: ignore[override]
        # Widen the hit area so the curve is easier to click.
        stroker_path = self.path()
        if stroker_path.isEmpty():
            return super().shape()
        stroker = stroker_path
        stroker.setFillRule(Qt.FillRule.WindingFill)
        return stroker

    def _rebuild_path(self) -> None:
        if self._src_anchor is None or self._dst_anchor is None:
            self.setPath(QPainterPath())
            return
        path = QPainterPath(self._src_anchor)
        dx = self._dst_anchor.x() - self._src_anchor.x()
        ctrl_offset = max(60.0, abs(dx) * 0.5)
        c1 = QPointF(self._src_anchor.x() + ctrl_offset, self._src_anchor.y())
        c2 = QPointF(self._dst_anchor.x() - ctrl_offset, self._dst_anchor.y())
        path.cubicTo(c1, c2, self._dst_anchor)
        self.setPath(path)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        pen = self.pen()
        if self.isSelected():
            pen.setColor(QColor("#2563eb"))
            pen.setWidthF(2.4)
        else:
            pen.setColor(QColor("#52606d"))
            pen.setWidthF(1.8)
        painter.setPen(pen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawPath(self.path())


__all__ = [
    "EDGE_HOVER_PADDING",
    "EdgeItem",
    "NodeItem",
    "NODE_WIDTH",
    "PORT_FILL_COLOR",
    "PortItem",
    "STATUS_BORDER_COLOR",
]
