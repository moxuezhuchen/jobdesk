"""Left-side palette of draggable node templates.

Each entry is a small :class:`QToolButton` carrying a :class:`NodeKind`.
When the user starts dragging a button we wrap the gesture in a
:class:`QDrag` and stamp the node-kind onto a custom MIME type the
scene knows how to read.

The OUTPUT row is special-cased: it is hidden when an OUTPUT node is
already in the graph (only one is allowed) and greyed out when there
are no calc nodes downstream of XYZ_FILE that would make sense to
terminate at.

Phase 16 (IMP-04): collapsible group headers.

The 10 button rows are split into three labelled groups: ``Inputs``
(XYZ_FILE), ``Calcs`` (everything else except OUTPUT), and
``Sentinels`` (OUTPUT). Each header is a clickable :class:`QToolButton`
that toggles a ``_collapsed_groups`` entry; collapsed headers hide every
button in that group via ``setVisible(False)`` AND register the kinds
in ``_hidden_by_topology`` so the search filter refuses to un-hide
them. State persists through :class:`GuiSettingsStore`.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QMouseEvent
from PySide6.QtWidgets import (
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.nodegraph.model import NodeGraph, NodeKind
from jobdesk_app.services.gui_settings import GuiSettingsStore

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


# Stable identifiers for the three library groups. Exposed so tests
# and persistence can refer to them without dealing with display
# strings.
GROUP_INPUTS = "inputs"
GROUP_CALCS = "calcs"
GROUP_SENTINELS = "sentinels"

GROUPS: tuple[tuple[str, tuple[NodeKind, ...]], ...] = (
    (GROUP_INPUTS, (NodeKind.XYZ_FILE,)),
    (
        GROUP_CALCS,
        (
            NodeKind.CONF_GEN,
            NodeKind.PRE_OPT,
            NodeKind.OPT,
            NodeKind.SINGLE_POINT,
            NodeKind.FREQUENCY,
            NodeKind.TS,
            NodeKind.REFINE,
            NodeKind.ADVANCED,
        ),
    ),
    (GROUP_SENTINELS, (NodeKind.OUTPUT,)),
)

# Default group titles are plain English; the panel translates them at
# display time via tr() the same way node titles are translated.
_GROUP_RAW_TITLES: dict[str, str] = {
    GROUP_INPUTS: "Inputs",
    GROUP_CALCS: "Calcs",
    GROUP_SENTINELS: "Sentinels",
}


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
    NodeKind.CONF_GEN: "Generate a conformational ensemble (Output: STRUCTURES, fans out to multiple OPTs / SPs)",
    NodeKind.PRE_OPT: "Cheap pre-optimization (force field); Input: STRUCTURE",
    NodeKind.OPT: "DFT / ab-initio geometry optimization; Input: STRUCTURE",
    NodeKind.SINGLE_POINT: "Single-point energy; Input: STRUCTURE",
    NodeKind.FREQUENCY: "Vibrational frequency; Input: STRUCTURE",
    NodeKind.TS: "Transition state search; Input: STRUCTURE",
    NodeKind.REFINE: "Refine best conformer with high accuracy; Input: STRUCTURE + ensemble",
    NodeKind.ADVANCED: "Free-form key=value options",
    NodeKind.OUTPUT: "Aggregate all upstream paths into workflow.yaml terminator",
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


def _group_for(kind: NodeKind) -> str:
    """Return the stable group id that ``kind`` belongs to."""
    for gid, members in GROUPS:
        if kind in members:
            return gid
    raise KeyError(f"no library group for kind={kind!r}")


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


class _GroupHeader(QToolButton):
    """A small clickable section header above a row of buttons.

    The button is checkable so it carries its own on/off visual state;
    the owner reads ``isChecked()`` to decide whether the rows below
    it are visible.
    """

    def __init__(self, group_id: str, language: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group_id = group_id
        self._language = language
        self.setCheckable(True)
        self.setChecked(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setArrowType(Qt.ArrowType.DownArrow)
        self._refresh_text()

    def set_language(self, language: str) -> None:
        self._language = language
        self._refresh_text()

    def _refresh_text(self) -> None:
        raw = _GROUP_RAW_TITLES[self._group_id]
        self.setText(tr(raw, self._language))

    def group_id(self) -> str:
        return self._group_id


class NodeLibraryPanel(QWidget):
    """A vertically scrolling palette of drag-source node buttons."""

    request_add_node = Signal(object)  # emits a NodeKind

    def __init__(
        self,
        language: str = "en",
        parent: QWidget | None = None,
        *,
        settings_store: GuiSettingsStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._buttons: dict[NodeKind, _DraggableButton] = {}
        self._group_headers: dict[str, _GroupHeader] = {}
        self._hidden_by_topology: set[NodeKind] = set()
        # IMP-04: collapsed group ids, persisted via GuiSettingsStore.
        self._collapsed_groups: set[str] = set()
        self._settings_store = settings_store

        # Read collapsed state once at construction. We do this before
        # building buttons so the initial visibility matches disk state.
        if settings_store is not None:
            settings = settings_store.load()
            self._collapsed_groups = set(settings.collapsed_library_groups)

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
        for header in self._group_headers.values():
            header.set_language(language)

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
        # forbids or that the user collapsed. The filter step at the
        # end respects this hidden set so a user-typed search doesn't
        # un-hide OUTPUT or buttons inside a collapsed group.
        self._hidden_by_topology.clear()
        for kind, button in self._buttons.items():
            button.setVisible(True)
            button.setEnabled(True)
            if _group_for(kind) in self._collapsed_groups:
                button.setVisible(False)
                self._hidden_by_topology.add(kind)
        if has_output:
            output_btn = self._buttons.get(NodeKind.OUTPUT)
            if output_btn is not None:
                # Only force-hide if the user has expanded the
                # Sentinels group; otherwise the collapsed-group hide
                # above is the visible state.
                if GROUP_SENTINELS not in self._collapsed_groups:
                    output_btn.setVisible(False)
                self._hidden_by_topology.add(NodeKind.OUTPUT)
        else:
            output_btn = self._buttons.get(NodeKind.OUTPUT)
            if output_btn is not None:
                if GROUP_SENTINELS not in self._collapsed_groups:
                    output_btn.setEnabled(has_calc)
                    output_btn.setToolTip(
                        tr("Add at least one calculation node first.", self._language)
                        if not has_calc
                        else _tooltip_text(self._language, NodeKind.OUTPUT)
                    )
        # Group header visibility reflects whether ANY of its members
        # is currently visible to the user.
        for gid, kinds in GROUPS:
            header = self._group_headers.get(gid)
            if header is None:
                continue
            header.blockSignals(True)
            header.setChecked(gid not in self._collapsed_groups)
            header.blockSignals(False)
            # Headers are always shown so the user can re-expand a
            # collapsed group; their checked/arrow state communicates
            # whether the rows below them are currently visible.
            header.setVisible(True)
            header.setArrowType(
                Qt.ArrowType.DownArrow if header.isChecked() else Qt.ArrowType.RightArrow
            )
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

    def collapsed_groups(self) -> tuple[str, ...]:
        """Return the ids of every group currently collapsed."""
        return tuple(sorted(self._collapsed_groups))

    def is_group_collapsed(self, group_id: str) -> bool:
        return group_id in self._collapsed_groups

    def set_group_collapsed(self, group_id: str, collapsed: bool) -> None:
        """Programmatically toggle a group's collapsed state.

        Persists if a ``GuiSettingsStore`` was injected, so the rest of
        the app can call this in response to other UI actions without
        forgetting the choice between sessions.

        Visually flips visibility for every member of ``group_id`` so
        callers — including the public test surface and the onboarding
        card shortcuts — get an immediately-visible UI result without
        having to drive ``refresh_visibility`` themselves.
        """
        if collapsed:
            self._collapsed_groups.add(group_id)
        else:
            self._collapsed_groups.discard(group_id)
        self._update_group_visuals(group_id)
        # When expanding, also re-show the header arrow / checked
        # state synchronously. Header sync covers all groups so
        # subsequent state is internally consistent regardless of
        # which toggle a caller drove.
        for gid, _kinds in GROUPS:
            h = self._group_headers.get(gid)
            if h is None:
                continue
            h.blockSignals(True)
            h.setChecked(gid not in self._collapsed_groups)
            h.setArrowType(
                Qt.ArrowType.DownArrow if h.isChecked() else Qt.ArrowType.RightArrow
            )
            h.blockSignals(False)
        self._persist_collapsed_groups()

    def _update_group_visuals(self, group_id: str) -> None:
        """Hide/show every member of ``group_id`` synchronously.

        This is the local visual update that mirrors
        ``_on_group_header_toggled``'s logic but does not call
        ``refresh_visibility`` (which depends on a graph that the
        caller may not have ready). It does respect the search box so
        expanded-without-search still hides non-matching kinds.
        """
        members = tuple(
            kind for gid, kinds in GROUPS if gid == group_id for kind in kinds
        )
        collapsed = group_id in self._collapsed_groups
        query = self._search_box.text()
        for kind in members:
            btn = self._buttons.get(kind)
            if btn is None:
                continue
            if collapsed:
                btn.setVisible(False)
                self._hidden_by_topology.add(kind)
            else:
                self._hidden_by_topology.discard(kind)
                # Honour the search box: only re-show kinds that
                # match the current query (an empty query = match).
                if _kind_matches_query(kind, query, self._language):
                    btn.setVisible(True)
                else:
                    btn.setVisible(False)

    def _persist_collapsed_groups(self) -> None:
        if self._settings_store is None:
            return
        # Use the same atomic read-modify-write helper as the onboarding
        # card so we don't race against concurrent updates.
        try:
            self._settings_store.update(
                collapsed_library_groups=sorted(self._collapsed_groups)
            )
        except (OSError, ValueError):
            # Best-effort: the in-memory state stays correct even if
            # the disk write fails.
            pass

    # ── construction ─────────────────────────────────────────────────

    def _build_buttons(self) -> None:
        # Remove existing buttons (e.g. after a language switch + rebuild).
        for btn in self._buttons.values():
            self._body_layout.removeWidget(btn)
            btn.deleteLater()
        for header in self._group_headers.values():
            self._body_layout.removeWidget(header)
            header.deleteLater()
        self._buttons.clear()
        self._group_headers.clear()
        # The collapsed-state accounting is "live" — the panel
        # constructor reads ``settings_store`` once into
        # ``_collapsed_groups``, but each button's visibility has to be
        # applied separately. Set initial membership now so the first
        # ``is_kind_shown`` call (typical right after construction)
        # returns the collapsed answer without waiting for
        # ``refresh_visibility`` to be driven by the editor.
        self._hidden_by_topology.clear()
        for gid, kinds in GROUPS:
            if gid in self._collapsed_groups:
                for kind in kinds:
                    self._hidden_by_topology.add(kind)

        insert_at = 0
        for gid, kinds in GROUPS:
            header = _GroupHeader(gid, self._language, self._body)
            header.blockSignals(True)
            header.setChecked(gid not in self._collapsed_groups)
            header.setArrowType(
                Qt.ArrowType.DownArrow if header.isChecked() else Qt.ArrowType.RightArrow
            )
            header.blockSignals(False)
            header.toggled.connect(
                lambda checked, g=gid: self._on_group_header_toggled(g, checked)
            )
            self._body_layout.insertWidget(insert_at, header)
            self._group_headers[gid] = header
            insert_at += 1
            for kind in kinds:
                button = _DraggableButton(kind, self._body)
                button.setText(_display_title(self._language, kind))
                button.setToolTip(_tooltip_text(self._language, kind))
                # Buttons whose group starts collapsed are hidden.
                if kind in self._hidden_by_topology:
                    button.setVisible(False)
                self._body_layout.insertWidget(insert_at, button)
                self._buttons[kind] = button
                insert_at += 1

    def _apply_filter(self, query: str) -> None:
        for kind, button in self._buttons.items():
            # Topology rules + user-collapsed groups trump search: if
            # either says "hide", we hide regardless of what the user
            # typed. The user can re-expand a collapsed group via the
            # header to bring the rows back.
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

    # ── group header handler ─────────────────────────────────────────

    def _on_group_header_toggled(self, group_id: str, checked: bool) -> None:
        """Handle a click on a group's header.

        ``checked`` is the inverse of "collapsed": when the user
        expands a previously collapsed group, ``checked`` is True and
        we drop the group from ``self._collapsed_groups``. The actual
        button visibility update is local: we touch only the buttons
        that belong to this group so the other groups and the OUTPUT
        topology gating are unaffected.
        """
        new_collapsed = not checked
        if new_collapsed:
            self._collapsed_groups.add(group_id)
        else:
            self._collapsed_groups.discard(group_id)
        # Update arrow direction immediately for snappy visual feedback.
        header = self._group_headers.get(group_id)
        if header is not None:
            header.setArrowType(
                Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
            )
        self._persist_collapsed_groups()
        # Flip visibility / hidden-set membership for every member of
        # this group. The existing search filter will reapply.
        members = [
            kind for gid, kinds in GROUPS if gid == group_id
            for kind in kinds
            if kind in self._buttons
        ]
        if new_collapsed:
            for kind in members:
                btn = self._buttons.get(kind)
                if btn is not None:
                    btn.setVisible(False)
                    self._hidden_by_topology.add(kind)
        else:
            for kind in members:
                self._hidden_by_topology.discard(kind)
                # The header click should NOT un-hide OUTPUT if the
                # graph already has one; ``refresh_visibility`` is
                # responsible for that.
                btn = self._buttons.get(kind)
                if btn is not None and btn.isVisibleTo(self._body):
                    # Honour the search box even after re-expanding so
                    # a still-non-matching kind stays hidden.
                    if _kind_matches_query(kind, self._search_box.text(), self._language):
                        btn.setVisible(True)
        # Sync header widgets to reflect the collapsed/expanded state
        # consistently across all groups.
        for gid, _kinds in GROUPS:
            h = self._group_headers.get(gid)
            if h is None:
                continue
            h.blockSignals(True)
            h.setChecked(gid not in self._collapsed_groups)
            h.setArrowType(
                Qt.ArrowType.DownArrow if h.isChecked() else Qt.ArrowType.RightArrow
            )
            h.blockSignals(False)
        # If the user collapsed the Sentinels group, make sure OUTPUT
        # is back on the hidden set even if the graph doesn't have an
        # OUTPUT node yet.
        if GROUP_SENTINELS in self._collapsed_groups:
            out_btn = self._buttons.get(NodeKind.OUTPUT)
            if out_btn is not None:
                self._hidden_by_topology.add(NodeKind.OUTPUT)
                out_btn.setVisible(False)


__all__ = [
    "GROUP_CALCS",
    "GROUP_INPUTS",
    "GROUP_SENTINELS",
    "GROUPS",
    "NodeLibraryPanel",
    "PALETTE_ORDER",
]
