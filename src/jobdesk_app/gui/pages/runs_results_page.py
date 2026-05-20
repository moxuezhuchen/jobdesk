"""运行+结果合并页 — 上方 run 列表，下方结果预览。"""

from __future__ import annotations

import csv
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QMessageBox,
    QMenu, QTextEdit,
)
from PySide6.QtCore import Qt, QTimer

from ...config.servers import load_servers
from ...services.gui_settings import GuiSettingsStore
from ...services.run_service import RunRecord, RunService
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client


def _format_status(summary: dict[str, int], language: str = "zh") -> str:
    if not summary:
        return ""
    from ..i18n import tr
    _LABELS = {
        "local_ready": tr("Preparing", language),
        "uploaded": tr("Uploaded", language),
        "submitted": tr("Submitted", language),
        "running": tr("Running", language),
        "remote_completed": tr("Completed", language),
        "downloaded": tr("Downloaded", language),
        "analyzed": tr("Analyzed", language),
        "failed": tr("Failed", language),
    }
    parts = []
    total = sum(summary.values())
    for k, v in summary.items():
        label = _LABELS.get(k, k)
        parts.append(f"{label} {v}" if total > 1 else label)
    return " | ".join(parts)


def _format_row(record: RunRecord, language: str = "zh") -> list[str]:
    return [
        record.run_id,
        record.server_id,
        record.remote_dir,
        _format_status(record.status_summary, language),
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
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
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
        self.retry_btn = QPushButton(tr("Retry Failed", self._language))
        self.retry_btn.clicked.connect(self._retry_failed)
        btn_row.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton(tr("Cancel", self._language))
        self.cancel_btn.clicked.connect(self._cancel_run)
        btn_row.addWidget(self.cancel_btn)
        self.delete_btn = QPushButton(tr("Delete", self._language))
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

        self.result_label = QLabel(tr("Result Preview", self._language))
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

        # Auto-refresh timer for active runs
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh_active)
        self._refresh_timer.setInterval(15000)

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
        self._refresh_timer.start()

    def apply_language(self, language: str):
        self._language = language
        self.retry_btn.setText(tr("Retry Failed", language))
        self.cancel_btn.setText(tr("Cancel", language))
        self.delete_btn.setText(tr("Delete", language))
        self.result_label.setText(tr("Result Preview", language))
        self._set_headers()
        self.refresh_run_list()

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels([
            tr("Run ID", self._language), tr("Server", self._language), tr("Remote Dir", self._language),
            tr("Status", self._language), tr("Command", self._language), tr("Created At", self._language),
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
        menu.addAction(tr("Refresh Status", self._language), self._refresh_all)
        menu.addAction(tr("Rerun", self._language), self._rerun_all)
        menu.addAction(tr("Show Logs", self._language), self._show_logs)
        menu.addAction(tr("Show Paths", self._language), self._show_paths)
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
            for col, value in enumerate(_format_row(record, self._language)):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._status_cb(tr("Run records: {n}", self._language, n=len(runs)))

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
                    self.result_label.setText(tr("Result Preview — Auto Analysis", self._language))
                    return

        # Fallback: analyze output files in workspace root
        for base in candidates:
            rows = self._analyze_workspace_files(record, base)
            if rows:
                self._show_analysis_rows(rows)
                self.result_label.setText(tr("Result Preview — Local Files", self._language))
                return

        # Last resort: read existing TSV
        for base in candidates:
            result_dir = base / "results" / record.run_id
            for name in ("final_results.tsv", "analysis_preview.tsv"):
                tsv = result_dir / name
                if tsv.exists() and tsv.stat().st_size > 30:
                    self._load_tsv(tsv)
                    self.result_label.setText(f"{tr('Result Preview', self._language)} — {name}")
                    return

        self.result_label.setText(tr("No results downloaded yet", self._language))
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
                                 tr("Yes", self._language) if r.normal_termination else tr("No", self._language)])
                except Exception:
                    rows.append([task.task_id, log_file.name, "Gaussian", tr("Parse Error", self._language), "", ""])
            # Check .out (ORCA)
            out_file = workspace / f"{stem}.out"
            if out_file.is_file():
                found = True
                try:
                    r = parse_orca_out(out_file)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([task.task_id, out_file.name, "ORCA", energy, gibbs,
                                 tr("Yes", self._language) if r.normal_termination else tr("No", self._language)])
                except Exception:
                    rows.append([task.task_id, out_file.name, "ORCA", tr("Parse Error", self._language), "", ""])
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
                                 tr("Yes", self._language) if r.normal_termination else tr("No", self._language)])
                except Exception:
                    rows.append([stem, log_file.name, "Gaussian", tr("Parse Error", self._language), "", ""])
            # ORCA .out
            out_file = task_dir / f"{stem}.out"
            if out_file.is_file():
                try:
                    r = parse_orca_out(out_file)
                    energy = f"{r.final_energy_au:.6f}" if r.final_energy_au else ""
                    gibbs = f"{r.gibbs_au:.6f}" if r.gibbs_au else ""
                    rows.append([stem, out_file.name, "ORCA", energy, gibbs,
                                 tr("Yes", self._language) if r.normal_termination else tr("No", self._language)])
                except Exception:
                    rows.append([stem, out_file.name, "ORCA", tr("Parse Error", self._language), "", ""])
        return rows

    def _show_analysis_rows(self, rows: list[list[str]]):
        headers = [tr("Task", self._language), tr("File", self._language), tr("Program", self._language), tr("Energy(Hartree)", self._language), "Gibbs(Hartree)", tr("Normal Term", self._language)]
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

    def _auto_refresh_active(self):
        """Periodically refresh status for submitted/running runs."""
        if getattr(self, '_auto_refresh_running', False):
            return
        workspace = self._workspace()
        runs = RunService(workspace).list_runs()
        active = [r for r in runs if r.status_summary.get("submitted", 0) > 0 or r.status_summary.get("running", 0) > 0]
        if not active:
            return

        self._auto_refresh_running = True

        def _run():
            from ...remote.status_refresh import refresh_batch_status
            cfg = load_servers()
            for record in active:
                srv = cfg.servers.get(record.server_id)
                if not srv:
                    continue
                try:
                    ssh = create_ssh_client(srv)
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
                        updated = RunService(workspace).load_run(record.run_id)
                        if updated.status_summary.get("remote_completed", 0) > 0:
                            patterns = self._get_download_patterns(record)
                            RunService(workspace).download_completed(record.run_id, sftp, patterns)
                    finally:
                        sftp.close()
                        ssh.close()
                except Exception:
                    pass

        from ..workers import BackgroundWorker
        worker = BackgroundWorker(_run)

        def _on_done():
            self._auto_refresh_running = False
            if worker in self._bg_workers:
                self._bg_workers.remove(worker)
            self.refresh_run_list()

        worker.finished.connect(_on_done)
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        worker.start()

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
                    return tr("Download done: {n} files, failed: {f}", self._language, n=len(recs), f=len(fails))
            finally:
                sftp.close()
                ssh.close()

        from ..workers import BackgroundWorker
        self._worker = BackgroundWorker(_run)
        self._worker.result.connect(lambda msg: self._status_cb(msg) if msg else None)
        self._worker.error.connect(lambda e: self._status_cb(tr("Refresh failed: {e}", self._language, e=e)))
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
        profiles = settings.software_profiles or {}
        cmd = record.command_template.lower()
        for profile in profiles.values():
            tmpl = profile.get("command_template", "").lower()
            # Match if the profile's command base appears in the record's command
            base = tmpl.split()[0] if tmpl else ""
            if base and base in cmd:
                raw = profile.get("download_patterns", "")
                return [p.strip() for p in raw.split(",") if p.strip()]
        return [".log", ".out"]

    def _retry_failed(self):
        record = self._selected_record()
        if record is None:
            return
        changed = RunService(self._workspace()).prepare_retry_failed(record.run_id)
        self.refresh_run_list()
        if changed <= 0:
            self._status_cb(tr("No failed tasks", self._language))
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
        if QMessageBox.question(self, tr("Cancel", self._language), tr("Cancel run {run_id}?", self._language, run_id=record.run_id),
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        RunService(self._workspace()).mark_run_cancelled(record.run_id)
        self.refresh_run_list()
        self._status_cb(tr("Cancelled: {run_id}", self._language, run_id=record.run_id))

    def _delete_run(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            return
        run_ids = []
        for row in rows:
            item = self.table.item(row, 0)
            if item:
                run_ids.append(item.text())
        if not run_ids:
            return
        msg = tr("Delete {n} run records?", self._language, n=len(run_ids)) if len(run_ids) > 1 else tr("Delete run {run_id} and results?", self._language, run_id=run_ids[0])
        if QMessageBox.question(self, tr("Delete", self._language), msg,
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        svc = RunService(self._workspace())
        for rid in run_ids:
            try:
                svc.delete_run(rid)
            except Exception:
                pass
        self.refresh_run_list()
        self._status_cb(tr("Deleted: {n} records", self._language, n=len(run_ids)))

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
        self._worker.error.connect(lambda e: self._status_cb(tr("Submit failed: {e}", self._language, e=e)))
        self._worker.start()

    def _on_submit_done(self, result):
        self.refresh_run_list()
        self._status_cb(tr("Submitted: {batch_id}", self._language, batch_id=result.batch_id))
        self._start_monitoring()

    def _show_logs(self):
        record = self._selected_record()
        if record is None:
            return
        remote_dir = f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{record.run_id}"
        self.result_text.setPlainText(
            f"{tr('Remote logs', self._language)}:\n  {remote_dir}/.jobdesk_submit.log\n  {remote_dir}/.jobdesk_submit.err")
        self.result_text.setVisible(True)

    def _show_paths(self):
        record = self._selected_record()
        if record is None:
            return
        ws = self._workspace()
        self.result_text.setPlainText(
            f"{tr('Run directory', self._language)}: {record.run_dir}\n"
            f"Manifest: {record.manifest_path}\n"
            f"{tr('Results directory', self._language)}: {ws / 'results' / record.run_id}")
        self.result_text.setVisible(True)

    def shutdown(self):
        self._refresh_timer.stop()
        self._monitor.stop_all()
        for w in list(getattr(self, "_bg_workers", [])):
            w.stop_safely(500)
        w = getattr(self, "_worker", None)
        if w and hasattr(w, "stop_safely"):
            w.stop_safely()
