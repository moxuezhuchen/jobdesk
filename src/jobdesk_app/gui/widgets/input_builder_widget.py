"""Reusable :class:`InputBuilderWidget` — extracted from ``InputBuilderDialog``.

Phase 14A refactor: the body of :class:`InputBuilderDialog` (a ``QDialog``)
is lifted out as a plain :class:`QWidget` so it can be embedded into the
future ``SubmitPage``.  No behaviour change — the source
``InputBuilderDialog`` class is left in place and the existing tests still
exercise it.

The widget exposes ``build_content()`` / ``build_content_to()`` instead of
``accept()`` / ``reject()`` so embedding callers (e.g. ``SubmitPage``)
control the lifecycle.  The original preview/generate buttons stay wired
to convenience helpers that return / write content instead of closing a
dialog.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.input_builder import (
    GAUSSIAN_PRESETS,
    ORCA_PRESETS,
    GaussianInputSpec,
    OrcaInputSpec,
    build_from_preset,
    build_gjf,
    build_inp,
    list_presets,
)
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr


class InputBuilderWidget(QWidget):
    """Embedded version of :class:`InputBuilderDialog` minus the QDialog shell.

    Generates Gaussian ``.gjf`` or ORCA ``.inp`` from an XYZ file plus
    either a preset or manual method/basis/keywords.  Use :meth:`build_content`
    to get the rendered string and :meth:`build_content_to` to write it to
    disk; both surface validation problems as ``ValueError`` /
    ``FileNotFoundError`` so embedding callers can show them inline.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        xyz_path: str | Path | None = None,
        language: str = "en",
    ):
        super().__init__(parent)
        self._language = language
        self._output_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── XYZ source ────────────────────────────────────────────────────
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel(tr("XYZ file:", self._language)))
        self.xyz_edit = QLineEdit()
        self.xyz_edit.setPlaceholderText(tr("Path to .xyz file…", self._language))
        if xyz_path:
            self.xyz_edit.setText(str(xyz_path))
        src_row.addWidget(self.xyz_edit, 1)
        browse_btn = apply_button_role(
            QPushButton(tr("Browse…", self._language)), ButtonRole.INSTANT_ACTION
        )
        browse_btn.clicked.connect(self._browse_xyz)
        src_row.addWidget(browse_btn)
        layout.addLayout(src_row)

        # ── Software toggle ───────────────────────────────────────────────
        sw_row = QHBoxLayout()
        sw_row.addWidget(QLabel(tr("Software:", self._language)))
        self._sw_group = QButtonGroup(self)
        self.gauss_radio = QRadioButton(tr("Gaussian (.gjf)", self._language))
        self.orca_radio = QRadioButton(tr("ORCA (.inp)", self._language))
        self.gauss_radio.setChecked(True)
        self._sw_group.addButton(self.gauss_radio, 0)
        self._sw_group.addButton(self.orca_radio, 1)
        sw_row.addWidget(self.gauss_radio)
        sw_row.addWidget(self.orca_radio)
        sw_row.addStretch()
        layout.addLayout(sw_row)
        self._sw_group.idToggled.connect(self._on_sw_changed)

        # ── Preset selector ───────────────────────────────────────────────
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(tr("Preset:", self._language)))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(manual)", None)
        for name, _desc in sorted(list_presets().items()):
            self.preset_combo.addItem(name, name)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        # ── Manual parameters ─────────────────────────────────────────────
        self.manual_group = QGroupBox(tr("Manual parameters", self._language))
        form = QFormLayout(self.manual_group)
        form.setLabelAlignment(Qt.AlignRight)

        self.method_edit = QLineEdit("B3LYP/6-31G(d)")
        form.addRow(tr("Method/Basis:", self._language), self.method_edit)

        self.keywords_edit = QLineEdit("opt freq")
        form.addRow(tr("Keywords:", self._language), self.keywords_edit)

        charge_row = QHBoxLayout()
        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(-10, 10)
        charge_row.addWidget(self.charge_spin)
        charge_row.addWidget(QLabel(tr("Mult:", self._language)))
        self.mult_spin = QSpinBox()
        self.mult_spin.setRange(1, 10)
        self.mult_spin.setValue(1)
        charge_row.addWidget(self.mult_spin)
        charge_row.addStretch()
        form.addRow(tr("Charge:", self._language), charge_row)

        nproc_row = QHBoxLayout()
        self.nproc_spin = QSpinBox()
        self.nproc_spin.setRange(1, 256)
        self.nproc_spin.setValue(8)
        nproc_row.addWidget(self.nproc_spin)
        nproc_row.addWidget(QLabel(tr("Mem:", self._language)))
        self.mem_edit = QLineEdit("16GB")
        self.mem_edit.setMaximumWidth(80)
        nproc_row.addWidget(self.mem_edit)
        nproc_row.addStretch()
        form.addRow(tr("nproc:", self._language), nproc_row)

        layout.addWidget(self.manual_group)

        # ── Output path ───────────────────────────────────────────────────
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel(tr("Output:", self._language)))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText(
            tr("Leave blank to preview only", self._language)
        )
        out_row.addWidget(self.output_edit, 1)
        out_browse = apply_button_role(
            QPushButton(tr("Save as…", self._language)), ButtonRole.INSTANT_ACTION
        )
        out_browse.clicked.connect(self._browse_output)
        out_row.addWidget(out_browse)
        layout.addLayout(out_row)

        # ── Preview ───────────────────────────────────────────────────────
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(160)
        self.preview.setFontFamily("Courier New")
        layout.addWidget(self.preview)

        # ── Buttons ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self.preview_btn = apply_button_role(
            QPushButton(tr("Preview", self._language)), ButtonRole.INSTANT_ACTION
        )
        self.preview_btn.clicked.connect(self._do_preview)
        btn_row.addWidget(self.preview_btn)
        btn_row.addStretch()
        self.generate_btn = QPushButton(tr("Generate", self._language))
        self.generate_btn.setObjectName("PrimaryBtn")
        apply_button_role(self.generate_btn, ButtonRole.PRIMARY_ACTION)
        self.generate_btn.clicked.connect(self._do_generate)
        btn_row.addWidget(self.generate_btn)
        self.close_btn = apply_button_role(
            QPushButton(tr("Close", self._language)), ButtonRole.INSTANT_ACTION
        )
        # The original dialog wired Close to ``reject()``.  For the embedded
        # widget we just hide it; the embedding widget decides whether to
        # truly tear the widget down.
        self.close_btn.clicked.connect(self.hide)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

        self._on_sw_changed()

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def language(self) -> str:
        return self._language

    def apply_language(self, language: str) -> None:
        """Re-translate every static label on the widget."""
        self._language = language
        # Buttons + static labels — embedded callers call this after flipping
        # the global language setting.  Field placeholders / values stay put
        # (matches the original dialog's behaviour — it had no re-translation
        # path either, since dialogs are constructed once and shown modally).
        self.preview_btn.setText(tr("Preview", self._language))
        self.generate_btn.setText(tr("Generate", self._language))
        self.close_btn.setText(tr("Close", self._language))

    @property
    def xyz_path(self) -> str:
        """Current XYZ path text (may be empty)."""
        return self.xyz_edit.text().strip()

    def set_xyz_path(self, p: str | Path) -> None:
        """Set the XYZ source path."""
        self.xyz_edit.setText(str(p))

    @property
    def program(self) -> Literal["gaussian", "orca"]:
        """Selected software package as a normalised string."""
        return "orca" if self.orca_radio.isChecked() else "gaussian"

    @property
    def preset_name(self) -> str | None:
        """Selected preset name, or ``None`` for the manual entry."""
        return self.preset_combo.currentData()

    @property
    def output_path(self) -> Path | None:
        """Current output path text wrapped as :class:`Path` (or ``None``)."""
        out = self.output_edit.text().strip()
        return Path(out) if out else None

    def set_output_path(self, p: Path | None) -> None:
        """Set the output path text."""
        self.output_edit.setText("" if p is None else str(p))

    def build_content(self) -> str:
        """Render the input file body (Gaussian ``.gjf`` or ORCA ``.inp``).

        Raises ``ValueError`` if the XYZ path is empty and
        ``FileNotFoundError`` if the file does not exist — same behaviour
        as the original dialog.
        """
        return self._build_content(None)

    def build_content_to(self, path: Path) -> Path:
        """Render the input file and write it to ``path``.

        Returns ``path`` so the caller can chain.
        """
        content = self._build_content(path)
        # ``_build_content`` already writes when ``output_path`` is set.
        # We double-check by reading back from the path: if it's empty,
        # the build helpers returned content but didn't write (e.g. older
        # build_gjf variants).  In that case we persist explicitly.
        if not path.exists():
            path.write_text(content, encoding="utf-8")
        return path

    def generated_path(self) -> Path | None:
        """Return the output path if generation succeeded."""
        return self.output_path

    # ── Internal helpers (mirrors InputBuilderDialog behaviour) ───────────

    def _browse_xyz(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select XYZ file", "", "XYZ files (*.xyz);;All files (*)"
        )
        if path:
            self.xyz_edit.setText(path)

    def _browse_output(self) -> None:
        suffix = ".inp" if self.orca_radio.isChecked() else ".gjf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save input file", "",
            f"Input files (*{suffix});;All files (*)",
        )
        if path:
            self.output_edit.setText(path)

    def _on_sw_changed(self, *_) -> None:
        is_orca = self.orca_radio.isChecked()
        # Repopulate presets for the selected software
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("(manual)", None)
        presets = ORCA_PRESETS if is_orca else GAUSSIAN_PRESETS
        for name in sorted(presets):
            self.preset_combo.addItem(name, name)
        self.preset_combo.blockSignals(False)
        # mem field only relevant for Gaussian
        self.mem_edit.setEnabled(not is_orca)

    def _on_preset_changed(self, idx: int) -> None:
        preset_name = self.preset_combo.itemData(idx)
        if preset_name is None:
            self.manual_group.setEnabled(True)
            return
        self.manual_group.setEnabled(False)

    def _build_content(self, output_path: Path | None = None) -> str:
        xyz = self.xyz_edit.text().strip()
        if not xyz:
            raise ValueError("XYZ file path is required")
        xyz_path = Path(xyz)
        if not xyz_path.exists():
            raise FileNotFoundError(f"XYZ file not found: {xyz_path}")

        preset_name = self.preset_combo.currentData()
        if preset_name:
            return build_from_preset(xyz_path, preset_name, output_path)

        if self.orca_radio.isChecked():
            orca_spec = OrcaInputSpec(
                keywords=f"! {self.method_edit.text().strip()} {self.keywords_edit.text().strip()}",
                charge=self.charge_spin.value(),
                multiplicity=self.mult_spin.value(),
                nproc=self.nproc_spin.value(),
            )
            return build_inp(xyz_path, orca_spec, output_path)
        else:
            gauss_spec = GaussianInputSpec(
                method_basis=self.method_edit.text().strip(),
                job_keywords=self.keywords_edit.text().split(),
                charge=self.charge_spin.value(),
                multiplicity=self.mult_spin.value(),
                nproc=self.nproc_spin.value(),
                mem=self.mem_edit.text().strip(),
            )
            return build_gjf(xyz_path, gauss_spec, output_path)

    def _do_preview(self) -> str | None:
        """Render the preview into the embedded :class:`QTextEdit`.

        Returns the rendered content on success, ``None`` on error (the
        error message is shown in the preview pane verbatim).
        """
        try:
            content = self._build_content()
            self.preview.setPlainText(content)
            return content
        except Exception as exc:
            self.preview.setPlainText(f"Error: {exc}")
            return None

    def _do_generate(self) -> Path | None:
        """Generate to the configured output path.

        Returns the :class:`Path` on success, ``None`` on error.  Mirrors
        the original dialog's behaviour: if no output path is set, the
        preview is updated but nothing is written.
        """
        out_text = self.output_edit.text().strip()
        output_path = Path(out_text) if out_text else None
        try:
            content = self._build_content(output_path)
            self.preview.setPlainText(content)
            self._output_path = output_path
            return output_path
        except Exception as exc:
            self.preview.setPlainText(f"Error: {exc}")
            return None


__all__ = ["InputBuilderWidget"]