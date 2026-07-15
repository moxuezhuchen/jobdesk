"""运行+结果合并页 — 上方 run 列表，下方结果预览。"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ...core.parsers import GaussianResult, OrcaResult

from ...config.servers import load_servers
from ...core.run import remote_run_dir
from ...services.gui_settings import GuiSettingsStore
from ...services.run_coordinator import RunCoordinator
from ...services.run_service import RunRecord, RunService
from ...services.session_pool import SessionPool
from ..button_feedback import ButtonFeedback, ButtonRole
from ..design.components import StyledTableWidget
from ..design.tokens import Colors, Radius
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client
from ..widgets import EmptyStateHint
from ..worker_utils import WorkerContext, start_context_worker
from .runs_detail_pane import ResultDetailPane, _resolve_output_path

MAX_PREVIEW_FILE_BYTES = 25 * 1024 * 1024

# Column indices for the analysis result table built by ``_show_analysis_rows``.
# Order matches the header list at the call site and the row produced by
# ``_analysis_row``: task id, file name, program, energy, gibbs, ZPE,
# imaginary frequency count, diagnosis.
COL_TASK = 0
COL_FILE = 1
COL_PROGRAM = 2
COL_ENERGY = 3
COL_GIBBS = 4
COL_ZPE = 5
COL_IMAG_FREQ = 6
COL_DIAGNOSIS = 7


def _format_status(summary: dict[str, int], language: str = "en") -> str:
    if not summary:
        return ""
    from ..i18n import tr
    _LABELS = {
        "local_ready": tr("Preparing", language),
        "uploaded": tr("Uploaded", language),
        "submitting": tr("Submitting", language),
        "uncertain": tr("Uncertain", language),
        "submitted": tr("Submitted", language),
        "running": tr("Running", language),
        "remote_completed": tr("Completed", language),
        "downloaded": tr("Downloaded", language),
        "analyzed": tr("Analyzed", language),
        "failed": tr("Failed", language),
        "cancelled": tr("Cancelled", language),
    }
    parts = []
    total = sum(summary.values())
    for k, v in summary.items():
        label = _LABELS.get(k, k)
        parts.append(f"{label} {v}" if total > 1 else label)
    return " | ".join(parts)


def _format_row(record: RunRecord, language: str = "en") -> list[str]:
    return [
        record.run_id,
        record.server_id,
        record.remote_dir,
        _format_status(record.status_summary, language),
        record.command_template,
        record.created_at,
    ]


class RunsResultsPage(QWidget):
    startup_recovery_failed = Signal(str)
    startup_recovery_finished = Signal()
    # Phase 2.1: emitted when the empty-runs hint asks the shell to swap
    # to Submit. MainWindow will be wired in a later phase. Same pattern
    # as ``open_settings_requested`` on FileTransferPage.
    go_to_submit_requested = Signal()
    # Phase 2.1 follow-up: the "Show example templates" button needs to
    # land on Submit AND open the Examples drawer so the user can pick a
    # template. Same destination as ``go_to_submit_requested`` but with
    # extra intent carried over the signal so MainWindow can chain the
    # editor's ``open_examples_menu`` call after the page-switch.
    go_to_submit_with_examples_requested = Signal()

    def __init__(self, state, log_cb, status_cb, coordinator_factory=None):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._raw_status_cb = status_cb
        # Wrap ``status_cb`` so every status-bar message is mirrored into
        # the persistent activity log (Phase 16). The original call still
        # happens — wrapping is one extra dict lookup per message and does
        # not block the UI thread.
        self._status_cb = self._wrap_status_cb(status_cb)
        self._coordinator_factory = coordinator_factory
        self._language = GuiSettingsStore().load().language
        self._shutting_down = False
        self._recovery_running = False
        self._recovery_complete = False
        self._preview_request_id = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        # Persistent scrolling activity log (Phase 16). Every status-bar
        # message the page emits via ``self._status_cb`` is *also* appended
        # here, so the user gets the same scrollable trail as SubmitPage's
        # activity list. Implemented as a QTextEdit with append-after-the-
        # end (plus an autoscroll); no blocking I/O.
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(160)
        self._log_view.setObjectName("RunsActivityLog")
        log_font = QFont("Consolas")
        log_font.setStyleHint(QFont.Monospace)
        log_font.setPointSize(9)
        self._log_view.setFont(log_font)
        self._log_view.setPlaceholderText(tr("Activity log — status messages and errors", self._language))

        log_card = QWidget()
        log_card.setObjectName("RunsActivityLogCard")
        log_card.setStyleSheet(
            f"#RunsActivityLogCard {{ background: {Colors.CARD_BG}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }}"
        )
        log_card_layout = QVBoxLayout(log_card)
        log_card_layout.setContentsMargins(10, 8, 10, 8)
        log_card_layout.setSpacing(4)

        log_header_row = QHBoxLayout()
        self.activity_log_label = QLabel(tr("Activity log", self._language))
        self.activity_log_label.setStyleSheet(f"color: {Colors.TEXT}; font-weight: 600; font-size: 18px;")
        log_header_row.addWidget(self.activity_log_label)
        log_header_row.addStretch()
        self.clear_log_btn = QPushButton(tr("Clear Log", self._language))
        self.clear_log_btn.clicked.connect(self._clear_activity_log)
        log_header_row.addWidget(self.clear_log_btn)
        log_card_layout.addLayout(log_header_row)
        log_card_layout.addWidget(self._log_view)
        layout.addWidget(log_card)

        # -- Phase 2.1: empty-state hint for "no runs yet" --
        # Shows when the runs list is empty; action buttons route to
        # the Submit page via the go_to_submit_requested signal.
        self._empty_hint = EmptyStateHint(
            title_key="No runs yet",
            body_key=(
                "Build a workflow on the Submit tab and click Submit to Remote. "
                "Your runs will appear here."
            ),
            action_texts=(
                ("go_to_submit", "Go to Submit"),
                ("show_examples", "Show example templates"),
            ),
            language=self._language,
            parent=self,
        )
        self._empty_hint.action_requested.connect(self._on_empty_action)
        self._empty_hint.setVisible(False)
        layout.addWidget(self._empty_hint)

        splitter = QSplitter(Qt.Vertical)

        # ─── Top: Run list ───
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self.table = StyledTableWidget()
        self.table.setColumnCount(6)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.currentCellChanged.connect(self._on_run_selected)
        self.table.bind_column_widths("runs_v2", [140, 100, 260, 180, 220, 160])

        table_card = QWidget()
        table_card.setObjectName("RunsTableCard")
        table_card.setStyleSheet(
            f"#RunsTableCard {{ background: {Colors.CARD_BG}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }}"
        )
        table_card_layout = QVBoxLayout(table_card)
        table_card_layout.setContentsMargins(16, 12, 16, 12)
        table_card_layout.addWidget(self.table)
        top_layout.addWidget(table_card, 1)

        # Buttons row (card style)
        btn_card = QWidget()
        btn_card.setObjectName("BtnCard")
        btn_card.setStyleSheet(
            f"#BtnCard {{ background: {Colors.CARD_BG}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }}"
        )
        btn_card.setFixedHeight(58)
        btn_row = QHBoxLayout(btn_card)
        btn_row.setContentsMargins(16, 0, 16, 0)
        self.retry_btn = QPushButton(tr("Retry Failed", self._language))
        self.retry_btn.clicked.connect(self._retry_failed)
        btn_row.addWidget(self.retry_btn)
        self.stop_btn = QPushButton(tr("Stop Task", self._language))
        self.stop_btn.clicked.connect(self._stop_run)
        btn_row.addWidget(self.stop_btn)
        self.retry_dl_btn = QPushButton(tr("Retry Download", self._language))
        self.retry_dl_btn.clicked.connect(self._retry_download)
        btn_row.addWidget(self.retry_dl_btn)
        self.delete_btn = QPushButton(tr("Delete", self._language))
        self.delete_btn.clicked.connect(self._delete_run)
        btn_row.addWidget(self.delete_btn)
        self.confirm_submitted_btn = QPushButton(tr("Confirm Submitted", self._language))
        self.confirm_submitted_btn.clicked.connect(self._confirm_submitted)
        self.confirm_submitted_btn.hide()
        btn_row.addWidget(self.confirm_submitted_btn)
        self.abandon_submit_btn = QPushButton(tr("Abandon Submit", self._language))
        self.abandon_submit_btn.clicked.connect(self._abandon_submit)
        self.abandon_submit_btn.hide()
        btn_row.addWidget(self.abandon_submit_btn)
        self._retry_feedback = ButtonFeedback(self.retry_btn, ButtonRole.PRIMARY_ACTION)
        self._stop_feedback = ButtonFeedback(self.stop_btn, ButtonRole.DANGER_ACTION)
        self._retry_download_feedback = ButtonFeedback(self.retry_dl_btn, ButtonRole.TRANSFER_ACTION)
        self._delete_feedback = ButtonFeedback(self.delete_btn, ButtonRole.DANGER_ACTION)
        btn_row.addStretch()
        top_layout.addWidget(btn_card)
        splitter.addWidget(top)

        # ─── Bottom: Results preview ───
        bottom = QWidget()
        bottom.setObjectName("ResultsCard")
        bottom.setStyleSheet(
            f"#ResultsCard {{ background: {Colors.CARD_BG}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }} "
            f" #ResultsCard QLabel {{ background: transparent; }} "
            f" #ResultsCard QTextEdit {{ background: transparent; border: none; }}"
        )
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 12, 16, 12)
        bottom_layout.setSpacing(4)

        self.result_label = QLabel(tr("Result Preview", self._language))
        self.result_label.setStyleSheet(f"color: {Colors.TEXT}; font-weight: 600; font-size: 18px;")
        bottom_layout.addWidget(self.result_label)

        self.result_table = StyledTableWidget()
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.result_table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.result_table.itemSelectionChanged.connect(self._update_uncertain_actions)
        self.result_table.itemDoubleClicked.connect(self._on_result_row_double_clicked)
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.bind_column_widths("runs_results.preview")
        bottom_layout.addWidget(self.result_table)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setMaximumHeight(80)
        self.result_text.setVisible(False)
        bottom_layout.addWidget(self.result_text)

        # Detail pane: shows full parsed Gaussian/ORCA result on double-click
        self.detail_pane = ResultDetailPane()
        bottom_layout.addWidget(self.detail_pane)

        splitter.addWidget(bottom)
        splitter.setSizes([500, 150])
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)

        # Real-time task completion monitor
        from ..run_monitor_qt import RunMonitor
        self._monitor = RunMonitor(self)
        self._monitor.task_done.connect(self._on_task_done)
        self._bg_workers: list = []
        self._remote_mutation_running = False

        # Debounce state for _on_task_done events
        self._pending_task_events: dict[str, dict] = {}  # run_id -> {server_id, has_done}
        self._task_done_timers: dict[str, QTimer] = {}

        # Auto-refresh timer for active runs
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._auto_refresh_active)
        self._refresh_timer.setInterval(15000)

        # Selection-driven preview is debounced so rapid scrolling through the
        # run list does not parse output files on the UI thread for every row.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._render_selected_preview)
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.timeout.connect(self._run_deferred_activation)
        # Memoized parsed rows keyed by result-dir, invalidated by file signature.
        self._analyze_cache: dict[str, tuple] = {}
        # Memoized detail-pane results keyed by (task_id, mtime, size) of the source
        # log/out. Invalidated by the _ckpt_ checkpoint handler and on parser failure.
        self._detail_cache: dict[tuple, object] = {}
        # run_ids currently being refreshed/downloaded, shared by the auto-refresh
        # timer and the monitor-driven flush to avoid duplicate concurrent work.
        self._in_progress: set[str] = set()
        # Tracks the last current_batch_id we auto-selected, so a freshly-set one
        # (a new submission) still jumps while later refreshes keep manual selection.
        self._applied_batch_id: str | None = None

        self._session_pool = SessionPool(create_ssh_client, create_sftp_client)

    def _start_monitoring(self):
        """Watch all running runs."""
        try:
            runs = RunService(self._workspace()).list_runs()
            cfg = load_servers()
            for record in runs:
                if (
                    record.status_summary.get("submitting", 0) > 0
                    or record.status_summary.get("running", 0) > 0
                    or record.status_summary.get("submitted", 0) > 0
                ):
                    srv = cfg.servers.get(record.server_id)
                    if srv:
                        batch_dir = remote_run_dir(record.remote_dir, record.run_id)
                        self._monitor.watch(record.run_id, record.server_id, batch_dir, srv)
        except Exception:
            pass

    def _on_task_done(self, event):
        """Called when a remote task changes state — debounce before refresh.

        Synthetic checkpoint events (``task_id`` starts with ``_ckpt_``)
        trigger an immediate refresh-only path: no download, no status
        mutation. They exist so the Runs page reflects ConfFlow step
        progress even when no DONE/RUNNING line has been emitted yet.
        """
        if self._shutting_down:
            return
        run_id = event.run_id
        is_checkpoint = isinstance(event.task_id, str) and event.task_id.startswith("_ckpt_")
        if is_checkpoint:
            # Immediate lightweight refresh: re-read run_summary /
            # workflow_stats from disk and update the preview. No
            # background worker needed since confflow_results parsing is
            # cheap and we already cache by signature in
            # ``_analyze_cache``.
            self.refresh_run_list()
            # Force the selected run's preview to reload so the new
            # Progress column reflects the latest workflow_stats.json.
            record = self._selected_record()
            if record is not None:
                # Drop analyze cache so the next preview re-reads
                # workflow_stats.json / run_summary.json from disk.
                self._analyze_cache.clear()
                self._detail_cache.clear()
                self._preview_timer.start()
            return
        # Real DONE/RUNNING path — debounce before refresh + download.
        if run_id in self._pending_task_events:
            state = self._pending_task_events[run_id]
            state["has_done"] = state["has_done"] or (event.exit_code is not None)
        else:
            self._pending_task_events[run_id] = {
                "server_id": event.server_id,
                "has_done": event.exit_code is not None,
            }
        # Start or restart debounce timer (1000ms)
        if run_id in self._task_done_timers:
            self._task_done_timers[run_id].start(1000)
        else:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda rid=run_id: self._flush_task_done(rid))
            self._task_done_timers[run_id] = timer
            timer.start(1000)

    def _flush_task_done(self, run_id: str):
        """Execute debounced refresh for a run after the quiet window."""
        state = self._pending_task_events.pop(run_id, None)
        self._task_done_timers.pop(run_id, None)
        if state is None:
            return
        if run_id in self._in_progress:
            return  # auto-refresh or a prior flush is already handling this run
        self._in_progress.add(run_id)
        has_done = state["has_done"]
        server_id = state["server_id"]
        fallback_ws = self._workspace()

        def _run():
            record = RunService(fallback_ws).load_run(run_id)
            patterns = self._get_download_patterns(record)
            outcome = self._execute_refresh_use_case(record, patterns, download=has_done)
            if outcome.errors:
                return tr(
                    "Automatic refresh failed: {errors}",
                    self._language,
                    errors="; ".join(outcome.errors),
                )
            if has_done and outcome.transfer_records:
                return tr("Run complete; results downloaded: {run_id}", self._language, run_id=run_id)
            return None

        class _FakeEvent:
            pass
        evt = _FakeEvent()
        evt.run_id = run_id
        evt.server_id = server_id

        from ..workers import BackgroundWorker
        w = BackgroundWorker(_run)
        w.result.connect(lambda message: self._status_cb(message) if message else None)
        w.error.connect(lambda error: self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error)))
        w.finished.connect(lambda: self._on_monitor_refresh_done(evt))
        w.finished.connect(lambda: self._in_progress.discard(run_id))
        w.finished.connect(lambda: self._bg_workers.remove(w) if w in self._bg_workers else None)
        w.finished.connect(w.deleteLater)
        self._bg_workers.append(w)
        w.start()

    def _on_monitor_refresh_done(self, event):
        self.refresh_run_list()
        try:
            updated = RunService(self._workspace()).load_run(event.run_id)
            if (
                updated.status_summary.get("submitting", 0) == 0
                and updated.status_summary.get("running", 0) == 0
                and updated.status_summary.get("submitted", 0) == 0
            ):
                self._monitor.unwatch(event.run_id, event.server_id)
        except Exception:
            pass

    def on_activated(self):
        settings = GuiSettingsStore().load()
        self._language = settings.language
        self._refresh_timer.setInterval(settings.auto_refresh_interval * 1000)
        self._refresh_timer.stop()
        self._activation_timer.start(0)

    def _run_deferred_activation(self):
        if self._shutting_down:
            return
        self.refresh_run_list()
        self._start_monitoring()
        self._refresh_timer.start()
        # Phase 2.1: explicitly set hint visibility for the initial load.
        # refresh_run_list already does this, but the deferred path may
        # race with status updates coming from _start_monitoring; keeping
        # the toggle here too keeps the empty-state intent obvious.
        if self.table.rowCount() == 0:
            self._empty_hint.setVisible(True)

    def start_startup_recovery(self) -> None:
        """Replay interrupted operations once, independently of page activation."""
        if self._shutting_down or self._recovery_running or self._recovery_complete:
            return
        self._recovery_running = True
        workspace = self._workspace()

        def _recover(_ctx: WorkerContext):
            return self._coordinator_for(workspace).recover_operations()

        start_context_worker(
            self,
            target=_recover,
            registry_attr="_bg_workers",
            on_result=self._apply_startup_recovery,
            on_error=self._apply_startup_recovery_error,
            on_finished=self._finish_startup_recovery,
        )

    def _apply_startup_recovery(self, outcome) -> None:
        if self._shutting_down:
            return
        if outcome.errors:
            error = "; ".join(outcome.errors)
            self._status_cb(tr("Operation recovery failed: {error}", self._language, error=error))
            self.startup_recovery_failed.emit(error)

    def _apply_startup_recovery_error(self, error: str) -> None:
        if self._shutting_down:
            return
        self._status_cb(tr("Operation recovery failed: {error}", self._language, error=error))
        self.startup_recovery_failed.emit(error)

    def _finish_startup_recovery(self) -> None:
        self._recovery_running = False
        self._recovery_complete = True
        self.startup_recovery_finished.emit()

    def apply_language(self, language: str):
        self._language = language
        self._retry_feedback.set_idle_text(tr("Retry Failed", language))
        self._stop_feedback.set_idle_text(tr("Stop Task", language))
        self._retry_download_feedback.set_idle_text(tr("Retry Download", language))
        self._delete_feedback.set_idle_text(tr("Delete", language))
        self.confirm_submitted_btn.setText(tr("Confirm Submitted", language))
        self.abandon_submit_btn.setText(tr("Abandon Submit", language))
        self.result_label.setText(tr("Result Preview", language))
        self.activity_log_label.setText(tr("Activity log", language))
        self.clear_log_btn.setText(tr("Clear Log", language))
        self._set_headers()
        self.refresh_run_list()
        # Phase 11.1 — F5 fix. Forward language to the result detail
        # pane so its placeholder text re-translates on the fly.
        if hasattr(self, "detail_pane") and self.detail_pane is not None:
            self.detail_pane.apply_language(language)
        # Phase 2.1: retranslate the empty-state hint copy.
        self._empty_hint.apply_language(language)

    # ─── Phase 16: persistent scrolling activity log ────────────────────

    def _wrap_status_cb(self, status_cb):
        """Return a status callback that forwards to the underlying widget
        *and* records a timestamped line in the persistent activity log.

        Status messages are short, single-string writes, so building the
        formatted line and posting it via ``QTimer.singleShot(0, ...)`` is
        cheap and never blocks the UI thread, even if dozens of status
        messages fire in quick succession.
        """
        def _wrapped(message, *args, **kwargs):
            try:
                self._append_activity_log(message)
            except Exception:
                pass
            if status_cb is not None:
                return status_cb(message, *args, **kwargs)
            return None
        return _wrapped

    def _append_activity_log(self, message: str) -> None:
        """Append one timestamped line to the activity log view.

        Uses ``moveCursor(QTextCursor.End)`` + ``insertPlainText`` which is
        cheap (O(text)) and runs entirely on the GUI thread. We also call
        ``ensureCursorVisible`` so the latest line stays in view.
        """
        if not message:
            return
        text = str(message)
        if not hasattr(self, "_log_view") or self._log_view is None:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}"
        # Append directly — QTextEdit is single-threaded GUI only, and
        # all callers of ``_status_cb`` are already on the GUI thread.
        cursor = self._log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        if self._log_view.document().characterCount() > 1:
            cursor.insertBlock()
        cursor.insertText(line)
        self._log_view.setTextCursor(cursor)
        self._log_view.ensureCursorVisible()
        # Also forward into the abstract ``_log`` sink the page received in
        # ``__init__`` if it is a real callable (not the dummy used during
        # tests / non-GUI contexts).
        log_sink = getattr(self, "_log", None)
        if callable(log_sink):
            try:
                log_sink(text)
            except Exception:
                pass

    def _clear_activity_log(self) -> None:
        """Clear the visible log lines. Sink log and status bar are untouched."""
        if hasattr(self, "_log_view") and self._log_view is not None:
            self._log_view.clear()

    def _set_headers(self):
        self.table.setHorizontalHeaderLabels([
            tr("Run ID", self._language), tr("Server", self._language), tr("Remote Dir", self._language),
            tr("Status", self._language), tr("Command", self._language), tr("Created At", self._language),
        ])


    def _build_context_actions(self) -> list[tuple[str, object]]:
        """Return (label, callback) pairs for the context menu."""
        return [
            (tr("Refresh Status", self._language), self._refresh_all),
            (tr("Rerun", self._language), self._rerun_all),
            (tr("Compare Selected", self._language), self._compare_selected),
            (tr("Open Results", self._language), self._open_results_folder),
            (tr("Show Logs", self._language), self._show_logs),
            (tr("Show Paths", self._language), self._show_paths),
            # Destructive action wired in Phase 17: gives a right-click way
            # to invoke the same delete logic as the existing Delete button.
            # Destructive labels are identified inside ``_context_menu`` so
            # the public contract stays a 2-tuple that ``test_gui_behavior``
            # can unpack with ``label, _callback``.
            (tr("Delete Run", self._language), self._delete_run_from_context),
        ]

    def _context_menu(self, pos):
        # Phase 17: when the user right-clicks *outside* the existing
        # selection, the menu is built against the row under the cursor
        # so the right-click target is what gets acted on. This mirrors
        # how the left side ``Delete`` button behaves when the user has
        # only one row selected.
        row_under_cursor = self.table.indexAt(pos).row()
        if row_under_cursor >= 0:
            selected_rows = {idx.row() for idx in self.table.selectedIndexes()}
            if row_under_cursor not in selected_rows:
                # Make the right-clicked row the sole selection so
                # ``_delete_run`` (and the other actions) operate on it.
                self.table.selectRow(row_under_cursor)
        # Identify destructive labels by translated string (no separate
        # data side-channel — keeping the public 2-tuple contract intact).
        danger_labels = {tr("Delete Run", self._language)}
        menu = QMenu(self)
        for label, callback in self._build_context_actions():
            action = QAction(label, self)
            if label in danger_labels:
                # Visual warning without a custom QStyle; the red
                # foreground + bold makes the danger obvious in both
                # light and dark themes.
                action.setStyleSheet(f"color: {Colors.ERROR}; font-weight: 600;")
            action.triggered.connect(callback)
            menu.addAction(action)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _delete_run_from_context(self):
        """Invoke ``_delete_run`` from a context-menu entry.

        ``_delete_run`` reads its target rows from ``self.table.selectedIndexes()``
        which the context menu handler has already normalized (see
        ``_context_menu``), so this is just a thin façade that also appends an
        explanatory status-bar message so the user understands the action
        actually ran.
        """
        self._append_activity_log(tr("Delete Run invoked from context menu", self._language))
        self._delete_run()

    def _refresh_all(self):
        self.refresh_run_list()
        row = self.table.currentRow()
        if row >= 0:
            self._refresh_status()

    def _on_empty_action(self, action_id: str) -> None:
        """Route the Runs-page empty-state buttons.

        ``go_to_submit`` simply lands the user on the Submit page so
        they can drag nodes from the library. ``show_examples`` does
        the same destination but also signals that the Examples drawer
        should pop open so the user can pick a template directly --
        otherwise the button would merely navigate and the user would
        have to click the toolbar Examples button again, which is a
        broken promise given the button text.
        """
        if action_id == "go_to_submit":
            self.go_to_submit_requested.emit()
        elif action_id == "show_examples":
            self.go_to_submit_with_examples_requested.emit()

    def refresh_run_list(self):
        workspace = self.state.current_project_root or Path.cwd()
        runs = RunService(workspace).list_runs()
        prev_selected = self._current_run_id()
        self._set_headers()
        self.table.blockSignals(True)
        self.table.setRowCount(len(runs))
        manual_row = None
        batch_row = None
        for row, record in enumerate(runs):
            for col, value in enumerate(_format_row(record, self._language)):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, record)  # cache to avoid another database lookup on selection
                self.table.setItem(row, col, item)
            if record.run_id == prev_selected:
                manual_row = row
            if record.run_id == getattr(self.state, "current_batch_id", None):
                batch_row = row
        self.table.blockSignals(False)
        # A freshly-set current_batch_id (new submission) jumps to that run;
        # otherwise keep the user's manual selection across refreshes.
        batch_id = getattr(self.state, "current_batch_id", None)
        if batch_id is not None and batch_id != self._applied_batch_id and batch_row is not None:
            target_row = batch_row
            self._applied_batch_id = batch_id
        else:
            target_row = manual_row if manual_row is not None else batch_row
        if target_row is not None:
            self.table.setCurrentCell(target_row, 0)
        self._update_uncertain_actions()
        self._status_cb(tr("Run records: {n}", self._language, n=len(runs)))
        # Phase 2.1: toggle the empty-state hint whenever the run list
        # is refreshed. The hint lives outside the splitter so this only
        # affects the layout above the run table.
        self._empty_hint.setVisible(not runs)

    def _current_run_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.text() if item else None

    def _workspace(self) -> Path:
        return Path(self.state.current_project_root or Path.cwd())

    def _coordinator_for(self, workspace: Path) -> RunCoordinator:
        if self._coordinator_factory is not None:
            return self._coordinator_factory(workspace)
        return RunCoordinator(
            RunService(workspace),
            server_lookup=lambda server_id: load_servers().servers[server_id],
            ssh_factory=create_ssh_client,
            sftp_factory=create_sftp_client,
            session_pool=self._session_pool,
        )

    def _execute_refresh_use_case(self, record, patterns: list[str], *, download: bool):
        coordinator = self._coordinator_for(self._result_workspace(record))
        if download:
            return coordinator.refresh_and_download(record.run_id, patterns)
        return coordinator.refresh(record.run_id)

    def _execute_download_use_case(self, record, patterns: list[str]):
        coordinator = self._coordinator_for(self._result_workspace(record))
        return coordinator.download(record.run_id, patterns)

    def _result_workspace(self, record: RunRecord) -> Path:
        """Resolve the workspace for a run record's results."""
        local_dir = getattr(record, "local_dir", "")
        if isinstance(local_dir, (str, os.PathLike)) and local_dir:
            return Path(local_dir)
        return self._workspace()

    def _load_tasks(self, record: RunRecord):
        try:
            tasks = RunService(self._result_workspace(record)).repository.load_tasks(record.run_id)
        except KeyError:
            tasks = []
        if tasks:
            return tasks
        manifest_path = Path(getattr(record, "manifest_path", ""))
        if manifest_path.is_file():
            from ...core.manifest import Manifest

            return Manifest.read(manifest_path)
        return []

    def _download_directory(self, record: RunRecord) -> Path:
        return self._result_workspace(record)

    def _legacy_results_directory(self, record: RunRecord, base: Path | None = None) -> Path:
        root = base if base is not None else self._result_workspace(record)
        return root / "results" / record.run_id

    def _result_search_directories(self, record: RunRecord, bases: list[Path]) -> list[Path]:
        directories: list[Path] = []
        for base in bases:
            for candidate in (self._legacy_results_directory(record, base), base):
                if candidate not in directories:
                    directories.append(candidate)
        return directories

    def _selected_record(self) -> RunRecord | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        cached = item.data(Qt.UserRole)
        if isinstance(cached, RunRecord):
            return cached
        return RunService(self._workspace()).load_run(item.text())

    def _on_run_selected(self, row, col, prev_row, prev_col):
        """Debounce selection so rapid scrolling doesn't parse files per row."""
        self._update_uncertain_actions()
        self._preview_timer.start()

    def _update_uncertain_actions(self) -> None:
        record = self._selected_record()
        enabled = bool(
            record
            and record.status_summary.get("uncertain", 0)
            and self._selected_uncertain_task_ids()
        )
        self.confirm_submitted_btn.setVisible(enabled)
        self.abandon_submit_btn.setVisible(enabled)
        self.confirm_submitted_btn.setEnabled(enabled)
        self.abandon_submit_btn.setEnabled(enabled)

    def _selected_uncertain_task_ids(self) -> list[str]:
        selected_rows = sorted(
            {index.row() for index in self.result_table.selectedIndexes()}
        )
        if not selected_rows:
            return []
        task_ids: list[str] = []
        for row in selected_rows:
            item = self.result_table.item(row, 0)
            data = item.data(Qt.UserRole) if item is not None else None
            if (
                not isinstance(data, tuple)
                or len(data) != 2
                or data[1] != "uncertain"
            ):
                return []
            task_ids.append(str(data[0]))
        return task_ids

    def _render_selected_preview(self):
        """Render the preview for the settled selection (called after debounce)."""
        record = self._selected_record()
        if record is None:
            self.result_table.setRowCount(0)
            return
        self._preview_request_id += 1
        request_id = self._preview_request_id

        def _run(_ctx: WorkerContext):
            return self._collect_result_preview(record)

        def _done(payload):
            if request_id != self._preview_request_id:
                return
            self._apply_result_preview(payload)

        start_context_worker(
            self,
            target=_run,
            registry_attr="_bg_workers",
            on_result=_done,
            on_error=lambda error: self._status_cb(tr("Preview failed: {e}", self._language, e=error.splitlines()[0])),
        )

    def _collect_result_preview(self, record: RunRecord):
        from ...services.gui_settings import GuiSettingsStore
        if getattr(record, "status_summary", {}).get("uncertain", 0):
            return ("uncertain", self._load_tasks(record))
        workspace = self._result_workspace(record)
        candidates = [workspace]
        default_folder = GuiSettingsStore().load().default_local_folder
        if default_folder and Path(default_folder) != workspace:
            candidates.append(Path(default_folder))
        gui_ws = self._workspace()
        if gui_ws != workspace and gui_ws not in candidates:
            candidates.append(gui_ws)

        is_confflow = "confflow" in (getattr(record, "command_template", "") or "").lower()
        if is_confflow:
            best_dir = None
            fallback_dir = None
            for result_dir in self._result_search_directories(record, candidates):
                if result_dir.exists():
                    if _confflow_result_dir_has_summary(
                        record,
                        result_dir,
                        self._load_tasks(record),
                    ):
                        best_dir = result_dir
                        break
                    if fallback_dir is None:
                        fallback_dir = result_dir
            return ("confflow", record, best_dir or fallback_dir or self._download_directory(record))

        for base in candidates:
            result_dir = self._legacy_results_directory(record, base)
            if result_dir.exists():
                summaries = sorted(result_dir.rglob("run_summary.json"))
                if summaries:
                    return ("confflow", record, result_dir)
                rows = self._auto_analyze(result_dir)
                if rows:
                    return ("analysis", rows, tr("Result Preview - Auto Analysis", self._language))

        for base in candidates:
            needs_refresh = False

            def _mark_needs_refresh() -> None:
                nonlocal needs_refresh
                needs_refresh = True

            rows = self._analyze_workspace_files(record, base, on_changed=_mark_needs_refresh)
            if rows:
                return ("analysis", rows, tr("Result Preview - Local Files", self._language), needs_refresh)

        for base in candidates:
            result_dir = self._legacy_results_directory(record, base)
            for name in ("final_results.tsv", "analysis_preview.tsv"):
                tsv = result_dir / name
                if tsv.exists() and tsv.stat().st_size > 30:
                    return ("tsv", tsv, f"{tr('Result Preview', self._language)} - {name}")

        return ("empty",)

    def _apply_result_preview(self, payload) -> None:
        kind = payload[0]
        if kind == "uncertain":
            self._show_uncertain_tasks(payload[1])
        elif kind == "confflow":
            _kind, record, result_dir = payload
            self._show_confflow_batch_results(record, result_dir)
        elif kind == "analysis":
            _kind, rows, label, *rest = payload
            self.result_text.setVisible(False)
            self._show_analysis_rows(rows)
            self._set_parsed_results_label(label)
            if rest and rest[0]:
                self.refresh_run_list()
        elif kind == "tsv":
            _kind, path, label = payload
            self._load_tsv(path)
            self.result_text.setVisible(False)
            self.result_label.setText(label)
        else:
            self.result_label.setText(tr("No results downloaded yet", self._language))
            self.result_text.setVisible(False)
            self.result_table.setRowCount(0)

    def _show_uncertain_tasks(self, tasks) -> None:
        self.result_table.clearSelection()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(
            [tr("Task", self._language), tr("Status", self._language), tr("Error", self._language)]
        )
        self.result_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            status = task.status.value
            values = (task.task_id, tr(status.title(), self._language), task.error_message or "")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(
                        Qt.UserRole,
                        {
                            "kind": "uncertain",
                            "task_id": task.task_id,
                            "status": status,
                            "error": task.error_message,
                        },
                    )
                self.result_table.setItem(row, column, item)
        self.result_label.setText(tr("Select uncertain tasks to recover", self._language))
        self._update_uncertain_actions()

    def _load_result_preview(self, record: RunRecord):
        """Load TSV results or run analysis for the selected run."""
        from ...services.gui_settings import GuiSettingsStore
        workspace = self._result_workspace(record)
        candidates = [workspace]
        default_folder = GuiSettingsStore().load().default_local_folder
        if default_folder and Path(default_folder) != workspace:
            candidates.append(Path(default_folder))
        gui_ws = self._workspace()
        if gui_ws != workspace and gui_ws not in candidates:
            candidates.append(gui_ws)

        # Detect ConfFlow batch by command_template
        is_confflow = "confflow" in (getattr(record, "command_template", "") or "").lower()
        if is_confflow:
            best_dir = None
            fallback_dir = None
            for result_dir in self._result_search_directories(record, candidates):
                if result_dir.exists():
                    if _confflow_result_dir_has_summary(
                        record,
                        result_dir,
                        self._load_tasks(record),
                    ):
                        best_dir = result_dir
                        break
                    if fallback_dir is None:
                        fallback_dir = result_dir
            chosen = best_dir or fallback_dir or self._download_directory(record)
            self._show_confflow_batch_results(record, chosen)
            return

        # Prefer auto-analysis on downloaded files
        for base in candidates:
            result_dir = self._legacy_results_directory(record, base)
            if result_dir.exists():
                summaries = sorted(result_dir.rglob("run_summary.json"))
                if summaries:
                    self._show_confflow_batch_results(record, result_dir)
                    return
                rows = self._auto_analyze(result_dir)
                if rows:
                    self.result_text.setVisible(False)
                    self._show_analysis_rows(rows)
                    self._set_parsed_results_label(tr("Result Preview — Auto Analysis", self._language))
                    return

        # Fallback: analyze output files in workspace root
        for base in candidates:
            rows = self._analyze_workspace_files(record, base)
            if rows:
                self._show_analysis_rows(rows)
                self._set_parsed_results_label(tr("Result Preview — Local Files", self._language))
                return

        # Last resort: read existing TSV
        for base in candidates:
            result_dir = self._legacy_results_directory(record, base)
            for name in ("final_results.tsv", "analysis_preview.tsv"):
                tsv = result_dir / name
                if tsv.exists() and tsv.stat().st_size > 30:
                    self._load_tsv(tsv)
                    self.result_text.setVisible(False)
                    self.result_label.setText(f"{tr('Result Preview', self._language)} — {name}")
                    return

        self.result_label.setText(tr("No results downloaded yet", self._language))
        self.result_text.setVisible(False)
        self.result_table.setRowCount(0)

    def _analyze_workspace_files(self, record: RunRecord, workspace: Path, *, on_changed=None) -> list[list[str]]:
        """Analyze output files directly from workspace if they exist locally."""
        from ...core.lifecycle import TaskStatus
        from ...core.parsers.gaussian import diagnose_gaussian_result, parse_gaussian_log
        from ...core.parsers.orca import diagnose_orca_result, parse_orca_out
        tasks = self._load_tasks(record)
        rows: list[list[str]] = []
        for task in tasks:
            if task.status not in (TaskStatus.downloaded, TaskStatus.analyzed):
                continue
            if not task.remote_task_files:
                continue
            source = task.remote_task_files[0]
            stem = PurePosixPath(source).stem
            # Check .log (Gaussian)
            log_file = workspace / f"{stem}.log"
            if log_file.is_file():
                if _too_large_for_preview(log_file):
                    rows.append(_placeholder_analysis_row(
                        task.task_id, log_file.name, "Gaussian",
                        tr("File too large for preview", self._language),
                    ))
                else:
                    try:
                        r = parse_gaussian_log(log_file)
                        rows.append(_analysis_row(task.task_id, log_file.name, "Gaussian", r, diagnose_gaussian_result(r), self._language))
                    except Exception:
                        rows.append(_placeholder_analysis_row(
                            task.task_id, log_file.name, "Gaussian",
                            tr("Parse Error", self._language),
                        ))
            # Check .out (ORCA)
            out_file = workspace / f"{stem}.out"
            if out_file.is_file():
                if _too_large_for_preview(out_file):
                    rows.append(_placeholder_analysis_row(
                        task.task_id, out_file.name, "ORCA",
                        tr("File too large for preview", self._language),
                    ))
                else:
                    try:
                        ro = parse_orca_out(out_file)
                        rows.append(_analysis_row(task.task_id, out_file.name, "ORCA", ro, diagnose_orca_result(ro), self._language))
                    except Exception:
                        rows.append(_placeholder_analysis_row(
                            task.task_id, out_file.name, "ORCA",
                            tr("Parse Error", self._language),
                        ))
        return rows

    def _auto_analyze(self, result_dir: Path) -> list[list[str]]:
        """Auto-detect and parse Gaussian/ORCA output files matching task stem."""
        key = str(result_dir)
        sig = _dir_parse_signature(result_dir)
        cached = self._analyze_cache.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
        from ...core.parsers.gaussian import diagnose_gaussian_result, parse_gaussian_log
        from ...core.parsers.orca import diagnose_orca_result, parse_orca_out
        rows: list[list[str]] = []
        dirs = sorted(d for d in result_dir.iterdir() if d.is_dir())
        if not dirs:
            dirs = [result_dir]
        for task_dir in dirs:
            stem = task_dir.name  # task_id == stem of source file
            # Gaussian .log
            log_file = task_dir / f"{stem}.log"
            if log_file.is_file():
                if _too_large_for_preview(log_file):
                    rows.append(_placeholder_analysis_row(
                        stem, log_file.name, "Gaussian",
                        tr("File too large for preview", self._language),
                    ))
                else:
                    try:
                        r = parse_gaussian_log(log_file)
                        rows.append(_analysis_row(stem, log_file.name, "Gaussian", r, diagnose_gaussian_result(r), self._language))
                    except Exception:
                        rows.append(_placeholder_analysis_row(
                            stem, log_file.name, "Gaussian",
                            tr("Parse Error", self._language),
                        ))
            # ORCA .out
            out_file = task_dir / f"{stem}.out"
            if out_file.is_file():
                if _too_large_for_preview(out_file):
                    rows.append(_placeholder_analysis_row(
                        stem, out_file.name, "ORCA",
                        tr("File too large for preview", self._language),
                    ))
                else:
                    try:
                        ro = parse_orca_out(out_file)
                        rows.append(_analysis_row(stem, out_file.name, "ORCA", ro, diagnose_orca_result(ro), self._language))
                    except Exception:
                        rows.append(_placeholder_analysis_row(
                            stem, out_file.name, "ORCA",
                            tr("Parse Error", self._language),
                        ))
        self._analyze_cache[key] = (sig, rows)
        return rows

    def _show_confflow_batch_results(self, record, result_dir: Path):
        """Display per-molecule ConfFlow summary table using manifest as authority."""
        from ...core.lifecycle import TaskStatus
        from ...services.confflow_results import load_summary

        headers = ["Molecule", "Status", "Conformers (in→out)", "Duration (s)", "Steps", "Progress"]
        rows: list[list[str]] = []

        tasks = self._load_tasks(record)

        if tasks:
            for task in tasks:
                mol_name = task.task_id
                summary_file = _confflow_summary_file(result_dir, mol_name)
                if task.status in (TaskStatus.downloaded, TaskStatus.analyzed) and summary_file.exists():
                    try:
                        s = load_summary(summary_file)
                        steps = ", ".join(f"{k}={v}" for k, v in s.step_status_counts.items()) if s.step_status_counts else ""
                        progress = _step_progress_text(result_dir, mol_name)
                        rows.append([mol_name, "✓ Done", f"{s.initial_conformers}→{s.final_conformers}", f"{s.total_duration_seconds:.1f}", steps, progress])
                    except Exception:
                        rows.append([mol_name, "⚠ Parse Error", "", "", "", ""])
                elif task.status == TaskStatus.failed:
                    reason = f" ({task.error_message})" if task.error_message else ""
                    rows.append([mol_name, f"✗ Failed{reason}", "", "", "", ""])
                elif task.status == TaskStatus.remote_completed:
                    reason = f" ({task.error_message})" if task.error_message else ""
                    rows.append([mol_name, f"⚠ Download Failed{reason}", "", "", "", ""])
                elif task.status in (TaskStatus.submitting, TaskStatus.submitted, TaskStatus.running):
                    label = "Running" if task.status == TaskStatus.running else "Pending"
                    progress = _step_progress_text(result_dir, mol_name)
                    rows.append([mol_name, f"⏳ {label}", "", "", "", progress])
                else:
                    rows.append([mol_name, "✗ Missing", "", "", "", ""])
        else:
            # Fallback: scan local directories if no manifest
            if result_dir.exists():
                for task_dir in sorted(d for d in result_dir.iterdir() if d.is_dir()):
                    mol_name = (
                        task_dir.name.removesuffix("_confflow_work")
                        if task_dir.name.endswith("_confflow_work")
                        else task_dir.name
                    )
                    summary_file = _confflow_summary_file(result_dir, mol_name)
                    if summary_file.exists():
                        try:
                            s = load_summary(summary_file)
                            steps = ", ".join(f"{k}={v}" for k, v in s.step_status_counts.items()) if s.step_status_counts else ""
                            progress = _step_progress_text(result_dir, mol_name)
                            rows.append([mol_name, "✓ Done", f"{s.initial_conformers}→{s.final_conformers}", f"{s.total_duration_seconds:.1f}", steps, progress])
                        except Exception:
                            rows.append([mol_name, "⚠ Parse Error", "", "", "", ""])
                    else:
                        rows.append([mol_name, "✗ Missing", "", "", "", ""])

        if not rows:
            self._set_parsed_results_label("ConfFlow Batch Results (no tasks)")
            self.result_text.setVisible(False)
            self.result_table.setRowCount(0)
            return

        self.result_text.setVisible(False)
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                self.result_table.setItem(r, c, QTableWidgetItem(val))
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._set_parsed_results_label(f"ConfFlow Batch Results ({len(rows)} molecules)")

    def _set_parsed_results_label(self, prefix: str) -> None:
        notice = tr("Execution output parsed; scientific review required", self._language)
        self.result_label.setText(f"{prefix} - {notice}")

    def _on_result_row_double_clicked(self, item: QTableWidgetItem) -> None:
        """Dispatch a double-click on a result-table row to the detail pane."""
        if item is None:
            return
        row = item.row()
        first_col = self.result_table.item(row, 0)
        if first_col is None:
            self.detail_pane.clear()
            return
        cached = first_col.data(Qt.UserRole)
        task_id = str(first_col.text())
        if isinstance(cached, dict) and cached.get("kind") == "uncertain":
            self._show_uncertain_row_detail(task_id, cached.get("status"), cached.get("error"))
            return
        if isinstance(cached, dict) and cached.get("kind") == "analysis":
            # Stash payload context on the first column so we can parse the
            # right file when the user double-clicks.
            workspace = cached.get("workspace")
            task = cached.get("task")
            self._render_detail_for_task(task_id, task, workspace)
            return
        # Default — fall back to clearing the pane.
        self.detail_pane.clear()

    def _show_uncertain_row_detail(self, task_id: str, status: str | None, error: str | None) -> None:
        self.detail_pane.title_label.setText(task_id)
        if error:
            self.detail_pane.status_label.setText(
                f"⚠ {status or 'Uncertain'}: {error}"
            )
            self.detail_pane.status_label.setStyleSheet(f"font-weight: 600; color: {Colors.WARNING};")
            self.detail_pane.error_value.setText(error)
            self.detail_pane.error_value.setVisible(True)
        else:
            self.detail_pane.status_label.setText(str(status or "Uncertain"))
            self.detail_pane.status_label.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_SECONDARY};")
            self.detail_pane.error_value.setText("")
            self.detail_pane.error_value.setVisible(False)
        for lbl in (
            self.detail_pane.energy_value,
            self.detail_pane.zpe_value,
            self.detail_pane.gibbs_value,
            self.detail_pane.imag_value,
            self.detail_pane.walltime_value,
            self.detail_pane.cputime_value,
        ):
            lbl.setText("—")
        self.detail_pane.termination_value.setText("—")
        self.detail_pane.geometry_view.setPlainText("(uncertain task — no parsed output)")

    def _render_detail_for_task(self, task_id: str, task, workspace: Path | None) -> None:
        """Resolve a parser output file and render the parsed result to the pane.

        Tries cache first; on miss, calls ``_resolve_output_path`` and the
        appropriate parser. The parser calls are monkeypatched in unit tests
        to avoid spawning the (slow, license-bound) real Gaussian.
        """
        from ...core.parsers.gaussian import parse_gaussian_log
        from ...core.parsers.orca import parse_orca_out

        output_path = _resolve_output_path(task, workspace)
        if output_path is None:
            self.detail_pane.title_label.setText(task_id)
            self.detail_pane.status_label.setText(tr("Output file not found", self._language))
            self.detail_pane.status_label.setStyleSheet(f"font-weight: 600; color: {Colors.ERROR};")
            self.detail_pane.geometry_view.setPlainText("")
            return

        sig = (task_id, output_path.stat().st_mtime, output_path.stat().st_size)
        cached = self._detail_cache.get(sig)
        if cached is not None:
            self._render_cached_detail(cached, task_id)
            return

        try:
            if output_path.suffix.lower() == ".log":
                result: GaussianResult | OrcaResult = parse_gaussian_log(output_path)
            elif output_path.suffix.lower() == ".out":
                result = parse_orca_out(output_path)
            else:
                self.detail_pane.clear()
                return
        except Exception as exc:
            self.detail_pane.title_label.setText(task_id)
            self.detail_pane.status_label.setText(tr("Parse error", self._language))
            self.detail_pane.status_label.setStyleSheet(f"font-weight: 600; color: {Colors.ERROR};")
            self.detail_pane.error_value.setText(str(exc))
            self.detail_pane.error_value.setVisible(True)
            return

        self._detail_cache[sig] = result
        if output_path.suffix.lower() == ".log":
            self.detail_pane.render_gaussian(result)
        else:
            self.detail_pane.render_orca(result)

    def _render_cached_detail(self, cached, task_id: str) -> None:
        # Heuristic by attribute set (GaussianResult vs OrcaResult / mock).
        if hasattr(cached, "zpe_au") and hasattr(cached, "walltime_seconds") and hasattr(cached, "error_termination"):
            if hasattr(cached, "total_energy_au") or "Total" in type(cached).__name__ or cached.__class__.__name__ == "OrcaResult":
                self.detail_pane.render_orca(cached)
                return
            self.detail_pane.render_gaussian(cached)
            return
        # Fallback by class name
        if cached.__class__.__name__ == "OrcaResult":
            self.detail_pane.render_orca(cached)
        else:
            self.detail_pane.render_gaussian(cached)

    def _show_analysis_rows(self, rows: list[list[str]], *, tasks=None, workspace: Path | None = None):
        headers = [tr("Task", self._language), tr("File", self._language), tr("Program", self._language), tr("Energy(Hartree)", self._language), "Gibbs(Hartree)", "ZPE(Hartree)", tr("Imag.Freq", self._language), tr("Diagnosis", self._language)]
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.restore_column_widths("runs_results.preview")
        self.result_table.setRowCount(len(rows))
        # Build a quick lookup from task_id to the corresponding TaskRecord
        # so the detail pane can re-parse the right output on double-click.
        task_by_id: dict[str, object] = {}
        if tasks:
            for t in tasks:
                task_by_id[getattr(t, "task_id", "")] = t
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                item = QTableWidgetItem(val)
                if c == COL_TASK:
                    task_id = str(row[COL_TASK]) if row else ""
                    item.setData(
                        Qt.UserRole,
                        {
                            "kind": "analysis",
                            "task": task_by_id.get(task_id),
                            "workspace": workspace,
                        },
                    )
                self.result_table.setItem(r, c, item)
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

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
        self.result_table.restore_column_widths("runs_results.preview")
        self.result_table.setRowCount(len(data))
        for r, row in enumerate(data):
            for c, val in enumerate(row):
                self.result_table.setItem(r, c, QTableWidgetItem(val))
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)

    def _auto_refresh_active(self):
        """Periodically refresh status for submitted/running runs and recover remote_completed."""
        if getattr(self, '_auto_refresh_running', False):
            return
        workspace = self._workspace()
        runs = RunService(workspace).list_runs()
        active = [
            r for r in runs
            if r.status_summary.get("submitting", 0) > 0
            or r.status_summary.get("submitted", 0) > 0
            or r.status_summary.get("running", 0) > 0
        ]
        # Include remote_completed runs that haven't permanently failed download
        needs_download = [
            r for r in runs
            if r not in active
            and r.status_summary.get("remote_completed", 0) > 0
            and not getattr(self, '_download_backoff', {}).get(r.run_id, 0) > 2
        ]
        # Skip runs the monitor-driven flush is already handling.
        active = [r for r in active if r.run_id not in self._in_progress]
        needs_download = [r for r in needs_download if r.run_id not in self._in_progress]
        if not active and not needs_download:
            return

        self._auto_refresh_running = True
        claimed = [r.run_id for r in active] + [r.run_id for r in needs_download]
        self._in_progress.update(claimed)
        backoff = getattr(self, '_download_backoff', {})

        def _run():
            errors = []
            downloaded = []
            dl_failures: dict[str, int] = {}

            for record in active:
                outcome = self._execute_refresh_use_case(
                    record,
                    self._get_download_patterns(record),
                    download=True,
                )
                if outcome.errors:
                    errors.extend(f"{record.run_id}: {error}" for error in outcome.errors)
                elif outcome.transfer_records:
                    downloaded.append(record.run_id)
            # Auto-recover remote_completed runs
            for record in needs_download:
                outcome = self._execute_download_use_case(
                    record,
                    self._get_download_patterns(record),
                )
                if outcome.errors:
                    errors.extend(f"{record.run_id}: {error}" for error in outcome.errors)
                    dl_failures[record.run_id] = backoff.get(record.run_id, 0) + 1
                else:
                    downloaded.append(record.run_id)
                    dl_failures[record.run_id] = 0
            return downloaded, errors, dl_failures

        from ..workers import BackgroundWorker
        worker = BackgroundWorker(_run)
        def _report(result):
            downloaded, errors, dl_failures = result
            if not hasattr(self, '_download_backoff'):
                self._download_backoff = {}
            self._download_backoff.update(dl_failures)
            if downloaded:
                self._status_cb(
                    tr(
                        "Run complete; results downloaded: {ids}",
                        self._language,
                        ids=", ".join(downloaded),
                    )
                )
            if errors:
                self._status_cb(
                    tr("Automatic refresh failed: {errors}", self._language, errors="; ".join(errors))
                )

        worker.result.connect(_report)

        def _on_done():
            self._auto_refresh_running = False
            self._in_progress.difference_update(claimed)
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
        def _run():
            outcome = self._execute_refresh_use_case(
                record,
                self._get_download_patterns(record),
                download=True,
            )
            if outcome.errors:
                raise RuntimeError("; ".join(outcome.errors))
            if not outcome.transfer_records and not outcome.failures:
                return tr("Refreshed", self._language)
            return tr(
                "Download done: {n} files, failed: {f}",
                self._language,
                n=len(outcome.transfer_records),
                f=len(outcome.failures),
            )

        from ..workers import BackgroundWorker
        worker = BackgroundWorker(_run)
        worker.result.connect(lambda msg: self._status_cb(msg) if msg else None)
        worker.error.connect(lambda e: self._status_cb(tr("Refresh failed: {e}", self._language, e=e)))
        worker.finished.connect(lambda: self._on_refresh_done())
        worker.finished.connect(lambda: self._bg_workers.remove(worker) if worker in self._bg_workers else None)
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        worker.start()

    def _on_refresh_done(self):
        self.refresh_run_list()
        record = self._selected_record()
        if record:
            self._load_result_preview(record)

    def _get_download_patterns(self, record: RunRecord) -> list[str]:
        """Get download patterns based on command template (auto-detect software)."""
        settings = GuiSettingsStore().load()
        profiles = settings.software_profiles or {}
        exe = _command_executable(record.command_template)
        for profile in profiles.values():
            # Match on the actual program (first token), not a substring anywhere,
            # so e.g. "python run_orca.py" is not misdetected as ORCA.
            if exe and exe == _command_executable(profile.get("command_template", "")):
                raw = profile.get("download_patterns", "")
                return [p.strip() for p in raw.split(",") if p.strip()]
        return [".log", ".out"]

    def _retry_failed(self):
        record = self._selected_record()
        if record is None:
            return
        if not self._begin_remote_mutation():
            return
        try:
            outcome = self._coordinator_for(self._result_workspace(record)).retry_failed(record.run_id)
        except Exception as exc:
            self._finish_remote_mutation()
            self._retry_feedback.error(tr("Retry failed", self._language))
            self._status_cb(tr("Submit failed: {e}", self._language, e=exc))
            return
        if outcome.errors:
            self._finish_remote_mutation()
            self._retry_feedback.error(tr("Retry failed", self._language))
            self._status_cb(tr("Submit failed: {e}", self._language, e="; ".join(outcome.errors)))
            return
        changed = outcome.changed_count
        self.refresh_run_list()
        if changed <= 0:
            self._finish_remote_mutation()
            self._status_cb(tr("No failed tasks", self._language))
            return
        self._retry_feedback.pending(tr("Retrying...", self._language))
        try:
            self._submit_record(
                record.run_id,
                feedback=self._retry_feedback,
                mutation_owned=True,
            )
        except Exception as exc:
            self._retry_feedback.error(tr("Retry failed", self._language))
            self._status_cb(tr("Submit failed: {e}", self._language, e=exc))

    def _retry_download(self):
        """Re-attempt download for tasks still at remote_completed."""
        record = self._selected_record()
        if record is None:
            return
        if not record.status_summary.get("remote_completed", 0):
            self._status_cb(tr("No tasks awaiting download", self._language))
            return
        self._retry_dl_running = True
        self._retry_download_feedback.pending(tr("Downloading...", self._language))

        def _run():
            outcome = self._execute_download_use_case(
                record,
                self._get_download_patterns(record),
            )
            if outcome.errors and not outcome.failures:
                raise RuntimeError("; ".join(outcome.errors))
            return outcome.transfer_records, outcome.failures

        from ..workers import BackgroundWorker
        worker = BackgroundWorker(_run)

        def _done(result):
            self._retry_dl_running = False
            _recs, failures = result
            self.refresh_run_list()
            if failures:
                self._retry_download_feedback.error(tr("Partial: {n} failed", self._language, n=len(failures)))
                self._status_cb(tr("Download partial: {n} failed", self._language, n=len(failures)))
            else:
                self._retry_download_feedback.success(tr("Downloaded", self._language))
                self._status_cb(tr("Download complete", self._language))

        def _err(exc):
            self._retry_dl_running = False
            self._retry_download_feedback.error(tr("Download failed", self._language))
            self._status_cb(tr("Download error: {e}", self._language, e=exc))

        worker.result.connect(_done)
        worker.error.connect(_err)
        if not hasattr(self, "_bg_workers"):
            self._bg_workers = []
        worker.finished.connect(lambda: self._bg_workers.remove(worker) if worker in self._bg_workers else None)
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        worker.start()

    def _open_results_folder(self):
        """Open the local results directory in file explorer."""
        record = self._selected_record()
        if record is None:
            return
        results_dir = self._download_directory(record)
        if not results_dir.exists():
            self._status_cb(tr("Results directory not found", self._language))
            return
        if hasattr(os, "startfile"):
            os.startfile(results_dir)
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(results_dir)])

    def _rerun_all(self):
        record = self._selected_record()
        if record is None:
            return
        if not self._begin_remote_mutation():
            return
        try:
            outcome = self._coordinator_for(self._result_workspace(record)).rerun(record.run_id)
        except Exception as exc:
            self._finish_remote_mutation()
            self._status_cb(tr("Submit failed: {e}", self._language, e=exc))
            return
        if outcome.errors:
            self._finish_remote_mutation()
            self._status_cb("; ".join(outcome.errors))
            return
        self.refresh_run_list()
        try:
            self._submit_record(record.run_id, mutation_owned=True)
        except Exception as exc:
            self._status_cb(tr("Submit failed: {e}", self._language, e=exc))

    def _selected_run_ids(self) -> list[str]:
        ids: list[str] = []
        for row in sorted({idx.row() for idx in self.table.selectedIndexes()}):
            item = self.table.item(row, 0)
            if item:
                ids.append(item.text())
        return ids

    def _compare_selected(self):
        """Compare energies across the selected runs and show them in the result table."""
        from PySide6.QtWidgets import QInputDialog

        from ...services.analysis_profiles import AnalysisProfileStore
        run_ids = self._selected_run_ids()
        if len(run_ids) < 2:
            self._status_cb(tr("Select at least two runs to compare", self._language))
            return
        profiles = sorted(AnalysisProfileStore().list_profiles())
        if not profiles:
            return
        default_idx = profiles.index("gaussian_opt_freq") if "gaussian_opt_freq" in profiles else 0
        profile, ok = QInputDialog.getItem(
            self, tr("Compare Selected", self._language),
            tr("Analysis profile:", self._language), profiles, default_idx, False,
        )
        if not ok:
            return
        energy_field = "final_energy" if profile.startswith("orca") else "scf_energy"
        workspace = self._workspace()

        def _run():
            from ...services.comparison import compare_runs
            return compare_runs(workspace, run_ids, energy_field=energy_field, profile_name=profile)

        from ..workers import BackgroundWorker
        worker = BackgroundWorker(_run)
        worker.result.connect(self._show_comparison_rows)
        worker.error.connect(lambda e: self._status_cb(tr("Compare failed: {e}", self._language, e=e)))
        worker.finished.connect(lambda: self._bg_workers.remove(worker) if worker in self._bg_workers else None)
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        worker.start()

    def _show_comparison_rows(self, comparison):
        if not comparison.rows:
            self._set_parsed_results_label(tr("Cross-run Comparison", self._language) + " - " + tr("No comparable results", self._language))
            self.result_text.setVisible(False)
            self.result_table.setRowCount(0)
            return
        headers = comparison.field_names
        self.result_text.setVisible(False)
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(headers)
        self.result_table.setRowCount(len(comparison.rows))
        for r, row in enumerate(comparison.rows):
            for c, key in enumerate(headers):
                self.result_table.setItem(r, c, QTableWidgetItem(str(row.get(key, ""))))
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._set_parsed_results_label(tr("Cross-run Comparison", self._language) + f" ({len(comparison.rows)})")

    def _stop_run(self):
        record = self._selected_record()
        if record is None:
            return
        if not self._begin_remote_mutation():
            return
        if QMessageBox.question(self, tr("Stop", self._language), tr("Stop run {run_id}?", self._language, run_id=record.run_id),
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            self._finish_remote_mutation()
            return
        self._stop_feedback.pending(tr("Stopping...", self._language))

        def _run(_ctx: WorkerContext):
            outcome = self._coordinator_for(self._result_workspace(record)).cancel(record.run_id)
            return outcome.changed_count, outcome.errors

        try:
            start_context_worker(
                self,
                target=_run,
                registry_attr="_bg_workers",
                on_result=lambda result: self._on_stop_done(record.run_id, result),
                on_error=self._on_stop_error,
                on_finished=self._finish_remote_mutation,
            )
        except Exception as exc:
            self._finish_remote_mutation()
            self._on_stop_error(exc)

    def _on_stop_error(self, exc: Exception | str):
        self._stop_feedback.error(tr("Stop failed", self._language))
        self._status_cb(tr("Stop failed: {e}", self._language, e=exc))

    def _on_stop_done(self, run_id: str, result: tuple[int, list[str]]):
        changed, errors = result
        self.refresh_run_list()
        if errors:
            self._stop_feedback.error(tr("Stop failed", self._language))
            self._status_cb(tr("Stop failed: {e}", self._language, e="; ".join(errors)))
        else:
            self._stop_feedback.success(tr("Stopped", self._language))
            self._status_cb(tr("Stopped: {run_id}", self._language, run_id=run_id))

    def _confirm_submitted(self) -> None:
        self._resolve_uncertain_selection(confirm=True)

    def _abandon_submit(self) -> None:
        self._resolve_uncertain_selection(confirm=False)

    def _resolve_uncertain_selection(self, *, confirm: bool) -> None:
        record = self._selected_record()
        if record is None or not record.status_summary.get("uncertain", 0):
            return
        task_ids = self._selected_uncertain_task_ids()
        if not task_ids:
            return
        action = "confirm" if confirm else "abandon"
        prompt = (
            tr(
                "Confirm submission state for {n} uncertain task(s)?",
                self._language,
                n=len(task_ids),
            )
            if confirm
            else self._abandon_confirmation_text(len(task_ids))
        )
        if QMessageBox.question(
            self,
            tr("Uncertain", self._language),
            prompt,
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        workspace = self._result_workspace(record)

        def _run():
            from ...core.lifecycle import TaskStatus

            current = RunService(workspace).repository.load_tasks(record.run_id)
            current_by_id = {task.task_id: task for task in current}
            if any(
                task_id not in current_by_id
                or current_by_id[task_id].status != TaskStatus.uncertain
                for task_id in task_ids
            ):
                raise ValueError("selected tasks are no longer uncertain")
            coordinator = self._coordinator_for(workspace)
            if confirm:
                return coordinator.confirm_submitted(record.run_id, task_ids)
            return coordinator.abandon_submit(record.run_id, task_ids)

        def _done(outcome):
            self.refresh_run_list()
            self._update_uncertain_actions()
            if outcome.errors:
                self._status_cb("; ".join(outcome.errors))
            else:
                self._status_cb(
                    f"{action.title()}ed {outcome.changed_count} uncertain task(s)"
                )

        start_context_worker(
            self,
            target=lambda _ctx: _run(),
            registry_attr="_bg_workers",
            on_result=_done,
            on_error=lambda error: self._status_cb(
                f"{action.title()} uncertain tasks failed: {error}"
            ),
        )

    def _abandon_confirmation_text(self, count: int) -> str:
        return tr(
            "Abandon {n} uncertain task(s) only after confirming the remote job does not exist; then retry?",
            self._language,
            n=count,
        )

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
        msg = tr("Delete {n} run records?", self._language, n=len(run_ids)) if len(run_ids) > 1 else tr("Delete run {run_id} record?", self._language, run_id=run_ids[0])
        if QMessageBox.question(self, tr("Delete", self._language), msg,
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        workspace = self._workspace()
        self._delete_feedback.pending(tr("Deleting...", self._language))

        def _run(_ctx: WorkerContext):
            deleted = 0
            errors: list[str] = []
            for rid in run_ids:
                try:
                    record = RunService(workspace).load_run(rid)
                    record_workspace = self._result_workspace(record)
                    outcome = self._coordinator_for(record_workspace).delete(rid)
                    if outcome.errors:
                        errors.extend(f"{rid}: {error}" for error in outcome.errors)
                    else:
                        deleted += 1
                except Exception as exc:
                    errors.append(f"{rid}: {exc}")
            return deleted, errors

        def _done(result):
            deleted, errors = result
            self.refresh_run_list()
            if errors:
                self._delete_feedback.error(tr("Delete failed", self._language))
                self._status_cb(tr("Delete failed", self._language) + f": {'; '.join(errors)}")
            else:
                self._delete_feedback.success(tr("Deleted {n}", self._language, n=deleted))
                self._status_cb(tr("Deleted: {n} records", self._language, n=deleted))

        def _error(error: Exception):
            self._delete_feedback.error(tr("Delete failed", self._language))
            self._status_cb(tr("Delete failed", self._language) + f": {error}")

        start_context_worker(
            self,
            target=_run,
            registry_attr="_bg_workers",
            on_result=_done,
            on_error=_error,
        )

    def _submit_record(
        self,
        run_id: str,
        *,
        feedback: ButtonFeedback | None = None,
        mutation_owned: bool = False,
    ):
        if not mutation_owned and not self._begin_remote_mutation():
            if feedback is not None:
                feedback.error(tr("Retry failed", self._language))
            return False
        workspace = self._workspace()

        def _run(_ctx: WorkerContext):
            outcome = self._coordinator_for(workspace).submit(run_id)
            if outcome.errors or not outcome.submit_results:
                raise RuntimeError("; ".join(outcome.errors) or "submit returned no result")
            return outcome.submit_results[0]

        try:
            start_context_worker(
                self,
                target=_run,
                registry_attr="_bg_workers",
                on_result=lambda result: self._on_submit_done(result, feedback=feedback),
                on_error=lambda error: self._on_submit_error(error, feedback=feedback),
                on_finished=self._finish_remote_mutation,
            )
        except Exception:
            self._finish_remote_mutation()
            raise
        return True

    def _begin_remote_mutation(self) -> bool:
        if self._shutting_down or self._remote_mutation_running:
            if not self._shutting_down:
                self._status_cb(tr("Remote operation already in progress", self._language))
            return False
        self._remote_mutation_running = True
        return True

    def _finish_remote_mutation(self) -> None:
        self._remote_mutation_running = False

    def _on_submit_error(self, exc: Exception | str, *, feedback: ButtonFeedback | None = None):
        if feedback is not None:
            feedback.error(tr("Retry failed", self._language))
        self._status_cb(tr("Submit failed: {e}", self._language, e=exc))

    def _on_submit_done(self, result, *, feedback: ButtonFeedback | None = None):
        self.refresh_run_list()
        errors = list(getattr(result, "errors", []) or [])
        if errors:
            if feedback is not None:
                feedback.error(tr("Retry failed", self._language))
            self._status_cb(tr("Submit failed: {e}", self._language, e="; ".join(errors)))
            return
        if feedback is not None:
            feedback.success(tr("Retried", self._language))
        self._status_cb(tr("Submitted: {batch_id}", self._language, batch_id=result.batch_id))
        self._start_monitoring()

    def _show_logs(self):
        record = self._selected_record()
        if record is None:
            return
        remote_dir = remote_run_dir(record.remote_dir, record.run_id)
        self.result_text.setPlainText(
            f"{tr('Remote logs', self._language)}:\n  {remote_dir}/.jobdesk_submit.log\n  {remote_dir}/.jobdesk_submit.err")
        self.result_text.setVisible(True)

    def _show_paths(self):
        record = self._selected_record()
        if record is None:
            return
        ws = self._result_workspace(record)
        self.result_text.setPlainText(
            f"{tr('Run directory', self._language)}: {record.run_dir}\n"
            f"Database: {record.run_dir.parent / 'jobdesk.db'}\n"
            f"{tr('Results directory', self._language)}: {ws}")
        self.result_text.setVisible(True)

    def shutdown(self):
        self._shutting_down = True
        self._finish_remote_mutation()
        self._preview_request_id += 1
        self._refresh_timer.stop()
        self._preview_timer.stop()
        self._activation_timer.stop()
        for timer in self._task_done_timers.values():
            timer.stop()
        self._task_done_timers.clear()
        self._pending_task_events.clear()
        self._monitor.stop_all()
        for w in list(getattr(self, "_bg_workers", [])):
            w.stop_safely(3000)
        w = getattr(self, "_worker", None)
        if w and hasattr(w, "stop_safely"):
            w.stop_safely(3000)
        self._session_pool.close()


def _too_large_for_preview(path: Path) -> bool:
    return path.stat().st_size > MAX_PREVIEW_FILE_BYTES


def _confflow_summary_file(result_dir: Path, mol_name: str) -> Path:
    candidates = [
        result_dir / f"{mol_name}_confflow_work" / "run_summary.json",
        result_dir / mol_name / f"{mol_name}_confflow_work" / "run_summary.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _confflow_step_stats_file(result_dir: Path, mol_name: str) -> Path:
    """Locate ``workflow_stats.json`` next to a run summary if present."""
    candidates = [
        result_dir / f"{mol_name}_confflow_work" / "workflow_stats.json",
        result_dir / mol_name / f"{mol_name}_confflow_work" / "workflow_stats.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _step_progress_text(result_dir: Path, mol_name: str) -> str:
    """Render a short step-progress string for the Runs page table."""
    from ...services.confflow_results import format_step_progress, load_step_progress

    stats_file = _confflow_step_stats_file(result_dir, mol_name)
    progress = load_step_progress(stats_file)
    return format_step_progress(progress)


def _confflow_result_dir_has_summary(record, result_dir: Path, tasks=None) -> bool:
    from ...core.lifecycle import TaskStatus

    if tasks is not None:
        return any(
            task.status in (TaskStatus.downloaded, TaskStatus.analyzed)
            and _confflow_summary_file(result_dir, task.task_id).exists()
            for task in tasks
        )
    return any(result_dir.rglob("run_summary.json")) if result_dir.exists() else False


def _command_executable(command: str) -> str:
    """Return the lowercased basename of a command's first token (the program)."""
    tokens = command.split()
    if not tokens:
        return ""
    return tokens[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()


def _dir_parse_signature(result_dir: Path) -> tuple:
    """Cheap signature of the parseable outputs under a results dir (name+mtime+size).

    Used to invalidate the parse cache when files change, without re-parsing.
    """
    if not result_dir.exists():
        return ()
    items = []
    for p in sorted(result_dir.rglob("*")):
        if p.suffix.lower() in (".log", ".out") and p.is_file():
            st = p.stat()
            items.append((str(p), st.st_mtime, st.st_size))
    return tuple(items)


def _analysis_row(task_id: str, file_name: str, program: str, result, diagnosis: str | None, language: str) -> list[str]:
    """Build an 8-column analysis row from a parsed Gaussian/ORCA result.

    Column order matches the ``COL_*`` constants at the top of this module
    and the header list in :py:meth:`RunsResultsPage._show_analysis_rows`.
    """
    energy = f"{result.final_energy_au:.6f}" if result.final_energy_au else ""
    gibbs = f"{result.gibbs_au:.6f}" if result.gibbs_au else ""
    zpe = f"{result.zpe_au:.6f}" if result.zpe_au else ""
    imag = str(result.imaginary_freq_count)
    return [
        task_id,
        file_name,
        program,
        energy,
        gibbs,
        zpe,
        imag,
        diagnosis or tr("OK", language),
    ]


def _placeholder_analysis_row(
    task_id: str,
    file_name: str,
    program: str,
    diagnosis: str,
) -> list[str]:
    """Build an 8-column analysis row when parsing failed or the file is too large.

    Fills the energy / gibbs / zpe / imag columns with empty strings so the
    row width matches :func:`_analysis_row` and the ``COL_*`` constants at
    the top of this module continue to line up.
    """
    return [
        task_id,
        file_name,
        program,
        diagnosis,
        "",
        "",
        "",
        "",
    ]
