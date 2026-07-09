"""Left-side palette of draggable node templates.

Each entry is a small :class:`QToolButton` carrying a :class:`NodeKind`.
When the user starts dragging a button we wrap the gesture in a
:class:`QDrag` and stamp the node-kind onto a custom MIME type the
scene knows how to read.

The OUTPUT row is special-cased: it is hidden when an OUTPUT node is
already in the graph (only one is allowed) and greyed out when there
are no calc nodes downstream of XYZ_FILE that would make sense to
terminate at.
"""
from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.nodegraph.canvas import NODE_KIND_MIME
from jobdesk_app.gui.nodegraph.model import NodeGraph, NodeKind


# Order shown in the panel — chosen so the typical input → calc →
# output flow reads top-to-bottom.
PALETTE_ORDER: tuple[NodeKind, ...] = (
    NodeKind.XYZ_FILE,
    NodeKind.CONF_GEN,
    NodeKind.PRE_OPT,
    NodeKind.OPT,
    NodeKind.SINGLE_POINT,
    NodeKind.FREQUENCY,
    NodeKind.TS,
    NodeKind.REFINE,
    NodeKind.ADVANCED,
    NodeKind.OUTPUT,
)


def _display_title(language: str, kind: NodeKind) -> str:
    return tr(_RAW_TITLE[kind], language)


def _tooltip_text(language: str, kind: NodeKind) -> str:
    return tr(_RAW_TOOLTIP[kind], language)


_RAW_TITLE: dict[NodeKind, str] = {
    NodeKind.XYZ_FILE: "XYZ file",
    NodeKind.CONF_GEN: "Conformer generation",
    NodeKind.PRE_OPT: "Pre-optimization",
    NodeKind.OPT: "Geometry optimization",
    NodeKind.SINGLE_POINT: "Single point",
    NodeKind.FREQUENCY: "Frequency",
    NodeKind.TS: "Transition state",
    NodeKind.REFINE: "Refine",
    NodeKind.ADVANCED: "Advanced options",
    NodeKind.OUTPUT: "Output",
}

_RAW_TOOLTIP: dict[NodeKind, str] = {
    NodeKind.XYZ_FILE: "Input XYZ geometry",
    NodeKind.CONF_GEN: "Generate a conformational ensemble",
    NodeKind.PRE_OPT: "Cheap pre-optimization (force field)",
    NodeKind.OPT: "DFT / ab-initio geometry optimization",
    NodeKind.SINGLE_POINT: "Single-point energy",
    NodeKind.FREQUENCY: "Vibrational frequency",
    NodeKind.TS: "Transition state search",
    NodeKind.REFINE: "Refine best conformer with high accuracy",
    NodeKind.ADVANCED: "Free-form key=value options",
    NodeKind.OUTPUT: "Workflow terminator (emits workflow.yaml)",
}


def _kind_matches_query(kind: NodeKind, query: str, language: str) -> bool:
    if not query:
        return True
    haystack = (
        _display_title(language, kind).lower()
        + " "
        + _RAW_TITLE[kind].lower()
        + " "
        + kind.value.lower()
    )
    return query.lower() in haystack


class _DraggableButton(QToolButton):
    """A :class:`QToolButton` that starts a :class:`QDrag` on mouse-move."""

    def __init__(self, kind: NodeKind, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setIconSize(QSize(16, 16))
        self.setAcceptDrops(False)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    @property
    def kind(self) -> NodeKind:
        return self._kind

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        drag = QDrag(self)
        from jobdesk_app.gui.nodegraph.canvas import GraphScene
        drag.setMimeData(GraphScene.mime_data_for_node_kind(self._kind))
        # Drag pixmap — render a 1× snapshot of the button so the user
        # sees what they're moving.
        pixmap = self.grab()
        scaled = pixmap.scaled(
            QSize(pixmap.width() // 2, pixmap.height() // 2),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        drag.setPixmap(scaled)
        drag.setHotSpot(QPoint(scaled.width() // 2, scaled.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)
        super().mouseMoveEvent(event)


class NodeLibraryPanel(QWidget):
    """A vertically scrolling palette of drag-source node buttons."""

    request_add_node = Signal(object)  # emits a NodeKind

    def __init__(self, language: str = "en", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._language = language
        self._buttons: dict[NodeKind, _DraggableButton] = {}
        self._hidden_by_topology: set[NodeKind] = set()
        self._search_box = QLineEdit(self)
        self._search_box.setPlaceholderText(tr("Search nodes", language))
        self._search_box.setClearButtonEnabled(True)
        self._search_box.textChanged.connect(self._apply_filter)
        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(6, 6, 6, 6)
        self._body_layout.setSpacing(4)
        self._body_layout.addStretch(1)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._body)
        title = QLabel(tr("Node library", language), self)
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(title)
        outer.addWidget(self._search_box)
        outer.addWidget(scroll, 1)
        self._build_buttons()

    # ── public API ───────────────────────────────────────────────────

    def set_language(self, language: str) -> None:
        self._language = language
        self._search_box.setPlaceholderText(tr("Search nodes", language))
        # Rebuild labels / tooltips so they retranslate.
        for kind, button in self._buttons.items():
            button.setText(_display_title(language, kind))
            button.setToolTip(_tooltip_text(language, kind))

    def language(self) -> str:
        return self._language

    def refresh_visibility(self, graph: NodeGraph) -> None:
        """Update OUTPUT visibility + calc-count greying."""
        has_output = any(node.kind is NodeKind.OUTPUT for node in graph.nodes.values())
        has_calc = any(
            node.kind is not NodeKind.XYZ_FILE and node.kind is not NodeKind.OUTPUT
            for node in graph.nodes.values()
        )
        # Re-show every button, then re-hide the ones the topology
        # forbids. The filter step at the end respects this hidden
        # set so a user-typed search doesn't un-hide OUTPUT.
        self._hidden_by_topology.clear()
        for kind, button in self._buttons.items():
            button.setVisible(True)
            button.setEnabled(True)
        if has_output:
            output_btn = self._buttons.get(NodeKind.OUTPUT)
            if output_btn is not None:
                output_btn.setVisible(False)
                self._hidden_by_topology.add(NodeKind.OUTPUT)
        else:
            output_btn = self._buttons.get(NodeKind.OUTPUT)
            if output_btn is not None:
                output_btn.setEnabled(has_calc)
                output_btn.setToolTip(
                    tr("Add at least one calculation node first.", self._language)
                    if not has_calc
                    else _tooltip_text(self._language, NodeKind.OUTPUT)
                )
                if not has_calc:
                    # No calc nodes — OUTPUT is "greyed" (visible but
                    # disabled). Track that as a topology-decided hide
                    # so the search filter keeps it visible but
                    # letting the user click on it would yield nothing.
                    pass
        self._apply_filter(self._search_box.text())

    def visible_kinds(self) -> list[NodeKind]:
        """Return the :class:`NodeKind` values currently shown in the panel.

        This uses the panel's own ``setVisible`` flag rather than the
        Qt widget tree, so it returns a stable answer even when the
        panel is hosted inside a not-yet-shown :class:`QScrollArea`.
        """
        result: list[NodeKind] = []
        for kind, button in self._buttons.items():
            if button.isVisible() and button.isEnabled():
                result.append(kind)
            elif button.isVisible() and not button.isEnabled():
                # Disabled but visible (e.g. OUTPUT pre-calc) — still
                # counted so callers can tell "the user can see it but
                # cannot drop it yet".
                result.append(kind)
        return result

    def is_kind_enabled(self, kind: NodeKind) -> bool:
        button = self._buttons.get(kind)
        return bool(button is not None and button.isEnabled())

    def is_kind_shown(self, kind: NodeKind) -> bool:
        button = self._buttons.get(kind)
        return bool(button is not None and button.isVisible())

    def shown_kinds(self) -> list[NodeKind]:
        """Return the :class:`NodeKind` values currently shown in the panel.

        This is the model-level source of truth: it returns the kinds
        for which the corresponding button ``isVisible()`` is True.
        Qt's parent-layout ``isVisible`` propagation is unreliable in
        offscreen / headless tests; ``QToolButton.isVisible()`` reflects
        the explicit visibility the panel set itself, which is what
        callers care about.
        """
        return [
            kind for kind, button in self._buttons.items()
            if button.isVisible()
        ]  # noqa: E501

    # ── construction ─────────────────────────────────────────────────

    def _build_buttons(self) -> None:
        # Remove existing buttons (e.g. after a language switch + rebuild).
        for btn in self._buttons.values():
            self._body_layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()
        for index, kind in enumerate(PALETTE_ORDER):
            button = _DraggableButton(kind, self._body)
            button.setText(_display_title(self._language, kind))
            button.setToolTip(_tooltip_text(self._language, kind))
            # Insert at the position before the trailing stretch.
            self._body_layout.insertWidget(index, button)
            self._buttons[kind] = button

    def _apply_filter(self, query: str) -> None:
        for kind, button in self._buttons.items():
            # Topology rules trump search: if the graph already has an
            # OUTPUT, don't show the OUTPUT button regardless of
            # what the user typed.
            if kind in self._hidden_by_topology:
                button.setVisible(False)
                continue
            if not _kind_matches_query(kind, query, self._language):
                button.setVisible(False)
                continue
            # Show the button — visibility rules (e.g. disable when
            # there are no calc nodes) are applied by
            # ``refresh_visibility``; the filter only decides if a
            # kind matches the search text.
            button.setVisible(True)


__all__ = [
    "NodeLibraryPanel",
    "PALETTE_ORDER",
]