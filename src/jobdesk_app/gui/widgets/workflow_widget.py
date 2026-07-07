"""Reusable :class:`WorkflowWidget` — extracted from ``_WorkflowPage``.

Phase 14A refactor: the body of :class:`_WorkflowPage` (a ``QWizardPage``) is
lifted out as a plain :class:`QWidget` so it can be embedded into the future
``SubmitPage``.  No behaviour change — the source ``_WorkflowPage`` class is
left in place and the existing wizard tests still exercise it.

The widget optionally embeds a :class:`CalculationWidget` so that callers can
build the :class:`WorkflowSpec` without going through ``QWizard``.  When no
``calc_widget`` is supplied, ``build_spec`` falls back to manual fields the
caller must populate via :attr:`method_edit`, etc.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.workflow_spec import (
    ConfFlowUnavailableError,
    DryRunReport,
    WorkflowSpec,
)
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr
from .calculation_widget import CalculationWidget

_DEFAULT_STEPS = ("confgen", "preopt", "opt", "refine", "sp")


class WorkflowWidget(QWidget):
    """Embedded version of the ConfFlow wizard's workflow page.

    Drop-in replacement for the old ``_WorkflowPage`` minus the
    ``QWizardPage`` superclass.

    Optionally takes a :class:`CalculationWidget` in its constructor so the
    embedded workflow builder can read calc form fields directly.  When
    ``calc_widget`` is ``None``, :meth:`render_yaml_preview` and
    :meth:`build_spec` return ``None`` / fall through silently — the
    embedding caller is responsible for wiring the two halves together.
    """

    dry_run_done = Signal(object)  # DryRunReport

    _hint_style = "color: #c00; font-style: italic;"

    def __init__(
        self,
        parent: QWidget | None = None,
        language: str = "en",
        calc_widget: CalculationWidget | None = None,
    ):
        super().__init__(parent)
        self._language = language
        self._calc_widget = calc_widget

        # Validation state — same pattern as CalculationWidget:
        # _touched gates which fields surface inline hints so we don't yell
        # mid-typing; _was_complete tracks the prior is_complete() result
        # so completeChanged only fires on validity flips (re-entry safe).
        self._touched: set[str] = set()
        self._errors: dict[str, str] = {}
        self._was_complete: bool | None = None

        layout = QVBoxLayout(self)

        # Steps
        steps_box = QGroupBox(tr("Steps", self._language))
        sb_layout = QHBoxLayout(steps_box)
        self._step_checks: dict[str, QCheckBox] = {}
        for step in _DEFAULT_STEPS:
            cb = QCheckBox(step)
            cb.setChecked(True)
            self._step_checks[step] = cb
            cb.toggled.connect(lambda _checked, s=step: self._on_step_toggled(s))
            sb_layout.addWidget(cb)
        sb_layout.addStretch()
        layout.addWidget(steps_box)

        # Step hint — placed right after the steps_box.
        self.steps_hint = QLabel("")
        self.steps_hint.setStyleSheet(self._hint_style)
        self.steps_hint.setWordWrap(True)
        layout.addWidget(self.steps_hint)

        # work_dir
        wd_row = QHBoxLayout()
        wd_row.addWidget(QLabel(tr("Work dir name:", self._language)))
        self.work_dir_edit = QLineEdit("{basename}_confflow_work")
        self.work_dir_edit.setPlaceholderText("{basename}_confflow_work")
        wd_row.addWidget(self.work_dir_edit, 1)
        layout.addLayout(wd_row)

        # work_dir hint — independent label since work_dir is a HBoxLayout,
        # not a QFormLayout row.
        self.work_dir_hint = QLabel("")
        self.work_dir_hint.setStyleSheet(self._hint_style)
        self.work_dir_hint.setWordWrap(True)
        layout.addWidget(self.work_dir_hint)

        # Advanced options (raw key=value lines; parsed on accept)
        adv = QGroupBox(
            tr(
                "Advanced options (key=value, one per line)",
                self._language,
            )
        )
        adv_layout = QVBoxLayout(adv)
        self.adv_edit = QTextEdit()
        self.adv_edit.setPlaceholderText("# examples:\n# solvent=water\n# scan=true")
        self.adv_edit.setMaximumHeight(80)
        adv_layout.addWidget(self.adv_edit)
        layout.addWidget(adv)

        # Advanced options hint — placed after the adv GroupBox so it visually
        # attaches to the field it describes.
        self.adv_hint = QLabel("")
        self.adv_hint.setStyleSheet(self._hint_style)
        self.adv_hint.setWordWrap(True)
        layout.addWidget(self.adv_hint)

        # Preview + dry-run
        preview_box = QGroupBox(tr("YAML preview", self._language))
        pv_layout = QVBoxLayout(preview_box)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFontFamily("Courier New")
        self.preview.setMinimumHeight(200)
        pv_layout.addWidget(self.preview)
        btn_row = QHBoxLayout()
        self.refresh_btn = apply_button_role(
            QPushButton(tr("Refresh preview", self._language)),
            ButtonRole.INSTANT_ACTION,
        )
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        btn_row.addWidget(self.refresh_btn)
        self.status_label = QLabel("")
        btn_row.addWidget(self.status_label, 1)
        pv_layout.addLayout(btn_row)
        layout.addWidget(preview_box, 1)

        self._last_spec: WorkflowSpec | None = None
        self._last_report: DryRunReport | None = None

        # Validation signal wiring (mirrors CalculationWidget):
        self.work_dir_edit.textChanged.connect(lambda _t: self._on_text_changed("work_dir"))
        self.work_dir_edit.editingFinished.connect(lambda: self._on_text_touched("work_dir"))
        self.adv_edit.textChanged.connect(lambda: self._on_adv_changed())

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def language(self) -> str:
        return self._language

    def apply_language(self, language: str) -> None:
        """Re-translate every static label on the widget.

        Note: this does NOT recursively re-translate the embedded
        ``calc_widget`` (if any).  Callers embedding both should call
        ``calc_widget.apply_language(language)`` themselves.
        """
        self._language = language
        self.refresh_btn.setText(tr("Refresh preview", self._language))

    @property
    def calc_widget(self) -> CalculationWidget | None:
        """The optional embedded :class:`CalculationWidget`."""
        return self._calc_widget

    def set_calc_widget(self, calc_widget: CalculationWidget | None) -> None:
        """Attach (or detach) a :class:`CalculationWidget` after construction."""
        self._calc_widget = calc_widget

    def work_dir_name(self) -> str:
        text = self.work_dir_edit.text().strip() or "{basename}_confflow_work"
        return text

    def steps(self) -> list[str]:
        """List of currently checked step names (e.g. ``["confgen", "opt"]``).

        Order matches the declared ``_DEFAULT_STEPS`` tuple — the spec
        consumer can rely on a stable order.
        """
        return self.selected_steps()

    def selected_steps(self) -> list[str]:
        return [name for name, cb in self._step_checks.items() if cb.isChecked()]

    def advanced_options(self) -> dict[str, Any]:
        """Parse ``adv_edit`` into a typed ``dict`` (best-effort)."""
        return self.extra_options()

    def validate(self) -> dict[str, str]:
        """Return a fresh ``{field_name: error_msg}`` map (empty = valid)."""
        self._compute_validation()
        return dict(self._errors)

    def is_complete(self) -> bool:
        return not bool(self.validate())

    def render_yaml_preview(self, spec: WorkflowSpec | None) -> None:
        """Populate the embedded :class:`QTextEdit` preview with ``spec``'s YAML.

        ``spec`` may be ``None`` to clear the preview.  Errors (e.g. when
        ``confflow`` is not installed) are surfaced on the status label
        rather than raised — keeps the widget forgiving for embedding
        contexts that want to show the error inline.
        """
        if spec is None:
            self.preview.setPlainText("")
            self.status_label.setText("")
            self._last_spec = None
            self._last_report = None
            return
        try:
            text = spec.to_yaml()
            self.preview.setPlainText(text)
            report = spec.dry_run()
        except ConfFlowUnavailableError as exc:
            self.status_label.setText(str(exc))
            self._last_report = None
            return
        except Exception as exc:
            self.status_label.setText(
                tr("Render failed: {exc}", self._language, exc=exc)
            )
            self._last_report = None
            return
        self._last_spec = spec
        self._last_report = report
        if report.ok:
            self.status_label.setText(tr("✓ YAML valid", self._language))
        else:
            self.status_label.setText(f"✗ {report.error}")

    def yaml_text(self) -> str:
        """Return the current preview text verbatim."""
        return self.preview.toPlainText()

    # ── Internal helpers (mirrors _WorkflowPage behaviour) ────────────────

    def extra_options(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for line in self.adv_edit.toPlainText().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            # Best-effort type coercion: bool/int/float/str.
            if value.lower() in ("true", "false"):
                out[key] = value.lower() == "true"
                continue
            try:
                out[key] = int(value)
                continue
            except ValueError:
                pass
            try:
                out[key] = float(value)
                continue
            except ValueError:
                pass
            out[key] = value
        return out

    def build_spec(self, calc: dict[str, Any]) -> WorkflowSpec:
        return WorkflowSpec.from_form(
            work_dir_name=self.work_dir_name(),
            program=calc["program"],
            method=calc["method"],
            basis=calc["basis"],
            charge=calc["charge"],
            multiplicity=calc["multiplicity"],
            nproc=calc["nproc"],
            memory_mb=calc["memory_mb"],
            steps=tuple(self.selected_steps()),
            extra_options=self.extra_options(),
        )

    def _on_refresh_clicked(self) -> None:
        calc = self._calc_fields_or_none()
        if calc is None:
            self.status_label.setText(
                tr("Build failed: {exc}", self._language, exc="no CalculationWidget attached")
            )
            return
        try:
            spec = self.build_spec(calc)
        except Exception as exc:
            self.status_label.setText(
                tr("Build failed: {exc}", self._language, exc=exc)
            )
            return
        self.render_yaml_preview(spec)

    def _calc_fields_or_none(self) -> dict[str, Any] | None:
        """Return the embedded calc's fields, or ``None`` if not wired."""
        if self._calc_widget is None:
            return None
        return self._calc_widget.calc_fields()

    def _compute_validation(self) -> dict[str, str]:
        """Return a fresh field-name → error message map for this widget."""
        errors: dict[str, str] = {}

        work_dir_name = self.work_dir_edit.text().strip()
        if not work_dir_name:
            errors["work_dir"] = tr("Work dir name is required.", self._language)
        elif "/" in work_dir_name or "\\" in work_dir_name:
            errors["work_dir"] = tr(
                "Work dir name cannot contain '/' or '\\'.", self._language
            )

        if not any(cb.isChecked() for cb in self._step_checks.values()):
            errors["steps"] = tr("Pick at least one workflow step.", self._language)

        # Duplicate-key detection on the advanced options textarea.
        seen: dict[str, int] = {}
        for line in self.adv_edit.toPlainText().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if not key:
                continue
            seen[key] = seen.get(key, 0) + 1
        for key, count in seen.items():
            if count > 1:
                errors["adv"] = tr(
                    "Duplicate advanced option key: '{key}'.",
                    self._language,
                    key=key,
                )
                break

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

    def _on_step_toggled(self, _step_name: str) -> None:
        """Mark ``steps`` as touched and refresh on any step checkbox toggle."""
        self._touched.add("steps")
        self._compute_validation()
        self._refresh_hint("steps")
        self._maybe_emit_complete_changed()

    def _on_adv_changed(self) -> None:
        """Live re-validation for the advanced options textarea."""
        self._touched.add("adv")
        self._compute_validation()
        self._refresh_hint("adv")
        self._maybe_emit_complete_changed()

    def _maybe_emit_complete_changed(self) -> None:
        """No-op shim kept for parity with CalculationWidget.

        Embedding callers can wire their own ``completeChanged`` signal
        via a thin subclass if they need QWizard-like behaviour; for now
        we don't expose one since this widget is no longer a
        ``QWizardPage``.
        """
        return None


__all__ = ["WorkflowWidget"]


# Re-export ButtonRole to keep the module's API surface small but explicit.
_ = ButtonRole