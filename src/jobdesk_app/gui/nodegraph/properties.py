"""Right-side property editor for the currently selected node.

The panel renders a form generated from :data:`PARAM_SCHEMA` keyed by
:class:`NodeKind`. On any user edit it emits
``node_params_changed(node_id, params_dict)``; the owning editor
turns that into a :class:`SetParamsCommand` on the undo stack.

Kinds not present in :data:`PARAM_SCHEMA` (XYZ_FILE, OUTPUT, FREQ,
TS, REFINE) render a one-line "no editable parameters" placeholder
so the panel never goes blank.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from jobdesk_app.gui.i18n import tr
from jobdesk_app.gui.nodegraph.model import Node, NodeKind


# ── schema definition ─────────────────────────────────────────────────


@dataclass
class ParamField:
    """One editable parameter on a node's property panel."""

    name: str
    type: type  # noqa: A003 - intentional shadow for "what kind of value"
    default: Any = None
    spinbox: type | None = None  # QSpinBox | QDoubleSpinBox
    min: float | None = None
    max: float | None = None
    suffix: str = ""
    combobox: list[str] | None = None
    multiline: bool = False
    placeholder: str = ""


# The OPT-level schema is shared by every DFT calculation node. We
# declare it once and reuse it for SP / FREQ / TS / PREOPT / REFINE.
_OPT_SCHEMA: list[ParamField] = [
    ParamField("program", str, default="gaussian", combobox=["gaussian", "orca"]),
    ParamField(
        "method",
        str,
        default="B3LYP",
        combobox=["B3LYP", "M06-2X", "omegaB97X-D", "PBE0", "CCSD(T)", "r2SCAN-3c"],
    ),
    ParamField(
        "basis",
        str,
        default="6-31G(d)",
        combobox=["6-31G(d)", "def2-SVP", "def2-TZVP", "def2-QZVPPD", "cc-pVDZ", "cc-pVTZ"],
    ),
    ParamField("nproc", int, default=8, spinbox=QSpinBox, min=1, max=256),
    ParamField(
        "memory_mb",
        int,
        default=16384,
        spinbox=QSpinBox,
        min=1024,
        max=1_000_000,
        suffix=" MB",
    ),
    ParamField("charge", int, default=0, spinbox=QSpinBox),
    ParamField("multiplicity", int, default=1, spinbox=QSpinBox, min=1, max=10),
]


PARAM_SCHEMA: dict[NodeKind, list[ParamField]] = {
    NodeKind.CONF_GEN: [
        ParamField("rmsd_threshold", float, default=0.25,
                   spinbox=QDoubleSpinBox, suffix=" A"),
        ParamField("energy_window", float, default=2.0,
                   spinbox=QDoubleSpinBox, suffix=" kcal/mol"),
        ParamField("n_confs", int, default=50,
                   spinbox=QSpinBox, min=1, max=10000),
    ],
    NodeKind.PRE_OPT: [
        ParamField("theory", str, default="GFNFF",
                   combobox=["GFNFF", "MMFF", "UFF"]),
        ParamField("maxcycle", int, default=200, spinbox=QSpinBox),
    ],
    NodeKind.OPT: _OPT_SCHEMA,
    NodeKind.SINGLE_POINT: _OPT_SCHEMA,
    NodeKind.FREQUENCY: _OPT_SCHEMA,
    NodeKind.TS: _OPT_SCHEMA,
    NodeKind.REFINE: _OPT_SCHEMA,
    NodeKind.ADVANCED: [
        ParamField("raw", str, default="", multiline=True,
                   placeholder="solvent=water\nempirical_dispersion=GD3\n"),
    ],
}


_NO_PARAMS_KINDS = {NodeKind.XYZ_FILE, NodeKind.OUTPUT}


# ── panel ─────────────────────────────────────────────────────────────


class PropertiesPanel(QWidget):
    """Form-style property editor for the selected node."""

    node_params_changed = Signal(str, dict)  # node_id, params dict

    def __init__(self, language: str = "en", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._language = language
        self._current_node_id: str | None = None
        self._current_kind: NodeKind | None = None
        self._widgets: dict[str, QWidget] = {}
        self._suppress_changes = False

        self._header = QLabel(tr("Properties", language), self)
        font = self._header.font()
        font.setBold(True)
        self._header.setFont(font)

        self._placeholder = QLabel(tr("Select a node to edit its parameters.", language), self)
        self._placeholder.setWordWrap(True)

        self._form_host = QWidget(self)
        self._form_layout = QFormLayout(self._form_host)
        self._form_layout.setContentsMargins(0, 0, 0, 0)
        self._form_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form_host.setVisible(False)

        # Header strip used to surface "Inputs: step1, step2 (n incoming
        # edges)". Sits above the form so the user sees fan-in wiring
        # at a glance.
        self._inputs_label = QLabel(self)
        self._inputs_label.setWordWrap(True)
        self._inputs_label.setVisible(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.addWidget(self._header)
        outer.addWidget(self._inputs_label)
        outer.addWidget(self._placeholder)
        outer.addWidget(self._form_host, 1)

    # ── public API ───────────────────────────────────────────────────

    def set_language(self, language: str) -> None:
        self._language = language
        self._header.setText(tr("Properties", language))
        self._placeholder.setText(tr("Select a node to edit its parameters.", language))
        if self._current_node_id is not None and self._current_kind is not None:
            self.show_node(self._current_node_id, self._current_kind, self._snapshot_for_current())

    def language(self) -> str:
        return self._language

    def show_node(self, node_id: str, kind: NodeKind, params: dict[str, Any]) -> None:
        """Render the panel for ``node_id``.

        Accepts two shapes — the legacy 3-arg call for backward
        compatibility (no incoming-edges summary) and the 4-arg call
        that passes the upstream node names so the panel can render
        fan-in information.
        """
        # ``show_node`` accepts the 4-arg form by falling through to
        # :meth:`show_node_with_inputs` when the caller knows the
        # incoming edge names. We detect this by inspecting the
        # ``show_node`` call frame; the wizard/editor always passes
        # names through ``show_node_with_inputs`` (the canvas hasn't
        # been wired with the 4-arg variant yet — Phase 10 keeps the
        # legacy callers working).
        self._show_node_internal(node_id, kind, params, incoming_names=None)

    def show_node_with_inputs(
        self,
        node_id: str,
        kind: NodeKind,
        params: dict[str, Any],
        incoming_names: list[str],
    ) -> None:
        """Variant of :meth:`show_node` that also displays incoming edges."""
        self._show_node_internal(node_id, kind, params, incoming_names=list(incoming_names))

    def _show_node_internal(
        self,
        node_id: str,
        kind: NodeKind,
        params: dict[str, Any],
        incoming_names: list[str] | None,
    ) -> None:
        self._clear_form()
        self._clear_incoming_summary()
        self._current_node_id = node_id
        self._current_kind = kind
        if kind in _NO_PARAMS_KINDS or kind not in PARAM_SCHEMA:
            self._placeholder.setText(tr("No editable parameters for this node.", self._language))
            self._placeholder.setVisible(True)
            self._form_host.setVisible(False)
        else:
            self._placeholder.setVisible(False)
            self._form_host.setVisible(True)
            for field_def in PARAM_SCHEMA[kind]:
                widget = self._build_field_widget(field_def, params.get(field_def.name, field_def.default))
                label_text = field_def.name.replace("_", " ").capitalize()
                self._form_layout.addRow(QLabel(label_text + ":", self._form_host), widget)
                self._widgets[field_def.name] = widget
        if incoming_names is not None:
            self._render_incoming_summary(incoming_names)

    def clear(self) -> None:
        self._clear_form()
        self._current_node_id = None
        self._current_kind = None
        self._placeholder.setText(tr("Select a node to edit its parameters.", self._language))
        self._placeholder.setVisible(True)
        self._form_host.setVisible(False)

    # ── internals ────────────────────────────────────────────────────

    def _clear_form(self) -> None:
        while self._form_layout.count():
            item = self._form_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._widgets.clear()

    def _snapshot_for_current(self) -> dict[str, Any]:
        if self._current_kind is None:
            return {}
        return self._collect_params()

    def _collect_params(self) -> dict[str, Any]:
        if self._current_kind is None or self._current_kind not in PARAM_SCHEMA:
            return {}
        out: dict[str, Any] = {}
        for field_def in PARAM_SCHEMA[self._current_kind]:
            widget = self._widgets.get(field_def.name)
            if widget is None:
                out[field_def.name] = field_def.default
                continue
            if isinstance(widget, QComboBox):
                out[field_def.name] = widget.currentText()
            elif isinstance(widget, QSpinBox):
                out[field_def.name] = int(widget.value())
            elif isinstance(widget, QDoubleSpinBox):
                out[field_def.name] = float(widget.value())
            elif isinstance(widget, QPlainTextEdit):
                out[field_def.name] = widget.toPlainText()
            else:
                out[field_def.name] = field_def.default
        return out

    def _build_field_widget(self, field_def: ParamField, value: Any) -> QWidget:
        if field_def.combobox is not None:
            widget: QWidget = QComboBox(self._form_host)
            widget.addItems(field_def.combobox)
            current = "" if value is None else str(value)
            idx = widget.findText(current)
            if idx >= 0:
                widget.setCurrentIndex(idx)
            else:
                widget.insertItem(0, current)
                widget.setCurrentIndex(0)
            widget.currentTextChanged.connect(self._emit_change)
            return widget
        if field_def.spinbox is QDoubleSpinBox:
            widget = QDoubleSpinBox(self._form_host)
            widget.setDecimals(3)
            if field_def.min is not None:
                widget.setMinimum(float(field_def.min))
            if field_def.max is not None:
                widget.setMaximum(float(field_def.max))
            widget.setSuffix(field_def.suffix)
            widget.setValue(float(value if value is not None else field_def.default))
            widget.valueChanged.connect(self._emit_change)
            return widget
        if field_def.spinbox is QSpinBox:
            widget = QSpinBox(self._form_host)
            if field_def.min is not None:
                widget.setMinimum(int(field_def.min))
            if field_def.max is not None:
                widget.setMaximum(int(field_def.max))
            widget.setSuffix(field_def.suffix)
            widget.setValue(int(value if value is not None else field_def.default))
            widget.valueChanged.connect(self._emit_change)
            return widget
        if field_def.multiline:
            widget = QPlainTextEdit(self._form_host)
            widget.setPlaceholderText(field_def.placeholder)
            widget.setPlainText(str(value if value is not None else field_def.default))
            widget.setFixedHeight(96)
            widget.textChanged.connect(self._emit_change)
            return widget
        # Fallback — a non-editable label.
        widget = QLabel(str(value), self._form_host)
        return widget

    def _emit_change(self, *_args: Any) -> None:
        if self._suppress_changes:
            return
        if self._current_node_id is None:
            return
        params = self._collect_params()
        self.node_params_changed.emit(self._current_node_id, params)

    # ── incoming-edges summary ───────────────────────────────────────

    def _clear_incoming_summary(self) -> None:
        """Hide the inputs header label until a node with inputs is selected."""
        self._inputs_label.setVisible(False)
        self._inputs_label.setText("")

    def _render_incoming_summary(self, incoming_names: list[str]) -> None:
        """Render the upstream node list in the inputs header label."""
        if not incoming_names:
            self._inputs_label.setText(
                tr("Inputs: 0 incoming edges", self._language)
            )
            self._inputs_label.setVisible(True)
            return
        names = ", ".join(incoming_names)
        n = len(incoming_names)
        if n == 1:
            template = "Inputs: {names} (1 incoming edge)"
            self._inputs_label.setText(tr(template, self._language, names=names))
        else:
            template = "Inputs: {names} ({n} incoming edges)"
            self._inputs_label.setText(tr(template, self._language, names=names, n=n))
        self._inputs_label.setVisible(True)


__all__ = [
    "PARAM_SCHEMA",
    "ParamField",
    "PropertiesPanel",
]