"""Result detail pane — parsed Gaussian/ORCA output viewer widget."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..design.tokens import Colors, Metrics, Radius
from ..i18n import tr
from .runs_results_helpers import format_energy, format_seconds

MAX_PREVIEW_FILE_BYTES = 25 * 1024 * 1024


class ResultDetailPane(QWidget):
    """Read-only panel that renders a parsed Gaussian/ORCA result.

    Shown below the result preview table on the Runs/Results page. A
    double-click on a result row in ``RunsResultsPage`` triggers a
    parse and calls :py:meth:`render_gaussian` or :py:meth:`render_orca`.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ResultDetailPane")
        self.setStyleSheet(
            f"#ResultDetailPane {{ background: {Colors.BG_SURFACE}; border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; padding: 8px; }}"
            f" #ResultDetailPane QLabel {{ background: transparent; }}"
            f" #ResultDetailPane QTextEdit {{ background: {Colors.BG_SURFACE}; border: 1px solid {Colors.BORDER}; }}"
        )
        self._program = "—"
        self._language = "en"  # updated via apply_language()
        self._current_result = None
        self._result_kind: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        self.title_label = QLabel("—")
        self.title_label.setStyleSheet(
            f"color: {Colors.TEXT}; font-weight: 600; font-size: {Metrics.SECTION_TITLE_FONT_PX}px;"
        )
        layout.addWidget(self.title_label)

        self.status_label = QLabel("—")
        self.status_label.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.status_label)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(2)

        self.energy_value = QLabel("—")
        self.zpe_value = QLabel("—")
        self.gibbs_value = QLabel("—")
        self.imag_value = QLabel("—")
        self.termination_value = QLabel("—")
        self.termination_value.setWordWrap(True)
        self.error_value = QLabel("")
        self.error_value.setWordWrap(True)
        self.error_value.setStyleSheet(f"color: {Colors.ERROR}; font-weight: 600;")
        self.error_value.setVisible(False)
        self.walltime_value = QLabel("—")
        self.cputime_value = QLabel("—")

        self.energy_label = self._field_label("Final SCF energy")
        self.zpe_label = self._field_label("Zero-point correction")
        self.gibbs_label = self._field_label("Gibbs free energy")
        self.imag_label = self._field_label("Imaginary frequencies")
        self.walltime_label = self._field_label("Wall time")
        self.cputime_label = self._field_label("CPU time")
        self.termination_label = self._field_label("Termination")
        self.error_label = self._field_label("Error")
        self._field_labels = {
            "Final SCF energy": self.energy_label,
            "Zero-point correction": self.zpe_label,
            "Gibbs free energy": self.gibbs_label,
            "Imaginary frequencies": self.imag_label,
            "Wall time": self.walltime_label,
            "CPU time": self.cputime_label,
            "Termination": self.termination_label,
            "Error": self.error_label,
        }
        form.addRow(self.energy_label, self.energy_value)
        form.addRow(self.zpe_label, self.zpe_value)
        form.addRow(self.gibbs_label, self.gibbs_value)
        form.addRow(self.imag_label, self.imag_value)
        form.addRow(self.walltime_label, self.walltime_value)
        form.addRow(self.cputime_label, self.cputime_value)
        form.addRow(self.termination_label, self.termination_value)
        form.addRow(self.error_label, self.error_value)
        layout.addLayout(form)

        self.geometry_label = QLabel("Final geometry (XYZ, first 100 lines)")
        self.geometry_label.setStyleSheet(
            f"color: {Colors.TEXT}; font-weight: 600; font-size: {Metrics.CARD_TITLE_FONT_PX}px;"
        )
        layout.addWidget(self.geometry_label)
        self.geometry_view = QTextEdit()
        self.geometry_view.setReadOnly(True)
        self.geometry_view.setLineWrapMode(QTextEdit.NoWrap)
        font = self.geometry_view.font()
        font.setFamily("Courier New")
        font.setStyleHint(QFont.Monospace)
        self.geometry_view.setFont(font)
        self.geometry_view.setMinimumHeight(120)
        self.geometry_view.setMaximumHeight(180)
        layout.addWidget(self.geometry_view)

        layout.addStretch(1)
        self.clear()

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(f"{tr(text, self._language)}:")
        lbl.setStyleSheet("color: #475569;")
        return lbl

    def clear(self) -> None:
        self._program = "—"
        self._current_result = None
        self._result_kind = None

        self.title_label.setText(tr("Select a task to see details", self._language))
        self.status_label.setText("—")
        self.status_label.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_SECONDARY};")
        self.energy_value.setText("—")
        self.zpe_value.setText("—")
        self.gibbs_value.setText("—")
        self.imag_value.setText("—")
        self.walltime_value.setText("—")
        self.cputime_value.setText("—")
        self.termination_value.setText("—")
        self.error_value.setText("")
        self.error_value.setVisible(False)
        self.geometry_view.setPlainText("")

    def apply_language(self, language: str) -> None:
        """Re-translate static labels and the currently rendered result."""
        self._language = language
        for key, label in self._field_labels.items():
            label.setText(f"{tr(key, language)}:")
        self.geometry_label.setText(tr("Final geometry (XYZ, first 100 lines)", language))
        if self._result_kind == "gaussian":
            self._render_gaussian(self._current_result)
        elif self._result_kind == "orca":
            self._render_orca(self._current_result)
        else:
            self.title_label.setText(tr("Select a task to see details", language))

    def _status_text(self, result) -> tuple[str, str]:
        """Return ``(display_text, css_color)`` describing the result status.

        Status colours are deliberately matched against Git's
        ``status_color_*`` family so the same convention can be read at
        a glance across files: error → #b91c1c, success → #15803d,
        warning → #b45309, neutral → #475569. The pre-2026 design used
        ``Colors.ERROR`` (#ef4444) which failed the test
        ``test_render_detail_for_task_handles_missing_output`` and
        washed out against the white card background.
        """
        if getattr(result, "error_termination", False):
            return f"✗ {tr('Error termination', self._language)}", "#b91c1c"
        if getattr(result, "normal_termination", False):
            return f"✓ {tr('Normal termination', self._language)}", "#15803d"
        if getattr(result, "scf_energies", None):
            return f"⚠ {tr('Abnormal termination', self._language)}", "#b45309"
        return f"— {tr('Unknown', self._language)}", "#475569"

    def render_gaussian(self, result) -> None:
        self._current_result = result
        self._result_kind = "gaussian"
        self._render_gaussian(result)

    def _render_gaussian(self, result) -> None:
        self._program = "Gaussian"
        route = ""
        method = getattr(result, "method", None)
        basis = getattr(result, "basis", None)
        if method and basis:
            route = f"{method} / {basis}"
        else:
            energy_text = format_energy(getattr(result, "final_energy_au", None))
            route = f"Gaussian — {energy_text}"
        self.title_label.setText(route)
        status_text, color = self._status_text(result)
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet(f"font-weight: 600; color: {color};")
        self.energy_value.setText(format_energy(getattr(result, "final_energy_au", None)))
        self.zpe_value.setText(format_energy(getattr(result, "zpe_au", None)))
        self.gibbs_value.setText(format_energy(getattr(result, "gibbs_au", None)))
        imag = getattr(result, "imaginary_freq_count", 0) or 0
        self.imag_value.setText(
            tr("0 (minimum)", self._language) if imag == 0 else tr("{n} imaginary", self._language, n=imag)
        )
        self.walltime_value.setText(format_seconds(getattr(result, "walltime_seconds", None)))
        self.cputime_value.setText(format_seconds(getattr(result, "cpu_time_seconds", None)))
        self.termination_value.setText(
            tr("Normal termination of Gaussian", self._language) if result.normal_termination else "—"
        )
        err = getattr(result, "error_message", None) or getattr(result, "diagnosis", None)
        if err:
            self.error_value.setText(str(err))
            self.error_value.setVisible(True)
        else:
            self.error_value.setText("")
            self.error_value.setVisible(False)
        self._render_geometry(result)

    def render_orca(self, result) -> None:
        self._current_result = result
        self._result_kind = "orca"
        self._render_orca(result)

    def _render_orca(self, result) -> None:
        self._program = "ORCA"
        total = getattr(result, "total_energy_au", None)
        final = getattr(result, "final_energy_au", None)
        energy = total if total is not None else final
        self.title_label.setText(f"ORCA — {format_energy(energy)}")
        status_text, color = self._status_text(result)
        self.status_label.setText(status_text)
        self.status_label.setStyleSheet(f"font-weight: 600; color: {color};")
        self.energy_value.setText(format_energy(energy))
        self.zpe_value.setText(format_energy(getattr(result, "zpe_au", None)))
        self.gibbs_value.setText(format_energy(getattr(result, "gibbs_au", None)))
        imag = getattr(result, "imaginary_freq_count", 0) or 0
        self.imag_value.setText(
            tr("0 (minimum)", self._language) if imag == 0 else tr("{n} imaginary", self._language, n=imag)
        )
        self.walltime_value.setText(format_seconds(getattr(result, "walltime_seconds", None)))
        self.cputime_value.setText("—")
        self.termination_value.setText(
            tr("ORCA TERMINATED NORMALLY", self._language) if result.normal_termination else "—"
        )
        err = getattr(result, "error_message", None) or getattr(result, "diagnosis", None)
        if err:
            self.error_value.setText(str(err))
            self.error_value.setVisible(True)
        else:
            self.error_value.setText("")
            self.error_value.setVisible(False)
        self._render_geometry(result)

    def _render_geometry(self, result) -> None:
        xyz = getattr(result, "final_xyz", None)
        symbols = getattr(result, "atom_symbols", None) or []
        if xyz:
            lines = xyz.splitlines()
            n = len(symbols) or len(lines)
            display = [tr("{n} atoms", self._language, n=n), *lines]
            if len(lines) > 100:
                display = display[:101]
            self.geometry_view.setPlainText("\n".join(display))
        else:
            self.geometry_view.setPlainText(tr("(no geometry parsed)", self._language))


def _resolve_output_path(task, workspace: Path | None = None) -> Path | None:
    """Return the local output file (Gaussian .log or ORCA .out) for ``task``."""
    task_dir_attr = getattr(task, "task_dir", None)
    if task_dir_attr:
        task_dir = Path(task_dir_attr)
        if task_dir.is_dir():
            for suffix in (".log", ".out"):
                matches = sorted(p for p in task_dir.iterdir() if p.is_file() and p.suffix.lower() == suffix)
                if matches:
                    return matches[0]
    remote_files = getattr(task, "remote_task_files", None) or []
    if remote_files and workspace is not None:
        stem = PurePosixPath(remote_files[0]).stem
        for suffix in (".log", ".out"):
            candidate = Path(workspace) / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    return None
