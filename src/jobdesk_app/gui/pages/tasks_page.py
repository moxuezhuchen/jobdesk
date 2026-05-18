"""Tasks 页面 — Scan/Create/Upload/Submit/Refresh/Download/Analyze。

支持 mixed-profile batch 的分组操作。
"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QLabel, QComboBox, QHeaderView, QMessageBox,
)

from ..table_models import load_tsv_to_table
from ..workers import BackgroundWorker
from ..session import create_ssh_client, create_sftp_client
from ...services.workflow_service import WorkflowService
from ...config.runtime import RuntimeBindingStore, resolve_execution_contexts_for_project
from ...config.servers import load_servers
from ...core.manifest import Manifest
from ...core.lifecycle import TaskStatus
from ...core.models import BatchSummary
from ...core.transfer import TransferRecord, TransferStatus
from ...services.preflight import PreflightReport


def summarize_task_statuses(tasks) -> dict[str, int]:
    counts = {status.value: 0 for status in TaskStatus}
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    return counts


def format_status_summary(counts: dict[str, int]) -> str:
    parts = [
        f"Local Ready: {counts.get('local_ready', 0)}",
        f"Uploaded: {counts.get('uploaded', 0)}",
        f"Submitted: {counts.get('submitted', 0)}",
        f"Running: {counts.get('running', 0)}",
        f"Completed: {counts.get('remote_completed', 0)}",
        f"Downloaded: {counts.get('downloaded', 0)}",
        f"Failed: {counts.get('failed', 0)}",
    ]
    return " | ".join(parts)


def format_batch_header(summary: BatchSummary, manifest_path: str) -> str:
    profiles = ", ".join(summary.execution_profiles) if summary.execution_profiles else "(none)"
    servers = ", ".join(summary.server_ids) if summary.server_ids else "(none)"
    return (
        f"{summary.batch_id} | tasks={summary.task_count} | profiles={profiles} | "
        f"servers={servers} | shared={summary.shared_files_count} | "
        f"manifest={manifest_path}"
    )


def build_button_reasons(statuses: set[TaskStatus]) -> dict[str, str]:
    return {
        "upload": "" if TaskStatus.local_ready in statuses else "No local_ready tasks",
        "submit": "" if TaskStatus.uploaded in statuses else "No uploaded tasks",
        "refresh": "" if (TaskStatus.submitted in statuses or TaskStatus.running in statuses)
        else "No submitted or running tasks",
        "download": "" if TaskStatus.remote_completed in statuses else "No remote_completed tasks",
        "analyze": "" if statuses else "No batch tasks",
    }


def format_transfer_summary(
    operation: str,
    records: list[TransferRecord],
    failure_count: int = 0,
) -> str:
    transferred = sum(1 for r in records if r.status == TransferStatus.transferred)
    skipped = sum(1 for r in records if r.status == TransferStatus.skipped)
    failed = sum(1 for r in records if r.status == TransferStatus.failed)
    return (
        f"{operation} complete: {transferred} transferred, {skipped} skipped, "
        f"{failed} failed, {failure_count} recorded failures"
    )


def format_preflight_report(report: PreflightReport) -> list[str]:
    state = "passed" if report.ok else "failed"
    lines = [
        f"Preflight {state}: {len(report.errors)} errors, {len(report.warnings)} warnings",
        (
            f"  tasks={report.task_count}, profiles={report.profiles or []}, "
            f"servers={report.servers or []}"
        ),
    ]
    for issue in report.errors:
        lines.append(f"ERROR {issue.code}: {issue.message}")
    for issue in report.warnings:
        lines.append(f"WARNING {issue.code}: {issue.message}")
    return lines


def _make_connected_sftp(server_config):
    """创建已连接的 SFTP 客户端 (用于 factory)。"""
    if server_config is None:
        return None
    ssh = create_ssh_client(server_config)
    ssh.connect()
    sftp = create_sftp_client(ssh)
    return sftp


class TasksPage(QWidget):
    def __init__(self, state, log_cb, status_cb, error_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._error_cb = error_cb
        self._binding_store = RuntimeBindingStore()
        layout = QVBoxLayout(self)

        title = QLabel("Tasks")
        title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        layout.addWidget(title)

        br = QHBoxLayout()
        br.addWidget(QLabel("Batch:"))
        self.batch_combo = QComboBox()
        self.batch_combo.currentTextChanged.connect(self._on_batch_changed)
        br.addWidget(self.batch_combo)
        br.addStretch()
        layout.addLayout(br)

        self.status_summary_label = QLabel("")
        layout.addWidget(self.status_summary_label)
        self.batch_header_label = QLabel("")
        self.batch_header_label.setWordWrap(True)
        layout.addWidget(self.batch_header_label)

        self.task_table = QTableWidget()
        layout.addWidget(self.task_table)

        b1 = QHBoxLayout()
        self.preflight_btn = QPushButton("Preflight"); self.preflight_btn.clicked.connect(self._preflight); b1.addWidget(self.preflight_btn)
        self.scan_btn = QPushButton("Scan Inputs"); self.scan_btn.clicked.connect(self._scan_inputs); b1.addWidget(self.scan_btn)
        self.create_btn = QPushButton("Create Batch"); self.create_btn.clicked.connect(self._create_batch); b1.addWidget(self.create_btn)
        self.upload_btn = QPushButton("Upload"); self.upload_btn.setEnabled(False); self.upload_btn.clicked.connect(self._upload); b1.addWidget(self.upload_btn)
        self.submit_btn = QPushButton("Submit"); self.submit_btn.setEnabled(False); self.submit_btn.clicked.connect(self._submit); b1.addWidget(self.submit_btn)
        self.refresh_btn = QPushButton("Refresh"); self.refresh_btn.setEnabled(False); self.refresh_btn.clicked.connect(self._refresh); b1.addWidget(self.refresh_btn)
        self.download_btn = QPushButton("Download"); self.download_btn.setEnabled(False); self.download_btn.clicked.connect(self._download); b1.addWidget(self.download_btn)
        self.analyze_btn = QPushButton("Analyze"); self.analyze_btn.setEnabled(False); self.analyze_btn.clicked.connect(self._analyze_batch); b1.addWidget(self.analyze_btn)
        b1.addStretch(); layout.addLayout(b1)

        b2 = QHBoxLayout()
        d1 = QPushButton("Dry-run Upload"); d1.clicked.connect(self._dry_upload); b2.addWidget(d1)
        d2 = QPushButton("Dry-run Submit"); d2.clicked.connect(self._dry_submit); b2.addWidget(d2)
        d3 = QPushButton("Dry-run Download"); d3.clicked.connect(self._dry_download); b2.addWidget(d3)
        b2.addStretch(); layout.addLayout(b2)

    def on_activated(self):
        self.refresh_batch_list()
        self._update_buttons()

    def _ensure_ctx(self):
        ctx = self.state.current_project_context
        if ctx is None:
            self._status_cb("Please open a project first (Projects tab)")
        return ctx

    def _resolve_all_contexts(self, ctx):
        """解析项目所有 execution_profiles 需要的 RuntimeBinding。"""
        if ctx is None:
            return None
        profiles = set(ctx.project_config.execution_profiles.keys())
        try:
            return resolve_execution_contexts_for_project(
                ctx.project_config, profiles, self._binding_store)
        except Exception as e:
            self._error_cb("Runtime Binding Error", str(e))
            return None

    def _ssh_factory(self, server_id: str):
        """根据 server_id 创建 SSH 连接 (factory pattern)。"""
        ctx = self._ensure_ctx()
        if ctx is None:
            return None
        rctx = self._resolved_cache.get(server_id)
        if rctx is None:
            for rc in getattr(self, '_all_rctx', {}).values():
                if rc.server_id == server_id:
                    rctx = rc
                    self._resolved_cache[server_id] = rctx
                    break
        if rctx is None:
            return None
        ssh = create_ssh_client(rctx.server_config)
        ssh.connect()
        return ssh

    def refresh_batch_list(self):
        ctx = self._ensure_ctx()
        if not ctx: return
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        try:
            svc = WorkflowService(ctx)
            summaries = svc.list_batches()
            self._batch_summaries = {s.batch_id: s for s in summaries}
            for s in summaries:
                label = f"{s.batch_id} ({s.task_count} tasks)"
                self.batch_combo.addItem(label, s.batch_id)
            # auto-select latest
            if self.batch_combo.count() > 0:
                self.batch_combo.setCurrentIndex(0)
        finally:
            self.batch_combo.blockSignals(False)
        if self.batch_combo.count() > 0:
            self._on_batch_changed(self.batch_combo.itemData(0) or "")

    def _on_batch_changed(self, bid):
        if not bid: return
        ctx = self._ensure_ctx()
        if not ctx: return
        # bid may be userData (batch_id) from combo
        actual_bid = bid
        # if bid is display text, try to extract from combo data
        idx = self.batch_combo.currentIndex()
        if idx >= 0:
            actual_bid = self.batch_combo.itemData(idx) or bid
        mp = ctx.batches_dir / actual_bid / "manifest.tsv"
        if mp.exists():
            load_tsv_to_table(self.task_table, mp)
            self.state.current_batch_id = actual_bid
            self.state.current_manifest_path = mp
            summary = getattr(self, "_batch_summaries", {}).get(actual_bid)
            if summary:
                self.batch_header_label.setText(format_batch_header(summary, str(mp)))
            else:
                self.batch_header_label.setText(f"{actual_bid} | manifest={mp}")
            self._update_buttons()

    def _reload_table(self):
        if self.state.current_manifest_path:
            load_tsv_to_table(self.task_table, self.state.current_manifest_path)

    def _get_statuses(self):
        mp = self.state.current_manifest_path
        if mp and mp.exists():
            return {t.status for t in Manifest.read(mp)}
        return set()

    def _refresh_status_summary(self):
        mp = self.state.current_manifest_path
        if mp and mp.exists():
            self.status_summary_label.setText(format_status_summary(
                summarize_task_statuses(Manifest.read(mp))
            ))
        else:
            self.status_summary_label.setText("")

    def _update_buttons(self):
        s = self._get_statuses()
        has = bool(s)
        self.upload_btn.setEnabled(has and TaskStatus.local_ready in s)
        self.submit_btn.setEnabled(has and TaskStatus.uploaded in s)
        self.refresh_btn.setEnabled(has and (TaskStatus.submitted in s or TaskStatus.running in s))
        self.download_btn.setEnabled(has and TaskStatus.remote_completed in s)
        self.analyze_btn.setEnabled(has)
        reasons = build_button_reasons(s)
        self.upload_btn.setToolTip(reasons["upload"])
        self.submit_btn.setToolTip(reasons["submit"])
        self.refresh_btn.setToolTip(reasons["refresh"])
        self.download_btn.setToolTip(reasons["download"])
        self.analyze_btn.setToolTip(reasons["analyze"])
        self._refresh_status_summary()

    def _scan_inputs(self):
        ctx = self._ensure_ctx()
        if not ctx: return
        svc = WorkflowService(ctx)
        try:
            packages = svc.scan_inputs()
            profiles = {p.execution_profile for p in packages}
            disc_names = {p.discovery_name for p in packages}
            self._log(f"Discovered {len(packages)} inputs from {len(disc_names)} rules: {disc_names}")
            self._log(f"  execution_profiles: {profiles}")
            self._status_cb(f"Discovered {len(packages)} inputs")
        except Exception as e:
            self._log(f"Scan error: {e}")

    def _preflight(self):
        ctx = self._ensure_ctx()
        if not ctx:
            return
        svc = WorkflowService(ctx)
        try:
            report = svc.preflight(self._binding_store, ctx.servers_path)
        except Exception as e:
            self._error_cb("Preflight Error", str(e))
            return

        for line in format_preflight_report(report):
            self._log(line)
        self._status_cb("Preflight OK" if report.ok else "Preflight failed")

    def _create_batch(self):
        ctx = self._ensure_ctx()
        if not ctx: return
        svc = WorkflowService(ctx)
        try:
            packages = svc.scan_inputs()
            if not packages:
                self._log("No inputs found"); return

            all_rctx = self._resolve_all_contexts(ctx)
            if all_rctx is None:
                return
            self._all_rctx = all_rctx
            self._resolved_cache = {}

            profiles = {p.execution_profile for p in packages}
            needed = {ep: all_rctx[ep] for ep in profiles}
            result = svc.create_batch(packages, needed)
            self._log(f"Batch {result.batch_meta.batch_id}: {len(result.tasks)} tasks")
            for ep in sorted(profiles):
                if ep in needed:
                    self._log(f"  {ep}: server={needed[ep].server_id}, remote={needed[ep].remote_work_dir}")
            self.refresh_batch_list()
            self.batch_combo.setCurrentText(result.batch_meta.batch_id)
            self._status_cb(f"Batch {result.batch_meta.batch_id} created")
        except Exception as e:
            self._log(f"Create batch error: {e}")

    # -- upload --
    def _upload(self):
        ctx = self._ensure_ctx()
        mp = self.state.current_manifest_path
        if not ctx or not mp: return
        tasks = Manifest.read(mp)
        svc = WorkflowService(ctx)
        batch_dir = ctx.batches_dir / (self.state.current_batch_id or "")

        try:
            server_map = load_servers(ctx.servers_path).servers if getattr(ctx, "servers_path", None) else {}
        except Exception as e:
            self._error_cb("Servers Error", str(e))
            return

        def _run():
            # Use service method for upload (handles shared_files + task files)
            return svc.upload_tasks(
                tasks,
                sftp_factory=lambda sid: _make_connected_sftp(server_map.get(sid)),
                dry_run=False,
                batch_dir=batch_dir,
                manifest_path=mp,
            )

        self._log("Upload started..."); self._status_cb("Uploading...")
        self.worker = BackgroundWorker(_run)
        self.worker.result.connect(lambda r: self._on_upload_done(r, tasks, mp))
        self.worker.error.connect(lambda e: self._error_cb("Upload Error", e))
        self.worker.start()

    def _on_upload_done(self, records, tasks, mp):
        if isinstance(records, tuple):
            records, failures = records
        else:
            failures = []
        n_task = sum(1 for r in records if r.category == "task")
        n_shared = sum(1 for r in records if r.category == "shared")
        self._log(f"Upload: {n_task} task files, {n_shared} shared files")
        if failures:
            self._log(f"  {len(failures)} upload failures")
        self._log(format_transfer_summary("Upload", records, len(failures)))
        self._status_cb(f"Upload: {n_task} task, {n_shared} shared"); self._reload_table(); self._update_buttons()

    def _dry_upload(self):
        ctx = self._ensure_ctx()
        mp = self.state.current_manifest_path
        if not ctx or not mp: return
        tasks = Manifest.read(mp)
        svc = WorkflowService(ctx)
        records = svc.upload_tasks(tasks, lambda sid: None, dry_run=True)
        self._log(f"Dry-run upload: {len(records)} files planned")

    # -- submit --
    def _submit(self):
        ctx = self._ensure_ctx()
        mp = self.state.current_manifest_path
        bid = self.state.current_batch_id
        if not ctx or not mp or not bid: return
        tasks = Manifest.read(mp)
        n = sum(1 for t in tasks if t.status == TaskStatus.uploaded)
        if QMessageBox.question(self, "Confirm Submit",
            f"Submit {n} tasks (batch={bid})?",
            QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return

        def _run():
            svc = WorkflowService(ctx)
            def connected_ssh(scfg):
                ssh = create_ssh_client(scfg)
                ssh.connect()
                return ssh

            def connected_sftp(scfg):
                ssh = connected_ssh(scfg)
                return create_sftp_client(ssh)

            return svc.submit_batch(
                mp, bid,
                ssh_factory=connected_ssh,
                sftp_factory=connected_sftp,
            )

        self._log("Submit started..."); self._status_cb("Submitting...")
        self.worker = BackgroundWorker(_run)
        self.worker.result.connect(lambda r: self._on_submit_done(r))
        self.worker.error.connect(lambda e: self._error_cb("Submit Error", e))
        self.worker.start()

    def _on_submit_done(self, results):
        for r in results:
            if r.errors:
                self._log(f"Submit errors: {r.errors}")
            else:
                self._log(f"Submit OK: {r.submitted_task_count} tasks")
        self._status_cb("Submit complete"); self._reload_table(); self._update_buttons()

    def _dry_submit(self):
        self._log("Dry-run submit: use Submit button to see plan")

    # -- refresh --
    def _refresh(self):
        ctx = self._ensure_ctx()
        mp = self.state.current_manifest_path
        bid = self.state.current_batch_id
        if not ctx or not mp or not bid: return

        def _run():
            svc = WorkflowService(ctx)
            def connected_ssh(scfg):
                ssh = create_ssh_client(scfg)
                ssh.connect()
                return ssh
            return svc.refresh_batch(
                mp, bid,
                ssh_factory=connected_ssh,
                write=True,
            )

        self._log("Refresh started..."); self._status_cb("Refreshing...")
        self.worker = BackgroundWorker(_run)
        self.worker.result.connect(lambda r: self._on_refresh_done(r))
        self.worker.error.connect(lambda e: self._error_cb("Refresh Error", e))
        self.worker.start()

    def _on_refresh_done(self, result):
        results, failures = result
        changed = sum(r.changed_count for r in results)
        total = sum(r.task_count for r in results)
        self._log(f"Refresh: {changed}/{total} changed")
        if failures:
            self._log(f"  {len(failures)} server/task refresh failures")
            for f in failures[:5]:
                self._log(f"    [{f.stage}] {f.reason[:80]}")
        self._reload_table(); self._update_buttons()
        self._status_cb(f"Refreshed: {changed} changed")

    # -- download --
    def _download(self):
        ctx = self._ensure_ctx()
        mp = self.state.current_manifest_path
        if not ctx or not mp: return
        tasks = Manifest.read(mp)
        try:
            server_map = load_servers(ctx.servers_path).servers if getattr(ctx, "servers_path", None) else {}
        except Exception as e:
            self._error_cb("Servers Error", str(e))
            return

        def _run():
            svc = WorkflowService(ctx)
            return svc.download_completed(
                tasks,
                sftp_factory=lambda sid: _make_connected_sftp(server_map.get(sid)),
                dry_run=False,
                manifest_path=mp,
            )

        self._log("Download started..."); self._status_cb("Downloading...")
        self.worker = BackgroundWorker(_run)
        self.worker.result.connect(lambda r: self._on_download_done(r))
        self.worker.error.connect(lambda e: self._error_cb("Download Error", e))
        self.worker.start()

    def _on_download_done(self, result):
        records, failures = result
        self._log(f"Download: {len(records)} files")
        self._log(format_transfer_summary("Download", records, len(failures)))
        if failures:
            self._log(f"  {len(failures)} download failures")
            for f in failures[:5]:
                self._log(f"    [{f.stage}] task={f.task_id}: {f.reason[:80]}")
        self._status_cb("Download: ok"); self._reload_table(); self._update_buttons()

    def _dry_download(self):
        self._log("Dry-run download: use Download button")

    # -- analyze --
    def _analyze_batch(self):
        ctx = self._ensure_ctx()
        mp = self.state.current_manifest_path
        bid = self.state.current_batch_id
        if not ctx or not bid or not mp: return
        tasks = Manifest.read(mp)
        svc = WorkflowService(ctx)
        def _run():
            return svc.analyze_batch(tasks, bid)
        self._log("Analyze started..."); self._status_cb("Analyzing...")
        self.worker = BackgroundWorker(_run)
        self.worker.result.connect(lambda r: self._log(f"Analyze: {len(r[0])} results, {len(r[1])} failures, {len(r[2])} groups"))
        self.worker.error.connect(lambda e: self._error_cb("Analyze Error", e))
        self.worker.start()
