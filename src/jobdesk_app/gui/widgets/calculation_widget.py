"""Reusable :class:`CalculationWidget` — extracted from ``_CalcPage``.

Phase 14A refactor: the body of :class:`_CalcPage` (a ``QWizardPage``) is
lifted out as a plain :class:`QWidget` so it can be embedded into the future
``SubmitPage``.  No behaviour change — the source ``_CalcPage`` class is
left in place and the existing wizard tests still exercise it.

The widget exposes the same field set as the original page plus a small
``CalculationFields`` dataclass so callers can consume values without
touching Qt widgets directly.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QToolButton,
    QWidget,
)

from ...core.input_builder import (
    GAUSSIAN_PRESETS,
    ORCA_PRESETS,
    preset_to_confflow_fields,
)
from ..button_feedback import apply_button_role
from ..i18n import tr

_PROGRAMS = ("gaussian", "orca")
_MAX_RECENT_PRESETS = 5


@dataclass
class CalculationFields:
    """Plain value type for :class:`CalculationWidget`'s form contents.

    Mirrors the keys previously returned by ``_CalcPage.calc_fields()`` —
    plus a ``preset_name`` and a ``job_keywords`` list so the consuming
    ``SubmitUseCase`` can route to either ``RunSpec`` (single) or
    ``WorkflowSpec`` (workflow) without re-reading Qt widgets.
    """

    program: str
    preset_name: str | None
    method_basis: str
    job_keywords: list[str]
    charge: int
    multiplicity: int
    nproc: int
    mem: str


class CalculationWidget(QWidget):
    """Embedded version of the ConfFlow wizard's calculation page.

    Drop-in replacement for the old ``_CalcPage`` minus the ``QWizardPage``
    superclass.  Tracks recent-preset picks in memory only (Phase 9D-4).
    """

    completeChanged = Signal()

    _hint_style = "color: #c00; font-style: italic;"

    def __init__(self, parent: QWidget | None = None, language: str = "en"):
        super().__init__(parent)
        self._language = language

        # Validation state — _touched gates which fields surface inline hints,
        # so the user is not yelled at mid-typing. _was_complete tracks the
        # previous is_complete() result so we can emit completeChanged only on
        # validity flips (mimics the QWizard semantics).
        self._touched: set[str] = set()
        self._errors: dict[str, str] = {}
        self._was_complete: bool | None = None

        form = QFormLayout(self)

        self.program_combo = QComboBox()
        self.program_combo.addItems(_PROGRAMS)
        form.addRow(tr("Program:", self._language), self.program_combo)

        # Preset dropdown — picks method/basis/nproc/memory in one click.
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(manual)", None)
        form.addRow(tr("Preset:", self._language), self.preset_combo)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        # Recent-presets strip — quick one-click access to the last few
        # presets the user picked. Populated lazily by
        # :meth:`_refresh_recent_strip`; in-memory only (Phase 9D-4).
        self.recent_strip = QHBoxLayout()
        self.recent_strip.setContentsMargins(0, 0, 0, 0)
        self.recent_strip.setSpacing(4)
        self.recent_strip_wrap = QWidget()
        self.recent_strip_wrap.setLayout(self.recent_strip)
        self.recent_label = QLabel(tr("Recent:", self._language))
        self.recent_label.setStyleSheet("color: #475569;")
        self.recent_strip.addWidget(self.recent_label)
        self.recent_strip.addStretch(1)
        self.recent_presets: OrderedDict[str, None] = OrderedDict()
        form.addRow("", self.recent_strip_wrap)
        self.recent_strip_wrap.setVisible(False)

        # ORCA-aware hint — updated when program changes.
        self.orca_hint = QLabel("")
        self.orca_hint.setWordWrap(True)
        hint_font = QFont()
        hint_font.setItalic(True)
        self.orca_hint.setFont(hint_font)
        self.orca_hint.setStyleSheet("color: #666;")
        form.addRow("", self.orca_hint)

        self.method_edit = QLineEdit("B3LYP")
        form.addRow(tr("Method:", self._language), self.method_edit)
        self.method_hint = QLabel("")
        self.method_hint.setStyleSheet(self._hint_style)
        self.method_hint.setWordWrap(True)
        form.addRow("", self.method_hint)

        self.basis_edit = QLineEdit("6-31G(d)")
        form.addRow(tr("Basis:", self._language), self.basis_edit)
        self.basis_hint = QLabel("")
        self.basis_hint.setStyleSheet(self._hint_style)
        self.basis_hint.setWordWrap(True)
        form.addRow("", self.basis_hint)

        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(-10, 10)
        form.addRow(tr("Charge:", self._language), self.charge_spin)
        self.charge_hint = QLabel("")
        self.charge_hint.setStyleSheet(self._hint_style)
        self.charge_hint.setWordWrap(True)
        form.addRow("", self.charge_hint)

        self.mult_spin = QSpinBox()
        self.mult_spin.setRange(1, 10)
        self.mult_spin.setValue(1)
        form.addRow(tr("Multiplicity:", self._language), self.mult_spin)
        self.mult_hint = QLabel("")
        self.mult_hint.setStyleSheet(self._hint_style)
        self.mult_hint.setWordWrap(True)
        form.addRow("", self.mult_hint)

        self.nproc_spin = QSpinBox()
        self.nproc_spin.setRange(1, 256)
        self.nproc_spin.setValue(8)
        form.addRow(tr("CPU cores:", self._language), self.nproc_spin)
        self.nproc_hint = QLabel("")
        self.nproc_hint.setStyleSheet(self._hint_style)
        self.nproc_hint.setWordWrap(True)
        form.addRow("", self.nproc_hint)

        self.mem_spin = QSpinBox()
        self.mem_spin.setRange(256, 1_000_000)
        self.mem_spin.setSingleStep(512)
        self.mem_spin.setValue(4096)
        self.mem_spin.setSuffix(" MB")
        form.addRow(tr("Memory:", self._language), self.mem_spin)
        self.mem_hint = QLabel("")
        self.mem_hint.setStyleSheet(self._hint_style)
        self.mem_hint.setWordWrap(True)
        form.addRow("", self.mem_hint)

        # Show context-sensitive hints and update defaults when program changes.
        self.program_combo.currentTextChanged.connect(self._on_program_changed)
        self._on_program_changed(self.program_combo.currentText())

        # Validation signals. textChanged on text fields re-validates live so
        # callers can flip UI state on the fly; editingFinished marks the
        # field as touched so the inline hint only appears once the user has
        # finished editing rather than mid-keystroke.
        self.method_edit.textChanged.connect(lambda _t: self._on_text_changed("method"))
        self.method_edit.editingFinished.connect(lambda: self._on_text_touched("method"))
        self.basis_edit.textChanged.connect(lambda _t: self._on_text_changed("basis"))
        self.basis_edit.editingFinished.connect(lambda: self._on_text_touched("basis"))
        self.charge_spin.valueChanged.connect(lambda _v: self._on_spin_touched("charge"))
        self.mult_spin.valueChanged.connect(lambda _v: self._on_spin_touched("mult"))
        self.nproc_spin.valueChanged.connect(lambda _v: self._on_spin_touched("nproc"))
        self.mem_spin.valueChanged.connect(lambda _v: self._on_spin_touched("mem"))

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def language(self) -> str:
        return self._language

    def apply_language(self, language: str) -> None:
        """Re-translate every static label on the widget."""
        self._language = language
        # Re-translated fields.  Spinbox suffixes (e.g. " MB") are static
        # English tokens in the original wizard; we keep them verbatim so
        # the i18n surface stays in lockstep with the source class.
        self.recent_label.setText(tr("Recent:", self._language))
        self._refresh_recent_strip()
        self._refresh_orca_hint(self.program_combo.currentText())

    def validate(self) -> dict[str, str]:
        """Return a fresh ``{field_name: error_msg}`` map (empty = valid).

        Same semantics as ``_CalcPage._compute_validation``: keys present in
        the dict are broken; the dict is also stored on ``self._errors`` so
        tests and callers can inspect the latest snapshot.
        """
        self._compute_validation()
        return dict(self._errors)

    def fields(self) -> CalculationFields:
        """Return a :class:`CalculationFields` snapshot of the current form."""
        program = self.program_combo.currentText()
        method = self.method_edit.text().strip()
        basis = self.basis_edit.text().strip()
        # The original wizard exposed only method/basis separately; the
        # new ``CalculationFields`` combines them into a single ``method_basis``
        # string so the input-builder side has the same shape as ``build_gjf``.
        if method and basis:
            method_basis = f"{method}/{basis}"
        else:
            method_basis = method or basis
        return CalculationFields(
            program=program,
            preset_name=self.preset_combo.currentData(),
            method_basis=method_basis,
            job_keywords=[],  # calc page does not collect job keywords
            charge=self.charge_spin.value(),
            multiplicity=self.mult_spin.value(),
            nproc=self.nproc_spin.value(),
            mem=f"{self.mem_spin.value()}MB",
        )

    def is_complete(self) -> bool:
        """Whether the form is currently valid.

        Preserves the QWizard semantics (no completion when manual fields
        are empty AND no preset is chosen) — callers that want the
        QWizardPage "preset suffices" behaviour can also inspect
        ``preset_name``.
        """
        return not bool(self.validate())

    def calc_fields(self) -> dict[str, Any]:
        """Legacy dict-style accessor kept for backwards compatibility.

        Equivalent to calling ``fields().__dict__`` but matches the shape
        historically returned by ``_CalcPage.calc_fields()`` so callers that
        expect ``memory_mb`` (an int) instead of ``mem`` (a string) keep
        working.
        """
        f = self.fields()
        # Parse mem back to int — defaults to 0 if the suffix isn't MB.
        mem_int = 0
        if f.mem.endswith("MB"):
            try:
                mem_int = int(f.mem[:-2])
            except ValueError:
                mem_int = 0
        return {
            "program": f.program,
            "method": self.method_edit.text().strip(),
            "basis": self.basis_edit.text().strip(),
            "charge": f.charge,
            "multiplicity": f.multiplicity,
            "nproc": f.nproc,
            "memory_mb": mem_int,
        }

    # ── Internal helpers (mirrors _CalcPage behaviour) ────────────────────

    def _refresh_orca_hint(self, program: str) -> None:
        if program == "orca":
            self.orca_hint.setText(
                tr(
                    "ORCA: ConfFlow's policy template already prefixes '!'. "
                    "Use a geometry optimization step (e.g. 'opt') — ORCA single-point "
                    "does not emit a geometry file and the run will fail.",
                    self._language,
                )
            )
        else:
            self.orca_hint.setText("")

    def _on_program_changed(self, program: str) -> None:
        """Update ORCA-specific hint and repopulate preset dropdown.

        Phase 7 lesson from real ORCA smoke testing: ORCA SP does not emit a
        companion ``.xyz`` file, so the ConfFlow runner fails with
        ``Calculation step did not produce an output XYZ file``. Geometry
        optimization works. We surface this caveat in the form.

        Also repopulates the preset dropdown so users see only valid presets
        for the selected program (Phase 8A).
        """
        self._refresh_orca_hint(program)

        # Repopulate presets without firing _on_preset_changed (which would
        # clobber whatever the user is typing into method/basis).
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("(manual)", None)
        presets = ORCA_PRESETS if program == "orca" else GAUSSIAN_PRESETS
        for name in sorted(presets):
            self.preset_combo.addItem(name, name)
        self.preset_combo.blockSignals(False)

    def _on_preset_changed(self, _idx: int) -> None:
        """Fill method/basis/nproc/memory from the selected preset.

        Selecting ``(manual)`` does nothing — the user keeps the values they
        typed. Selecting any preset overwrites the manual fields; if the
        user later edits a manual field, we leave the preset on whatever it
        was (the dropdown is informational, not authoritative).
        """
        preset_name = self.preset_combo.currentData()
        if not preset_name:
            return
        fields = preset_to_confflow_fields(preset_name)
        if fields.get("method"):
            self.method_edit.setText(fields["method"])
        if fields.get("basis"):
            self.basis_edit.setText(fields["basis"])
        if fields.get("nproc"):
            self.nproc_spin.setValue(int(fields["nproc"]))
        if fields.get("memory_mb"):
            self.mem_spin.setValue(int(fields["memory_mb"]))
        self._record_recent_preset(preset_name)
        self._refresh_recent_strip()

    def _record_recent_preset(self, preset_name: str) -> None:
        """Move ``preset_name`` to the front of the recent-presets MRU list.

        Caps the list at :data:`_MAX_RECENT_PRESETS` so the strip never grows
        unbounded. Ordering is most-recent-first via OrderedDict.
        """
        if preset_name in self.recent_presets:
            self.recent_presets.move_to_end(preset_name, last=False)
        else:
            self.recent_presets[preset_name] = None
            self.recent_presets.move_to_end(preset_name, last=False)
        while len(self.recent_presets) > _MAX_RECENT_PRESETS:
            self.recent_presets.popitem(last=True)

    def _refresh_recent_strip(self) -> None:
        """Rebuild the recent-presets strip from :attr:`recent_presets`."""
        # Remove all widgets after the "Recent:" label.
        while self.recent_strip.count() > 2:  # 1 = label, 2 = trailing stretch
            item = self.recent_strip.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self.recent_presets:
            self.recent_strip_wrap.setVisible(False)
            return
        self.recent_strip_wrap.setVisible(True)
        for name in self.recent_presets:
            btn = QToolButton()
            btn.setText(name)
            btn.setAutoRaise(True)
            btn.setToolTip(
                tr("Apply preset: {name}", self._language, name=name)
            )
            btn.clicked.connect(lambda _checked=False, n=name: self._apply_recent_preset(n))
            self.recent_strip.insertWidget(self.recent_strip.count() - 1, btn)

    def _apply_recent_preset(self, preset_name: str) -> None:
        """Apply a recent preset by routing through ``_on_preset_changed``."""
        idx = self.preset_combo.findData(preset_name)
        if idx < 0:
            return
        self.preset_combo.setCurrentIndex(idx)
        self._on_preset_changed(idx)

    def _compute_validation(self) -> dict[str, str]:
        """Return a fresh field-name → error message map."""
        errors: dict[str, str] = {}

        method = self.method_edit.text().strip()
        if not method:
            errors["method"] = tr("Method is required.", self._language)

        basis = self.basis_edit.text().strip()
        if not basis:
            errors["basis"] = tr("Basis set is required.", self._language)

        charge = self.charge_spin.value()
        if not -10 <= charge <= 10:
            errors["charge"] = tr(
                "Charge must be between -10 and 10.", self._language
            )

        mult = self.mult_spin.value()
        if mult < 1:
            errors["mult"] = tr("Multiplicity must be at least 1.", self._language)

        nproc = self.nproc_spin.value()
        if nproc < 1:
            errors["nproc"] = tr("CPU cores must be at least 1.", self._language)

        mem = self.mem_spin.value()
        if mem < 1024:
            errors["mem"] = tr(
                "Memory must be at least 1024 MB.", self._language
            )

        self._errors = errors
        return errors

    def _update_hint(self, label: QLabel, message: str) -> None:
        """Show ``message`` on ``label``, or clear it if empty."""
        label.setText(message or "")

    def _refresh_hint(self, field: str) -> None:
        """Re-render the inline hint for ``field`` based on current errors."""
        label = getattr(self, f"{field}_hint", None)
        if label is None:
            return
        if field in self._touched and field in self._errors:
            self._update_hint(label, self._errors[field])
        else:
            self._update_hint(label, "")

    def _on_text_changed(self, field: str) -> None:
        """Live re-validation on every keystroke for text fields."""
        self._compute_validation()
        if field in self._touched:
            self._refresh_hint(field)
        self._maybe_emit_complete_changed()

    def _on_text_touched(self, field: str) -> None:
        """Mark a text field as touched when the user finishes editing it."""
        self._touched.add(field)
        self._compute_validation()
        self._refresh_hint(field)

    def _on_spin_touched(self, field: str) -> None:
        """Mark a spinbox as touched on its first valueChanged."""
        self._touched.add(field)
        self._compute_validation()
        self._refresh_hint(field)

    def _maybe_emit_complete_changed(self) -> None:
        """Emit ``completeChanged`` when validity flips, re-entry safe."""
        complete = not self._errors
        prev = self._was_complete
        self._was_complete = complete
        if prev is not None and prev != complete:
            self.completeChanged.emit()


__all__ = ["CalculationFields", "CalculationWidget"]


# apply_button_role is not used inside the widget itself, but importing it
# keeps the module's public API consistent with the source dialog and
# avoids unused-import warnings during refactors.
_ = apply_button_role