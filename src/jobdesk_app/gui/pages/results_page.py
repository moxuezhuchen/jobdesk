"""Results page — RunService-based analysis, profile selector, cross-run comparison, CSV export."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFileDialog, QListWidget, QAbstractItemView, QSplitter,
    QMessageBox,
)
from PySide6.QtCore import Qt

from ...services.run_service import RunService
from ...services.analysis_profiles import AnalysisProfileStore
from ...services.comparison import compare_runs, export_csv, export_markdown
from ..i18n import tr
from ..table_models import load_rows_to_table


def _fill_table(table: QTableWidget, field_names: list[str], rows: list[dict]) -> None:
    table.clear()
    table.setColumnCount(len(field_names))
    table.setHorizontalHeaderLabels(field_names)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, key in enumerate(field_names):
            table.setItem(r, c, QTableWidgetItem(str(row.get(key, ""))))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)


class ResultsPage(QWidget):
    def __init__(self, state, log_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._language = "en"
        self._last_comparison = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        # ── top controls ──────────────────────────────────────────────────
        ctrl = QHBoxLayout()

        self.profile_label = QLabel()
        ctrl.addWidget(self.profile_label)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        ctrl.addWidget(self.profile_combo)

        self.analyze_btn = QPushButton()
        self.analyze_btn.clicked.connect(self._analyze_selected)
        ctrl.addWidget(self.analyze_btn)

        self.compare_btn = QPushButton()
        self.compare_btn.clicked.connect(self._compare_selected)
        ctrl.addWidget(self.compare_btn)

        ctrl.addStretch()

        self.export_csv_btn = QPushButton()
        self.export_csv_btn.clicked.connect(self._export_csv)
        ctrl.addWidget(self.export_csv_btn)

        self.export_md_btn = QPushButton()
        self.export_md_btn.clicked.connect(self._export_markdown)
        ctrl.addWidget(self.export_md_btn)

        layout.addLayout(ctrl)

        # ── splitter: run list | result table ─────────────────────────────
        splitter = QSplitter(Qt.Horizontal)

        self.run_list = QListWidget()
        self.run_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.run_list.setMaximumWidth(260)
        self.run_list.setMinimumWidth(140)
        splitter.addWidget(self.run_list)

        self.result_table = QTableWidget()
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.verticalHeader().setVisible(False)
        splitter.addWidget(self.result_table)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        # ── status label ──────────────────────────────────────────────────
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(self.status_label)

        self._populate_profiles()
        self.apply_language(self._language)

    # ── public API ────────────────────────────────────────────────────────

    def on_activated(self):
        self._populate_profiles()
        self._refresh_run_list()

    def apply_language(self, language: str):
        self._language = language
        self.profile_label.setText(tr("Profile:", language))
        self.analyze_btn.setText(tr("Analyze", language))
        self.compare_btn.setText(tr("Compare Runs", language))
        self.export_csv_btn.setText(tr("Export CSV", language))
        self.export_md_btn.setText(tr("Copy Markdown", language))

    # ── internal ──────────────────────────────────────────────────────────

    def _workspace(self) -> Path:
        return Path(self.state.current_project_root or Path.cwd())

    def _populate_profiles(self):
        current = self.profile_combo.currentText()
        self.profile_combo.clear()
        for name in AnalysisProfileStore().list_names():
            self.profile_combo.addItem(name)
        if current:
            idx = self.profile_combo.findText(current)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)

    def _refresh_run_list(self):
        self.run_list.clear()
        try:
            runs = RunService(self._workspace()).list_runs()
        except Exception:
            return
        for r in runs:
            summary = " | ".join(f"{k}={v}" for k, v in r.status_summary.items())
            self.run_list.addItem(f"{r.run_id[:12]}  {summary}")
            self.run_list.item(self.run_list.count() - 1).setData(Qt.UserRole, r.run_id)

    def _selected_run_ids(self) -> list[str]:
        return [
            item.data(Qt.UserRole)
            for item in self.run_list.selectedItems()
            if item.data(Qt.UserRole)
        ]

    def _analyze_selected(self):
        run_ids = self._selected_run_ids()
        if not run_ids:
            self.status_label.setText(tr("Select one or more runs first", self._language))
            return
        profile_name = self.profile_combo.currentText()
        if not profile_name:
            self.status_label.setText(tr("Select a profile first", self._language))
            return

        from ...core.analyzer import analyze_tasks
        from ...core.manifest import Manifest

        workspace = self._workspace()
        svc = RunService(workspace)
        profile = AnalysisProfileStore().get(profile_name)
        if profile is None:
            self.status_label.setText(f"Profile not found: {profile_name}")
            return

        all_rows: list[dict] = []
        field_set: list[str] = []
        seen_fields: set[str] = set()

        for run_id in run_ids:
            try:
                record = svc.load_run(run_id)
                tasks = Manifest.read(record.manifest_path)
                results, _ = analyze_tasks(
                    profile.extract_rules, tasks,
                    workspace / "results", run_id,
                )
            except Exception as exc:
                self._log(f"Analyze {run_id}: {exc}")
                continue

            task_data: dict[str, dict] = {}
            for r in results:
                if r.task_id not in task_data:
                    task_data[r.task_id] = {"run_id": run_id, "task_id": r.task_id}
                task_data[r.task_id][r.field_name] = r.value
                if r.field_name not in seen_fields:
                    seen_fields.add(r.field_name)
                    field_set.append(r.field_name)
            all_rows.extend(task_data.values())

        if not all_rows:
            self.status_label.setText(tr("No results found", self._language))
            return

        fields = ["run_id", "task_id"] + field_set
        _fill_table(self.result_table, fields, all_rows)
        self._last_comparison = None
        self.status_label.setText(
            tr("{n} rows from {r} run(s)", self._language, n=len(all_rows), r=len(run_ids))
        )

    def _compare_selected(self):
        run_ids = self._selected_run_ids()
        if len(run_ids) < 2:
            self.status_label.setText(tr("Select at least 2 runs to compare", self._language))
            return
        profile_name = self.profile_combo.currentText()
        from ...services.comparison import compare_runs
        comparison = compare_runs(self._workspace(), run_ids, profile_name=profile_name)
        if not comparison.rows:
            self.status_label.setText(tr("No results found", self._language))
            return
        _fill_table(self.result_table, comparison.field_names, comparison.rows)
        self._last_comparison = comparison
        self.status_label.setText(
            tr("{n} rows compared across {r} runs", self._language,
               n=len(comparison.rows), r=len(run_ids))
        )

    def _export_csv(self):
        if self._last_comparison is None:
            self.status_label.setText(tr("Run Compare Runs first", self._language))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export CSV", self._language), "", "CSV files (*.csv)"
        )
        if not path:
            return
        export_csv(self._last_comparison, path)
        self.status_label.setText(f"Exported: {path}")

    def _export_markdown(self):
        if self._last_comparison is None:
            self.status_label.setText(tr("Run Compare Runs first", self._language))
            return
        from PySide6.QtWidgets import QApplication
        md = export_markdown(self._last_comparison)
        QApplication.clipboard().setText(md)
        self.status_label.setText(tr("Markdown copied to clipboard", self._language))
