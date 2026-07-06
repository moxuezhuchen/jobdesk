"""Workflow Builder page — Stage 4 YAML wizard.

Layout:

* **Top bar** — load / save / check / preset / language controls
* **Splitter (left)** — step list with add / remove / up / down / enable toggle
* **Splitter (centre)** — dynamically rendered form for the selected step
* **Splitter (right)** — global settings groupbox (collapsible) and YAML live
  preview
* **Bottom bar** — Submit-to-agent action (delegated to FileTransferPage's
  agent pipeline), Save-to-disk, Run validation

The form renderer is **purely data-driven**: it walks
:mod:`jobdesk_app.workflow.schema` and instantiates widgets based on
:class:`FieldSpec.kind`. Adding a new field means editing ``schema.py`` only —
no GUI code changes required.

Layer validation, per the plan:

1. **Form layer** — :func:`jobdesk_app.workflow.builder.validate_state`
2. **Local Pydantic-equivalent** — :func:`validate_runtime` calls
   :class:`WorkflowConfig.from_mapping`
3. **Agent dry-run** — performed by the daemon after submit (out of scope
   here); the GUI surfaces layer-1 and layer-2 errors inline.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...services.agent_bridge import AgentBridge
from ...workflow.builder import (
    BuilderError,
    FormState,
    StepState,
    ValidationError,
    default_form_state,
    form_state_to_yaml,
    validate_runtime,
    validate_state,
    yaml_to_form_state,
)
from ...workflow.config.models import ConfigurationError
from ...workflow.schema import (
    CALC_FIELDS,
    CONFGEN_FIELDS,
    GLOBAL_FIELDS,
    FieldSpec,
    STEP_KINDS,
    StepKindSpec,
    default_global_state,
    default_step_state,
    get_step_fields,
)
from ..i18n import schema_hints, tr


# ---------------------------------------------------------------------------
# Field widgets
# ---------------------------------------------------------------------------


@dataclass
class _FieldBinding:
    spec: FieldSpec
    widget: QWidget
    getter: Callable[[], Any]


def _make_field_widget(spec: FieldSpec, language: str) -> _FieldBinding:
    label_text = tr(spec.label_key, language)
    help_text = tr(spec.help_key, language) if spec.help_key else ""

    if spec.kind == "bool":
        widget = QCheckBox(label_text)
        widget.setChecked(bool(spec.default))
        if help_text:
            widget.setToolTip(help_text)
        return _FieldBinding(spec=spec, widget=widget, getter=lambda w=widget: w.isChecked())

    if spec.kind == "choice":
        widget = QComboBox()
        widget.addItems([str(c) for c in spec.choices])
        if spec.default is not None:
            text = str(spec.default)
            idx = widget.findText(text)
            if idx >= 0:
                widget.setCurrentIndex(idx)
        return _FieldBinding(spec=spec, widget=widget, getter=lambda w=widget: w.currentText())

    if spec.kind == "int":
        widget = QSpinBox()
        widget.setRange(int(spec.min_value) if spec.min_value is not None else -2_147_483_648,
                        int(spec.max_value) if spec.max_value is not None else 2_147_483_647)
        widget.setValue(int(spec.default) if spec.default is not None else 0)
        widget.setSuffix("")
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label_text + (f"  ({help_text})" if help_text else "")))
        layout.addStretch()
        layout.addWidget(widget)
        return _FieldBinding(spec=spec, widget=wrapper, getter=lambda w=widget: w.value())

    if spec.kind == "float":
        widget = QDoubleSpinBox()
        widget.setDecimals(4)
        widget.setRange(float(spec.min_value) if spec.min_value is not None else -1e9,
                        float(spec.max_value) if spec.max_value is not None else 1e9)
        try:
            widget.setValue(float(spec.default) if spec.default is not None else 0.0)
        except (TypeError, ValueError):
            widget.setValue(0.0)
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(label_text + (f"  ({help_text})" if help_text else "")))
        layout.addStretch()
        layout.addWidget(widget)
        return _FieldBinding(spec=spec, widget=wrapper, getter=lambda w=widget: w.value())

    # str / list_str / list_int / list_pair / str_or_dict all use QLineEdit
    default_value = spec.default
    if default_value is None:
        initial = ""
    elif isinstance(default_value, (list, tuple)):
        # Render non-empty lists as comma-separated; empty containers as blank.
        initial = ", ".join(str(x) for x in default_value) if default_value else ""
    else:
        initial = str(default_value)
    widget = QLineEdit(initial)
    if spec.placeholder:
        widget.setPlaceholderText(spec.placeholder)
    if help_text:
        widget.setToolTip(help_text)
    wrapper = QWidget()
    layout = QFormLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addRow(label_text, widget)
    return _FieldBinding(spec=spec, widget=wrapper, getter=lambda w=widget: w.text())


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


class WorkflowBuilderPage(QWidget):
    """Stage 4 wizard — emit :attr:`workflow_built` when YAML is ready to upload."""

    workflow_built = Signal(str, dict)  # yaml_text, {"name": str, "steps": list}

    def __init__(self, state, log, status_cb, error_cb, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = state
        self._log = log
        self._status_cb = status_cb
        self._error_cb = error_cb
        self._language = "en"
        self._current_step_index: int = -1
        self._global_bindings: list[_FieldBinding] = []
        self._step_bindings: list[_FieldBinding] = []

        # -- underlying state ------------------------------------------------
        self._form_state: FormState = default_form_state()

        self._build_ui()
        self._refresh_step_list()
        self._render_global_form()
        self._refresh_yaml_preview()

    # -- lifecycle ----------------------------------------------------------

    def apply_language(self, language: str) -> None:
        self._language = language
        self._refresh_step_list()
        self._render_global_form()
        self._render_step_form()
        self._retranslate_static()

    def on_activated(self) -> None:
        self._refresh_yaml_preview()

    # -- UI scaffolding -----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        # Top action bar
        top = QHBoxLayout()
        self.btn_load = QPushButton()
        self.btn_save = QPushButton()
        self.btn_validate = QPushButton()
        self.btn_preset = QPushButton()
        self.btn_submit = QPushButton()
        self.btn_submit.setObjectName("primary")
        for btn in (self.btn_load, self.btn_save, self.btn_validate, self.btn_preset, self.btn_submit):
            btn.setMinimumHeight(36)
            top.addWidget(btn)
        top.addStretch()
        root.addLayout(top)

        # Splitter: list / form / preview+global
        splitter = QSplitter(Qt.Horizontal)

        # ----- left: step list -----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_steps = QLabel()
        self.lbl_steps.setObjectName("sectionTitle")
        left_layout.addWidget(self.lbl_steps)

        self.step_list = QListWidget()
        self.step_list.currentRowChanged.connect(self._on_step_selected)
        left_layout.addWidget(self.step_list, 1)

        step_btns = QHBoxLayout()
        self.btn_add_step = QToolButton()
        self.btn_add_step.setPopupMode(QToolButton.InstantPopup)
        self.btn_remove_step = QPushButton()
        self.btn_step_up = QPushButton()
        self.btn_step_down = QPushButton()
        for btn in (self.btn_add_step, self.btn_remove_step, self.btn_step_up, self.btn_step_down):
            btn.setMinimumHeight(32)
            step_btns.addWidget(btn)
        left_layout.addLayout(step_btns)

        # Build the "add step" sub-menu dynamically
        from PySide6.QtWidgets import QMenu
        self._add_step_menu = QMenu(self.btn_add_step)
        self.btn_add_step.setMenu(self._add_step_menu)

        splitter.addWidget(left)

        # ----- center: form (scrollable) -----
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_form_title = QLabel()
        self.lbl_form_title.setObjectName("sectionTitle")
        center_layout.addWidget(self.lbl_form_title)

        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_container = QWidget()
        self.form_layout = QVBoxLayout(self.form_container)
        self.form_layout.setContentsMargins(8, 8, 8, 8)
        self.form_layout.addStretch()
        self.form_scroll.setWidget(self.form_container)
        center_layout.addWidget(self.form_scroll, 1)
        splitter.addWidget(center)

        # ----- right: global groupbox + YAML preview -----
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.global_group = QGroupBox()
        self.global_group.setCheckable(True)
        self.global_group.setChecked(False)
        self.global_layout = QFormLayout(self.global_group)
        right_layout.addWidget(self.global_group)

        self.lbl_preview = QLabel()
        self.lbl_preview.setObjectName("sectionTitle")
        right_layout.addWidget(self.lbl_preview)
        self.yaml_preview = QPlainTextEdit()
        self.yaml_preview.setReadOnly(True)
        mono = QFont("Courier New", 10)
        mono.setStyleHint(QFont.Monospace)
        self.yaml_preview.setFont(mono)
        right_layout.addWidget(self.yaml_preview, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        splitter.setSizes([260, 520, 520])

        root.addWidget(splitter, 1)

        # Status strip
        self.status_strip = QLabel()
        self.status_strip.setObjectName("muted")
        root.addWidget(self.status_strip)

        # Wire actions
        self.btn_load.clicked.connect(self._on_load_yaml)
        self.btn_save.clicked.connect(self._on_save_yaml)
        self.btn_validate.clicked.connect(self._on_validate)
        self.btn_preset.clicked.connect(self._on_pick_preset)
        self.btn_submit.clicked.connect(self._on_submit_to_agent)
        self.btn_remove_step.clicked.connect(self._on_remove_step)
        self.btn_step_up.clicked.connect(lambda: self._on_move_step(-1))
        self.btn_step_down.clicked.connect(lambda: self._on_move_step(1))
        self.global_group.toggled.connect(lambda _v: self._refresh_yaml_preview())

        self._add_step_menu.aboutToShow.connect(self._populate_add_step_menu)
        self._retranslate_static()

    def _retranslate_static(self) -> None:
        self.btn_load.setText(tr("wf.load", self._language))
        self.btn_save.setText(tr("wf.save", self._language))
        self.btn_validate.setText(tr("wf.validate", self._language))
        self.btn_preset.setText(tr("wf.preset", self._language))
        self.btn_submit.setText(tr("wf.submit_agent", self._language))
        self.btn_add_step.setText(tr("wf.add_step", self._language))
        self.btn_remove_step.setText(tr("wf.remove_step", self._language))
        self.btn_step_up.setText(tr("wf.up", self._language))
        self.btn_step_down.setText(tr("wf.down", self._language))
        self.lbl_steps.setText(tr("wf.steps", self._language))
        self.lbl_form_title.setText(tr("wf.form", self._language))
        self.lbl_preview.setText(tr("wf.preview", self._language))
        self.global_group.setTitle(tr("wf.global_options", self._language))

    # -- step list -----------------------------------------------------------

    def _populate_add_step_menu(self) -> None:
        self._add_step_menu.clear()
        for kind in STEP_KINDS:
            action = self._add_step_menu.addAction(tr(kind.label_key, self._language))
            action.triggered.connect(lambda _checked=False, k=kind.name: self._on_add_step(k))

    def _refresh_step_list(self) -> None:
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for idx, step in enumerate(self._form_state.steps, start=1):
            text = f"{idx:02d}. {step.type}"
            if step.params.get("name"):
                text += f" — {step.params['name']}"
            if not step.enabled:
                text = "⊘ " + text
            item = QListWidgetItem(text)
            self.step_list.addItem(item)
        self.step_list.blockSignals(False)
        if self._form_state.steps:
            self.step_list.setCurrentRow(min(self._current_step_index, len(self._form_state.steps) - 1))
        else:
            self._current_step_index = -1

    def _on_add_step(self, kind: str) -> None:
        step = StepState(type=kind, enabled=True, params=default_step_state(kind))
        self._form_state.steps.append(step)
        self._current_step_index = len(self._form_state.steps) - 1
        self._refresh_step_list()
        self._render_step_form()
        self._refresh_yaml_preview()
        self._log(f"added step {kind} (#{len(self._form_state.steps)})")

    def _on_remove_step(self) -> None:
        if not self._form_state.steps or self._current_step_index < 0:
            return
        del self._form_state.steps[self._current_step_index]
        self._current_step_index = max(0, self._current_step_index - 1)
        self._refresh_step_list()
        self._render_step_form()
        self._refresh_yaml_preview()

    def _on_move_step(self, delta: int) -> None:
        idx = self._current_step_index
        if idx < 0:
            return
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(self._form_state.steps):
            return
        steps = self._form_state.steps
        steps[idx], steps[new_idx] = steps[new_idx], steps[idx]
        self._current_step_index = new_idx
        self._refresh_step_list()
        self._refresh_yaml_preview()

    def _on_step_selected(self, row: int) -> None:
        # Pull current step form values into state before switching
        self._pull_step_form()
        self._current_step_index = row
        self._render_step_form()

    # -- global form ---------------------------------------------------------

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                child_layout = item.layout()
                if child_layout is not None:
                    self._clear_layout(child_layout)
                continue
            widget.setParent(None)

    def _render_global_form(self) -> None:
        self._clear_layout(self.global_layout)
        self._global_bindings = []
        for spec in GLOBAL_FIELDS:
            binding = _make_field_widget(spec, self._language)
            self._global_bindings.append(binding)
            # Initial value from state (might differ from default after load)
            current = self._form_state.global_options.get(spec.key, spec.default)
            self._apply_to_widget(binding, current)
            self.global_layout.addRow(binding.widget)
            binding.widget.destroyed.connect(self._make_remove_handler(self._global_bindings, binding))

    def _apply_to_widget(self, binding: _FieldBinding, value: Any) -> None:
        spec = binding.spec
        w = binding.widget
        if spec.kind == "bool":
            # The actual QCheckBox is inside the wrapper for non-bool; for bool
            # the wrapper IS the QCheckBox.
            cb = w if isinstance(w, QCheckBox) else w.findChild(QCheckBox)
            if cb is not None:
                cb.setChecked(bool(value))
            return
        if spec.kind == "choice":
            combo = w.findChild(QComboBox) if not isinstance(w, QComboBox) else w
            if combo is not None:
                text = str(value) if value is not None else ""
                idx = combo.findText(text)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            return
        if spec.kind == "int":
            spin = w.findChild(QSpinBox)
            if spin is not None:
                try:
                    spin.setValue(int(value))
                except (TypeError, ValueError):
                    pass
            return
        if spec.kind == "float":
            spin = w.findChild(QDoubleSpinBox)
            if spin is not None:
                try:
                    spin.setValue(float(value))
                except (TypeError, ValueError):
                    pass
            return
        # text-like kinds
        line = w.findChild(QLineEdit)
        if line is not None:
            if value is None:
                line.setText("")
            elif isinstance(value, (list, tuple)):
                line.setText(", ".join(str(x) for x in value))
            else:
                line.setText(str(value))

    def _pull_global_form(self) -> None:
        for binding in self._global_bindings:
            self._form_state.global_options[binding.spec.key] = binding.getter()

    # -- step form -----------------------------------------------------------

    def _render_step_form(self) -> None:
        self._clear_layout(self.form_layout)
        self._step_bindings = []
        idx = self._current_step_index
        if idx < 0 or idx >= len(self._form_state.steps):
            placeholder = QLabel(tr("wf.no_step_selected", self._language))
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setObjectName("muted")
            self.form_layout.addWidget(placeholder)
            self.form_layout.addStretch()
            return

        step = self._form_state.steps[idx]
        # Header with enable checkbox
        header = QHBoxLayout()
        enabled_cb = QCheckBox(tr("wf.step_enabled", self._language))
        enabled_cb.setChecked(step.enabled)
        enabled_cb.toggled.connect(lambda v, s=step: self._on_step_enabled_toggled(s, v))
        header.addWidget(enabled_cb)
        header.addStretch()
        type_lbl = QLabel(f"{tr('wf.step_type', self._language)}: {step.type}")
        type_lbl.setObjectName("muted")
        header.addWidget(type_lbl)
        header_widget = QWidget()
        header_widget.setLayout(header)
        self.form_layout.addWidget(header_widget)

        # Group fields by section
        sections: dict[str, list[FieldSpec]] = {"general": []}
        for spec in get_step_fields(step.type):
            sections.setdefault(spec.section, []).append(spec)

        for section_name, fields in sections.items():
            if section_name == "general":
                title = ""
            else:
                title = tr(f"section.{section_name}", self._language)
            group = QGroupBox(title) if title else QGroupBox()
            form = QFormLayout(group)
            for spec in fields:
                binding = _make_field_widget(spec, self._language)
                self._step_bindings.append(binding)
                current = step.params.get(spec.key, spec.default)
                self._apply_to_widget(binding, current)
                form.addRow(binding.widget)
                binding.widget.destroyed.connect(self._make_remove_handler(self._step_bindings, binding))
            self.form_layout.addWidget(group)

        self.form_layout.addStretch()

    def _on_step_enabled_toggled(self, step: StepState, enabled: bool) -> None:
        step.enabled = enabled
        self._refresh_step_list()
        self._refresh_yaml_preview()

    def _pull_step_form(self) -> None:
        idx = self._current_step_index
        if idx < 0 or idx >= len(self._form_state.steps):
            return
        step = self._form_state.steps[idx]
        for binding in self._step_bindings:
            step.params[binding.spec.key] = binding.getter()

    def _make_remove_handler(self, target: list, binding: _FieldBinding):
        """Build a slot that removes ``binding`` from ``target`` on widget
        destruction. Uses ``functools.partial`` style to keep the right
        binding reference (lambdas in a loop otherwise all see the latest
        binding).
        """
        def _handler():
            try:
                target.remove(binding)
            except ValueError:
                pass
        return _handler

    # -- yaml preview --------------------------------------------------------

    def _refresh_yaml_preview(self) -> None:
        self._pull_global_form()
        self._pull_step_form()
        try:
            text = form_state_to_yaml(self._form_state)
            self.yaml_preview.setPlainText(text)
            self.status_strip.setText(tr("wf.preview_ok", self._language))
            self.status_strip.setObjectName("muted")
            self.status_strip.style().unpolish(self.status_strip)
            self.status_strip.style().polish(self.status_strip)
        except ValidationError as exc:
            self.yaml_preview.setPlainText("# " + "; ".join(exc.errors))
            self.status_strip.setText(tr("wf.preview_invalid", self._language))
            self.status_strip.setObjectName("error")

    # -- top-bar actions -----------------------------------------------------

    def _on_load_yaml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("wf.load", self._language), str(Path.cwd()), "YAML (*.yaml *.yml)"
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8")
            state = yaml_to_form_state(text)
        except (BuilderError, OSError) as exc:
            self._error_cb(tr("wf.load_error", self._language), str(exc))
            return
        self._form_state = state
        self._current_step_index = 0 if self._form_state.steps else -1
        self._refresh_step_list()
        self._render_global_form()
        self._render_step_form()
        self._refresh_yaml_preview()
        self._log(f"loaded workflow YAML: {path}")

    def _on_save_yaml(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, tr("wf.save", self._language), str(Path.cwd() / "confflow.yaml"), "YAML (*.yaml *.yml)"
        )
        if not path:
            return
        try:
            text = form_state_to_yaml(self._form_state)
        except ValidationError as exc:
            self._error_cb(tr("wf.save_invalid", self._language), "; ".join(exc.errors))
            return
        Path(path).write_text(text, encoding="utf-8")
        self._status_cb(f"saved {path}")
        self._log(f"saved workflow YAML: {path}")

    def _on_validate(self) -> None:
        try:
            validate_state(self._form_state)
        except ValidationError as exc:
            for err in exc.errors:
                self._status_cb(f"validation: {err}")
            self._error_cb(tr("wf.validate_failed", self._language), "; ".join(exc.errors))
            return
        try:
            wf = validate_runtime(self._form_state)
        except ConfigurationError as exc:
            self._error_cb(tr("wf.validate_failed", self._language), str(exc))
            return
        self._status_cb(
            tr("wf.validate_ok", self._language).format(n=len(wf.steps))
        )

    def _on_pick_preset(self) -> None:
        from ...cli.workflow_cmd import PRESETS
        names = sorted(PRESETS.keys())
        choice, ok = _pick_from_list(self, tr("wf.preset_pick", self._language), names)
        if not ok or not choice:
            return
        from ...workflow.builder import default_form_state
        preset = PRESETS[choice]
        state = default_form_state()
        for key, value in (preset.get("global") or {}).items():
            state.global_options[key] = value
        for step_raw in preset.get("steps") or []:
            params = dict(step_raw.get("params") or {})
            if "name" not in params and "name" in step_raw:
                params["name"] = step_raw["name"]
            state.steps.append(
                StepState(
                    type=step_raw["type"],
                    enabled=bool(step_raw.get("enabled", True)),
                    params=params,
                )
            )
        self._form_state = state
        self._current_step_index = 0 if state.steps else -1
        self._refresh_step_list()
        self._render_global_form()
        self._render_step_form()
        self._refresh_yaml_preview()
        self._status_cb(f"applied preset: {choice}")

    def _on_submit_to_agent(self) -> None:
        try:
            text = form_state_to_yaml(self._form_state)
        except ValidationError as exc:
            self._error_cb(tr("wf.submit_invalid", self._language), "; ".join(exc.errors))
            return
        # Pick a connected server — rely on shared AppState / main_window hook.
        server_id = getattr(self._state, "last_agent_server", None)
        if not server_id:
            self._error_cb(tr("wf.no_server", self._language), "")
            return
        # Emit a signal so the host (MainWindow) can run the existing pipeline.
        self.workflow_built.emit(text, {"name": "wizard", "steps": []})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pick_from_list(parent: QWidget, title: str, options: list[str]) -> tuple[str, bool]:
    """Small modal picker without depending on QInputDialog.getItem ordering."""

    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QVBoxLayout

    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    layout = QVBoxLayout(dialog)
    list_widget = QListWidget(dialog)
    list_widget.addItems(options)
    list_widget.setCurrentRow(0)
    layout.addWidget(list_widget)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return ("", False)
    item = list_widget.currentItem()
    return (item.text() if item else "", True)


__all__ = ["WorkflowBuilderPage", "schema_hints"]