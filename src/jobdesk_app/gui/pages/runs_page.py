from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QCheckBox,
    QSpinBox,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon

from ...config.servers import load_servers
from ...services.gui_settings import GuiSettingsStore
from ...services.run_service import RunRecord, RunService
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client
from ..workers import BackgroundWorker as _BackgroundRunWorker


def _send_notification(title: str, message: str) -> None:
    """Send a Windows system tray notification if possible."""
    try:
        from PySide6.QtWidgets import QSystemTrayIcon, QApplication
        from PySide6.QtGui import QIcon
        app = QApplication.instance()
        if app is None:
            return
        tray = QSystemTrayIcon(app)
        tray.setIcon(app.windowIcon() or QIcon())
        tray.show()
        tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
    except Exception:
        pass  # Notifications are best-effort


def format_run_status_summary(summary: dict[str, int]) -> str:
    if not summary:
        return "(none)"
    return " | ".join(f"{key}={value}" for key, value in summary.items())


def format_run_row(record: RunRecord) -> list[str]:
    return [
        record.run_id,
        record.server_id,
        record.remote_dir,
        record.mode,
        str(record.max_parallel),
        format_run_status_summary(record.status_summary),
        record.command_template,
        record.created_at,
    ]


def parse_download_patterns(text: str) -> list[str]:
    raw = text.replace("\n", ",").split(",")
    return [part.strip() for part in raw if part.strip()]


def run_log_paths(record: RunRecord) -> list[str]:
    remote_batch_dir = f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{record.run_id}"
    return [
        f"{remote_batch_dir}/.jobdesk_submit.log",
        f"{remote_batch_dir}/.jobdesk_submit.err",
    ]


class RunsPage(QWidget):
    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._language = GuiSettingsStore().load().language
        self._background_workers = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        # Hide low-value columns: mode(3), max_parallel(4)
        self.table.setColumnHidden(3, True)
        self.table.setColumnHidden(4, True)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.table)

        # Workflow toolbar
        wf_row = QHBoxLayout()
        self.new_workflow_btn = QPushButton()
        self.new_workflow_btn.clicked.connect(self._start_workflow)
        wf_row.addWidget(self.new_workflow_btn)
        wf_row.addStretch()
        layout.addLayout(wf_row)

        download_row = QHBoxLayout()
        self.download_label = QLabel()
        download_row.addWidget(self.download_label)
        self.download_patterns = QLineEdit("result.log, output.log, .jobdesk_submit.log")
        download_row.addWidget(self.download_patterns, 1)
        layout.addLayout(download_row)

        # Auto-refresh controls
        auto_row = QHBoxLayout()
        self.auto_refresh_check = QCheckBox()
        self.auto_refresh_check.toggled.connect(self._on_auto_refresh_toggled)
        auto_row.addWidget(self.auto_refresh_check)
        self.auto_refresh_interval = QSpinBox()
        self.auto_refresh_interval.setRange(10, 3600)
        self.auto_refresh_interval.setValue(30)
        self.auto_refresh_interval.setSuffix(" s")
        auto_row.addWidget(self.auto_refresh_interval)
        self.auto_download_check = QCheckBox()
        auto_row.addWidget(self.auto_download_check)
        self.notify_check = QCheckBox()
        auto_row.addWidget(self.notify_check)
        auto_row.addStretch()
        layout.addLayout(auto_row)

        btns = QHBoxLayout()
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self._refresh_all)
        btns.addWidget(self.refresh_btn)
        self.download_btn = QPushButton()
        self.download_btn.clicked.connect(self._download_results)
        btns.addWidget(self.download_btn)
        self.retry_btn = QPushButton()
        self.retry_btn.clicked.connect(self._retry_failed)
        btns.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton()
        self.cancel_btn.clicked.connect(self._cancel_run)
        btns.addWidget(self.cancel_btn)
        self.delete_btn = QPushButton()
        self.delete_btn.clicked.connect(self._delete_run)
        btns.addWidget(self.delete_btn)
        btns.addStretch()
        layout.addLayout(btns)

        # Auto-refresh timer
        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._auto_refresh_tick)
        self._downloading_run_ids: set[str] = set()

        self.apply_language(self._language)
        self._restore_state()

    def _restore_state(self):
        s = GuiSettingsStore().load()
        self.download_patterns.setText(s.download_patterns)
        self.auto_refresh_interval.setValue(s.auto_refresh_interval)
        self.auto_download_check.setChecked(s.auto_download_enabled)
        self.notify_check.setChecked(s.notify_enabled)
        # Restore auto-refresh last (triggers timer start if checked)
        self.auto_refresh_check.setChecked(s.auto_refresh_enabled)

    def _save_state(self):
        from dataclasses import replace
        store = GuiSettingsStore()
        current = store.load()
        store.save(replace(current,
            download_patterns=self.download_patterns.text(),
            auto_refresh_enabled=self.auto_refresh_check.isChecked(),
            auto_refresh_interval=self.auto_refresh_interval.value(),
            auto_download_enabled=self.auto_download_check.isChecked(),
            notify_enabled=self.notify_check.isChecked(),
        ))

    def _context_menu(self, pos):
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.addAction(tr("Rerun", self._language), self._rerun_all)
        menu.addAction(tr("Show Logs", self._language), self._show_logs)
        menu.addAction(tr("Show Paths", self._language), self._show_paths)
        menu.addSeparator()
        menu.addAction(tr("Analyze Run", self._language), self._analyze_run)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def on_activated(self):
        self.apply_language(GuiSettingsStore().load().language)
        self.refresh_run_list()

    def apply_language(self, language: str):
        self._language = language
        self.download_label.setText(tr("Download files:", language))
        self.refresh_btn.setText(tr("Refresh", language))
        self.download_btn.setText(tr("Download", language))
        self.retry_btn.setText(tr("Retry Failed", language))
        self.cancel_btn.setText(tr("Cancel", language))
        self.delete_btn.setText(tr("Delete", language))
        self.auto_refresh_check.setText(tr("Auto-refresh", language))
        self.auto_download_check.setText(tr("Auto-download", language))
        self.notify_check.setText(tr("Notify on complete", language))
        self.new_workflow_btn.setText(tr("New Workflow…", language))
        self.table.setHorizontalHeaderLabels([
            tr("run_id", language), tr("server", language), tr("remote_dir", language),
            tr("mode", language), tr("Max parallel", language), tr("status", language),
            tr("command", language), tr("created_at", language),
        ])

    def _on_auto_refresh_toggled(self, checked: bool):
        if checked:
            interval_ms = self.auto_refresh_interval.value() * 1000
            self._auto_timer.start(interval_ms)
            self._status_cb(f"Auto-refresh every {self.auto_refresh_interval.value()}s")
        else:
            self._auto_timer.stop()
            self._status_cb("Auto-refresh stopped")

    def _auto_refresh_tick(self):
        """Called by QTimer: refresh all active runs, auto-download if enabled."""
        self.refresh_run_list()
        if not self.auto_download_check.isChecked():
            return
        # Auto-download any newly completed runs
        workspace = Path(self.state.current_project_root or Path.cwd())
        for record in RunService(workspace).list_runs():
            if record.status_summary.get("remote_completed", 0) > 0:
                self._auto_download_run(record)

    def _auto_download_run(self, record: RunRecord):
        """Download results for a completed run in the background."""
        if record.run_id in self._downloading_run_ids:
            return
        self._downloading_run_ids.add(record.run_id)
        workspace = Path(self.state.current_project_root or Path.cwd())
        patterns = parse_download_patterns(self.download_patterns.text())
        if not patterns:
            return

        def _run():
            try:
                server = load_servers().servers[record.server_id]
            except KeyError:
                return None
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            try:
                recs, failures = RunService(workspace).download_completed(record.run_id, sftp, patterns)
                return (record.run_id, recs, failures)
            finally:
                sftp.close()
                ssh.close()

        worker = _BackgroundRunWorker(_run)
        worker.result.connect(lambda r: self._on_auto_download_done(r))
        worker.error.connect(lambda e: self._log(f"Auto-download error: {e}"))
        self._background_workers.append(worker)
        worker.start()

    def _on_auto_download_done(self, result):
        if result is None:
            return
        run_id, records, failures = result
        self._downloading_run_ids.discard(run_id)
        transferred = sum(1 for r in records if r.status.value == "transferred")
        if transferred > 0:
            self.refresh_run_list()
            self._log(f"Auto-downloaded {transferred} file(s) for run {run_id}")
            if self.notify_check.isChecked():
                _send_notification(f"JobDesk: run {run_id} complete", f"Downloaded {transferred} file(s)")

    def shutdown(self):
        self._save_state()
        self._auto_timer.stop()
        for w in self._background_workers:
            if hasattr(w, "stop_safely"):
                w.stop_safely()

    def _refresh_all(self):
        """Refresh list, and update status of selected run if any."""
        self.refresh_run_list()
        row = self.table.currentRow()
        if row >= 0:
            self._refresh_status()

    def refresh_run_list(self):
        workspace = self.state.current_project_root or Path.cwd()
        runs = RunService(workspace).list_runs()
        self.table.setRowCount(len(runs))
        for row, record in enumerate(runs):
            for col, value in enumerate(format_run_row(record)):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._status_cb(f"Runs: {len(runs)}")

    def _workspace(self) -> Path:
        return Path(self.state.current_project_root or Path.cwd())

    def _selected_record(self) -> RunRecord | None:
        row = self.table.currentRow()
        if row < 0:
            self._status_cb("Select a run first")
            return None
        run_id_item = self.table.item(row, 0)
        if run_id_item is None:
            self._status_cb("Select a run first")
            return None
        return RunService(self._workspace()).load_run(run_id_item.text())

    def _refresh_status(self):
        record = self._selected_record()
        if record is None:
            return
        updated = RunService(self._workspace()).update_run_from_manifest(record.run_id)
        self.refresh_run_list()
        self._status_cb(f"Status refreshed: {updated.run_id}")

    def _download_results(self):
        record = self._selected_record()
        if record is None:
            return
        patterns = parse_download_patterns(self.download_patterns.text())
        if not patterns:
            self._status_cb("Enter download file names first")
            return
        try:
            server = load_servers().servers[record.server_id]
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            try:
                records, failures = RunService(self._workspace()).download_completed(record.run_id, sftp, patterns)
            finally:
                sftp.close()
                ssh.close()
            for task_id, reason in failures:
                self._log(f"Download failed for {task_id}: {reason}")
            self.refresh_run_list()
            self._status_cb(f"Downloaded {len(records)} file(s), failures={len(failures)}")
        except Exception as exc:
            self._status_cb(f"Download failed: {exc}")
            self._log(f"Download failed: {exc}")

    def _retry_failed(self):
        record = self._selected_record()
        if record is None:
            return
        changed = RunService(self._workspace()).prepare_retry_failed(record.run_id)
        self.refresh_run_list()
        if changed <= 0:
            self._status_cb("No failed tasks to retry")
            return
        self._submit_record(record.run_id, f"Retrying {changed} failed task(s)")

    def _rerun_all(self):
        record = self._selected_record()
        if record is None:
            return
        changed = RunService(self._workspace()).prepare_rerun(record.run_id)
        self.refresh_run_list()
        self._submit_record(record.run_id, f"Rerunning {changed} task(s)")

    def _submit_record(self, run_id: str, label: str):
        workspace = self._workspace()
        record = RunService(workspace).load_run(run_id)

        def _run():
            server = load_servers().servers[record.server_id]
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            from ...services.scheduler_helpers import scheduler_from_server, resources_from_server
            try:
                return RunService(workspace).submit_run(
                    record.run_id, ssh, sftp,
                    env_init_scripts=list(getattr(server, "env_init_scripts", []) or []),
                    scheduler=scheduler_from_server(server),
                    resources=resources_from_server(server),
                )
            finally:
                sftp.close()
                ssh.close()

        self._status_cb(f"{label}: {run_id}")
        self.worker = _BackgroundRunWorker(_run)
        self.worker.result.connect(self._on_submit_done)
        self.worker.error.connect(lambda error: self._log(f"Submit failed: {error}"))
        self.worker.error.connect(lambda error: self._status_cb(f"Submit failed: {error}"))
        self.worker.start()

    def _on_submit_done(self, result):
        self.refresh_run_list()
        self._log(f"Run submitted: {result.batch_id}, tasks={result.submitted_task_count}, errors={len(result.errors)}")
        for error in result.errors:
            self._log(f"  {error}")
        self._status_cb(f"Submitted {result.batch_id}")

    def _cancel_run(self):
        record = self._selected_record()
        if record is None:
            return
        if QMessageBox.question(
            self, "Cancel Run", f"Cancel run {record.run_id}?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            RunService(self._workspace()).mark_run_cancelled(record.run_id)
            self.refresh_run_list()
            self._status_cb(f"Cancelled: {record.run_id}")
        except Exception as exc:
            self._status_cb(f"Cancel failed: {exc}")

    def _delete_run(self):
        record = self._selected_record()
        if record is None:
            return
        if QMessageBox.question(
            self, "Delete Run", f"Delete run {record.run_id} and its results?",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            RunService(self._workspace()).delete_run(record.run_id)
            self.refresh_run_list()
            self._status_cb(f"Deleted: {record.run_id}")
        except Exception as exc:
            self._status_cb(f"Delete failed: {exc}")

    def _show_logs(self):
        record = self._selected_record()
        if record is None:
            return
        self._log(f"Run {record.run_id} remote logs")
        for path in run_log_paths(record):
            self._log(f"  {path}")

    def _show_paths(self):
        record = self._selected_record()
        if record is None:
            return
        self._log(f"Run {record.run_id}")
        self._log(f"  manifest: {record.manifest_path}")
        self._log(f"  batch: {record.batch_path}")
        self._log(f"  dir: {record.run_dir}")
        self._log(f"  results: {self._workspace() / 'results' / record.run_id}")

    def _analyze_run(self):
        """Analyze selected run and navigate to Results page."""
        record = self._selected_record()
        if record is None:
            return
        # Store run_id in state so Results page can pre-select it
        self.state.current_batch_id = record.run_id
        # Navigate to Results tab (index 2) via parent MainWindow
        mw = self.window()
        if hasattr(mw, "shell"):
            mw.shell.set_current(2)
            results_page = mw.shell.pages.widget(2)
            if hasattr(results_page, "on_activated"):
                results_page.on_activated()
        self._status_cb(f"Analyzing run: {record.run_id}")

    def _start_workflow(self):
        """Open WorkflowDialog and launch a workflow."""
        from ..dialogs.workflow_dialog import WorkflowDialog
        from ...services.workflow_service import WorkflowRunner, BUILTIN_WORKFLOWS
        from ...core.run import RunSpec, RunMode, RunSource

        dlg = WorkflowDialog(self, workspace=self._workspace())
        if dlg.exec() != WorkflowDialog.Accepted:
            return

        wf_name = dlg.workflow_name()
        spec = BUILTIN_WORKFLOWS.get(wf_name)
        if spec is None:
            self._status_cb(f"Unknown workflow: {wf_name}")
            return

        workspace = self._workspace()
        runner = WorkflowRunner(workspace)
        wf_run = runner.create(spec, dlg.server_id(), dlg.remote_dir(), dlg.input_file())

        # Advance: create first-step runs
        started = runner.advance(spec, wf_run)
        if not started:
            self._status_cb("Workflow: no steps ready to start")
            return

        # Submit each created run
        for step_name in started:
            run_id = wf_run.step_run_ids.get(step_name)
            if run_id:
                self._submit_record(run_id, f"Workflow {wf_name} step {step_name}")

        self.refresh_run_list()
        self._status_cb(f"Workflow {wf_name} started: {len(started)} step(s)")

    def shutdown(self):
        worker = getattr(self, "worker", None)
        if worker is not None and hasattr(worker, "stop_safely"):
            worker.stop_safely()


