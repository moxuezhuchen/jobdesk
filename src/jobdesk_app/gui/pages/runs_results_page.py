"""运行+结果合并页 — 上方 run 列表，下方结果预览。"""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QMessageBox,
    QMenu, QTextEdit,
)
from PySide6.QtCore import Qt

from ...config.servers import load_servers
from ...services.gui_settings import GuiSettingsStore
from ...services.run_service import RunRecord, RunService
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client


def _format_status(summary: dict[str, int]) -> str:
    if not summary:
        return ""
    return " | ".join(f"{k}={v}" for k, v in summary.items())


def _format_row(record: RunRecord) -> list[str]:
    return [
        record.run_id,
        record.server_id,
        record.remote_dir,
        _format_status(record.status_summary),
        record.command_template,
        record.created_at,
    ]


class RunsResultsPage(QWidget):
    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._language = GuiSettingsStore().load().language

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        splitter = QSplitter(Qt.Vertical)

        # ─── Top: Run list ───
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.currentCellChanged.connect(self._on_run_selected)
        self._restore_runs_column_widths()
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._save_runs_column_widths())
        top_layout.addWidget(self.table)

        # Buttons row
        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._refresh_all)
        btn_row.addWidget(self.refresh_btn)

        self.download_patterns = QLineEdit("*.log")
        self.download_patterns.setMaximumWidth(200)
        self.download_patterns.setPlaceholderText("下载模式")
        btn_row.addWidget(self.download_patterns)

        self.download_btn = QPushButton("下载")
        self.download_btn.clicked.connect(self._download_results)
        btn_row.addWidget(self.download_btn)
        self.retry_btn = QPushButton("重试")
        self.retry_btn.clicked.connect(self._retry_failed)
        btn_row.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._cancel_run)
        btn_row.addWidget(self.cancel_btn)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.clicked.connect(self._delete_run)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch()
        top_layout.addLayout(btn_row)
        splitter.addWidget(top)

        # ─── Bottom: Results preview ───
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(4)

        self.result_label = QLabel("结果预览")
        self.result_label.setStyleSheet("color: #475569;")
        bottom_layout.addWidget(self.result_label)

        self.result_table = QTableWidget()
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.verticalHeader().setVisible(False)
        bottom_layout.addWidget(self.result_table)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(80)
        self.result_text.setVisible(False)
        bottom_layout.addWidget(self.result_text)

        splitter.addWidget(bottom)
        splitter.setSizes([500, 150])
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

    def on_activated(self):
        self._language = GuiSettingsStore().load().language
        self.refresh_run_list()

    def apply_language(self, language: str):
        self._language = language
        self.refresh_btn.setText(tr("Refresh", language))
        self.download_btn.setText(tr("Download", language))
        self.retry_btn.setText(tr("Retry Failed", language))
        self.cancel_btn.setText(tr("Cancel", language))
        self.delete_btn.setText(tr("Delete", language))
        self._set_headers()

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels([
            "运行ID", "服务器", "远程目录", "状态", "命令", "创建时间",
        ])

    def _restore_runs_column_widths(self):
        from ...services.gui_settings import GuiSettingsStore
        widths = (GuiSettingsStore().load().column_widths or {}).get("runs_v2")
        if widths:
            for col, w in enumerate(widths):
                if col < self.table.columnCount() and w > 0:
                    self.table.setColumnWidth(col, w)

    def _save_runs_column_widths(self):
        from dataclasses import replace
        from ...services.gui_settings import GuiSettingsStore
        store = GuiSettingsStore()
        current = store.load()
        widths = dict(current.column_widths or {})
        widths["runs_v2"] = [
            self.table.columnWidth(c) for c in range(self.table.columnCount())
            if not self.table.isColumnHidden(c)
        ]
        store.save(replace(current, column_widths=widths))

    def _context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("重新运行", self._rerun_all)
        menu.addAction("显示日志", self._show_logs)
        menu.addAction("显示路径", self._show_paths)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _refresh_all(self):
        self.refresh_run_list()
        row = self.table.currentRow()
        if row >= 0:
            self._refresh_status()

    def refresh_run_list(self):
        workspace = self.state.current_project_root or Path.cwd()
        runs = RunService(workspace).list_runs()
        self._set_headers()
        self.table.setRowCount(len(runs))
        for row, record in enumerate(runs):
            for col, value in enumerate(_format_row(record)):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._status_cb(f"运行记录: {len(runs)}")

    def _workspace(self) -> Path:
        return Path(self.state.current_project_root or Path.cwd())

    def _selected_record(self) -> RunRecord | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        return RunService(self._workspace()).load_run(item.text())

    def _on_run_selected(self, row, col, prev_row, prev_col):
        """When a run is selected, show its results below."""
        record = self._selected_record()
        if record is None:
            self.result_table.setRowCount(0)
            return
        self._load_result_preview(record)

    def _load_result_preview(self, record: RunRecord):
        """Load TSV results or run analysis for the selected run."""
        workspace = self._workspace()
        result_dir = workspace / "results" / record.run_id
        # Try existing TSV first
        for name in ("final_results.tsv", "analysis_preview.tsv"):
            tsv = result_dir / name
            if tsv.exists():
                self._load_tsv(tsv)
                self.result_label.setText(f"结果预览 — {name}")
                return
        # Try auto-analysis on downloaded files
        if result_dir.exists():
            rows = self._auto_analyze(result_dir)
            if rows:
                self._show_analysis_rows(rows)
                self.result_label.setText("结果预览 — 自动分析")
                return
        # No results yet
        manifest_path = record.manifest_path
        if manifest_path and Path(manifest_path).exists() and not result_dir.exists():
            self.result_label.setText("⚠ 尚未下载结果")
            self.result_table.setRowCount(0)
        else:
            self.result_label.setText("结果预览 — 无数据")
            self.result_table.setRowCount(0)

    def _auto_analyze(self, result_dir: Path) -> list[list[str]]:
        """Auto-detect and parse Gaussian/ORCA output files."""
        from ...core.parsers.gaussian import parse_gaussian_log, GaussianResult
        from ...core.parsers.orca import parse_orca_out, OrcaResult
        rows: list[list[str]] = []
        # Scan all task subdirs
        dirs = sorted(d for d in result_dir.iterdir() if d.is_dir())
        if not dirs:
            # Flat files in result_dir
            dirs = [result_dir]
        for task_dir in dirs:
            # Gaussian .log
            for f in sorted(task_dir.glob("*.log")):
                if f.name.startswith(".jobdesk"):
                    continue
                try:
                    r = parse_gaussian_log(f)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([task_dir.name, f.name, "Gaussian", energy, gibbs,
                                 "是" if r.normal_termination else "否"])
                except Exception:
                    rows.append([task_dir.name, f.name, "Gaussian", "解析错误", "", ""])
            # ORCA .out
            for f in sorted(task_dir.glob("*.out")):
                try:
                    r = parse_orca_out(f)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([task_dir.name, f.name, "ORCA", energy, gibbs,
                                 "是" if r.normal_termination else "否"])
                except Exception:
                    rows.append([task_dir.name, f.name, "ORCA", "解析错误", "", ""])
        return rows

    def _show_analysis_rows(self, rows: list[list[str]]):
        headers = ["任务", "文件", "程序", "能量(Hartree)", "Gibbs(Hartree)", "正常结束"]
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                self.result_table.setItem(r, c, QTableWidgetItem(val))
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _load_tsv(self, path: Path):
        with open(path, "r", newline="", encoding="utf-8") as f:
            rows = [row for row in csv.reader(f, delimiter="\t") if row and any(row)]
        if not rows:
            self.result_table.setRowCount(0)
            return
        headers = rows[0]
        data = rows[1:]
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.setRowCount(len(data))
        for r, row in enumerate(data):
            for c, val in enumerate(row):
                self.result_table.setItem(r, c, QTableWidgetItem(val))
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _refresh_status(self):
        record = self._selected_record()
        if record is None:
            return
        RunService(self._workspace()).update_run_from_manifest(record.run_id)
        self.refresh_run_list()

    def _download_results(self):
        record = self._selected_record()
        if record is None:
            self._status_cb("请先选择一个运行记录")
            return
        raw = self.download_patterns.text().replace("\n", ",")
        patterns = [p.strip() for p in raw.split(",") if p.strip()]
        if not patterns:
            self._status_cb("请输入下载文件模式")
            return
        try:
            server = load_servers().servers[record.server_id]
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            try:
                records, failures = RunService(self._workspace()).download_completed(
                    record.run_id, sftp, patterns)
            finally:
                sftp.close()
                ssh.close()
            self.refresh_run_list()
            self._status_cb(f"下载完成: {len(records)} 文件, 失败: {len(failures)}")
        except Exception as exc:
            self._status_cb(f"下载失败: {exc}")

    def _retry_failed(self):
        record = self._selected_record()
        if record is None:
            return
        changed = RunService(self._workspace()).prepare_retry_failed(record.run_id)
        self.refresh_run_list()
        if changed <= 0:
            self._status_cb("没有失败的任务")
            return
        self._submit_record(record.run_id)

    def _rerun_all(self):
        record = self._selected_record()
        if record is None:
            return
        RunService(self._workspace()).prepare_rerun(record.run_id)
        self.refresh_run_list()
        self._submit_record(record.run_id)

    def _cancel_run(self):
        record = self._selected_record()
        if record is None:
            return
        if QMessageBox.question(self, "取消", f"取消运行 {record.run_id}?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        RunService(self._workspace()).mark_run_cancelled(record.run_id)
        self.refresh_run_list()
        self._status_cb(f"已取消: {record.run_id}")

    def _delete_run(self):
        record = self._selected_record()
        if record is None:
            return
        if QMessageBox.question(self, "删除", f"删除运行 {record.run_id} 及其结果?",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        RunService(self._workspace()).delete_run(record.run_id)
        self.refresh_run_list()
        self._status_cb(f"已删除: {record.run_id}")

    def _submit_record(self, run_id: str):
        workspace = self._workspace()
        record = RunService(workspace).load_run(run_id)

        def _run():
            server = load_servers().servers[record.server_id]
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            try:
                return RunService(workspace).submit_run(run_id, ssh, sftp)
            finally:
                sftp.close()
                ssh.close()

        from ..workers import BackgroundWorker
        self._worker = BackgroundWorker(_run)
        self._worker.result.connect(lambda r: self._on_submit_done(r))
        self._worker.error.connect(lambda e: self._status_cb(f"提交失败: {e}"))
        self._worker.start()

    def _on_submit_done(self, result):
        self.refresh_run_list()
        self._status_cb(f"已提交: {result.batch_id}")

    def _show_logs(self):
        record = self._selected_record()
        if record is None:
            return
        remote_dir = f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{record.run_id}"
        self.result_text.setPlainText(
            f"远程日志:\n  {remote_dir}/.jobdesk_submit.log\n  {remote_dir}/.jobdesk_submit.err")
        self.result_text.setVisible(True)

    def _show_paths(self):
        record = self._selected_record()
        if record is None:
            return
        ws = self._workspace()
        self.result_text.setPlainText(
            f"运行目录: {record.run_dir}\n"
            f"Manifest: {record.manifest_path}\n"
            f"结果目录: {ws / 'results' / record.run_id}")
        self.result_text.setVisible(True)

    def shutdown(self):
        w = getattr(self, "_worker", None)
        if w and hasattr(w, "stop_safely"):
            w.stop_safely()
