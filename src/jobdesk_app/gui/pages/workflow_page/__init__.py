"""Two-pane workflow authoring page.

The page deliberately has one editable source at a time: the YAML fragment
for the selected step (or the workflow-global YAML tab).  The graph owns
topology, and the full confflow YAML is a read-only generated preview.

This module is organized into the following submodules:

- ``_state``: WorkflowDraft dataclass, node kind mappings, and YAML helpers
- ``_form_builder``: UI widget construction helpers
- ``_preview``: Flow diagram and YAML preview rendering
- ``workflow_page_helpers``: Utility functions for step detail formatting
"""

from __future__ import annotations

from typing import Any, Callable

import yaml
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ....core.workflow_spec import WorkflowSpec
from ....services.method_presets import (
    MethodPresetStore,
    StepPresetStore,
)
from ...design.tokens import Colors, Metrics, Radius, Spacing
from ...i18n import tr
from ...nodegraph.model import Edge, NodeGraph, NodeKind, default_node
from ...nodegraph.spec_bridge import from_workflow_spec
from . import _form_builder, _preview
from ._state import _STEP_KINDS, WorkflowDraft, _dump_yaml, _node_fragment, _step_kind


class WorkflowPage(QWidget):
    """Author workflow topology and individual step YAML side by side."""

    preset_chosen_for_submit = Signal(str, str)
    preset_saved = Signal(str, str)
    workflow_authored = Signal(object, str)

    def __init__(
        self,
        state: Any,
        *,
        language: str = "en",
        preset_store: MethodPresetStore,
        settings_store: Any = None,
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._language = language
        self._store = preset_store
        self._step_store = StepPresetStore()
        self._settings_store = settings_store
        self._on_status = on_status or (lambda _message: None)
        self._on_error = on_error or (lambda _title, _message: None)
        self._draft = WorkflowDraft(self._empty_graph(), self._default_global())
        self._selected_node_id: str | None = None
        self._step_text_dirty = False
        self._loaded_step_preset: tuple[str, str] | None = None
        self._global_text_dirty = False
        self._current_server_label = ""
        self._remote_dir = "/"
        self.setMinimumWidth(1040)

        self.setStyleSheet(
            f"QFrame#workflowHeader {{ background: {Colors.CARD_BG}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }} "
            f"QFrame#workflowSettingsPanel {{ background: {Colors.CARD_BG}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }} "
            f"QPlainTextEdit {{ background: {Colors.BG_SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; padding: 10px; "
            f"font-size: {Metrics.CARD_BODY_FONT_PX}px; }} "
            f"QScrollArea {{ background: {Colors.BG_SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }} "
            f"QTabBar::tab {{ padding: 12px 20px; "
            f"font-size: {Metrics.CARD_BODY_FONT_PX}px; }} "
            f"QComboBox {{ min-height: 40px; padding: 6px 14px; "
            f"font-size: {Metrics.CARD_BODY_FONT_PX}px; }}"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(Spacing.LG, Spacing.LG, Spacing.LG, Spacing.LG)
        outer.setSpacing(Spacing.MD)

        # Build header
        (
            self._header,
            self.preset_combo,
            self.btn_new,
            self.btn_validate,
            self.dirty_label,
        ) = _form_builder.build_header(
            self,
            self._language,
            self._new_workflow,
            self._validate_workflow,
        )
        self.preset_combo.currentIndexChanged.connect(self._on_workflow_preset_changed)
        outer.addWidget(self._header)

        # Build workspace with flow body
        self._flow_body, self._flow_layout = _preview.build_flow_body_and_layout(self)
        (
            self._graph_panel,
            self.add_step_button,
            self.flow_scroll,
            self.save_workflow_button,
        ) = _form_builder.build_graph_panel(
            self,
            self._language,
            self._flow_body,
            self._flow_layout,
            self._add_step,
            self._save_workflow,
        )

        # Build step tab with widgets
        self._build_step_tab_widgets()
        self._build_global_tab_widgets()
        left_panel = self._build_left_panel()

        # Keep the YAML editor and flow diagram in the same horizontal
        # splitter.  The refactor introduced ``build_workspace`` but was
        # accidentally wiring only ``build_graph_panel`` into ``outer``,
        # leaving the left panel detached from the visible layout.
        self._workspace = _form_builder.build_workspace(
            self,
            self._language,
            lambda: left_panel,
            lambda: self._graph_panel,
        )
        outer.addWidget(self._workspace, 1)

        # Build preview
        self._preview_box, self.full_yaml_preview = _form_builder.build_preview_box(self, self._language)
        outer.addWidget(self._preview_box)

        # Build footer
        (
            self._footer,
            self.server_pill,
            self.btn_dispatch,
        ) = _form_builder.build_footer(
            self,
            self._language,
            self._on_use_for_submit,
        )
        outer.addWidget(self._footer)

        self._refresh_workflow_presets()
        self._refresh_step_presets()
        self._load_initial_preset()

    # ---- step tab widget setup -----------------------------------------

    def _build_step_tab_widgets(self) -> None:
        """Create and setup the step tab widgets."""
        (
            self.selected_step_label,
            self.inputs_label,
            self.step_preset_combo,
            self.new_step_button,
            self.apply_step_preset_btn,
            self.step_yaml_editor,
            self.step_error_label,
            self.save_step_preset_btn,
        ) = _form_builder.build_step_tab_widgets(self, self._language)

        # Setup new step menu
        self._new_step_menu = QMenu(self.new_step_button)
        self._new_step_menu.addAction(
            tr("Calculation step (calc)", self._language),
            lambda: self._new_step("calc"),
        )
        self._new_step_menu.addAction(
            tr("Conformer generation step (confgen)", self._language),
            lambda: self._new_step("confgen"),
        )
        self.new_step_button.setMenu(self._new_step_menu)

        # Connect signals
        self.step_preset_combo.currentIndexChanged.connect(self._on_step_preset_selected)
        self.step_yaml_editor.textChanged.connect(self._on_step_text_changed)
        self.apply_step_preset_btn.clicked.connect(self._apply_step_preset)
        self.save_step_preset_btn.clicked.connect(self._save_step_preset)

        # Compatibility alias
        self.yaml_editor = self.step_yaml_editor

    def _build_global_tab_widgets(self) -> None:
        """Create and setup the global tab widgets."""
        self.global_yaml_editor = QPlainTextEdit(self)
        self.global_error_label = QLabel("", self)

        self.global_yaml_editor.textChanged.connect(self._on_global_text_changed)
        self.global_yaml_editor.setObjectName("WorkflowGlobalYamlEditor")

    def _build_left_panel(self) -> QWidget:
        """Build the left settings panel with tabs."""
        left_panel = QFrame(self)
        left_panel.setObjectName("workflowSettingsPanel")
        left_panel.setMinimumWidth(340)
        left_panel.setMaximumWidth(560)
        layout = QVBoxLayout(left_panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)

        from PySide6.QtWidgets import QTabWidget

        self.settings_tabs = QTabWidget(left_panel)

        # Build step tab
        step_tab = _form_builder.build_step_tab(
            left_panel,
            self._language,
            self.step_preset_combo,
            self.new_step_button,
            self.step_yaml_editor,
            self.step_error_label,
            self.selected_step_label,
            self.inputs_label,
            self.apply_step_preset_btn,
            self.save_step_preset_btn,
        )
        self.settings_tabs.addTab(step_tab, tr("Step YAML", self._language))

        # Build global tab
        global_tab = _form_builder.build_global_tab(
            left_panel,
            self._language,
            self.global_yaml_editor,
            self.global_error_label,
            self._apply_global_yaml,
        )
        self.settings_tabs.addTab(global_tab, tr("Global YAML", self._language))

        layout.addWidget(self.settings_tabs, 1)
        return left_panel

    # ---- workflow / graph loading ------------------------------------

    @staticmethod
    def _default_global() -> dict[str, Any]:
        return {"cores_per_task": 8, "total_memory": "16GB", "charge": 0, "multiplicity": 1}

    @staticmethod
    def _empty_graph() -> NodeGraph:
        graph = NodeGraph()
        xyz = default_node(NodeKind.XYZ_FILE, position=(30.0, 120.0))
        output = default_node(NodeKind.OUTPUT, position=(540.0, 120.0))
        graph.add_node(xyz)
        graph.add_node(output)
        return graph

    def _refresh_workflow_presets(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._store.list_presets():
            if preset.source != "user":
                continue
            self.preset_combo.addItem(preset.name, (preset.name, preset.source))
        self.preset_combo.setEnabled(self.preset_combo.count() > 0)
        self.preset_combo.blockSignals(False)

    def _refresh_step_presets(self) -> None:
        current = self.step_preset_combo.currentData()
        self.step_preset_combo.blockSignals(True)
        self.step_preset_combo.clear()
        for preset in self._step_store.list_presets():
            self.step_preset_combo.addItem(preset.name, (preset.name, preset.source))
        if current:
            index = self.step_preset_combo.findData(current)
            self.step_preset_combo.setCurrentIndex(index)
        self.step_preset_combo.blockSignals(False)

    def _load_initial_preset(self) -> None:
        if self.preset_combo.count():
            self._on_workflow_preset_changed(0)
        else:
            self._replace_draft(WorkflowDraft(self._empty_graph(), self._default_global()))

    def _restore_preset_selection(self) -> None:
        current = self._draft.preset
        self.preset_combo.blockSignals(True)
        if current is None:
            self.preset_combo.setCurrentIndex(-1)
        else:
            for index in range(self.preset_combo.count()):
                if self.preset_combo.itemData(index) == (current.name, current.source):
                    self.preset_combo.setCurrentIndex(index)
                    break
        self.preset_combo.blockSignals(False)

    def _on_workflow_preset_changed(self, _index: int) -> None:
        if not self._confirm_discard_step_text():
            self._restore_preset_selection()
            return
        if self._draft.dirty:
            reply = QMessageBox.question(
                self,
                tr("Discard workflow changes?", self._language),
                tr("Current workflow edits will be discarded. Continue?", self._language),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._restore_preset_selection()
                return
        data = self.preset_combo.currentData()
        if not data:
            return
        name, source = data
        preset = next(
            (p for p in self._store.list_presets() if p.name == name and p.source == source),
            None,
        )
        if preset is None:
            return
        raw = getattr(preset.spec, "_raw", {}) or {}
        global_config = dict(raw.get("global") or self._default_global())
        try:
            graph_payload = dict(global_config)
            graph_payload["steps"] = list(raw.get("steps") or [])
            graph = from_workflow_spec(graph_payload)
        except Exception:
            graph = self._empty_graph()
        self._auto_place_steps(graph)
        self._replace_draft(WorkflowDraft(graph, global_config, preset, False))

    @staticmethod
    def _auto_place_steps(graph: NodeGraph) -> None:
        x = 250.0
        for node in graph.topological_order():
            if node.kind in _STEP_KINDS:
                node.position = (x, 120.0)
                x += 230.0

    def _replace_draft(self, draft: WorkflowDraft) -> None:
        self._draft = draft
        self._selected_node_id = None
        self._step_text_dirty = False
        self._global_text_dirty = False
        self._refresh_flow_diagram()
        self._sync_global_editor()
        self._sync_step_editor()
        self._refresh_generated_yaml()
        self._refresh_dirty_label()

    # ---- selected step YAML ------------------------------------------

    def _on_node_selected(self, node_id: str) -> None:
        if node_id == self._selected_node_id:
            return
        if not self._confirm_discard_step_text():
            return
        node = self._draft.graph.nodes.get(node_id)
        self._selected_node_id = node_id if node is not None and node.kind in _STEP_KINDS else None
        self._sync_step_editor()

    def _sync_step_editor(self) -> None:
        node = self._draft.graph.nodes.get(self._selected_node_id or "")
        self.step_yaml_editor.blockSignals(True)
        if node is None:
            self.step_yaml_editor.setReadOnly(False)
            self.selected_step_label.setText(tr("Choose a step to edit.", self._language))
            self.inputs_label.setText("")
            self.step_preset_combo.setEnabled(True)
            self.apply_step_preset_btn.setEnabled(False)
            self.save_step_preset_btn.setEnabled(True)
            self._load_selected_step_into_editor()
        else:
            self.step_yaml_editor.setReadOnly(False)
            self.step_yaml_editor.setPlainText(_dump_yaml(_node_fragment(node)))
            self.selected_step_label.setText(node.title)
            incoming = [
                self._draft.graph.nodes[edge.src_node].title
                for edge in self._draft.graph.incoming_edges(node.id)
                if edge.src_node in self._draft.graph.nodes
                and self._draft.graph.nodes[edge.src_node].kind in _STEP_KINDS
            ]
            self.inputs_label.setText("Inputs: " + (", ".join(incoming) if incoming else "workflow input"))
            self.step_preset_combo.setEnabled(True)
            self.apply_step_preset_btn.setEnabled(True)
            self.save_step_preset_btn.setEnabled(True)
        self.step_yaml_editor.blockSignals(False)
        self.step_error_label.setText("")
        self._step_text_dirty = False

    def _on_step_preset_selected(self, _index: int) -> None:
        if not self._confirm_discard_step_text():
            self._restore_step_preset_selection()
            return
        self._load_selected_step_into_editor()

    def _restore_step_preset_selection(self) -> None:
        self.step_preset_combo.blockSignals(True)
        self.step_preset_combo.setCurrentIndex(
            self.step_preset_combo.findData(self._loaded_step_preset) if self._loaded_step_preset else -1
        )
        self.step_preset_combo.blockSignals(False)

    def _new_step(self, step_type: str = "calc") -> None:
        if not self._confirm_discard_step_text():
            return
        if step_type not in {"calc", "confgen"}:
            raise ValueError(f"Unsupported step type: {step_type}")
        self._selected_node_id = None
        self._loaded_step_preset = None
        self.step_preset_combo.blockSignals(True)
        self.step_preset_combo.setCurrentIndex(-1)
        self.step_preset_combo.blockSignals(False)
        self.step_yaml_editor.blockSignals(True)
        self.step_yaml_editor.setReadOnly(False)
        if step_type == "confgen":
            fragment = {
                "name": "new_confgen",
                "type": "confgen",
                "params": {"chains": ["1-2-3-4"], "angle_step": 120, "bond_multiplier": 1.15},
            }
        else:
            fragment = {
                "name": "new_step",
                "type": "calc",
                "params": {"iprog": "gaussian", "itask": "opt", "keyword": ""},
            }
        self.step_yaml_editor.setPlainText(_dump_yaml(fragment))
        self.step_yaml_editor.blockSignals(False)
        self.selected_step_label.setText(tr("New step", self._language))
        self.inputs_label.setText("")
        self.apply_step_preset_btn.setEnabled(False)
        self.save_step_preset_btn.setEnabled(True)
        self.step_error_label.setText("")
        self._step_text_dirty = True

    def _load_selected_step_into_editor(self) -> None:
        data = self.step_preset_combo.currentData()
        if not data:
            self.step_yaml_editor.blockSignals(True)
            self.step_yaml_editor.setPlainText("")
            self.step_yaml_editor.blockSignals(False)
            return
        try:
            step = self._step_store.load(data[0], source=data[1])
        except Exception as exc:
            self.step_error_label.setText(str(exc))
            return
        fragment = {"name": data[0], "type": step["type"], "params": step["params"]}
        self.step_yaml_editor.blockSignals(True)
        self.step_yaml_editor.setPlainText(_dump_yaml(fragment))
        self.step_yaml_editor.blockSignals(False)
        self.step_error_label.setText("")
        self._step_text_dirty = False
        self._loaded_step_preset = data

    def _on_step_text_changed(self) -> None:
        self._step_text_dirty = True
        try:
            self._parse_step_text(require_unique=False)
            self.step_error_label.setText("")
        except Exception as exc:
            self.step_error_label.setText(str(exc))

    def _parse_step_text(self, *, require_unique: bool = True) -> dict[str, Any]:
        value = yaml.safe_load(self.step_yaml_editor.toPlainText()) or {}
        if not isinstance(value, dict):
            raise ValueError("Step YAML must be a mapping.")
        forbidden = {"inputs", "global", "steps"}.intersection(value)
        if forbidden:
            raise ValueError("Topology is graph-owned; remove: " + ", ".join(sorted(forbidden)))
        name = value.get("name")
        params = value.get("params")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Step name is required.")
        if not isinstance(params, dict):
            raise ValueError("params must be a YAML mapping.")
        fragment = {"name": name.strip(), "type": value.get("type"), "params": dict(params)}
        _step_kind(fragment)
        if require_unique:
            for node_id, node in self._draft.graph.nodes.items():
                if node_id != self._selected_node_id and node.kind in _STEP_KINDS and node.title == fragment["name"]:
                    raise ValueError("Step names must be unique.")
        return fragment

    def _apply_step_yaml(self) -> None:
        if self._selected_node_id is None:
            try:
                self._parse_step_text(require_unique=False)
            except Exception as exc:
                self.step_error_label.setText(str(exc))
                return
            self._step_text_dirty = False
            self.step_error_label.setText("")
            return
        try:
            fragment = self._parse_step_text()
            kind = _step_kind(fragment)
            old = self._draft.graph.nodes[self._selected_node_id]
            replacement = default_node(kind, position=old.position)
            replacement.id = old.id
            replacement.title = fragment["name"]
            replacement.params = dict(fragment["params"])
            self._draft.graph.nodes[old.id] = replacement
            issues = self._draft.graph.validate()
            blocking = [issue.message for issue in issues if issue.severity == "error"]
            if blocking:
                self._draft.graph.nodes[old.id] = old
                raise ValueError("; ".join(blocking))
        except Exception as exc:
            self.step_error_label.setText(str(exc))
            return
        self._step_text_dirty = False
        self._draft.dirty = True
        self._refresh_flow_diagram()
        self._sync_step_editor()
        self._refresh_generated_yaml()
        self._refresh_dirty_label()
        self._on_status(tr("Step YAML applied.", self._language))

    def _commit_pending_step_yaml(self) -> bool:
        if not self._step_text_dirty:
            return True
        self._apply_step_yaml()
        return not self._step_text_dirty

    def _apply_step_preset(self) -> None:
        if self._selected_node_id is None:
            return
        data = self.step_preset_combo.currentData()
        if not data:
            return
        try:
            step = self._step_store.load(data[0], source=data[1])
            node = self._draft.graph.nodes[self._selected_node_id]
            fragment = {"name": node.title, "type": step["type"], "params": step["params"]}
            self.step_yaml_editor.setPlainText(_dump_yaml(fragment))
            self._apply_step_yaml()
        except Exception as exc:
            self.step_error_label.setText(str(exc))

    def _save_step_preset(self) -> None:
        try:
            if self._selected_node_id is None or self._step_text_dirty:
                fragment = self._parse_step_text(require_unique=False)
            else:
                fragment = _node_fragment(self._draft.graph.nodes[self._selected_node_id])
            step = {"type": fragment["type"], "params": fragment["params"]}
            name, ok = QInputDialog.getText(
                self,
                tr("Save step", self._language),
                tr("Name:", self._language),
                text=fragment["name"],
            )
            name = name.strip()
            if not ok or not name:
                return
            self._step_store.save_user(name, step)
        except Exception as exc:
            self.step_error_label.setText(str(exc))
            return
        self._refresh_step_presets()
        self.step_preset_combo.blockSignals(True)
        for index in range(self.step_preset_combo.count()):
            if self.step_preset_combo.itemData(index) == (name, "user"):
                self.step_preset_combo.setCurrentIndex(index)
                break
        self.step_preset_combo.blockSignals(False)
        self._load_selected_step_into_editor()
        self._on_status(tr("Step saved.", self._language))

    # ---- global YAML / graph actions ---------------------------------

    def _sync_global_editor(self) -> None:
        self.global_yaml_editor.blockSignals(True)
        self.global_yaml_editor.setPlainText(_dump_yaml(self._draft.global_config))
        self.global_yaml_editor.blockSignals(False)
        self.global_error_label.setText("")
        self._global_text_dirty = False

    def _on_global_text_changed(self) -> None:
        self._global_text_dirty = True
        try:
            value = yaml.safe_load(self.global_yaml_editor.toPlainText()) or {}
            if not isinstance(value, dict):
                raise ValueError("Global YAML must be a mapping.")
            self.global_error_label.setText("")
        except Exception as exc:
            self.global_error_label.setText(str(exc))

    def _apply_global_yaml(self) -> None:
        try:
            value = yaml.safe_load(self.global_yaml_editor.toPlainText()) or {}
            if not isinstance(value, dict):
                raise ValueError("Global YAML must be a mapping.")
            candidate = self._build_workflow_yaml(global_config=dict(value))
            WorkflowSpec.from_yaml(candidate)
        except Exception as exc:
            self.global_error_label.setText(str(exc))
            return
        self._draft.global_config = dict(value)
        self._global_text_dirty = False
        self._draft.dirty = True
        self._refresh_generated_yaml()
        self._refresh_dirty_label()
        self._on_status(tr("Global YAML applied.", self._language))

    def _add_step(self) -> None:
        try:
            fragment = self._parse_step_text(require_unique=False)
            kind = _step_kind(fragment)
        except Exception as exc:
            self._on_error(tr("Add step", self._language), str(exc))
            return
        node = default_node(kind)
        node.title = self._next_step_name_from(fragment["name"])
        node.params = dict(fragment["params"])
        self._draft.graph.add_node(node)
        try:
            self._rewire_linear_flow()
        except ValueError as exc:
            self._draft.graph.remove_node(node.id)
            self._on_error(tr("Add step", self._language), str(exc))
            return
        self._draft.dirty = True
        self._selected_node_id = node.id
        self._refresh_flow_diagram()
        self._sync_step_editor()
        self._refresh_generated_yaml()
        self._refresh_dirty_label()

    def _delete_step(self, node_id: str) -> None:
        node = self._draft.graph.nodes.get(node_id)
        if node is None or node.kind not in _STEP_KINDS:
            return
        self._draft.graph.remove_node(node.id)
        self._rewire_linear_flow()
        if self._selected_node_id == node_id:
            self._selected_node_id = None
        self._sync_step_editor()
        self._draft.dirty = True
        self._refresh_flow_diagram()
        self._refresh_generated_yaml()
        self._refresh_dirty_label()

    def _ordered_step_nodes(self) -> list[Any]:
        return [node for node in self._draft.graph.topological_order() if node.kind in _STEP_KINDS]

    def _next_step_name(self, kind: NodeKind) -> str:
        return self._next_step_name_from(kind.value)

    def _next_step_name_from(self, base: str) -> str:
        used = {node.title for node in self._draft.graph.nodes.values()}
        if base not in used:
            return base
        suffix = 2
        while f"{base}_{suffix}" in used:
            suffix += 1
        return f"{base}_{suffix}"

    def _rewire_linear_flow(self, ordered: list[Any] | None = None) -> None:
        graph = self._draft.graph
        ordered = ordered or self._ordered_step_nodes()
        previous_edges = list(graph.edges.values())
        for edge in previous_edges:
            graph.remove_edge(edge.id)
        xyz = next((node for node in graph.nodes.values() if node.kind is NodeKind.XYZ_FILE), None)
        output = next((node for node in graph.nodes.values() if node.kind is NodeKind.OUTPUT), None)
        if xyz is None:
            xyz = default_node(NodeKind.XYZ_FILE)
            graph.add_node(xyz)
        if output is None:
            output = default_node(NodeKind.OUTPUT)
            graph.add_node(output)
        try:
            if not ordered:
                return
            previous = xyz
            for index, node in enumerate(ordered):
                node.position = (260.0, 80.0 + index * 116.0)
                graph.add_edge(Edge(Edge.new_id(), previous.id, "out", node.id, "in"))
                previous = node
            graph.add_edge(Edge(Edge.new_id(), previous.id, "out", output.id, "in"))
            errors = [issue.message for issue in graph.validate() if issue.severity == "error"]
            if errors:
                raise ValueError("; ".join(errors))
        except Exception as exc:
            for edge in list(graph.edges.values()):
                graph.remove_edge(edge.id)
            for edge in previous_edges:
                graph.add_edge(edge)
            raise ValueError(str(exc)) from exc

    def _move_step(self, node_id: str, delta: int) -> None:
        ordered = self._ordered_step_nodes()
        index = next((i for i, node in enumerate(ordered) if node.id == node_id), -1)
        target = index + delta
        if index < 0 or not 0 <= target < len(ordered):
            return
        ordered[index], ordered[target] = ordered[target], ordered[index]
        try:
            self._rewire_linear_flow(ordered)
        except ValueError as exc:
            self._on_error(tr("Move step", self._language), str(exc))
            return
        self._draft.dirty = True
        self._refresh_flow_diagram()
        self._refresh_generated_yaml()
        self._refresh_dirty_label()

    # ---- serialisation / actions -------------------------------------

    def _build_workflow_yaml(self, *, global_config: dict[str, Any] | None = None) -> str:
        graph = self._draft.graph
        issues = graph.validate()
        errors = [issue.message for issue in issues if issue.severity == "error"]
        if errors:
            raise ValueError("; ".join(errors))
        step_nodes = [node for node in graph.topological_order() if node.kind in _STEP_KINDS]
        if not step_nodes:
            raise ValueError("Add at least one workflow step.")
        by_id = {node.id: node.title for node in step_nodes}
        steps: list[dict[str, Any]] = []
        for node in step_nodes:
            upstream = [by_id[edge.src_node] for edge in graph.incoming_edges(node.id) if edge.src_node in by_id]
            if len(upstream) > 1:
                raise ValueError(f"Step '{node.title}' has multiple inputs; fan-in is not executable yet.")
            step = _node_fragment(node)
            step["inputs"] = upstream
            steps.append(step)
        return _dump_yaml(
            {
                "global": global_config if global_config is not None else self._draft.global_config,
                "steps": steps,
            }
        )

    def _refresh_flow_diagram(self) -> None:
        _preview.refresh_flow_diagram(
            self._flow_layout,
            self._flow_body,
            self._ordered_step_nodes(),
            self._selected_node_id,
            self._language,
            self._on_node_selected,
            self._move_step,
            self._delete_step,
        )

    def _refresh_generated_yaml(self) -> None:
        _preview.refresh_generated_yaml(
            self.full_yaml_preview,
            self._build_workflow_yaml,
        )

    def _validate_workflow(self) -> None:
        if not self._commit_pending_step_yaml():
            return
        _preview.validate_workflow(
            self._build_workflow_yaml,
            lambda exc: self._on_error(tr("Workflow validation", self._language), exc),
            lambda: self._on_status(tr("Workflow is valid.", self._language)),
        )

    def _new_workflow(self) -> None:
        if not self._confirm_discard_step_text():
            return
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(-1)
        self.preset_combo.blockSignals(False)
        self._replace_draft(WorkflowDraft(self._empty_graph(), self._default_global(), None, True))

    def _save_workflow(self) -> None:
        if not self._commit_pending_step_yaml():
            return
        try:
            yaml_text = self._build_workflow_yaml()
            spec = WorkflowSpec.from_yaml(yaml_text)
        except Exception as exc:
            self._on_error(tr("Save workflow", self._language), str(exc))
            return
        default = (
            self._draft.preset.name if self._draft.preset and self._draft.preset.source == "user" else "new_workflow"
        )
        name, ok = QInputDialog.getText(
            self,
            tr("Save workflow", self._language),
            tr("Name:", self._language),
            text=default,
        )
        name = name.strip()
        if not ok or not name:
            return
        self._store.save_user_yaml(name, yaml_text)
        self._refresh_workflow_presets()
        saved_preset = next(
            (preset for preset in self._store.list_presets() if preset.name == name and preset.source == "user"),
            None,
        )
        if saved_preset is None:
            self._on_error(tr("Save workflow", self._language), "Saved workflow could not be reloaded.")
            return
        self._draft.preset = saved_preset
        for index in range(self.preset_combo.count()):
            data = self.preset_combo.itemData(index)
            if data == (name, "user"):
                self.preset_combo.setCurrentIndex(index)
                break
        self._draft.dirty = False
        self._refresh_dirty_label()
        self.preset_saved.emit(name, "user")
        self.workflow_authored.emit(spec, name)
        self._on_status(tr("Workflow saved.", self._language))

    def _on_use_for_submit(self) -> None:
        if not self._commit_pending_step_yaml():
            return
        if self._draft.dirty or self._draft.preset is None:
            self._on_error(
                tr("Use this workflow for submit", self._language),
                tr("Save the workflow before submitting.", self._language),
            )
            return
        self.preset_chosen_for_submit.emit(self._draft.preset.name, self._draft.preset.source)

    def _refresh_dirty_label(self) -> None:
        self.dirty_label.setText(
            tr("Modified — save workflow before submitting.", self._language) if self._draft.dirty else ""
        )

    def _confirm_discard_step_text(self) -> bool:
        if not self._step_text_dirty:
            return True
        reply = QMessageBox.question(
            self,
            tr("Unsaved step YAML", self._language),
            tr("Apply the current step YAML before switching?", self._language),
            QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Apply,
        )
        if reply == QMessageBox.StandardButton.Apply:
            self._apply_step_yaml()
            return not self._step_text_dirty
        if reply == QMessageBox.StandardButton.Discard:
            self._step_text_dirty = False
            return True
        return False

    # ---- MainWindow contract -----------------------------------------

    def set_server_status(self, connected: bool, server_label: str) -> None:
        self._current_server_label = server_label
        if connected and server_label:
            self.server_pill.set_state("success")
            self.server_pill.setText(server_label)
        else:
            self.server_pill.set_state("neutral")
            self.server_pill.setText(tr("No server", self._language))

    def set_remote_dir(self, remote_dir: str) -> None:
        self._remote_dir = remote_dir

    def apply_language(self, language: str) -> None:
        self._language = language
        self._refresh_flow_diagram()


__all__ = ["WorkflowDraft", "WorkflowPage"]
