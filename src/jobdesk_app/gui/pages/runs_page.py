from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

from ...config.servers import load_servers
from ...services.gui_settings import GuiSettingsStore
from ...services.run_service import RunRecord, RunService
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client


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
        layout = QVBoxLayout(self)

        self.title = QLabel()
        self.title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(self.title)

        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.table)

        download_row = QHBoxLayout()
        self.download_label = QLabel()
        download_row.addWidget(self.download_label)
        self.download_patterns = QLineEdit("result.log, output.log, .jobdesk_submit.log")
        download_row.addWidget(self.download_patterns, 1)
        layout.addLayout(download_row)

        btns = QHBoxLayout()
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.refresh_run_list)
        btns.addWidget(self.refresh_btn)
        self.refresh_status_btn = QPushButton()
        self.refresh_status_btn.clicked.connect(self._refresh_status)
        btns.addWidget(self.refresh_status_btn)
        self.download_btn = QPushButton()
        self.download_btn.clicked.connect(self._download_results)
        btns.addWidget(self.download_btn)
        self.retry_btn = QPushButton()
        self.retry_btn.clicked.connect(self._retry_failed)
        btns.addWidget(self.retry_btn)
        self.rerun_btn = QPushButton()
        self.rerun_btn.clicked.connect(self._rerun_all)
        btns.addWidget(self.rerun_btn)
        self.logs_btn = QPushButton()
        self.logs_btn.clicked.connect(self._show_logs)
        btns.addWidget(self.logs_btn)
        self.details_btn = QPushButton()
        self.details_btn.clicked.connect(self._show_paths)
        btns.addWidget(self.details_btn)
        btns.addStretch()
        layout.addLayout(btns)
        self.apply_language(self._language)

    def on_activated(self):
        self.apply_language(GuiSettingsStore().load().language)
        self.refresh_run_list()

    def apply_language(self, language: str):
        self._language = language
        self.title.setText(tr("Runs", language))
        self.download_label.setText(tr("Download files:", language))
        self.refresh_btn.setText(tr("Refresh List", language))
        self.refresh_status_btn.setText(tr("Refresh Status", language))
        self.download_btn.setText(tr("Download", language))
        self.retry_btn.setText(tr("Retry Failed", language))
        self.rerun_btn.setText(tr("Rerun", language))
        self.logs_btn.setText(tr("Show Logs", language))
        self.details_btn.setText(tr("Show Paths", language))
        self.table.setHorizontalHeaderLabels([
            tr("run_id", language), tr("server", language), tr("remote_dir", language),
            tr("mode", language), tr("Max parallel", language), tr("status", language),
            tr("command", language), tr("created_at", language),
        ])

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
            try:
                return RunService(workspace).submit_run(record.run_id, ssh, sftp)
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

    def shutdown(self):
        worker = getattr(self, "worker", None)
        if worker is not None and hasattr(worker, "stop_safely"):
            worker.stop_safely()


class _BackgroundRunWorker:
    def __new__(cls, target):
        from ..workers import BackgroundWorker
        return BackgroundWorker(target)
