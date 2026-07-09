"""Top-level widget that composes the three node-graph panels.

Layout
------

::

    ┌──────────────────────────────────────────────────┐
    │ Toolbar: Undo Redo Fit Grid Clear Load Save Vali │
    ├────────┬──────────────────────────────┬──────────┤
    │Library │ GraphView (centered)         │ Properties
    │        │                              │          │
    │        │                              │          │
    ├────────┴──────────────────────────────┴──────────┤
    │ Status pill: errors red / warnings amber / OK   │
    └──────────────────────────────────────────────────┘

Public API
----------

* :meth:`graph` — returns the underlying :class:`NodeGraph`.
* :meth:`set_graph` — replace state wholesale (template load).
* :meth:`validate` — re-run :meth:`NodeGraph.validate` and refresh
  the status pill.
* :meth:`apply_language` — retranslate labels.
"""
from __future__ import annotations

import json
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from jobdesk_app.gui.button_feedback import ButtonFeedback, ButtonRole
from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.nodegraph.canvas import GraphScene, GraphView
from jobdesk_app.gui.nodegraph.library import NodeLibraryPanel
from jobdesk_app.gui.nodegraph.model import (
    GraphIssue,
    NodeGraph,
    NodeKind,
)
from jobdesk_app.gui.nodegraph.properties import PropertiesPanel
from jobdesk_app.gui.nodegraph.serialization import (
    SetParamsCommand,
    from_json,
    to_json,
)


_DEFAULT_LIBRARY_WIDTH = 250
_DEFAULT_PROPERTIES_WIDTH = 300


class WorkflowGraphEditor(QMainWindow):
    """A drop-in :class:`QMainWindow` that hosts the node-graph editor."""

    # Emitted whenever the underlying graph changed (add / remove /
    # edit / undo / redo / load template). UI panels that wrap this
    # editor should listen here to refresh their previews.
    graph_changed = Signal()

    def __init__(
        self,
        language: str = "en",
        parent: QWidget | None = None,
    ) -> None:
        # We accept being embedded as a child widget too: if a non-None
        # ``parent`` is passed we still construct a QMainWindow but we
        # expose ``self.root_widget`` (a plain QWidget) for embedding.
        super().__init__(parent)
        self._language = language
        self._scene = GraphScene(self)
        self._view = GraphView(self._scene, self)
        self._library = NodeLibraryPanel(language=language, parent=self)
        self._properties = PropertiesPanel(language=language, parent=self)
        self._status_pill = QLabel(self)
        self._status_pill.setObjectName("nodegraphStatusPill")
        self._status_pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._undo_btn = QPushButton(self)
        self._redo_btn = QPushButton(self)
        self._fit_btn = QPushButton(self)
        self._grid_btn = QPushButton(self)
        self._clear_btn = QPushButton(self)
        self._load_btn = QPushButton(self)
        self._save_btn = QPushButton(self)
        self._validate_btn = QPushButton(self)

        self._undo_feedback = ButtonFeedback(self._undo_btn, ButtonRole.INSTANT_ACTION)
        self._redo_feedback = ButtonFeedback(self._redo_btn, ButtonRole.INSTANT_ACTION)
        self._fit_feedback = ButtonFeedback(self._fit_btn, ButtonRole.INSTANT_ACTION)
        self._grid_feedback = ButtonFeedback(self._grid_btn, ButtonRole.INSTANT_ACTION)
        self._clear_feedback = ButtonFeedback(self._clear_btn, ButtonRole.DANGER_ACTION)
        self._load_feedback = ButtonFeedback(self._load_btn, ButtonRole.SETTINGS_ACTION)
        self._save_feedback = ButtonFeedback(self._save_btn, ButtonRole.SETTINGS_ACTION)
        self._validate_feedback = ButtonFeedback(self._validate_btn, ButtonRole.PRIMARY_ACTION)

        self._build_layout()
        self._wire_signals()
        self.apply_language(language)
        self._refresh_status_pill(self._scene.validate())

    # ── public API ───────────────────────────────────────────────────

    def graph(self) -> NodeGraph:
        return self._scene.graph()

    def set_graph(self, graph: NodeGraph) -> None:
        self._scene.set_graph(graph)
        self._library.refresh_visibility(graph)
        self.graph_changed.emit()

    def validate(self) -> list[GraphIssue]:
        issues = self._scene.validate()
        self._refresh_status_pill(issues)
        return issues

    def apply_language(self, language: str) -> None:
        self._language = language
        self._library.set_language(language)
        self._properties.set_language(language)
        self._undo_btn.setText(tr("Undo", language))
        self._redo_btn.setText(tr("Redo", language))
        self._fit_btn.setText(tr("Fit", language))
        self._grid_btn.setText(tr("Grid", language))
        self._clear_btn.setText(tr("Clear", language))
        self._load_btn.setText(tr("Load\u2026", language))
        self._save_btn.setText(tr("Save\u2026", language))
        self._validate_btn.setText(tr("Validate", language))
        self._library.setWindowTitle(tr("Node library", language))
        self._properties.setWindowTitle(tr("Properties", language))
        self._refresh_status_pill(self._scene.validate())

    def language(self) -> str:
        return self._language

    def scene(self) -> GraphScene:
        return self._scene

    def view(self) -> GraphView:
        return self._view

    def library_panel(self) -> NodeLibraryPanel:
        return self._library

    def properties_panel(self) -> PropertiesPanel:
        return self._properties

    # ── construction helpers ─────────────────────────────────────────

    def _build_layout(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = QToolBar(central)
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        for button in (
            self._undo_btn,
            self._redo_btn,
            self._fit_btn,
            self._grid_btn,
            self._clear_btn,
            self._load_btn,
            self._save_btn,
            self._validate_btn,
        ):
            toolbar.addWidget(button)
        outer.addWidget(toolbar)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._library.setMinimumWidth(_DEFAULT_LIBRARY_WIDTH)
        self._library.setMaximumWidth(_DEFAULT_LIBRARY_WIDTH)
        body.addWidget(self._library)
        body.addWidget(self._view, 1)
        self._properties.setMinimumWidth(_DEFAULT_PROPERTIES_WIDTH)
        self._properties.setMaximumWidth(_DEFAULT_PROPERTIES_WIDTH)
        body.addWidget(self._properties)
        outer.addLayout(body, 1)
        # Status bar with a coloured pill.
        status_bar = QStatusBar(central)
        status_bar.addPermanentWidget(self._status_pill, 1)
        outer.addWidget(status_bar)
        self.setCentralWidget(central)

    def _wire_signals(self) -> None:
        self._undo_btn.clicked.connect(self._on_undo)
        self._redo_btn.clicked.connect(self._on_redo)
        self._fit_btn.clicked.connect(self._on_fit)
        self._grid_btn.setCheckable(True)
        self._grid_btn.setChecked(True)
        self._grid_btn.toggled.connect(self._on_toggle_grid)
        self._clear_btn.clicked.connect(self._on_clear)
        self._load_btn.clicked.connect(self._on_load_template)
        self._save_btn.clicked.connect(self._on_save_template)
        self._validate_btn.clicked.connect(self._on_validate)
        self._scene.topology_changed.connect(self._on_topology_changed)
        self._scene.validation_changed.connect(self._on_validation_changed)
        self._scene.selection_changed.connect(self._on_scene_selection_changed)
        self._properties.node_params_changed.connect(self._on_params_changed)
        undo_stack = self._scene.undo_stack()
        undo_stack.canUndoChanged.connect(self._undo_btn.setEnabled)
        undo_stack.canRedoChanged.connect(self._redo_btn.setEnabled)
        self._undo_btn.setEnabled(undo_stack.canUndo())
        self._redo_btn.setEnabled(undo_stack.canRedo())
        # Library double-click adds at scene centre.
        for kind, button in self._library._buttons.items():
            button.clicked.connect(lambda _checked=False, k=kind: self._add_at_centre(k))
        self._library.refresh_visibility(self._scene.graph())

    # ── toolbar handlers ─────────────────────────────────────────────

    def _on_undo(self) -> None:
        self._scene.undo_stack().undo()
        self._library.refresh_visibility(self._scene.graph())

    def _on_redo(self) -> None:
        self._scene.undo_stack().redo()
        self._library.refresh_visibility(self._scene.graph())

    def _on_fit(self) -> None:
        self._view.fit_to_items()

    def _on_toggle_grid(self, checked: bool) -> None:
        from jobdesk_app.gui.nodegraph.canvas import make_blank_brush, make_grid_brush
        self._scene.setBackgroundBrush(make_grid_brush() if checked else make_blank_brush())

    def _on_clear(self) -> None:
        if not self._scene.graph().nodes:
            return
        confirm = QMessageBox.question(
            self,
            tr("Clear graph", self._language),
            tr("Remove every node from the graph?", self._language),
        )
        if confirm == QMessageBox.StandardButton.Yes:
            self._scene.clear_graph()
            self._library.refresh_visibility(self._scene.graph())

    def _on_save_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("Save workflow template", self._language),
            "",
            tr("Workflow templates (*.json)", self._language),
        )
        if not path:
            return
        data = to_json(self._scene.graph())
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        self._save_feedback.success(tr("Saved", self._language))

    def _on_load_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Load workflow template", self._language),
            "",
            tr("Workflow templates (*.json)", self._language),
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        try:
            graph = from_json(data)
        except ValueError as exc:
            QMessageBox.warning(
                self,
                tr("Load failed", self._language),
                str(exc),
            )
            return
        self.set_graph(graph)
        self._load_feedback.success(tr("Loaded", self._language))

    def _on_validate(self) -> None:
        issues = self.validate()
        n_err = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        self._validate_feedback.success(
            tr("{n} error(s), {m} warning(s)", self._language, n=n_err, m=n_warn)
        )

    def _add_at_centre(self, kind: NodeKind) -> None:
        centre = self._view.mapToScene(self._view.viewport().rect().center())
        from jobdesk_app.gui.nodegraph.nodes import NODE_WIDTH
        self._scene.add_node(kind, (centre.x() - NODE_WIDTH / 2.0, centre.y() - 12.0))

    # ── scene handlers ───────────────────────────────────────────────

    def _on_topology_changed(self) -> None:
        self._library.refresh_visibility(self._scene.graph())
        self.graph_changed.emit()

    def _on_validation_changed(self) -> None:
        self._refresh_status_pill(self._scene.validate())

    def _on_scene_selection_changed(self) -> None:
        selected = self._scene.selected_node()
        if selected is None:
            self._properties.clear()
            return
        node = self._scene.graph().nodes.get(selected.node_id)
        if node is None:
            self._properties.clear()
            return
        self._properties.show_node(node.id, node.kind, dict(node.params))

    def _on_params_changed(self, node_id: str, params: dict) -> None:
        cmd = SetParamsCommand(self._scene.graph(), node_id, params)
        self._scene.undo_stack().push(cmd)
        self.graph_changed.emit()

    # ── status pill ──────────────────────────────────────────────────

    def _refresh_status_pill(self, issues: list[GraphIssue]) -> None:
        n_err = sum(1 for i in issues if i.severity == "error")
        n_warn = sum(1 for i in issues if i.severity == "warning")
        if n_err > 0:
            self._status_pill.setText(
                tr("{n} error(s) \u2014 see properties panel", self._language, n=n_err)
            )
            self._status_pill.setStyleSheet(
                "background-color: #fdecea; color: #c0392b; padding: 4px 12px;"
                " border-radius: 10px; font-weight: 600;"
            )
        elif n_warn > 0:
            self._status_pill.setText(
                tr("{n} warning(s)", self._language, n=n_warn)
            )
            self._status_pill.setStyleSheet(
                "background-color: #fff4e0; color: #a35d00; padding: 4px 12px;"
                " border-radius: 10px; font-weight: 600;"
            )
        else:
            self._status_pill.setText(tr("Workflow OK", self._language))
            self._status_pill.setStyleSheet(
                "background-color: #e7f5ec; color: #1f7a3a; padding: 4px 12px;"
                " border-radius: 10px; font-weight: 600;"
            )


__all__ = ["WorkflowGraphEditor"]