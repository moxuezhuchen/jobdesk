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

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Signal
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
    QVBoxLayout,
    QWizard,
    QWizardPage,
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

_PROGRAMS = ("gaussian", "orca")
_DEFAULT_STEPS = ("confgen", "preopt", "opt", "refine", "sp")


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
    def __init__(self, parent: QDialog | None = None):
        super().__init__(parent)
        self.setTitle("Input XYZ files")
        self.setSubTitle("Pick one or more .xyz files. ConfFlow will run each independently.")

        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        layout.addWidget(self.list, 1)

        btn_row = QHBoxLayout()
        add_btn = apply_button_role(QPushButton("Add…"), ButtonRole.INSTANT_ACTION)
        add_btn.clicked.connect(self._add)
        rm_btn = apply_button_role(QPushButton("Remove"), ButtonRole.INSTANT_ACTION)
        rm_btn.clicked.connect(self._remove)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._xyz_paths: list[Path] = []

    def _add(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select XYZ files", "", "XYZ files (*.xyz);;All files (*)"
        )
        for raw in files:
            p = Path(raw)
            if any(existing == p for existing in self._xyz_paths):
                continue
            self._xyz_paths.append(p)
            item = QListWidgetItem(str(p))
            self.list.addItem(item)

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            row = self.list.row(item)
            del self._xyz_paths[row]
            self.list.takeItem(row)

    def xyz_paths(self) -> list[Path]:
        return list(self._xyz_paths)

    def isComplete(self) -> bool:  # type: ignore[override]
        return bool(self._xyz_paths)


class _CalcPage(QWizardPage):
    def __init__(self, parent: QDialog | None = None):
        super().__init__(parent)
        self.setTitle("Calculation settings")
        self.setSubTitle("Program, method/basis, charge, resources.")

        form = QFormLayout(self)

        self.program_combo = QComboBox()
        self.program_combo.addItems(_PROGRAMS)
        form.addRow("Program:", self.program_combo)

        self.method_edit = QLineEdit("B3LYP")
        form.addRow("Method:", self.method_edit)

        self.basis_edit = QLineEdit("6-31G(d)")
        form.addRow("Basis:", self.basis_edit)

        self.charge_spin = QSpinBox()
        self.charge_spin.setRange(-10, 10)
        form.addRow("Charge:", self.charge_spin)

        self.mult_spin = QSpinBox()
        self.mult_spin.setRange(1, 10)
        self.mult_spin.setValue(1)
        form.addRow("Multiplicity:", self.mult_spin)

        self.nproc_spin = QSpinBox()
        self.nproc_spin.setRange(1, 256)
        self.nproc_spin.setValue(8)
        form.addRow("CPU cores:", self.nproc_spin)

        self.mem_spin = QSpinBox()
        self.mem_spin.setRange(256, 1_000_000)
        self.mem_spin.setSingleStep(512)
        self.mem_spin.setValue(4096)
        self.mem_spin.setSuffix(" MB")
        form.addRow("Memory:", self.mem_spin)

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


class _WorkflowPage(QWizardPage):
    """Step list + work_dir + advanced options + YAML preview."""

    dry_run_done = Signal(object)  # DryRunReport

    def __init__(self, parent: QDialog | None = None):
        super().__init__(parent)
        self.setTitle("Workflow settings & preview")
        self.setSubTitle("Pick steps, set work_dir, then preview & validate the YAML.")

        layout = QVBoxLayout(self)

        # Steps
        steps_box = QGroupBox("Steps")
        sb_layout = QHBoxLayout(steps_box)
        self._step_checks: dict[str, QCheckBox] = {}
        for step in _DEFAULT_STEPS:
            cb = QCheckBox(step)
            cb.setChecked(True)
            self._step_checks[step] = cb
            sb_layout.addWidget(cb)
        sb_layout.addStretch()
        layout.addWidget(steps_box)

        # work_dir
        wd_row = QHBoxLayout()
        wd_row.addWidget(QLabel("Work dir name:"))
        self.work_dir_edit = QLineEdit("{basename}_confflow_work")
        self.work_dir_edit.setPlaceholderText("{basename}_confflow_work")
        wd_row.addWidget(self.work_dir_edit, 1)
        layout.addLayout(wd_row)

        # Advanced options (raw key=value lines; parsed on accept)
        adv = QGroupBox("Advanced options (key=value, one per line)")
        adv_layout = QVBoxLayout(adv)
        self.adv_edit = QTextEdit()
        self.adv_edit.setPlaceholderText("# examples:\n# solvent=water\n# scan=true")
        self.adv_edit.setMaximumHeight(80)
        adv_layout.addWidget(self.adv_edit)
        layout.addWidget(adv)

        # Preview + dry-run
        preview_box = QGroupBox("YAML preview")
        pv_layout = QVBoxLayout(preview_box)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFontFamily("Courier New")
        self.preview.setMinimumHeight(200)
        pv_layout.addWidget(self.preview)
        btn_row = QHBoxLayout()
        self.refresh_btn = apply_button_role(
            QPushButton("Refresh preview"), ButtonRole.INSTANT_ACTION
        )
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        btn_row.addWidget(self.refresh_btn)
        self.status_label = QLabel("")
        btn_row.addWidget(self.status_label, 1)
        pv_layout.addLayout(btn_row)
        layout.addWidget(preview_box, 1)

        self._last_spec: WorkflowSpec | None = None
        self._last_report: DryRunReport | None = None

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
            self.status_label.setText(f"Render failed: {exc}")
            self._last_report = None
            return
        self._last_spec = spec
        self._last_report = report
        if report.ok:
            self.status_label.setText("✓ YAML valid")
        else:
            self.status_label.setText(f"✗ {report.error}")

    def _on_refresh_clicked(self) -> None:
        calc_page = self.wizard().calc_page  # type: ignore[attr-defined]
        try:
            spec = self.build_spec(calc_page.calc_fields())
        except Exception as exc:
            self.status_label.setText(f"Build failed: {exc}")
            return
        self.render_preview(spec)


class ConfFlowWizard(QWizard):
    """QWizard wrapper that produces a :class:`WizardResult` on accept."""

    def __init__(
        self,
        parent: QDialog | None = None,
        *,
        server_id: str = "",
        remote_dir: str = "",
        default_workflow_yaml: str | Path | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("ConfFlow Workflow Wizard")
        self.setMinimumSize(760, 620)
        self._server_id = server_id
        self._remote_dir = remote_dir

        self.xyz_page = _XyzPage()
        self.calc_page = _CalcPage()
        self.workflow_page = _WorkflowPage()
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
                "ConfFlow Wizard",
                "Server, remote directory, and at least one XYZ file are required.",
            )
            return
        super().accept()


__all__ = ["ConfFlowWizard", "WizardResult"]
