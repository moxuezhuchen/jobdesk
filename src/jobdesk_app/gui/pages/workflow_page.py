"""Two-pane workflow authoring page.

The page deliberately has one editable source at a time: the YAML fragment
for the selected step (or the workflow-global YAML tab).  The graph owns
topology, and the full confflow YAML is a read-only generated preview.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import yaml
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.workflow_spec import WorkflowSpec
from ...services.method_presets import (
    MethodPreset,
    MethodPresetStore,
    StepPresetStore,
)
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr
from ..nodegraph.model import Edge, Node, NodeGraph, NodeKind, default_node
from ..nodegraph.spec_bridge import from_workflow_spec

_STEP_KINDS = {
    NodeKind.CONF_GEN,
    NodeKind.PRE_OPT,
    NodeKind.OPT,
    NodeKind.SINGLE_POINT,
    NodeKind.FREQUENCY,
    NodeKind.TS,
    NodeKind.REFINE,
}
_ITASK_TO_KIND = {
    "preopt": NodeKind.PRE_OPT,
    "opt": NodeKind.OPT,
    "sp": NodeKind.SINGLE_POINT,
    "freq": NodeKind.FREQUENCY,
    "ts": NodeKind.TS,
    "refine": NodeKind.REFINE,
}
_KIND_TO_ITASK = {value: key for key, value in _ITASK_TO_KIND.items()}
def _dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True, default_flow_style=False)


def _step_kind(fragment: dict[str, Any]) -> NodeKind:
    if fragment.get("type") == "confgen":
        return NodeKind.CONF_GEN
    if fragment.get("type") != "calc":
        raise ValueError("Step type must be 'calc' or 'confgen'.")
    params = fragment.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("Step params must be a mapping.")
    return _ITASK_TO_KIND.get(str(params.get("itask") or "opt").lower(), NodeKind.OPT)


def _node_fragment(node: Node) -> dict[str, Any]:
    if node.kind is NodeKind.CONF_GEN:
        return {"name": node.title, "type": "confgen", "params": dict(node.params)}
    params = dict(node.params)
    params.setdefault("itask", _KIND_TO_ITASK.get(node.kind, "opt"))
    return {"name": node.title, "type": "calc", "params": params}


@dataclass
class WorkflowDraft:
    """The single in-memory source for page controls and YAML output."""

    graph: NodeGraph
    global_config: dict[str, Any]
    preset: MethodPreset | None = None
    dirty: bool = False


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
        # A graph canvas plus a useful YAML editor cannot be operated in the
        # old narrow Submit-page footprint. Let the main shell honour this
        # minimum instead of silently compressing the settings column.
        self.setMinimumWidth(1040)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)
        self.setStyleSheet(
            "QFrame#workflowHeader { background: #ffffff; border: 1px solid #e2e8f0; "
            "border-radius: 10px; } "
            "QFrame#workflowSettingsPanel { background: #f8fafc; border: 1px solid #e2e8f0; "
            "border-radius: 10px; } "
            "QPlainTextEdit { background: #ffffff; border: 1px solid #d9e2ec; border-radius: 7px; "
            "padding: 6px; } "
            "QScrollArea { background: #f8fafc; border: 1px solid #d9e2ec; border-radius: 10px; } "
            "QTabBar::tab { padding: 7px 13px; font-size: 13px; } "
            "QComboBox { min-height: 28px; padding: 1px 7px; }"
        )
        outer.addWidget(self._build_header())
        outer.addWidget(self._build_workspace(), 1)
        outer.addWidget(self._build_preview())
        outer.addWidget(self._build_footer())
        self._refresh_workflow_presets()
        self._refresh_step_presets()
        self._load_initial_preset()

    # ---- construction -------------------------------------------------

    def _build_header(self) -> QWidget:
        panel = QFrame(self)
        panel.setMinimumWidth(560)
        panel.setObjectName("workflowHeader")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title = QLabel(tr("Workflow", self._language), panel)
        font = title.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 3)
        title.setFont(font)
        layout.addWidget(title)
        row = QHBoxLayout()
        self.preset_combo = QComboBox(panel)
        self.preset_combo.setObjectName("WorkflowPresetCombo")
        self.preset_combo.setPlaceholderText(tr("No saved workflows", self._language))
        self.preset_combo.currentIndexChanged.connect(self._on_workflow_preset_changed)
        row.addWidget(self.preset_combo, 1)
        self.btn_new = QPushButton(tr("New", self._language), panel)
        self.btn_new.clicked.connect(self._new_workflow)
        row.addWidget(self.btn_new)
        self.btn_validate = QPushButton(tr("Validate", self._language), panel)
        self.btn_validate.clicked.connect(self._validate_workflow)
        row.addWidget(self.btn_validate)
        layout.addLayout(row)
        self.dirty_label = QLabel("", panel)
        self.dirty_label.setStyleSheet("color: #b45309; font-style: italic;")
        layout.addWidget(self.dirty_label)
        return panel

    def _build_workspace(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        splitter.setObjectName("WorkflowAuthoringSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_graph_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([420, 900])
        return splitter

    def _build_left_panel(self) -> QWidget:
        panel = QFrame(self)
        panel.setObjectName("workflowSettingsPanel")
        panel.setMinimumWidth(340)
        panel.setMaximumWidth(560)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)
        self.settings_tabs = QTabWidget(panel)
        self.settings_tabs.addTab(self._build_step_tab(), tr("Step YAML", self._language))
        self.settings_tabs.addTab(self._build_global_tab(), tr("Global YAML", self._language))
        layout.addWidget(self.settings_tabs, 1)
        return panel

    def _build_step_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 0, 0)
        self.selected_step_label = QLabel(tr("Select a workflow step on the graph.", self._language), tab)
        self.selected_step_label.setWordWrap(True)
        self.selected_step_label.setStyleSheet("font-weight: 600; color: #374151;")
        layout.addWidget(self.selected_step_label)
        self.inputs_label = QLabel("", tab)
        self.inputs_label.setWordWrap(True)
        self.inputs_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(self.inputs_label)
        preset_row = QHBoxLayout()
        self.step_preset_combo = QComboBox(tab)
        self.step_preset_combo.currentIndexChanged.connect(self._on_step_preset_selected)
        preset_row.addWidget(self.step_preset_combo, 1)
        self.new_step_button = QPushButton(tr("New step", self._language), tab)
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
        self.new_step_button.setToolTip(tr("Choose the type for the new step.", self._language))
        preset_row.addWidget(self.new_step_button)
        self.apply_step_preset_btn = QPushButton(tr("Load step", self._language), tab)
        self.apply_step_preset_btn.clicked.connect(self._apply_step_preset)
        preset_row.addWidget(self.apply_step_preset_btn)
        layout.addLayout(preset_row)
        self.step_yaml_editor = QPlainTextEdit(tab)
        self.step_yaml_editor.setObjectName("WorkflowStepYamlEditor")
        self.step_yaml_editor.setPlaceholderText("name: opt\ntype: calc\nparams:\n  iprog: orca\n  itask: opt")
        self.step_yaml_editor.setStyleSheet("font-family: Consolas, Menlo, monospace; font-size: 13px;")
        self.step_yaml_editor.textChanged.connect(self._on_step_text_changed)
        layout.addWidget(self.step_yaml_editor, 1)
        self.yaml_editor = self.step_yaml_editor  # compatibility for integrations that locate this editor
        self.step_error_label = QLabel("", tab)
        self.step_error_label.setWordWrap(True)
        self.step_error_label.setStyleSheet("color: #b91c1c;")
        layout.addWidget(self.step_error_label)
        self.save_step_preset_btn = QPushButton(tr("Save step", self._language), tab)
        self.save_step_preset_btn.setStyleSheet("min-height: 34px; font-size: 13px;")
        self.save_step_preset_btn.clicked.connect(self._save_step_preset)
        layout.addWidget(self.save_step_preset_btn)
        return tab

    def _build_global_tab(self) -> QWidget:
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 4, 0, 0)
        hint = QLabel(tr("Workflow-wide resources and molecular settings.", self._language), tab)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.global_yaml_editor = QPlainTextEdit(tab)
        self.global_yaml_editor.setObjectName("WorkflowGlobalYamlEditor")
        self.global_yaml_editor.setStyleSheet("font-family: Consolas, Menlo, monospace; font-size: 13px;")
        self.global_yaml_editor.textChanged.connect(self._on_global_text_changed)
        layout.addWidget(self.global_yaml_editor, 1)
        self.global_error_label = QLabel("", tab)
        self.global_error_label.setWordWrap(True)
        self.global_error_label.setStyleSheet("color: #b91c1c;")
        layout.addWidget(self.global_error_label)
        button = apply_button_role(QPushButton(tr("Apply global settings", self._language), tab), ButtonRole.PRIMARY_ACTION)
        button.clicked.connect(self._apply_global_yaml)
        layout.addWidget(button)
        return tab

    def _build_graph_panel(self) -> QWidget:
        panel = QFrame(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 0, 0, 0)
        title = QLabel(tr("Workflow flow", self._language), panel)
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #1f2937;")
        layout.addWidget(title)
        toolbar = QHBoxLayout()
        self.add_step_button = QPushButton(tr("Add current step", self._language), panel)
        self.add_step_button.setToolTip(tr("Add the step currently shown on the left.", self._language))
        self.add_step_button.setStyleSheet("font-size: 13px; min-height: 30px; padding: 2px 10px;")
        self.add_step_button.clicked.connect(self._add_step)
        toolbar.addWidget(self.add_step_button)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)
        self.flow_scroll = QScrollArea(panel)
        self.flow_scroll.setWidgetResizable(True)
        self.flow_scroll.setFrameShape(QFrame.Shape.StyledPanel)
        self._flow_body = QWidget(self.flow_scroll)
        self._flow_layout = QVBoxLayout(self._flow_body)
        self._flow_layout.setContentsMargins(18, 14, 18, 14)
        self._flow_layout.setSpacing(4)
        self.flow_scroll.setWidget(self._flow_body)
        layout.addWidget(self.flow_scroll, 1)
        self.save_workflow_button = apply_button_role(
            QPushButton(tr("Save workflow", self._language), panel),
            ButtonRole.PRIMARY_ACTION,
        )
        self.save_workflow_button.setObjectName("SaveWorkflowButton")
        self.save_workflow_button.setMinimumHeight(34)
        self.save_workflow_button.setStyleSheet("font-size: 13px; padding: 3px 12px;")
        self.save_workflow_button.clicked.connect(self._save_workflow)
        layout.addWidget(self.save_workflow_button)
        return panel

    def _build_preview(self) -> QGroupBox:
        box = QGroupBox(tr("Final workflow YAML", self._language), self)
        box.setCheckable(True)
        box.setChecked(False)
        layout = QVBoxLayout(box)
        self.full_yaml_preview = QPlainTextEdit(box)
        self.full_yaml_preview.setObjectName("WorkflowYamlPreview")
        self.full_yaml_preview.setReadOnly(True)
        self.full_yaml_preview.setMaximumBlockCount(2000)
        self.full_yaml_preview.setStyleSheet("font-family: Consolas, Menlo, monospace; font-size: 12px;")
        layout.addWidget(self.full_yaml_preview)
        box.toggled.connect(self.full_yaml_preview.setVisible)
        self.full_yaml_preview.setVisible(False)
        return box

    def _build_footer(self) -> QWidget:
        panel = QFrame(self)
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.server_pill = QLabel(tr("No server", self._language), panel)
        self.server_pill.setStyleSheet("padding: 4px 10px; border: 1px solid #d1d5db; border-radius: 10px;")
        layout.addWidget(self.server_pill)
        layout.addStretch(1)
        self.btn_dispatch = apply_button_role(QPushButton(tr("Use this workflow for submit", self._language), panel), ButtonRole.PRIMARY_ACTION)
        self.btn_dispatch.setObjectName("WorkflowDispatchBtn")
        self.btn_dispatch.clicked.connect(self._on_use_for_submit)
        layout.addWidget(self.btn_dispatch)
        return panel

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
            # Built-ins are reusable *steps*.  The workflow chooser only
            # lists compositions explicitly saved by the user.
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
        """Keep the selector honest when loading a new preset is cancelled."""
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
        preset = next((p for p in self._store.list_presets() if p.name == name and p.source == source), None)
        if preset is None:
            return
        raw = getattr(preset.spec, "_raw", {}) or {}
        global_config = dict(raw.get("global") or self._default_global())
        try:
            # The bridge accepts the global configuration in the legacy flat
            # shape. Passing a literal ``global`` wrapper made it look like
            # an advanced option and produced a spurious orphan node.
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
                if edge.src_node in self._draft.graph.nodes and self._draft.graph.nodes[edge.src_node].kind in _STEP_KINDS
            ]
            self.inputs_label.setText("Inputs: " + (", ".join(incoming) if incoming else "workflow input"))
            self.step_preset_combo.setEnabled(True)
            self.apply_step_preset_btn.setEnabled(True)
            self.save_step_preset_btn.setEnabled(True)
        self.step_yaml_editor.blockSignals(False)
        self.step_error_label.setText("")
        self._step_text_dirty = False

    def _on_step_preset_selected(self, _index: int) -> None:
        """Load the selected reusable step into the independent left editor."""
        if not self._confirm_discard_step_text():
            self._restore_step_preset_selection()
            return
        self._load_selected_step_into_editor()

    def _restore_step_preset_selection(self) -> None:
        """Undo a selector change when its unsaved editor text was kept."""
        self.step_preset_combo.blockSignals(True)
        self.step_preset_combo.setCurrentIndex(
            self.step_preset_combo.findData(self._loaded_step_preset)
            if self._loaded_step_preset else -1
        )
        self.step_preset_combo.blockSignals(False)

    def _new_step(self, step_type: str = "calc") -> None:
        """Start an independent YAML fragment of the chosen step type."""
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
                # A valid, visibly editable starting point.  Users replace
                # the atom chain with the one for their own molecule.
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
            # The left panel is also a standalone step editor.  There is no
            # graph node to mutate in this mode; successful validation is the
            # apply operation and allows the user to switch or save safely.
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
        """Apply unsaved editor text before an action needs the workflow model."""
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
        """Delete the exact step whose card invoked the action."""
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

    def _ordered_step_nodes(self) -> list[Node]:
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

    def _rewire_linear_flow(self, ordered: list[Node] | None = None) -> None:
        """Make the simple diagram's visual order its execution order."""
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
            # Empty is a valid editing state. Saving/submitting still
            # rejects it through _build_workflow_yaml().
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

    def _refresh_flow_diagram(self) -> None:
        while self._flow_layout.count():
            item = self._flow_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        start = QLabel(tr("Input structure", self._language), self._flow_body)
        start.setAlignment(Qt.AlignmentFlag.AlignCenter)
        start.setFixedHeight(42)
        start.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #1e3a5f; "
            "background: #f3f8ff; border: 1px solid #bed8f5; border-radius: 8px;"
        )
        self._flow_layout.addWidget(start)
        ordered = self._ordered_step_nodes()
        if not ordered:
            hint = QLabel(tr("Choose a step on the left, then add it to the workflow.", self._language), self._flow_body)
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setFixedHeight(42)
            hint.setStyleSheet("font-size: 12px; color: #64748b; border: none;")
            self._flow_layout.addWidget(hint)
        for index, node in enumerate(ordered):
            arrow = QLabel("↓", self._flow_body)
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setFixedHeight(24)
            arrow.setStyleSheet("font-size: 16px; font-weight: 600; color: #94a3b8;")
            self._flow_layout.addWidget(arrow)
            card = QFrame(self._flow_body)
            card.setFixedHeight(64)
            selected = node.id == self._selected_node_id
            accent = "#0f766e" if node.kind is NodeKind.CONF_GEN else "#2563eb"
            card.setStyleSheet(
                "QFrame { background: %s; border: 1px solid %s; border-left: 4px solid %s; border-radius: 8px; }"
                % ("#f6faff" if selected else "#ffffff", "#60a5fa" if selected else "#d8dee8", accent)
            )
            row = QHBoxLayout(card)
            row.setContentsMargins(10, 6, 8, 6)
            content = QVBoxLayout()
            content.setSpacing(1)
            select = QPushButton(f"{index + 1}. {node.title}", card)
            select.setFlat(True)
            select.setStyleSheet(
                "QPushButton { text-align: left; color: #172033; font-size: 14px; font-weight: 600; "
                "border: none; padding: 0; } QPushButton:hover { color: #1d4ed8; }"
            )
            select.clicked.connect(lambda _checked=False, node_id=node.id: self._on_node_selected(node_id))
            content.addWidget(select)
            detail = QLabel(self._flow_step_detail(node), card)
            detail.setStyleSheet("color: #64748b; font-size: 11px; border: none;")
            content.addWidget(detail)
            row.addLayout(content, 1)
            up = QPushButton("↑", card)
            up.setEnabled(index > 0)
            up.setFixedSize(38, 30)
            up.setStyleSheet("font-size: 16px; padding: 0;")
            up.setToolTip(tr("Move up", self._language))
            up.clicked.connect(lambda _checked=False, node_id=node.id: self._move_step(node_id, -1))
            row.addWidget(up)
            down = QPushButton("↓", card)
            down.setEnabled(index < len(ordered) - 1)
            down.setFixedSize(38, 30)
            down.setStyleSheet("font-size: 16px; padding: 0;")
            down.setToolTip(tr("Move down", self._language))
            down.clicked.connect(lambda _checked=False, node_id=node.id: self._move_step(node_id, +1))
            row.addWidget(down)
            remove = QPushButton("×", card)
            remove.setFixedSize(34, 30)
            remove.setStyleSheet(
                "QPushButton { color: #b42318; font-size: 18px; padding: 0; border: 1px solid #fecaca; "
                "border-radius: 5px; background: #fffafa; } QPushButton:hover { background: #fef2f2; }"
            )
            remove.clicked.connect(lambda _checked=False, node_id=node.id: self._delete_step(node_id))
            row.addWidget(remove)
            self._flow_layout.addWidget(card)
        if ordered:
            arrow = QLabel("↓", self._flow_body)
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setFixedHeight(24)
            arrow.setStyleSheet("font-size: 16px; font-weight: 600; color: #94a3b8;")
            self._flow_layout.addWidget(arrow)
        output = QLabel(tr("Workflow output", self._language), self._flow_body)
        output.setAlignment(Qt.AlignmentFlag.AlignCenter)
        output.setFixedHeight(42)
        output.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: #276749; "
            "background: #f3fbf5; border: 1px solid #a7d8b0; border-radius: 18px;"
        )
        self._flow_layout.addWidget(output)
        self._flow_layout.addStretch(1)

    @staticmethod
    def _flow_step_detail(node: Node) -> str:
        params = node.params
        if node.kind is NodeKind.CONF_GEN:
            chains = params.get("chains") or []
            chain_text = ", ".join(map(str, chains)) if isinstance(chains, list) else str(chains)
            angle = params.get("angle_step")
            return " · ".join(part for part in (
                f"chains: {chain_text}" if chain_text else "",
                f"angle: {angle}°" if angle is not None else "",
            ) if part) or "confgen"
        return str(params.get("keyword") or " · ".join(
            str(params[key]) for key in ("iprog", "itask") if params.get(key)
        ) or node.kind.value)

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
        return _dump_yaml({"global": global_config if global_config is not None else self._draft.global_config, "steps": steps})

    def _refresh_generated_yaml(self) -> None:
        try:
            text = self._build_workflow_yaml()
            WorkflowSpec.from_yaml(text)
            self.full_yaml_preview.setPlainText(text)
        except Exception as exc:
            self.full_yaml_preview.setPlainText(f"# Cannot generate workflow YAML\n# {exc}")

    def _validate_workflow(self) -> None:
        if not self._commit_pending_step_yaml():
            return
        try:
            WorkflowSpec.from_yaml(self._build_workflow_yaml())
        except Exception as exc:
            self._on_error(tr("Workflow validation", self._language), str(exc))
            return
        self._on_status(tr("Workflow is valid.", self._language))

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
        default = self._draft.preset.name if self._draft.preset and self._draft.preset.source == "user" else "new_workflow"
        name, ok = QInputDialog.getText(self, tr("Save workflow", self._language), tr("Name:", self._language), text=default)
        name = name.strip()
        if not ok or not name:
            return
        self._store.save_user_yaml(name, yaml_text)
        self._refresh_workflow_presets()
        saved_preset = next(
            (
                preset for preset in self._store.list_presets()
                if preset.name == name and preset.source == "user"
            ),
            None,
        )
        if saved_preset is None:
            self._on_error(tr("Save workflow", self._language), "Saved workflow could not be reloaded.")
            return
        # ``_refresh_workflow_presets`` blocks selection signals; assigning
        # this directly is required when the new entry already occupies index
        # zero, because setCurrentIndex(0) then emits no change signal.
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
            self._on_error(tr("Use this workflow for submit", self._language), tr("Save the workflow before submitting.", self._language))
            return
        self.preset_chosen_for_submit.emit(self._draft.preset.name, self._draft.preset.source)

    def _refresh_dirty_label(self) -> None:
        self.dirty_label.setText(tr("Modified — save workflow before submitting.", self._language) if self._draft.dirty else "")

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
        self.server_pill.setText(server_label if connected and server_label else tr("No server", self._language))

    def set_remote_dir(self, remote_dir: str) -> None:
        self._remote_dir = remote_dir

    def apply_language(self, language: str) -> None:
        self._language = language
        self._refresh_flow_diagram()


__all__ = ["WorkflowDraft", "WorkflowPage"]
