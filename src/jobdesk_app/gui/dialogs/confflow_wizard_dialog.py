"""ConfFlow workflow wizard — XYZ + workflow fields → workflow.yaml + RunSpec.

Mirrors ``InputBuilderDialog``'s layout but produces a ConfFlow workflow
YAML instead of a .gjf/.inp. Three steps:

  1. XYZ inputs (one or many)
  2. Calculation settings (program / method / basis / charge / mult / nproc / mem)
  3. Workflow settings (work_dir, steps, advanced options) + YAML preview

The dialog does not submit anything; it returns a ``WorkflowSpec`` and the
paths the caller should upload (workflow.yaml + xyz files). The caller
(``FileTransferPage`` integration) is responsible for creating the run via
``ConfFlowAdapter.build_spec`` and the run service.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from ...core.input_builder import (
    GAUSSIAN_PRESETS,
    ORCA_PRESETS,
    preset_to_confflow_fields,
)
from ...core.run import RunSpec
from ...core.workflow_spec import (
    ConfFlowUnavailableError,
    DryRunReport,
    WorkflowSpec,
    write_workflow_yaml,
)
from ...services.program_adapters import ConfFlowAdapter
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr

_PROGRAMS = ("gaussian", "orca")
_DEFAULT_STEPS = ("confgen", "preopt", "opt", "refine", "sp")
_MAX_RECENT_PRESETS = 5


@dataclass
class WizardResult:
    """Returned by ``ConfFlowWizard.accepted_payload()`` after the user clicks Finish."""

    spec: WorkflowSpec
    xyz_paths: list[Path]
    workflow_yaml_path: Path
    run_spec: RunSpec
    server_id: str
    remote_dir: str


class _XyzPage(QWizardPage):
    def __init__(self, parent: QDialog | None = None, language: str = "en"):
        super().__init__(parent)
        self._language = language
        self.setTitle(tr("Input XYZ files", self._language))
        self.setSubTitle(
            tr(
                "Pick one or more .xyz files (or a whole directory). "
                "ConfFlow will run each independently. "
                "You can also drag .xyz files or folders here.",
                self._language,
            )
        )

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        self.list.setAcceptDrops(True)
        self.list.setDropIndicatorShown(True)
        layout.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        add_btn = apply_button_role(
            QPushButton(tr("Add files…", self._language)), ButtonRole.INSTANT_ACTION
        )
        add_btn.clicked.connect(self._add)
        add_dir_btn = apply_button_role(
            QPushButton(tr("Add directory…", self._language)), ButtonRole.INSTANT_ACTION
        )
        add_dir_btn.clicked.connect(self._add_directory)
        rm_btn = apply_button_role(
            QPushButton(tr("Remove", self._language)), ButtonRole.INSTANT_ACTION
        )
        rm_btn.clicked.connect(self._remove)
        clear_btn = apply_button_role(
            QPushButton(tr("Clear", self._language)), ButtonRole.INSTANT_ACTION
        )
        clear_btn.clicked.connect(self._clear)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(add_dir_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Recursive scan toggle. Default off so the wizard stays safe —
        # users must opt in to recursive directory walking.
        self.recursive_checkbox = QCheckBox(
            tr("Include files in subdirectories", self._language)
        )
        layout.addWidget(self.recursive_checkbox)

        # Counter / status line that updates as the user adds files.
        self.count_label = QLabel(tr("0 file(s) selected", self._language))
        self.count_label.setStyleSheet("color: #666;")
        layout.addWidget(self.count_label)

        self._xyz_paths: list[Path] = []

    def _add(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select XYZ files", "", "XYZ files (*.xyz);;All files (*)"
        )
        added = 0
        for raw in files:
            p = Path(raw)
            if self._try_add_path(p):
                added += 1
        self._refresh_count(added)

    def _add_directory(self) -> None:
        """Open a directory picker and add every ``*.xyz`` inside.

        Honors :attr:`recursive_checkbox` — when checked, scans
        subdirectories too. Empty directories report zero added files
        rather than an error so the wizard stays forgiving.
        """
        directory = QFileDialog.getExistingDirectory(self, "Select directory")
        if not directory:
            return
        added = self.add_directory(Path(directory), recursive=self.recursive_checkbox.isChecked())
        self._refresh_count(added)

    def add_directory(self, directory: Path, recursive: bool = False) -> int:
        """Scan ``directory`` for ``*.xyz`` files and add them.

        Returns the count of *newly added* files (i.e. excluding ones
        already in the list).  This method is exposed for tests and for
        callers that want to feed a directory without going through
        the QFileDialog prompt.

        Files with invalid XYZ content are skipped silently — the
        wizard does not enforce validation here; the workflow page
        will surface problems via the dry-run status label.
        """
        if not directory.is_dir():
            return 0
        pattern = "**/*.xyz" if recursive else "*.xyz"
        added = 0
        for xyz in sorted(directory.glob(pattern)):
            if not xyz.is_file():
                continue
            if self._try_add_path(xyz):
                added += 1
        self._refresh_count(added)
        return added

    def _try_add_path(self, p: Path) -> bool:
        """Append ``p`` to the list if it's not already there.

        Returns True if the path was added, False if it was a duplicate.
        """
        if any(existing == p for existing in self._xyz_paths):
            return False
        self._xyz_paths.append(p)
        self.list.addItem(QListWidgetItem(str(p)))
        return True

    def _refresh_count(self, added: int) -> None:
        n = len(self._xyz_paths)
        suffix = "s" if n != 1 else ""
        if added > 0:
            self.count_label.setText(
                tr(
                    "{n} file{suffix} selected (+{added} new)",
                    self._language,
                    n=n,
                    suffix=suffix,
                    added=added,
                )
            )
        else:
            self.count_label.setText(
                tr("{n} file{suffix} selected", self._language, n=n, suffix=suffix)
            )

    def _clear(self) -> None:
        self._xyz_paths.clear()
        self.list.clear()
        self._refresh_count(0)

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            row = self.list.row(item)
            del self._xyz_paths[row]
            self.list.takeItem(row)
        self._refresh_count(0)

    def xyz_paths(self) -> list[Path]:
        return list(self._xyz_paths)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(url.isLocalFile() for url in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if any(url.isLocalFile() for url in urls):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        added = 0
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir():
                added += self.add_directory(p, recursive=self.recursive_checkbox.isChecked())
            elif p.is_file():
                if p.suffix.lower() != ".xyz":
                    continue
                if self._try_add_path(p):
                    added += 1
        if added > 0:
            event.acceptProposedAction()
            self._refresh_count(added)
        else:
            event.ignore()

    def isComplete(self) -> bool:  # type: ignore[override]
        return bool(self._xyz_paths)


class _CalcPage(QWizardPage):
    _hint_style = "color: #c00; font-style: italic;"

    def __init__(self, parent: QDialog | None = None, language: str = "en"):
        super().__init__(parent)
        self._language = language
        self.setTitle(tr("Calculation settings", self._language))
        self.setSubTitle(
            tr("Program, method/basis, charge, resources.", self._language)
        )

        # Validation state — _touched gates which fields surface inline hints,
        # so the user is not yelled at mid-typing. _was_complete tracks the
        # previous isComplete() result so we can emit completeChanged only on
        # validity flips (QWizard listens to that signal to enable Next).
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
        # presets the user picked in this wizard session. Populated lazily by
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

        # ORCA-aware hint — updated when program changes (see _refresh_orca_hint).
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
        # the wizard can flip Next on the fly; editingFinished marks the field
        # as touched so the inline hint only appears once the user has finished
        # editing rather than mid-keystroke. Spinboxes don't have a meaningful
        # editingFinished equivalent, so their first valueChanged marks them
        # touched.
        self.method_edit.textChanged.connect(lambda _t: self._on_text_changed("method"))
        self.method_edit.editingFinished.connect(lambda: self._on_text_touched("method"))
        self.basis_edit.textChanged.connect(lambda _t: self._on_text_changed("basis"))
        self.basis_edit.editingFinished.connect(lambda: self._on_text_touched("basis"))
        self.charge_spin.valueChanged.connect(lambda _v: self._on_spin_touched("charge"))
        self.mult_spin.valueChanged.connect(lambda _v: self._on_spin_touched("mult"))
        self.nproc_spin.valueChanged.connect(lambda _v: self._on_spin_touched("nproc"))
        self.mem_spin.valueChanged.connect(lambda _v: self._on_spin_touched("mem"))

    def calc_fields(self) -> dict[str, Any]:
        return {
            "program": self.program_combo.currentText(),
            "method": self.method_edit.text().strip(),
            "basis": self.basis_edit.text().strip(),
            "charge": self.charge_spin.value(),
            "multiplicity": self.mult_spin.value(),
            "nproc": self.nproc_spin.value(),
            "memory_mb": self.mem_spin.value(),
        }

    def _on_program_changed(self, program: str) -> None:
        """Update ORCA-specific hint and steer default steps.

        Phase 7 lesson from real ORCA smoke testing: ORCA SP does not emit a
        companion ``.xyz`` file, so the ConfFlow runner fails with
        ``Calculation step did not produce an output XYZ file``. Geometry
        optimization works. We surface this caveat in the wizard and gently
        uncheck ``sp`` on the workflow page when ORCA is picked.

        Also repopulates the preset dropdown so users see only valid presets
        for the selected program (Phase 8A).
        """
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

        # Repopulate presets without firing _on_preset_changed (which would
        # clobber whatever the user is typing into method/basis).
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("(manual)", None)
        presets = ORCA_PRESETS if program == "orca" else GAUSSIAN_PRESETS
        for name in sorted(presets):
            self.preset_combo.addItem(name, name)
        self.preset_combo.blockSignals(False)

        # Best-effort: nudge the workflow page to drop 'sp' for ORCA users.
        wiz = self.wizard()
        if wiz is None:
            return
        workflow_page = getattr(wiz, "workflow_page", None)
        if workflow_page is None:
            return
        sp_cb = workflow_page._step_checks.get("sp")
        if sp_cb is None:
            return
        if program == "orca" and sp_cb.isChecked():
            sp_cb.setChecked(False)

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
        """Rebuild the recent-presets strip from :attr:`recent_presets`.

        Clear existing buttons (except the "Recent:" label), then add one
        :class:`QToolButton` per preset in MRU order. Clicking a button sets
        the preset combo to that preset, which fires :meth:`_on_preset_changed`
        and fills method/basis/nproc/memory.
        """
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
        """Apply a recent preset by routing through ``_on_preset_changed``.

        Setting the combo index does not always fire the signal (Qt skips
        duplicate index changes), so we invoke the handler directly to
        guarantee method/basis/nproc/memory get refreshed.
        """
        idx = self.preset_combo.findData(preset_name)
        if idx < 0:
            return
        self.preset_combo.setCurrentIndex(idx)
        self._on_preset_changed(idx)

    def _compute_validation(self) -> dict[str, str]:
        """Return a fresh field-name → error message map.

        Empty string for a field means it is valid. The returned mapping is
        also stored on ``self._errors`` so callers and tests can inspect the
        latest snapshot without re-running validation.
        """
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

    def isComplete(self) -> bool:  # type: ignore[override]
        errors = self._compute_validation()
        complete = not errors
        # Update _was_complete BEFORE emitting so that any synchronous
        # re-entry triggered by completeChanged (QWizard re-queries this
        # page's isComplete() to decide whether to enable the Next button)
        # does not see the stale value and emit again, which would recurse.
        prev = self._was_complete
        self._was_complete = complete
        if prev is not None and prev != complete:
            self.completeChanged.emit()
        return complete

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
        """Live re-validation on every keystroke for text fields.

        Does NOT mark the field as touched — that happens on
        :meth:`_on_text_touched` (wired to ``editingFinished``) so the
        inline hint stays quiet while the user is still typing.
        """
        self._compute_validation()
        if field in self._touched:
            self._refresh_hint(field)
        # Toggle Next availability even before the field is touched, so the
        # user is not stuck on a page they think they have finished.
        self.isComplete()

    def _on_text_touched(self, field: str) -> None:
        """Mark a text field as touched when the user finishes editing it."""
        self._touched.add(field)
        self._compute_validation()
        self._refresh_hint(field)

    def _on_spin_touched(self, field: str) -> None:
        """Mark a spinbox as touched on its first valueChanged.

        Spinboxes don't expose ``editingFinished`` with the same semantics
        we want (the user could just open the dropdown and arrow-key without
        committing a value), so the first valueChanged counts as interaction.
        """
        self._touched.add(field)
        self._compute_validation()
        self._refresh_hint(field)


class _WorkflowPage(QWizardPage):
    """Step list + work_dir + advanced options + YAML preview."""

    dry_run_done = Signal(object)  # DryRunReport

    _hint_style = "color: #c00; font-style: italic;"

    def __init__(self, parent: QDialog | None = None, language: str = "en"):
        super().__init__(parent)
        self._language = language
        self.setTitle(tr("Workflow settings & preview", self._language))
        self.setSubTitle(
            tr(
                "Pick steps, set work_dir, then preview & validate the YAML.",
                self._language,
            )
        )

        # Validation state — same pattern as _CalcPage (Phase 9C):
        # _touched gates which fields surface inline hints so we don't yell
        # mid-typing; _was_complete tracks the prior isComplete() result so
        # completeChanged only fires on validity flips (re-entry safe).
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

        # Validation signal wiring (mirrors _CalcPage from Phase 9C):
        # textChanged on text fields re-validates live so Next can flip on
        # the fly; editingFinished marks the field as touched so the inline
        # hint stays quiet until the user has finished editing. Step
        # checkboxes don't have an editingFinished equivalent, so the
        # first toggle counts as touching the steps field.
        self.work_dir_edit.textChanged.connect(lambda _t: self._on_text_changed("work_dir"))
        self.work_dir_edit.editingFinished.connect(lambda: self._on_text_touched("work_dir"))
        self.adv_edit.textChanged.connect(lambda: self._on_adv_changed())

    def selected_steps(self) -> list[str]:
        return [name for name, cb in self._step_checks.items() if cb.isChecked()]

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

    def work_dir_name(self) -> str:
        text = self.work_dir_edit.text().strip() or "{basename}_confflow_work"
        return text

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

    def render_preview(self, spec: WorkflowSpec) -> None:
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

    def _on_refresh_clicked(self) -> None:
        calc_page = self.wizard().calc_page  # type: ignore[attr-defined]
        try:
            spec = self.build_spec(calc_page.calc_fields())
        except Exception as exc:
            self.status_label.setText(
                tr("Build failed: {exc}", self._language, exc=exc)
            )
            return
        self.render_preview(spec)

    def _compute_validation(self) -> dict[str, str]:
        """Return a fresh field-name → error message map for this page.

        Mirrors :meth:`_CalcPage._compute_validation`: empty mapping means the
        page is valid; presence of a key means that field is broken. The
        returned mapping is also stored on :attr:`_errors` so tests and
        callers can inspect the latest snapshot without re-running.
        """
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

        # Duplicate-key detection on the advanced options textarea: parse the
        # raw text the same way extra_options() does, then count occurrences
        # of each non-empty key. We piggyback on the same ignore-rules
        # (comments, blanks, no '=') so what we report matches what would
        # actually be parsed on accept.
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

    def isComplete(self) -> bool:  # type: ignore[override]
        """QWizard re-queries this on every field change to enable Next.

        Re-entry safe: we update :attr:`_was_complete` BEFORE emitting so
        that a synchronous re-query (which QWizard performs on the
        ``completeChanged`` signal) does not see the stale value and emit
        again, which would recurse.
        """
        errors = self._compute_validation()
        complete = not errors
        prev = self._was_complete
        self._was_complete = complete
        if prev is not None and prev != complete:
            self.completeChanged.emit()
        return complete

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
        """Live re-validation on every keystroke for text fields.

        Does NOT mark the field as touched — that happens on
        :meth:`_on_text_touched` (wired to ``editingFinished``) so the
        inline hint stays quiet while the user is still typing.
        """
        self._compute_validation()
        if field in self._touched:
            self._refresh_hint(field)
        # Toggle Next availability even before the field is touched, so the
        # user is not stuck on a page they think they have finished.
        self.isComplete()

    def _on_text_touched(self, field: str) -> None:
        """Mark a text field as touched when the user finishes editing it."""
        self._touched.add(field)
        self._compute_validation()
        self._refresh_hint(field)

    def _on_step_toggled(self, _step_name: str) -> None:
        """Mark ``steps`` as touched and refresh on any step checkbox toggle.

        Checkboxes don't expose ``editingFinished`` with the same semantics
        we want (the user could just click without committing), so any
        toggle counts as interaction — same pattern as the spinbox helper
        in :class:`_CalcPage`.
        """
        self._touched.add("steps")
        self._compute_validation()
        self._refresh_hint("steps")
        self.isComplete()

    def _on_adv_changed(self) -> None:
        """Live re-validation for the advanced options textarea.

        The first time the user edits the box we additionally mark it as
        touched (mirrors ``editingFinished`` for fields that don't emit it),
        then on every keystroke we re-validate so the hint stays current.
        """
        self._touched.add("adv")
        self._compute_validation()
        self._refresh_hint("adv")
        self.isComplete()


class ConfFlowWizard(QWizard):
    """QWizard wrapper that produces a :class:`WizardResult` on accept."""

    def __init__(
        self,
        parent: QDialog | None = None,
        *,
        server_id: str = "",
        remote_dir: str = "",
        default_workflow_yaml: str | Path | None = None,
        language: str = "en",
    ):
        super().__init__(parent)
        self._language = language
        self.setWindowTitle(tr("ConfFlow Workflow Wizard", self._language))
        self.setMinimumSize(760, 620)
        self._server_id = server_id
        self._remote_dir = remote_dir

        self.xyz_page = _XyzPage(language=language)
        self.calc_page = _CalcPage(language=language)
        self.workflow_page = _WorkflowPage(language=language)
        self.addPage(self.xyz_page)
        self.addPage(self.calc_page)
        self.addPage(self.workflow_page)
        self.setStartId(0)

        if default_workflow_yaml is not None:
            try:
                text = Path(default_workflow_yaml).read_text(encoding="utf-8")
                spec = WorkflowSpec.from_yaml(text)
                form = spec.to_form()
                self._populate_calc(form)
                self._populate_workflow(form)
            except Exception:
                # Best-effort; ignore parse failure so the wizard still opens.
                pass

    def _populate_calc(self, form: dict[str, Any]) -> None:
        if "program" in form and form["program"] in _PROGRAMS:
            idx = self.calc_page.program_combo.findText(form["program"])
            if idx >= 0:
                self.calc_page.program_combo.setCurrentIndex(idx)
        if "method" in form:
            self.calc_page.method_edit.setText(str(form["method"]))
        if "basis" in form:
            self.calc_page.basis_edit.setText(str(form["basis"]))
        for key, widget in (
            ("charge", self.calc_page.charge_spin),
            ("multiplicity", self.calc_page.mult_spin),
            ("nproc", self.calc_page.nproc_spin),
            ("memory_mb", self.calc_page.mem_spin),
        ):
            if key in form:
                try:
                    widget.setValue(int(form[key]))
                except (TypeError, ValueError):
                    pass

    def _populate_workflow(self, form: dict[str, Any]) -> None:
        if "work_dir_name" in form:
            self.workflow_page.work_dir_edit.setText(str(form["work_dir_name"]))
        steps = form.get("steps") or []
        for name, cb in self.workflow_page._step_checks.items():
            cb.setChecked(name in steps)

    def set_server(self, server_id: str, remote_dir: str) -> None:
        self._server_id = server_id
        self._remote_dir = remote_dir

    def accepted_payload(self) -> WizardResult | None:
        if not self._server_id or not self._remote_dir:
            return None
        calc = self.calc_page.calc_fields()
        xyz_paths = self.xyz_page.xyz_paths()
        if not xyz_paths:
            return None
        try:
            spec = self.workflow_page.build_spec(calc)
        except ConfFlowUnavailableError:
            return None
        # Write workflow.yaml next to the first XYZ file so SFTP uploads it.
        workflow_yaml_path = xyz_paths[0].with_name("workflow.yaml")
        write_workflow_yaml(spec, workflow_yaml_path)
        run_spec = ConfFlowAdapter.build_spec(
            server_id=self._server_id,
            remote_dir=self._remote_dir,
            xyz_paths=[str(p) for p in xyz_paths],
            config_path=str(workflow_yaml_path),
            max_parallel=1,
            resume=False,
        )
        return WizardResult(
            spec=spec,
            xyz_paths=xyz_paths,
            workflow_yaml_path=workflow_yaml_path,
            run_spec=run_spec,
            server_id=self._server_id,
            remote_dir=self._remote_dir,
        )

    def accept(self) -> None:  # type: ignore[override]
        result = self.accepted_payload()
        if result is None:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(
                self,
                tr("ConfFlow Wizard", self._language),
                tr(
                    "Server, remote directory, and at least one XYZ file are required.",
                    self._language,
                ),
            )
            return
        super().accept()


__all__ = ["ConfFlowWizard", "WizardResult"]
