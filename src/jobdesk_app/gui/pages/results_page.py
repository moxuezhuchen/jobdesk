"""Results 页面 — 显示分析结果 TSV 和 summary.json，联动 Tasks。"""

import json
import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QLabel, QComboBox, QTextEdit, QHeaderView,
)

from ...core.manifest import Manifest
from ..table_models import load_rows_to_table, load_tsv_to_table


RESULT_TABLES = [
    "enriched_results",
    "final_results.tsv",
    "failures.tsv",
    "group_summary.tsv",
    "job_status.tsv",
]


def load_enriched_results_rows(
    final_results_path: Path,
    manifest_path: Path,
) -> tuple[list[str], list[list[str]]]:
    if not final_results_path.exists() or not manifest_path.exists():
        return [], []
    with open(final_results_path, "r", newline="", encoding="utf-8") as f:
        rows = [row for row in csv.reader(f, delimiter="\t") if row and any(row)]
    if not rows:
        return [], []
    base_header = rows[0]
    task_id_index = base_header.index("task_id") if "task_id" in base_header else -1
    metadata_header = [
        "discovery_name",
        "execution_profile",
        "server_id",
        "remote_work_dir",
        "status",
        "remote_job_dir",
    ]
    tasks = {task.task_id: task for task in Manifest.read(manifest_path)}
    enriched_rows: list[list[str]] = []
    for row in rows[1:]:
        task_id = row[task_id_index] if task_id_index >= 0 and task_id_index < len(row) else ""
        task = tasks.get(task_id)
        metadata = [
            task.discovery_name if task else "",
            task.execution_profile if task else "",
            task.server_id if task else "",
            task.remote_work_dir if task else "",
            task.status.value if task else "",
            task.remote_job_dir if task else "",
        ]
        enriched_rows.append(row + metadata)
    return base_header + metadata_header, enriched_rows


def build_results_diagnostics(batch_dir: Path, result_dir: Path) -> dict[str, str]:
    files = {
        "manifest.tsv": batch_dir / "manifest.tsv",
        "batch.json": batch_dir / "batch.json",
        "failures.tsv": batch_dir / "failures.tsv",
        "job_status.tsv": batch_dir / "job_status.tsv",
        "final_results.tsv": result_dir / "final_results.tsv",
        "group_summary.tsv": result_dir / "group_summary.tsv",
        "summary.json": result_dir / "summary.json",
    }
    return {
        name: f"{path} - {'present' if path.exists() else 'missing'}"
        for name, path in files.items()
    }


class ResultsPage(QWidget):
    def __init__(self, state, log_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        layout = QVBoxLayout(self)

        title = QLabel("Results")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(title)

        batch_row = QHBoxLayout()
        batch_row.addWidget(QLabel("Batch:"))
        self.batch_combo = QComboBox()
        self.batch_combo.currentTextChanged.connect(self._on_batch_changed)
        batch_row.addWidget(self.batch_combo)
        batch_row.addStretch()
        layout.addLayout(batch_row)

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("Table:"))
        self.file_combo = QComboBox()
        self.file_combo.addItems(RESULT_TABLES)
        self.file_combo.currentTextChanged.connect(self._load_table)
        file_row.addWidget(self.file_combo)
        file_row.addStretch()
        layout.addLayout(file_row)

        self.data_table = QTableWidget()
        layout.addWidget(self.data_table)

        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(100)
        layout.addWidget(QLabel("Summary:"))
        layout.addWidget(self.summary_text)

        btn_row = QHBoxLayout()
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._load_table)
        btn_row.addWidget(reload_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def on_activated(self):
        self.refresh_batch_list()

    def _ensure_ctx(self):
        ctx = self.state.current_project_context
        if ctx is None:
            return None
        return ctx

    def refresh_batch_list(self):
        ctx = self._ensure_ctx()
        if not ctx:
            return
        current = self.batch_combo.currentText()
        self.batch_combo.clear()
        seen = set()
        for base in [ctx.batches_dir, ctx.local_result_dir]:
            if base.exists():
                for d in sorted(base.iterdir(), reverse=True):
                    if d.is_dir() and d.name not in seen:
                        seen.add(d.name)
                        self.batch_combo.addItem(d.name)
        if current and current in seen:
            self.batch_combo.setCurrentText(current)

    def _on_batch_changed(self, bid: str):
        self.batch_combo.blockSignals(True)
        self.refresh_batch_list()
        self.batch_combo.blockSignals(False)
        if bid:
            self.state.current_batch_id = bid
        self._load_table()

    def _load_table(self):
        ctx = self._ensure_ctx()
        bid = self.state.current_batch_id
        if not ctx or not bid:
            return
        filename = self.file_combo.currentText()
        batch_dir = ctx.batches_dir / bid
        result_dir = ctx.local_result_dir / bid
        if filename == "enriched_results":
            header, rows = load_enriched_results_rows(
                result_dir / "final_results.tsv",
                batch_dir / "manifest.tsv",
            )
            load_rows_to_table(self.data_table, header, rows)
            self._load_summary_and_diagnostics(batch_dir, result_dir)
            return

        candidates = [
            batch_dir / filename,
            result_dir / filename,
        ]
        found = False
        for fp in candidates:
            if fp.exists():
                load_tsv_to_table(self.data_table, fp)
                found = True
                break
        if not found:
            self.data_table.clear()
            self.data_table.setRowCount(0)
            self.data_table.setColumnCount(0)

        self._load_summary_and_diagnostics(batch_dir, result_dir)

    def _load_summary_and_diagnostics(self, batch_dir: Path, result_dir: Path):
        summary_lines = []
        for bd in [batch_dir, result_dir]:
            sj = bd / "summary.json"
            if sj.exists():
                try:
                    data = json.loads(sj.read_text(encoding="utf-8"))
                    summary_lines.extend(f"{k}: {v}" for k, v in data.items())
                except Exception:
                    pass
                break
        diagnostics = build_results_diagnostics(batch_dir, result_dir)
        if summary_lines:
            summary_lines.append("")
        summary_lines.append("Diagnostics")
        summary_lines.extend(f"{k}: {v}" for k, v in diagnostics.items())
        self.summary_text.setPlainText("\n".join(summary_lines))
