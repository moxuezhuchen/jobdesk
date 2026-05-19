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
    _LABELS = {
        "local_ready": "准备中",
        "uploaded": "已上传",
        "submitted": "已提交",
        "running": "运行中",
        "remote_completed": "已完成",
        "downloaded": "已下载",
        "analyzed": "已分析",
        "failed": "失败",
    }
    parts = []
    total = sum(summary.values())
    for k, v in summary.items():
        label = _LABELS.get(k, k)
        parts.append(f"{label} {v}" if total > 1 else label)
    return " | ".join(parts)


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
        self.table.setStyleSheet(
            "QTableWidget { background: transparent; border: none;"
            " alternate-background-color: transparent; gridline-color: #94a3b8; }"
            " QTableWidget::item { background: transparent; }"
        )
        self.table.horizontalHeader().setStyleSheet(
            "QHeaderView { background: transparent; }"
            " QHeaderView::section { background: transparent; border: none;"
            " border-bottom: 1px solid #94a3b8; border-right: 1px solid #94a3b8; }"
        )
        self._restore_runs_column_widths()
        self.table.horizontalHeader().sectionResized.connect(lambda *_: self._save_runs_column_widths())

        table_card = QWidget()
        table_card.setObjectName("RunsTableCard")
        table_card.setStyleSheet(
            "#RunsTableCard { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; }"
        )
        table_card_layout = QVBoxLayout(table_card)
        table_card_layout.setContentsMargins(16, 12, 16, 12)
        table_card_layout.addWidget(self.table)
        top_layout.addWidget(table_card, 1)

        # Buttons row (card style)
        btn_card = QWidget()
        btn_card.setObjectName("BtnCard")
        btn_card.setStyleSheet(
            "#BtnCard { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; }"
            " #BtnCard QPushButton { background: #cbd5e1; border: 1px solid #94a3b8;"
            " padding: 0 16px; border-radius: 4px; min-height: 44px; max-height: 44px; }"
            " #BtnCard QPushButton:pressed { background: #93c5fd; border-color: #3b82f6; }"
            " #BtnCard QLineEdit { background: #cbd5e1; border: 1px solid #94a3b8;"
            " border-radius: 4px; padding: 0 8px; min-height: 44px; max-height: 44px; }"
        )
        btn_card.setFixedHeight(60)
        btn_row = QHBoxLayout(btn_card)
        btn_row.setContentsMargins(16, 0, 16, 0)
        self.retry_btn = QPushButton("重试失败项")
        self.retry_btn.clicked.connect(self._retry_failed)
        btn_row.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self._cancel_run)
        btn_row.addWidget(self.cancel_btn)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.clicked.connect(self._delete_run)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch()
        top_layout.addWidget(btn_card)
        splitter.addWidget(top)

        # ─── Bottom: Results preview ───
        bottom = QWidget()
        bottom.setObjectName("ResultsCard")
        bottom.setStyleSheet(
            "#ResultsCard { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; }"
            " #ResultsCard QLabel { background: transparent; }"
            " #ResultsCard QTableWidget { background: transparent; border: none;"
            "   alternate-background-color: transparent; gridline-color: #94a3b8; }"
            " #ResultsCard QTableWidget::item { background: transparent; }"
            " #ResultsCard QHeaderView { background: transparent; }"
            " #ResultsCard QHeaderView::section { background: transparent; border: none;"
            "   border-bottom: 1px solid #94a3b8; border-right: 1px solid #94a3b8; }"
            " #ResultsCard QTextEdit { background: transparent; border: none; }"
        )
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 12, 16, 12)
        bottom_layout.setSpacing(4)

        self.result_label = QLabel("结果预览")
        self.result_label.setStyleSheet("color: #0f172a; font-weight: 600;")
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

        # Real-time task completion monitor
        from ...services.run_monitor import RunMonitor
        self._monitor = RunMonitor(self)
        self._monitor.task_done.connect(self._on_task_done)
        self._bg_workers: list = []

    def _start_monitoring(self):
        """Watch all running runs."""
        try:
            runs = RunService(self._workspace()).list_runs()
            cfg = load_servers()
            for record in runs:
                if record.status_summary.get("running", 0) > 0 or record.status_summary.get("submitted", 0) > 0:
                    srv = cfg.servers.get(record.server_id)
                    if srv:
                        batch_dir = f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{record.run_id}"
                        self._monitor.watch(record.run_id, record.server_id, batch_dir, srv)
        except Exception:
            pass

    def _on_task_done(self, event):
        """Called when a remote task changes state — refresh in background."""
        workspace = self._workspace()

        def _run():
            from ...remote.status_refresh import refresh_batch_status
            record = RunService(workspace).load_run(event.run_id)
            server = load_servers().servers[record.server_id]
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            try:
                refresh_batch_status(
                    ssh=ssh,
                    manifest_path=record.manifest_path,
                    remote_batch_dir=f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{record.run_id}",
                    batch_id=record.run_id,
                    write=True,
                )
                RunService(workspace).update_run_from_manifest(record.run_id)
                # Only download on DONE (exit_code is not None)
                if event.exit_code is not None:
                    updated = RunService(workspace).load_run(record.run_id)
                    if updated.status_summary.get("remote_completed", 0) > 0:
                        patterns = self._get_download_patterns(record)
                        RunService(workspace).download_completed(record.run_id, sftp, patterns)
                    RunService(workspace).update_run_from_manifest(record.run_id)
            finally:
                sftp.close()
                ssh.close()

        from ..workers import BackgroundWorker
        w = BackgroundWorker(_run)
        w.finished.connect(lambda: self._on_monitor_refresh_done(event))
        w.finished.connect(lambda: self._bg_workers.remove(w) if w in self._bg_workers else None)
        w.finished.connect(w.deleteLater)
        self._bg_workers.append(w)
        w.start()

    def _on_monitor_refresh_done(self, event):
        self.refresh_run_list()
        try:
            updated = RunService(self._workspace()).load_run(event.run_id)
            if updated.status_summary.get("running", 0) == 0 and updated.status_summary.get("submitted", 0) == 0:
                self._monitor.unwatch(event.run_id, event.server_id)
        except Exception:
            pass

    def on_activated(self):
        self._language = GuiSettingsStore().load().language
        self.refresh_run_list()
        self._start_monitoring()

    def apply_language(self, language: str):
        self._language = language
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
        menu.addAction("刷新状态", self._refresh_all)
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
        from ...services.gui_settings import GuiSettingsStore
        workspace = self._workspace()
        candidates = [workspace]
        default_folder = GuiSettingsStore().load().default_local_folder
        if default_folder and Path(default_folder) != workspace:
            candidates.append(Path(default_folder))

        # Prefer auto-analysis on downloaded files
        for base in candidates:
            result_dir = base / "results" / record.run_id
            if result_dir.exists():
                rows = self._auto_analyze(result_dir)
                if rows:
                    self._show_analysis_rows(rows)
                    self.result_label.setText("结果预览 — 自动分析")
                    return

        # Fallback: analyze output files in workspace root
        for base in candidates:
            rows = self._analyze_workspace_files(record, base)
            if rows:
                self._show_analysis_rows(rows)
                self.result_label.setText("结果预览 — 本地文件")
                return

        # Last resort: read existing TSV
        for base in candidates:
            result_dir = base / "results" / record.run_id
            for name in ("final_results.tsv", "analysis_preview.tsv"):
                tsv = result_dir / name
                if tsv.exists() and tsv.stat().st_size > 30:
                    self._load_tsv(tsv)
                    self.result_label.setText(f"结果预览 — {name}")
                    return

        self.result_label.setText("⚠ 尚未下载结果")
        self.result_table.setRowCount(0)

    def _analyze_workspace_files(self, record: RunRecord, workspace: Path) -> list[list[str]]:
        """Analyze output files directly from workspace if they exist locally."""
        from ...core.manifest import Manifest
        from ...core.lifecycle import TaskStatus
        from ...core.parsers.gaussian import parse_gaussian_log
        from ...core.parsers.orca import parse_orca_out
        manifest_path = record.manifest_path
        if not manifest_path or not Path(manifest_path).exists():
            return []
        tasks = list(Manifest.read(Path(manifest_path)))
        rows: list[list[str]] = []
        changed = False
        for task in tasks:
            if not task.remote_task_files:
                continue
            source = task.remote_task_files[0]
            stem = source.rsplit(".", 1)[0] if "." in source else source
            found = False
            # Check .log (Gaussian)
            log_file = workspace / f"{stem}.log"
            if log_file.is_file():
                found = True
                try:
                    r = parse_gaussian_log(log_file)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([task.task_id, log_file.name, "Gaussian", energy, gibbs,
                                 "是" if r.normal_termination else "否"])
                except Exception:
                    rows.append([task.task_id, log_file.name, "Gaussian", "解析错误", "", ""])
            # Check .out (ORCA)
            out_file = workspace / f"{stem}.out"
            if out_file.is_file():
                found = True
                try:
                    r = parse_orca_out(out_file)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([task.task_id, out_file.name, "ORCA", energy, gibbs,
                                 "是" if r.normal_termination else "否"])
                except Exception:
                    rows.append([task.task_id, out_file.name, "ORCA", "解析错误", "", ""])
            if found and task.status == TaskStatus.remote_completed:
                task.status = TaskStatus.downloaded
                changed = True
        if changed:
            Manifest.write(Path(manifest_path), tasks)
            RunService(workspace).update_run_from_manifest(record.run_id)
            self.refresh_run_list()
        return rows

    def _auto_analyze(self, result_dir: Path) -> list[list[str]]:
        """Auto-detect and parse Gaussian/ORCA output files matching task stem."""
        from ...core.parsers.gaussian import parse_gaussian_log
        from ...core.parsers.orca import parse_orca_out
        rows: list[list[str]] = []
        dirs = sorted(d for d in result_dir.iterdir() if d.is_dir())
        if not dirs:
            dirs = [result_dir]
        for task_dir in dirs:
            stem = task_dir.name  # task_id == stem of source file
            # Gaussian .log
            log_file = task_dir / f"{stem}.log"
            if log_file.is_file():
                try:
                    r = parse_gaussian_log(log_file)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([stem, log_file.name, "Gaussian", energy, gibbs,
                                 "是" if r.normal_termination else "否"])
                except Exception:
                    rows.append([stem, log_file.name, "Gaussian", "解析错误", "", ""])
            # ORCA .out
            out_file = task_dir / f"{stem}.out"
            if out_file.is_file():
                try:
                    r = parse_orca_out(out_file)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([stem, out_file.name, "ORCA", energy, gibbs,
                                 "是" if r.normal_termination else "否"])
                except Exception:
                    rows.append([stem, out_file.name, "ORCA", "解析错误", "", ""])
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
        workspace = self._workspace()
        run_id = record.run_id

        def _run():
            from ...remote.status_refresh import refresh_batch_status
            server = load_servers().servers[record.server_id]
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            try:
                refresh_batch_status(
                    ssh=ssh,
                    manifest_path=record.manifest_path,
                    remote_batch_dir=f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{run_id}",
                    batch_id=run_id,
                    write=True,
                )
                RunService(workspace).update_run_from_manifest(run_id)
                updated = RunService(workspace).load_run(run_id)
                if updated.status_summary.get("remote_completed", 0) > 0:
                    patterns = self._get_download_patterns(record)
                    recs, fails = RunService(workspace).download_completed(run_id, sftp, patterns)
                    return f"下载完成: {len(recs)} 文件, 失败: {len(fails)}"
            finally:
                sftp.close()
                ssh.close()

        from ..workers import BackgroundWorker
        self._worker = BackgroundWorker(_run)
        self._worker.result.connect(lambda msg: self._status_cb(msg) if msg else None)
        self._worker.error.connect(lambda e: self._status_cb(f"刷新失败: {e}"))
        self._worker.finished.connect(lambda: self._on_refresh_done())
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _on_refresh_done(self):
        self.refresh_run_list()
        record = self._selected_record()
        if record:
            self._load_result_preview(record)

    def _get_download_patterns(self, record: RunRecord) -> list[str]:
        """Get download patterns based on command template (auto-detect software)."""
        settings = GuiSettingsStore().load()
        patterns_map = settings.software_download_patterns or {}
        cmd = record.command_template.lower()
        if "g16" in cmd or "g09" in cmd or "gaussian" in cmd:
            raw = patterns_map.get("Gaussian", "*.log,*.chk")
        elif "orca" in cmd:
            raw = patterns_map.get("ORCA", "*.out,*.gbw")
        else:
            raw = "*.log,*.out"
        return [p.strip() for p in raw.split(",") if p.strip()]

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
        self._start_monitoring()

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
        self._monitor.stop_all()
        w = getattr(self, "_worker", None)
        if w and hasattr(w, "stop_safely"):
            w.stop_safely()
