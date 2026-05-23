"""Input file builder dialog — XYZ → GJF/INP with preset or manual parameters."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
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


class InputBuilderDialog(QDialog):
    """Dialog for generating Gaussian .gjf or ORCA .inp from an XYZ file."""

    def __init__(self, parent=None, xyz_path: str | Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("Input File Builder")
        self.setMinimumWidth(560)
        self._output_path: Path | None = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # ── XYZ source ────────────────────────────────────────────────────
        src_row = QHBoxLayout()
        src_row.addWidget(QLabel("XYZ file:"))
        self.xyz_edit = QLineEdit()
        self.xyz_edit.setPlaceholderText("Path to .xyz file…")
        if xyz_path:
            self.xyz_edit.setText(str(xyz_path))
        src_row.addWidget(self.xyz_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_xyz)
        src_row.addWidget(browse_btn)
        layout.addLayout(src_row)

        # ── Software toggle ───────────────────────────────────────────────
        sw_row = QHBoxLayout()
        sw_row.addWidget(QLabel("Software:"))
        self._sw_group = QButtonGroup(self)
        self.gauss_radio = QRadioButton("Gaussian (.gjf)")
        self.orca_radio = QRadioButton("ORCA (.inp)")
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
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(manual)", None)
        for name, desc in sorted(list_presets().items()):
            self.preset_combo.addItem(name, name)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        preset_row.addWidget(self.preset_combo, 1)
        layout.addLayout(preset_row)

        # ── Manual parameters ─────────────────────────────────────────────
        self.manual_group = QGroupBox("Parameters")
        form = QFormLayout(self.manual_group)
        form.setLabelAlignment(Qt.AlignRight)

        self.method_edit = QLineEdit("B3LYP/6-31G(d)")
        form.addRow("Method/Basis:", self.method_edit)

        self.keywords_edit = QLineEdit("opt freq")
        form.addRow("Keywords:", self.keywords_edit)

        charge_row = QHBoxLayout()
        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(-10, 10)
        charge_row.addWidget(self.charge_spin)
        charge_row.addWidget(QLabel("Mult:"))
        self.mult_spin = QSpinBox()
        self.mult_spin.setRange(1, 10)
        self.mult_spin.setValue(1)
        charge_row.addWidget(self.mult_spin)
        charge_row.addStretch()
        form.addRow("Charge:", charge_row)

        nproc_row = QHBoxLayout()
        self.nproc_spin = QSpinBox()
        self.nproc_spin.setRange(1, 256)
        self.nproc_spin.setValue(8)
        nproc_row.addWidget(self.nproc_spin)
        nproc_row.addWidget(QLabel("Mem:"))
        self.mem_edit = QLineEdit("16GB")
        self.mem_edit.setMaximumWidth(80)
        nproc_row.addWidget(self.mem_edit)
        nproc_row.addStretch()
        form.addRow("nproc:", nproc_row)

        layout.addWidget(self.manual_group)

        # ── Output path ───────────────────────────────────────────────────
        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output:"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Leave blank to preview only")
        out_row.addWidget(self.output_edit, 1)
        out_browse = QPushButton("Save as…")
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
        preview_btn = QPushButton("Preview")
        preview_btn.clicked.connect(self._do_preview)
        btn_row.addWidget(preview_btn)
        btn_row.addStretch()
        self.generate_btn = QPushButton("Generate")
        self.generate_btn.setObjectName("PrimaryBtn")
        self.generate_btn.clicked.connect(self._do_generate)
        btn_row.addWidget(self.generate_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._on_sw_changed()

    # ── helpers ───────────────────────────────────────────────────────────

    def _browse_xyz(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select XYZ file", "", "XYZ files (*.xyz);;All files (*)")
        if path:
            self.xyz_edit.setText(path)

    def _browse_output(self):
        suffix = ".inp" if self.orca_radio.isChecked() else ".gjf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save input file", "",
            f"Input files (*{suffix});;All files (*)",
        )
        if path:
            self.output_edit.setText(path)

    def _on_sw_changed(self, *_):
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

    def _on_preset_changed(self, idx: int):
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

    def _do_preview(self):
        try:
            content = self._build_content()
            self.preview.setPlainText(content)
        except Exception as exc:
            self.preview.setPlainText(f"Error: {exc}")

    def _do_generate(self):
        out_text = self.output_edit.text().strip()
        output_path = Path(out_text) if out_text else None
        try:
            content = self._build_content(output_path)
            self.preview.setPlainText(content)
            if output_path:
                self.accept()
        except Exception as exc:
            self.preview.setPlainText(f"Error: {exc}")

    def generated_path(self) -> Path | None:
        """Return the output path if generation succeeded."""
        out = self.output_edit.text().strip()
        return Path(out) if out else None
