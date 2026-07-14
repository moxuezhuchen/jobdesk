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

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from jobdesk_app.gui.button_feedback import ButtonFeedback, ButtonRole
from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.nodegraph.canvas import GraphScene, GraphView
from jobdesk_app.gui.nodegraph.examples_drawer import ExamplesDrawer, get_example
from jobdesk_app.gui.nodegraph.library import NodeLibraryPanel
from jobdesk_app.gui.nodegraph.model import (
    GraphIssue,
    NodeGraph,
    NodeKind,
)
from jobdesk_app.gui.nodegraph.onboarding_card import OnboardingCard
from jobdesk_app.gui.nodegraph.properties import PropertiesPanel
from jobdesk_app.gui.nodegraph.serialization import (
    SetParamsCommand,
    from_json,
    to_json,
)
from jobdesk_app.services.gui_settings import GuiSettingsStore

_DEFAULT_LIBRARY_WIDTH = 250
_DEFAULT_PROPERTIES_WIDTH = 300


class WorkflowGraphEditor(QWidget):
    """A drop-in :class:`QWidget` that hosts the node-graph editor.

    Phase 11.1 — used to inherit :class:`QMainWindow`, but Qt refuses to
    silently embed a top-level window as a child layout item, so the
    Submit page rendered an empty editor area. Now a plain ``QWidget``
    with a ``QVBoxLayout`` that stacks toolbar / body / status bar, so
    it slots into ``SubmitPage``'s VBoxLayout cleanly.
    """

    # Emitted whenever the underlying graph changed (add / remove /
    # edit / undo / redo / load template). UI panels that wrap this
    # editor should listen here to refresh their previews.
    graph_changed = Signal()
    selected_node_changed = Signal(str)  # empty string means no selected node
    example_template_requested = Signal(str)
    tour_requested = Signal()

    def __init__(
        self,
        language: str = "en",
        parent: QWidget | None = None,
        *,
        settings_store: GuiSettingsStore | None = None,
        show_library: bool = True,
        show_properties: bool = True,
        show_template_actions: bool = True,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._settings_store = settings_store or GuiSettingsStore()
        self._show_library = show_library
        self._show_properties = show_properties
        self._show_template_actions = show_template_actions
        self._gui_settings = self._settings_store.load()
        self._scene = GraphScene(self)
        self._canvas_area = QWidget(self)
        self._canvas_area.setObjectName("nodegraphCanvasArea")
        self._view = GraphView(self._scene, self._canvas_area)
        self._library = NodeLibraryPanel(
            language=language,
            parent=self,
            settings_store=self._settings_store,
        )
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
        self._examples_btn = ExamplesDrawer(language=language, parent=self)
        self._validate_btn = QPushButton(self)

        self._undo_feedback = ButtonFeedback(self._undo_btn, ButtonRole.INSTANT_ACTION)
        self._redo_feedback = ButtonFeedback(self._redo_btn, ButtonRole.INSTANT_ACTION)
        self._fit_feedback = ButtonFeedback(self._fit_btn, ButtonRole.INSTANT_ACTION)
        self._grid_feedback = ButtonFeedback(self._grid_btn, ButtonRole.INSTANT_ACTION)
        self._clear_feedback = ButtonFeedback(self._clear_btn, ButtonRole.DANGER_ACTION)
        self._load_feedback = ButtonFeedback(self._load_btn, ButtonRole.SETTINGS_ACTION)
        self._save_feedback = ButtonFeedback(self._save_btn, ButtonRole.SETTINGS_ACTION)
        self._examples_feedback = ButtonFeedback(self._examples_btn, ButtonRole.SETTINGS_ACTION)
        self._validate_feedback = ButtonFeedback(self._validate_btn, ButtonRole.PRIMARY_ACTION)

        self._onboarding_card: OnboardingCard | None = None
        self._settings_refresh_timer = QTimer(self)
        self._settings_refresh_timer.setInterval(1500)
        self._settings_refresh_timer.timeout.connect(self._reload_gui_settings)

        self._build_layout()
        if not self._show_library:
            self._library.hide()
        if not self._show_properties:
            self._properties.hide()
        self._build_onboarding_overlay()
        self._wire_signals()
        self.apply_language(language)
        self._refresh_status_pill(self._scene.validate())
        self._refresh_onboarding_visibility()
        self._settings_refresh_timer.start()

    # ── public API ───────────────────────────────────────────────────

    def graph(self) -> NodeGraph:
        return self._scene.graph()

    def is_empty(self) -> bool:
        """True when the canvas has no nodes.

        Lets surrounding widgets (Submit page, empty-state card) tell
        the difference between "user has not started" and "graph has
        nodes but is broken". The two states must render differently
        so the user never sees "Graph incomplete" while the page
        already claims "Workflow OK" with zero steps in the canvas.
        """
        return not bool(self._scene.graph().nodes)

    def set_graph(self, graph: NodeGraph) -> None:
        self._scene.set_graph(graph)
        self._library.refresh_visibility(graph)
        self._refresh_onboarding_visibility()
        self.graph_changed.emit()
        # Review-fix: previously, after a wholesale template load
        # (e.g. Quick-start or toolbar Examples), the scene was
        # populated but the view's scroll position stayed wherever it
        # was — which on a freshly-empty canvas is the centre of the
        # 8000×6000 sceneRect, far from the new nodes at (40, 80)…
        # (700, 80). The user would see a blank canvas after
        # clicking Quick-start even though the model contained a
        # valid graph. ``fit_to_items`` re-centres and scales the
        # view so the new nodes land inside the viewport on the same
        # paint cycle, no manual Fit click required.
        if graph.nodes:
            # ``QGraphicsView.fitInView`` requires the items to be
            # laid out; call it once processEvents has had a chance
            # to compute geometry. ``processEvents`` is safe here
            # because this code path is triggered by user actions
            # (button clicks / menu selections), not during initial
            # widget construction where the parent layout isn't
            # realised yet.
            from PySide6.QtCore import QCoreApplication
            QCoreApplication.processEvents()
            self._view.fit_to_items()
            # During page construction the editor may not have received its
            # final geometry yet. Queue one more fit for the first visible
            # event-loop turn so a freshly loaded preset never opens on an
            # apparently empty grid.
            QTimer.singleShot(0, self._view.fit_to_items)

    def open_examples_menu(self) -> None:
        """Pop the toolbar Examples menu without requiring a click.

        Used by MainWindow when the Runs-page empty-state "Show example
        templates" button is pressed: we land on Submit and want the
        drawer to open immediately so the user can pick a template in
        one step instead of clicking the toolbar button a second time.

        Falls back silently if the drawer isn't built yet (e.g. during
        very early construction) so callers don't need to guard.
        """
        drawer = getattr(self, "_examples_btn", None)
        if drawer is None:
            return
        drawer.popup()

    def open_tour(self) -> None:
        """Emit :attr:`tour_requested` from external code.

        Lets MainWindow re-launch the 60-second tour dialog when the
        user clicks "Read 60-second tour" in the empty-state hint,
        instead of forcing them to find the toolbar button.
        """
        self.tour_requested.emit()

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
        self._examples_btn.set_language(language)
        self._library.setWindowTitle(tr("Node library", language))
        self._properties.setWindowTitle(tr("Properties", language))
        if self._onboarding_card is not None:
            self._onboarding_card.apply_language(language)
            self._position_onboarding_card()
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

    def onboarding_card(self) -> OnboardingCard | None:
        return self._onboarding_card

    def add_node(self, kind: NodeKind) -> None:
        """Add a node at the visible canvas centre.

        The workflow page uses this small public API instead of exposing
        the editor's internal node-library widget.
        """
        self._add_at_centre(kind)

    def remove_selected(self) -> None:
        self._scene.remove_selected()

    def fit_to_items(self) -> None:
        self._view.fit_to_items()

    # ── construction helpers ─────────────────────────────────────────

    def _build_layout(self) -> None:
        # The whole editor is a single QVBoxLayout on ``self`` — no
        # central widget / no dock plumbing. Toolbar / body / status
        # bar stack vertically.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar = QToolBar(self)
        toolbar.setMovable(False)
        toolbar.setIconSize(toolbar.iconSize())
        toolbar_buttons = [
            self._undo_btn,
            self._redo_btn,
            self._fit_btn,
            self._grid_btn,
            self._clear_btn,
        ]
        if self._show_template_actions:
            toolbar_buttons.extend([self._load_btn, self._examples_btn, self._save_btn])
        toolbar_buttons.append(self._validate_btn)
        for button in toolbar_buttons:
            toolbar.addWidget(button)
        outer.addWidget(toolbar)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        if self._show_library:
            self._library.setMinimumWidth(_DEFAULT_LIBRARY_WIDTH)
            self._library.setMaximumWidth(_DEFAULT_LIBRARY_WIDTH)
            body.addWidget(self._library)
        canvas_layout = QVBoxLayout(self._canvas_area)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)
        canvas_layout.addWidget(self._view)
        body.addWidget(self._canvas_area, 1)
        if self._show_properties:
            self._properties.setMinimumWidth(_DEFAULT_PROPERTIES_WIDTH)
            self._properties.setMaximumWidth(_DEFAULT_PROPERTIES_WIDTH)
            body.addWidget(self._properties)
        outer.addLayout(body, 1)
        # Status bar with a coloured pill.
        status_bar = QStatusBar(self)
        status_bar.addPermanentWidget(self._status_pill, 1)
        outer.addWidget(status_bar)

    def _build_onboarding_overlay(self) -> None:
        self._onboarding_card = OnboardingCard(self._language, self._canvas_area)
        self._onboarding_card.hide()
        self._onboarding_card.example_template_requested.connect(
            self._on_examples_selected
        )
        self._onboarding_card.tour_requested.connect(lambda: self.tour_requested.emit())
        self._onboarding_card.hide_forever_requested.connect(self._hide_onboarding_forever)
        self._onboarding_card.quick_start_requested.connect(
            lambda: self._on_examples_selected("linear_opt_freq")
        )
        self._canvas_area.installEventFilter(self)

    def _position_onboarding_card(self) -> None:
        if self._onboarding_card is None:
            return
        hint = self._onboarding_card.sizeHint()
        area = self._canvas_area.rect()
        width = min(max(hint.width(), 420), max(area.width() - 48, 420))
        height = hint.height()
        x = max(0, (area.width() - width) // 2)
        y = max(0, (area.height() - height) // 2)
        self._onboarding_card.setGeometry(x, y, width, height)
        self._onboarding_card.raise_()

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self._canvas_area and event.type() == QEvent.Type.Resize:
            self._position_onboarding_card()
        return super().eventFilter(watched, event)

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
        self._examples_btn.selected.connect(self._on_examples_selected)
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

    def _on_examples_selected(self, template_id: str) -> None:
        """Toolbar Examples → load the named built-in template into the editor."""
        try:
            tpl = get_example(template_id)
        except KeyError:
            return
        try:
            graph = tpl.load_graph()
        except (FileNotFoundError, ValueError, OSError):
            return
        self.set_graph(graph)
        self.example_template_requested.emit(template_id)

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
        self._refresh_onboarding_visibility()
        self.graph_changed.emit()

    def _on_validation_changed(self) -> None:
        self._refresh_status_pill(self._scene.validate())

    def _on_scene_selection_changed(self) -> None:
        selected = self._scene.selected_node()
        if selected is None:
            self._properties.clear()
            self.selected_node_changed.emit("")
            return
        node = self._scene.graph().nodes.get(selected.node_id)
        if node is None:
            self._properties.clear()
            self.selected_node_changed.emit("")
            return
        # Surface fan-in info: walk the graph's incoming edges for this
        # node and collect the upstream node titles. The properties
        # panel renders the list in its "Inputs:" header strip.
        graph = self._scene.graph()
        incoming_names = [
            graph.nodes[edge.src_node].title
            for edge in graph.edges.values()
            if edge.dst_node == node.id and edge.src_node in graph.nodes
        ]
        self._properties.show_node_with_inputs(
            node.id, node.kind, dict(node.params), incoming_names
        )
        self.selected_node_changed.emit(node.id)

    def _on_params_changed(self, node_id: str, params: dict) -> None:
        cmd = SetParamsCommand(self._scene.graph(), node_id, params)
        self._scene.undo_stack().push(cmd)
        self.graph_changed.emit()

    # ── onboarding card ──────────────────────────────────────────────

    def _reload_gui_settings(self) -> None:
        settings = self._settings_store.load()
        previous = self._gui_settings.show_onboarding
        self._gui_settings = settings
        if settings.show_onboarding != previous:
            self._refresh_onboarding_visibility()

    def _refresh_onboarding_visibility(self) -> None:
        if self._onboarding_card is None:
            return
        should_show = self._gui_settings.show_onboarding and not self._scene.graph().nodes
        self._onboarding_card.setVisible(should_show)
        if should_show:
            self._position_onboarding_card()
            # Review-fix: while the card is on screen, the search box
            # is the first stop a keyboard-only user lands on, but the
            # default tab order then walks through every library
            # button (~12) + the toolbar (7) before the onboarding
            # buttons become reachable. Wire the search box's Tab key
            # straight to the Quick-start button so the canvas's
            # primary call-to-action is reachable in a single keystroke.
            quick_start = self._onboarding_card._quick_start_btn
            self._library.set_search_box_tab_shortcut(quick_start)
        else:
            # Canvas has nodes / card hidden — restore the natural
            # focus chain so library/toolbar tabs behave normally.
            self._library.set_search_box_tab_shortcut(None)

    def _hide_onboarding_forever(self) -> None:
        self._gui_settings = self._settings_store.update(show_onboarding=False)
        self._refresh_onboarding_visibility()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._settings_refresh_timer.stop()
        if self._onboarding_card is not None:
            self._onboarding_card.hide()
        super().closeEvent(event)

    # ── status pill ──────────────────────────────────────────────────

    def _refresh_status_pill(self, issues: list[GraphIssue]) -> None:
        # Review-fix: an empty canvas is a neutral "Start by adding a
        # node" state — NOT a green "Workflow OK" and NOT a red
        # "graph incomplete" warning. The previous code flipped both
        # the pill green and the preview to "graph incomplete" at the
        # same time, which contradicted each other. Empty canvas now
        # has its own styling so the user sees a coherent "nothing to
        # validate yet" message.
        if self.is_empty():
            self._status_pill.setText(tr("Empty canvas", self._language))
            self._status_pill.setStyleSheet(
                "background-color: #eef1f5; color: #4b5563; padding: 4px 12px;"
                " border-radius: 10px; font-weight: 600;"
            )
            return
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
