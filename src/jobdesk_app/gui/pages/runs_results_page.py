"""运行+结果合并页 — 上方 run 列表，下方结果预览。"""

from __future__ import annotations

import csv
import logging
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable

from PySide6.QtCore import QItemSelectionModel, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from ...core.parsers import GaussianResult, OrcaResult

from ...application.runs_actions import RunActionIntent, RunsActionController
from ...application.runs_artifacts import (
    MAX_PREVIEW_FILE_BYTES,
    ComparePayload,
    PreviewPayload,
    PreviewRequest,
    UncertainTaskPayload,
    build_preview_payload,
    choose_existing_artifact,
    has_workspace_binding,
    is_preview_too_large,
    resolve_result_workspace,
    resolve_run_artifacts,
)
from ...application.runs_monitor import (
    MonitorContext,
    RunsMonitorController,
    monitor_watch_id,
)
from ...application.runs_query import (
    RunFilterSpec,
    RunQueryController,
    RunQuerySnapshot,
    RunSelectionState,
    filter_run_snapshots,
    workflow_filter_value,
)
from ...application.runs_runtime import RunsPageRuntime
from ...config.servers import load_servers
from ...core.confflow_contract import (
    RUN_SUMMARY_FILE,
    WORK_DIR_SUFFIX,
    WORKFLOW_STATE_FILE,
    WORKFLOW_STATS_FILE,
)
from ...core.run import WorkflowKind, remote_run_dir
from ...services.confflow_control import CONTROL_BACKEND
from ...services.confflow_control_state import load_state
from ...services.gui_settings import GuiSettingsStore
from ...services.run_coordinator import RunCoordinator
from ...services.run_service import RunRecord, RunService
from ...services.session_pool import SessionPool
from ...services.ssh_confflow_client import SSHConfFlowClient
from ..button_feedback import ButtonFeedback, ButtonRole
from ..design.components import StyledTableWidget
from ..design.tokens import Colors, Metrics, Radius
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client
from ..theme import section_title_label
from ..widgets import EmptyStateHint
from ..worker_utils import WorkerContext, start_context_worker
from .runs_detail_pane import ResultDetailPane, _resolve_output_path

CHECKPOINT_RETRY_BASE_MS = 1000
CHECKPOINT_RETRY_MAX_MS = 30000
ACTIVITY_LOG_MAX_BLOCKS = 500
RUNS_SPLITTER_SETTINGS_KEY = "runs.main"

_logger = logging.getLogger(__name__)


class _RunsGuiDispatcher(QObject):
    """Deliver worker payloads through an explicit GUI-thread queue."""

    dispatch_requested = Signal(object)

    def __init__(self, owner: "RunsResultsPage") -> None:
        super().__init__(owner)
        self._owner = owner
        self._open = True
        self.dispatch_requested.connect(self._dispatch, Qt.QueuedConnection)

    def post(self, callback: Callable[..., Any], *args: Any) -> None:
        if not self._open or self._owner._shutting_down:
            return
        payload = (callback, args)
        if QThread.currentThread() == self.thread():
            self._dispatch(payload)
        else:
            self.dispatch_requested.emit(payload)

    def _dispatch(self, payload: object) -> None:
        if not self._open or self._owner._shutting_down:
            return
        if not isinstance(payload, tuple) or len(payload) != 2:
            return
        callback, args = payload
        if not callable(callback) or not isinstance(args, tuple):
            return
        callback(*args)

    def close(self) -> None:
        if not self._open:
            return
        self._open = False
        # Remove the receiver before its owner can be torn down.  This also
        # prevents a queued signal emitted by a worker just before shutdown
        # from retaining a dead page callback in the Qt event queue.
        try:
            self.dispatch_requested.disconnect(self._dispatch)
        except (RuntimeError, TypeError):
            pass


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


def _format_status_overview(summaries: list[dict[str, int]], language: str = "en") -> str:
    """Format a compact status overview from multiple run status summaries.

    Phase 19: lightweight overview that shows aggregate task counts at a glance.
    """
    from ..i18n import tr

    if not summaries:
        return tr("No runs yet", language)

    totals = {
        "running": 0,
        "submitted": 0,
        "completed": 0,
        "failed": 0,
        "total": 0,
    }

    for summary in summaries:
        totals["running"] += summary.get("running", 0) + summary.get("submitting", 0)
        totals["submitted"] += summary.get("submitted", 0)
        totals["completed"] += (
            summary.get("downloaded", 0) + summary.get("analyzed", 0) + summary.get("remote_completed", 0)
        )
        totals["failed"] += summary.get("failed", 0)
        totals["total"] += sum(summary.values())

    parts = []
    if totals["running"] > 0:
        parts.append(tr("Running", language) + f" {totals['running']}")
    if totals["submitted"] > 0:
        parts.append(tr("Submitted", language) + f" {totals['submitted']}")
    if totals["completed"] > 0:
        parts.append(tr("Completed", language) + f" {totals['completed']}")
    if totals["failed"] > 0:
        parts.append(tr("Failed", language) + f" {totals['failed']}")

    if not parts:
        return tr("No active runs", language)
    return " · ".join(parts)


# A run may be shown as active in the list before it has a remote monitor
# (``local_ready``/``uploaded``).  Keep the monitor lifecycle narrower and
# centralised so startup and stale-watcher pruning cannot drift apart.
_MONITOR_ACTIVE_STATUSES = frozenset({"submitting", "submitted", "running"})
_ACTIVE_STATUSES = {"local_ready", "uploaded", *_MONITOR_ACTIVE_STATUSES}
_COMPLETED_STATUSES = {"remote_completed", "downloaded", "analyzed"}


def _has_monitor_active_status(summary: Any) -> bool:
    """Return whether a run needs a remote monitor watcher."""
    try:
        return any(int(summary.get(status, 0) or 0) > 0 for status in _MONITOR_ACTIVE_STATUSES)
    except (AttributeError, TypeError, ValueError):
        return False


def _status_visual(summary: dict[str, int]) -> tuple[str, str]:
    if summary.get("failed", 0):
        return "#b91c1c", "Failed"
    if summary.get("uncertain", 0):
        return "#b45309", "Uncertain"
    if any(summary.get(key, 0) for key in _ACTIVE_STATUSES):
        return "#1d4ed8", "Active"
    if any(summary.get(key, 0) for key in _COMPLETED_STATUSES):
        return "#15803d", "Completed"
    if summary.get("cancelled", 0):
        return "#475569", "Cancelled"
    return "#475569", "Other"


def _format_workflow_kind(workflow_kind: object, language: str = "en") -> str:
    """Render the persisted workflow kind without inferring it from commands."""
    value = workflow_filter_value(workflow_kind)
    if value == "Unknown":
        return tr("Unknown", language)
    return value


def _format_row(record: RunRecord, language: str = "en") -> list[str]:
    return [
        record.run_id,
        record.server_id,
        record.remote_dir,
        _format_status(record.status_summary, language),
        _format_workflow_kind(record.workflow_kind, language),
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

    def __init__(
        self,
        state: Any,
        log_cb: Callable[[str], None] | None,
        status_cb: Callable[[str], None] | None,
        coordinator_factory: Callable[..., RunCoordinator] | None = None,
        client_factory: Callable[[RunCoordinator, str], SSHConfFlowClient] | None = None,
        session_pool: SessionPool | None = None,
    ) -> None:
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
        self._client_factory = client_factory
        # The application runtime owns the concrete service graph.  Keep
        # closures over this module's historical symbols so existing tests and
        # extensions that monkeypatch them retain their observable seam.
        self._runtime = RunsPageRuntime(
            service_constructor=lambda: RunService,
            session_pool=session_pool,
            session_pool_constructor=SessionPool,
            server_loader=lambda: load_servers(),
            durable_backend_loader=lambda service, run_id: load_state(service, run_id),
            ssh_factory=lambda server: create_ssh_client(server),
            sftp_factory=lambda ssh: create_sftp_client(ssh),
            client_constructor=lambda: SSHConfFlowClient,
        )
        self._owns_session_pool = self._runtime.owns_session_pool
        # Compatibility alias retained for tests/extensions that inspect or
        # replace the page-owned pool.  Runtime methods accept this alias when
        # assembling coordinators and closing the page.
        self._session_pool = self._runtime.session_pool
        self._settings_store = GuiSettingsStore()
        initial_settings = self._settings_store.load()
        self._language = initial_settings.language
        self._shutting_down = False
        self._actions = RunsActionController()
        self._gui_dispatcher = _RunsGuiDispatcher(self)
        self._recovery_running = False
        self._recovery_complete = False
        self._preview_request_id = 0

        layout = QVBoxLayout(self)
        # Phase 18 visual cleanup: bring the page padding in line with
        # the other three pages so the runs-page chrome matches the
        # rest of the design system. The previous (14, 10, 14, 10) had
        # the page content butting up against the splitter handle.
        layout.setContentsMargins(
            Metrics.PAGE_PADDING,
            Metrics.PAGE_PADDING - 4,
            Metrics.PAGE_PADDING,
            Metrics.PAGE_PADDING - 4,
        )
        layout.setSpacing(12)

        # Persistent scrolling activity log (Phase 16). Every status-bar
        # message the page emits via ``self._status_cb`` is *also* appended
        # here, so the user gets the same scrollable trail as SubmitPage's
        # activity list. Implemented as a QTextEdit with append-after-the-
        # end (plus an autoscroll); no blocking I/O.
        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(160)
        self._log_view.setObjectName("RunsActivityLog")
        self._log_view.document().setMaximumBlockCount(ACTIVITY_LOG_MAX_BLOCKS)
        self._log_view.setVisible(False)
        self._last_activity_message: str | None = None
        self._last_reported_run_count: int | None = None
        log_font = QFont("Consolas")
        log_font.setStyleHint(QFont.Monospace)
        log_font.setPixelSize(Metrics.CARD_BODY_FONT_PX)
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
        log_header_row.setSpacing(8)
        self.activity_log_label = QLabel(tr("Activity log", self._language))
        self.activity_log_label.setStyleSheet(
            f"color: {Colors.TEXT}; font-weight: 600; font-size: {Metrics.CARD_TITLE_FONT_PX}px;"
        )
        log_header_row.addWidget(self.activity_log_label)
        log_header_row.addStretch()
        self.activity_log_toggle = QToolButton()
        self.activity_log_toggle.setObjectName("RunsActivityLogToggle")
        self.activity_log_toggle.setCheckable(True)
        self.activity_log_toggle.setChecked(False)
        self.activity_log_toggle.clicked.connect(self._set_activity_log_expanded)
        log_header_row.addWidget(self.activity_log_toggle)
        self.clear_log_btn = QPushButton(tr("Clear Log", self._language))
        self.clear_log_btn.clicked.connect(self._clear_activity_log)
        self.clear_log_btn.setVisible(False)
        log_header_row.addWidget(self.clear_log_btn)
        log_card_layout.addLayout(log_header_row)
        log_card_layout.addWidget(self._log_view)
        layout.addWidget(log_card)

        self._submit_warning_banner = QLabel()
        self._submit_warning_banner.setObjectName("SubmitWarningBanner")
        self._submit_warning_banner.setWordWrap(True)
        self._submit_warning_banner.setStyleSheet(
            f"#SubmitWarningBanner {{ color: {Colors.WARNING}; "
            "background: #fffbeb; border: 1px solid #f59e0b; "
            "border-radius: 6px; padding: 8px 12px; font-weight: 600; }"
        )
        self._submit_warning_banner.setVisible(False)
        layout.addWidget(self._submit_warning_banner)

        # -- Phase 2.1: empty-state hint for "no runs yet" --
        # Shows when the runs list is empty; action buttons route to
        # the Submit page via the go_to_submit_requested signal.
        self._empty_hint = EmptyStateHint(
            title_key="No runs yet",
            body_key=("Build a workflow on the Submit tab and click Submit to Remote. Your runs will appear here."),
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

        # Phase 19: lightweight task status overview bar
        self._status_overview = QWidget()
        self._status_overview.setObjectName("RunsStatusOverview")
        self._status_overview.setStyleSheet(
            f"#RunsStatusOverview {{ background: {Colors.BG_SURFACE}; "
            f"border-bottom: 1px solid {Colors.BORDER}; padding: 8px 16px; }}"
        )
        status_layout = QHBoxLayout(self._status_overview)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(16)
        self._overview_title = QLabel(tr("Runs overview:", self._language), self._status_overview)
        status_layout.addWidget(self._overview_title)
        self._overview_label = QLabel(tr("No runs yet", self._language), self._status_overview)
        self._overview_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.CARD_BODY_FONT_PX}px;")
        status_layout.addWidget(self._overview_label)
        status_layout.addStretch(1)
        self._refresh_overview_timer = QTimer(self)
        self._refresh_overview_timer.setInterval(5000)
        self._refresh_overview_timer.timeout.connect(self._refresh_status_overview)
        # Timer refreshes the aggregate from the already-loaded run records;
        # it never opens the runs database itself.
        layout.addWidget(self._status_overview)

        self._run_records: list[RunRecord] = []
        self._filtered_records: list[RunRecord] = []
        self._run_snapshots: tuple[RunQuerySnapshot, ...] = ()
        # Keep all list reads behind the RunService boundary while exposing
        # immutable projections to filtering/selection code.
        self._run_query = RunQueryController(self._runtime.service)
        self._filters_ready = False

        # ─── Top: Run list ───
        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("RunsSearch")
        self.search_edit.setClearButtonEnabled(True)
        filter_row.addWidget(self.search_edit, 2)
        self.status_filter = QComboBox()
        self.status_filter.setObjectName("RunsStatusFilter")
        filter_row.addWidget(self.status_filter)
        self.server_filter = QComboBox()
        self.server_filter.setObjectName("RunsServerFilter")
        filter_row.addWidget(self.server_filter)
        self.workflow_filter = QComboBox()
        self.workflow_filter.setObjectName("RunsWorkflowFilter")
        filter_row.addWidget(self.workflow_filter)
        self.date_filter = QComboBox()
        self.date_filter.setObjectName("RunsDateFilter")
        filter_row.addWidget(self.date_filter)
        self.refresh_btn = QPushButton()
        self.refresh_btn.setObjectName("RunsRefreshButton")
        self.refresh_btn.setProperty("buttonRole", ButtonRole.REFRESH_ACTION.value)
        self.refresh_btn.clicked.connect(self._refresh_all)
        filter_row.addWidget(self.refresh_btn)
        self.last_updated_label = QLabel()
        self.last_updated_label.setObjectName("RunsLastUpdated")
        self.last_updated_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY};")
        filter_row.addWidget(self.last_updated_label)
        top_layout.addLayout(filter_row)

        self.table = StyledTableWidget()
        self.table.setColumnCount(7)
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
        # Phase 18 visual cleanup: drop the hard 58 px height so the
        # card grows with its content; layout margins give the buttons
        # their natural spacing.
        btn_row = QHBoxLayout(btn_card)
        btn_row.setContentsMargins(16, 8, 16, 8)
        btn_row.setSpacing(8)
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
        bottom_layout.setSpacing(8)

        # "Result Preview" uses the shared ``section_title_label`` helper
        # (22 px / 600)
        # so it stops competing with the page-level activity log label
        # for visual weight.
        self.result_label = section_title_label(tr("Result Preview", self._language))
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
        self.detail_pane.setVisible(False)
        bottom_layout.addWidget(self.detail_pane)

        self._results_card = bottom
        self._results_card.setVisible(False)
        splitter.addWidget(bottom)
        # Phase 18 visual cleanup: stretched the run-list vs. result
        # preview 5:2 ratio — the previous 5:1.5 was visually
        # unbalanced and produced the large empty band the user
        # reported. The 3:2 stretch factor lets the splitter settle
        # into a more natural 5:3 ratio on a typical screen and
        # removes the dead vertical space below the preview buttons.
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self._main_splitter = splitter
        self._restore_main_splitter(initial_settings)
        self._main_splitter.splitterMoved.connect(self._persist_main_splitter)
        layout.addWidget(splitter)

        # Real-time task completion monitor
        from ..run_monitor_qt import RunMonitor

        self._monitor = RunMonitor(self)
        # The application controller owns the identity registry and rejects
        # events after retirement/shutdown.  Keep the adapter lookup dynamic so
        # existing test/extension seams that replace ``_monitor`` remain valid.
        self._monitor_controller = RunsMonitorController(monitor_getter=lambda: self._monitor)
        self._monitor.task_done.connect(self._on_monitor_event)
        self._bg_workers: list = []
        self._remote_mutation_running = False
        self._manual_refresh_running = False

        # Debounce state for _on_task_done events
        # Key monitor work by its workspace-bound watcher id, not a bare
        # run_id: different workspaces may contain identically named runs.
        self._pending_task_events: dict[str, dict] = {}
        # Checkpoint updates share the same per-watcher gate as full refreshes,
        # but must remain distinct so a terminal DONE can retire them first.
        self._pending_checkpoint_events: dict[str, tuple[object, Path]] = {}
        self._checkpoint_retry_events: dict[str, tuple[object, Path]] = {}
        self._checkpoint_retry_timers: dict[str, QTimer] = {}
        self._checkpoint_retry_attempts: dict[str, int] = {}
        self._task_done_timers: dict[str, QTimer] = {}
        self._monitor_contexts: dict[str, tuple[Path, str, str]] = {}

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
        # Workspace-bound watcher ids currently being refreshed/downloaded.
        self._in_progress: set[str] = set()
        # Tracks the last current_batch_id we auto-selected, so a freshly-set one
        # (a new submission) still jumps while later refreshes keep manual selection.
        self._selection_state = RunSelectionState()
        self._active_action_intent: RunActionIntent | None = None
        self._active_refresh_action_intent: RunActionIntent | None = None

        self.search_edit.textChanged.connect(self._apply_filters)
        self.status_filter.currentIndexChanged.connect(self._apply_filters)
        self.server_filter.currentIndexChanged.connect(self._apply_filters)
        self.workflow_filter.currentIndexChanged.connect(self._apply_filters)
        self.date_filter.currentIndexChanged.connect(self._apply_filters)
        self._populate_static_filters()
        self._filters_ready = True
        self._set_activity_log_expanded(False)
        self._update_action_buttons()
        self.apply_language(self._language, refresh=False)

    def _restore_main_splitter(self, settings) -> None:
        raw = (getattr(settings, "splitter_sizes", None) or {}).get(RUNS_SPLITTER_SETTINGS_KEY)
        if (
            isinstance(raw, list)
            and len(raw) == 2
            and all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in raw)
        ):
            sizes = list(raw)
        else:
            sizes = [700, 300]
        self._restored_main_splitter_sizes = sizes
        self._main_splitter.setSizes(sizes)

    def _persist_main_splitter(self, _position: int, _index: int) -> None:
        sizes = self._main_splitter.sizes()
        if len(sizes) != 2 or any(not isinstance(value, int) or value <= 0 for value in sizes):
            return
        settings = self._settings_store.load()
        persisted = dict(getattr(settings, "splitter_sizes", None) or {})
        persisted[RUNS_SPLITTER_SETTINGS_KEY] = list(sizes)
        self._restored_main_splitter_sizes = list(sizes)
        self._settings_store.update(splitter_sizes=persisted)

    def _populate_static_filters(self) -> None:
        values = {
            self.status_filter: [
                ("All statuses", "all"),
                ("Active", "active"),
                ("Completed", "completed"),
                ("Failed", "failed"),
                ("Uncertain", "uncertain"),
                ("Cancelled", "cancelled"),
            ],
            self.date_filter: [
                ("Any date", "all"),
                ("Today", "today"),
                ("Past 7 days", "7d"),
                ("Past 30 days", "30d"),
            ],
        }
        for combo, options in values.items():
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for label, value in options:
                combo.addItem(tr(label, self._language), value)
            index = combo.findData(selected)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _populate_record_filters(self) -> None:
        records = self._run_snapshots or tuple(RunQuerySnapshot.from_record(record) for record in self._run_records)
        for combo, all_label, values in (
            (
                self.server_filter,
                "All servers",
                sorted({record.server_id for record in records}),
            ),
            (
                self.workflow_filter,
                "All workflows",
                sorted({_format_workflow_kind(record.workflow_kind, "en") for record in records}),
            ),
        ):
            selected = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(tr(all_label, self._language), "all")
            for value in values:
                combo.addItem(str(value), str(value))
            index = combo.findData(selected)
            combo.setCurrentIndex(index if index >= 0 else 0)
            combo.blockSignals(False)

    def _filter_spec_from_controls(self) -> RunFilterSpec:
        """Build an immutable application query from the current controls."""
        return RunFilterSpec(
            search=self.search_edit.text(),
            status=self.status_filter.currentData(),
            server_id=self.server_filter.currentData(),
            workflow_kind=self.workflow_filter.currentData(),
            date_range=self.date_filter.currentData(),
        )

    def _apply_filters(self, *_args) -> None:
        if not self._filters_ready:
            return
        self._remember_run_selection()
        selected_run_id = self._current_run_id()
        snapshots = self._query_snapshots_for_records()
        matched_snapshot_ids = {
            id(snapshot)
            for snapshot in filter_run_snapshots(
                snapshots,
                self._filter_spec_from_controls(),
            )
        }
        self._filtered_records = [
            record
            for record, snapshot in zip(self._run_records, snapshots, strict=True)
            if id(snapshot) in matched_snapshot_ids
        ]
        self._render_run_rows(
            self._filtered_records,
            selected_run_id,
            set(self._selection_state.snapshot().selected_ids),
        )
        self._refresh_status_overview()
        self._update_empty_state()

    def _render_run_rows(
        self,
        records: list[RunRecord],
        selected_run_id: str | None,
        selected_run_ids: set[str] | None = None,
    ) -> None:
        selected_run_ids = set(selected_run_ids or ())
        if selected_run_id is not None:
            selected_run_ids.add(selected_run_id)
        self._set_headers()
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        selected_row = None
        for row, record in enumerate(records):
            for col, value in enumerate(_format_row(record, self._language)):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, record)
                elif col == 3:
                    color, cue = _status_visual(record.status_summary)
                    item.setForeground(QColor(color))
                    item.setToolTip(f"{tr(cue, self._language)}: {value or tr('No status', self._language)}")
                    item.setData(Qt.AccessibleDescriptionRole, tr(cue, self._language))
                self.table.setItem(row, col, item)
            if record.run_id == selected_run_id:
                selected_row = row
        self.table.blockSignals(False)
        selected_rows = [row for row, record in enumerate(records) if record.run_id in selected_run_ids]
        if selected_rows:
            selection = self.table.selectionModel()
            selection.clearSelection()
            for row in selected_rows:
                selection.select(
                    self.table.model().index(row, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows,
                )
            current_row = selected_row if selected_row is not None else selected_rows[0]
            selection.setCurrentIndex(self.table.model().index(current_row, 0), QItemSelectionModel.NoUpdate)
        else:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self._set_results_visible(False)
        self._update_action_buttons()

    def _remember_run_selection(self) -> None:
        self._selection_state.remember(self._selected_run_ids(), self._current_run_id())

    def selection_snapshot(self):
        """Return the immutable selection state for tests and shell adapters."""
        return self._selection_state.snapshot()

    def _query_snapshots_for_records(self) -> tuple[RunQuerySnapshot, ...]:
        """Keep test/fixture assignment of ``_run_records`` compatible."""
        if len(self._run_snapshots) == len(self._run_records) and all(
            snapshot.run_id == str(record.run_id)
            for snapshot, record in zip(self._run_snapshots, self._run_records, strict=True)
        ):
            return self._run_snapshots
        return tuple(RunQuerySnapshot.from_record(record) for record in self._run_records)

    def _update_empty_state(self) -> None:
        no_runs = not self._run_records
        no_matches = bool(self._run_records) and not self._filtered_records
        self._empty_hint._title_key = "No runs yet" if no_runs else "No results found"
        self._empty_hint._body_key = (
            "Build a workflow on the Submit tab and click Submit to Remote. Your runs will appear here."
            if no_runs
            else ""
        )
        self._empty_hint.apply_language(self._language)
        for button in self._empty_hint._action_buttons.values():
            button.setVisible(no_runs)
        self._empty_hint.setVisible(no_runs or no_matches)

    def _set_results_visible(self, visible: bool) -> None:
        was_visible = self._results_card.isVisible()
        self._results_card.setVisible(visible)
        if visible and not was_visible:
            self._main_splitter.setSizes(self._restored_main_splitter_sizes)
        if not visible:
            self._preview_timer.stop()
            self._preview_request_id += 1
            self.result_table.setRowCount(0)
            self.result_text.clear()
            self.result_text.setVisible(False)
            self.detail_pane.clear()

    def _set_activity_log_expanded(self, expanded: bool) -> None:
        self.activity_log_toggle.setChecked(expanded)
        self.activity_log_toggle.setText("▾" if expanded else "▸")
        self.activity_log_toggle.setToolTip(
            tr(
                "Collapse activity log" if expanded else "Expand activity log",
                self._language,
            )
        )
        self._log_view.setVisible(expanded)
        self.clear_log_btn.setVisible(expanded)

    @staticmethod
    def _monitor_identity(workspace: Path, run_id: str, server_id: str) -> str:
        """Return a stable identity for a watcher and all of its UI state."""
        return monitor_watch_id(workspace, run_id, server_id)

    @property
    def _monitor_contexts(self):
        """Legacy mutable facade; controller storage remains encapsulated."""

        return self._monitor_controller.legacy_contexts_view()

    @_monitor_contexts.setter
    def _monitor_contexts(self, contexts: Mapping[str, object]) -> None:
        # Several legacy tests/extensions replace this mapping wholesale.  The
        # controller normalizes their tuple values into immutable contexts.
        if hasattr(self, "_monitor_controller"):
            self._monitor_controller.replace_contexts(contexts)

    def _on_monitor_event(self, event: object) -> None:
        """Freeze/validate a worker event, then explicitly queue it to Qt."""

        normalized = self._monitor_controller.accept_event(event)
        if normalized is None:
            return
        self._gui_dispatcher.post(self._on_task_done, normalized)

    def _monitor_context_for_event(self, event) -> tuple[Path, str, str] | None:
        watch_id = getattr(event, "watch_id", None)
        if isinstance(watch_id, str) and watch_id:
            context = self._monitor_controller.get_context(watch_id)
            if context is None:
                _logger.warning("Ignoring event from unknown monitor watcher %s", watch_id)
                return None
            # A registered watcher id is necessary but not sufficient proof of
            # ownership.  A late/reused signal carrying the id of another run
            # must not be allowed to create debounce state, start a refresh, or
            # consume the wrong watcher gate.
            if (
                getattr(event, "run_id", None),
                getattr(event, "server_id", None),
            ) != context[1:]:
                _logger.warning(
                    "Ignoring monitor event with mismatched identity for %s: expected (%s, %s), got (%s, %s)",
                    watch_id,
                    context[1],
                    context[2],
                    getattr(event, "run_id", None),
                    getattr(event, "server_id", None),
                )
                return None
            event_workspace = getattr(event, "workspace", None)
            if event_workspace is not None and not self._same_monitor_workspace(event_workspace, context[0]):
                _logger.warning("Ignoring monitor event with mismatched workspace for %s", watch_id)
                return None
            return context
        if not self._monitor_controller.context_keys():
            # Preserve the public legacy callback contract for a page that
            # has not registered any watcher contexts yet.  Older/custom
            # monitors omit ``watch_id`` and historically targeted the
            # currently selected workspace by run/server identity.  Once a
            # context exists, the stricter matching below prevents an event
            # from being applied to the wrong run or workspace.
            return self._workspace(), event.run_id, event.server_id
        # Compatibility for custom monitors that have not yet adopted
        # watch_id. Built-in monitors always provide it, so this never binds a
        # real watcher through the currently selected workspace.
        matches = [
            context
            for _watch_id, context in self._monitor_controller.iter_contexts()
            if context[1:] == (event.run_id, event.server_id)
        ]
        if len(matches) == 1:
            return matches[0]
        if matches:
            _logger.warning("Ignoring ambiguous legacy monitor event for %s", event.run_id)
            return None
        _logger.warning(
            "Ignoring legacy monitor event with no registered context for %s",
            event.run_id,
        )
        return None

    def _monitor_watch_id_for_event(self, event, context: tuple[Path, str, str]) -> str | None:
        """Resolve an event to the registered watcher key for its context.

        Older/custom monitors may omit ``watch_id``.  Once their event has
        been matched to the single registered context, all debounce and gate
        state must still use that context's composite watcher key rather than
        falling back to the bare run id.
        """
        watch_id = getattr(event, "watch_id", None)
        if isinstance(watch_id, str) and self._monitor_controller.get_context(watch_id) is not None:
            return watch_id
        if not self._monitor_controller.context_keys():
            # Match the historical single-page fallback used by callbacks
            # that predate ``watch_id``.  The caller has already resolved the
            # current workspace and run/server identity above.
            return str(event.run_id)
        matches = [key for key, registered in self._monitor_controller.iter_contexts() if registered == context]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            _logger.warning(
                "Ignoring ambiguous watcher context for legacy monitor event %s",
                event.run_id,
            )
        return None

    def _prune_missing_server_monitors(self, server_ids: set[str]) -> None:
        """Stop watchers whose server was removed from the current config.

        ``_start_monitoring`` runs on every activation/refresh cycle.  A
        server can disappear between cycles, while the monitor's reconnect
        loop would otherwise keep trying to occupy a transport slot forever.
        Retire the UI-side context in a ``finally`` block so a failed stop
        cannot make the page retry the same stale watcher on every cycle.
        """
        stale = [
            (watch_id, context)
            for watch_id, context in self._monitor_controller.iter_contexts()
            if context[2] not in server_ids
        ]
        for watch_id, (workspace, run_id, server_id) in stale:
            self._unwatch_monitor_watch(watch_id, (workspace, run_id, server_id))

    @staticmethod
    def _same_monitor_workspace(left: Path, right: Path) -> bool:
        """Compare watcher workspaces without allowing relative-path aliases."""
        return Path(left).resolve() == Path(right).resolve()

    def _unwatch_monitor_watch(self, watch_id: str, context: tuple[Path, str, str]) -> None:
        """Stop one watcher and retire every page-local state in all outcomes."""
        workspace, run_id, server_id = context
        try:
            self._monitor_controller.unsubscribe(watch_id, MonitorContext.create(workspace, run_id, server_id))
        except Exception:
            _logger.exception(
                "Failed to stop monitor for run %s on server %s (workspace %s)",
                run_id,
                server_id,
                workspace,
            )
        finally:
            # A failed stop must not leave a stale UI context that can be
            # retried forever or accept late events.
            self._retire_monitor_watch(watch_id)

    def _prune_inactive_monitor_watches(self, workspace: Path, runs: list[RunRecord]) -> None:
        """Retire current-workspace watchers missing from the active run set.

        A run can become terminal without emitting a final monitor event, or
        can be deleted between two list calls.  The monitor's reconnect loop
        otherwise keeps its transport slot indefinitely.  Scope this check to
        the refreshed workspace so a page switch cannot tear down a watcher
        that still belongs to another concurrently visible workspace.
        """
        active_keys: set[tuple[str, str, str]] = set()
        for record in runs:
            summary = getattr(record, "status_summary", {}) or {}
            if _has_monitor_active_status(summary):
                active_keys.add(
                    (
                        str(workspace.resolve()),
                        str(getattr(record, "run_id", "")),
                        str(getattr(record, "server_id", "")),
                    )
                )

        stale = []
        for watch_id, context in self._monitor_controller.iter_contexts():
            context_workspace, run_id, server_id = context
            if not self._same_monitor_workspace(context_workspace, workspace):
                continue
            key = (str(context_workspace.resolve()), str(run_id), str(server_id))
            if key not in active_keys:
                stale.append((watch_id, context))
        for watch_id, context in stale:
            self._unwatch_monitor_watch(watch_id, context)

    def _retire_monitor_watches_for_runs(self, workspace: Path, run_ids: set[str]) -> None:
        """Immediately release watchers for runs successfully removed locally."""
        for watch_id, context in self._monitor_controller.iter_contexts():
            context_workspace, run_id, _server_id = context
            if self._same_monitor_workspace(context_workspace, workspace) and str(run_id) in run_ids:
                self._unwatch_monitor_watch(watch_id, context)

    def _start_monitoring(self):
        """Watch all running runs."""
        if self._shutting_down:
            return
        try:
            workspace = self._workspace()
            monitor_inputs = self._runtime.monitor_inputs(workspace)
        except Exception:
            _logger.exception("Failed to enumerate runs for monitoring")
            return
        self._prune_missing_server_monitors(set(monitor_inputs.server_ids))
        self._prune_inactive_monitor_watches(workspace, monitor_inputs.runs)
        for monitor_input in monitor_inputs.runs:
            try:
                if _has_monitor_active_status(monitor_input.status_summary):
                    durable_backend = monitor_input.durable_backend
                    if durable_backend is not None and durable_backend.get("backend") == CONTROL_BACKEND:
                        # Control runs are polled through RemoteRunHandle.events()
                        # by the facade refresh path; never mix them with the
                        # legacy events.log tailer.
                        continue
                    srv = monitor_input.server_config
                    if srv:
                        watch_id = self._monitor_identity(workspace, monitor_input.run_id, monitor_input.server_id)
                        try:
                            self._monitor_controller.subscribe_values(
                                workspace,
                                monitor_input.run_id,
                                monitor_input.server_id,
                                monitor_input.remote_batch_dir,
                                srv,
                                monitor_input.progress_paths,
                                watch_id,
                            )
                        except Exception:
                            self._monitor_controller.remove_context(watch_id)
                            raise
            except Exception:
                _logger.exception(
                    "Failed to start monitoring run %s",
                    monitor_input.run_id,
                )

    def _on_task_done(self, event):
        """Called when a remote task changes state — debounce before refresh.

        Synthetic checkpoint events (``task_id`` starts with ``_ckpt_``)
        trigger a progress-only background transfer before local widgets are
        refreshed. They never change task status or invoke full result download.
        """
        if self._shutting_down:
            return
        context = self._monitor_context_for_event(event)
        if context is None:
            return
        workspace, run_id, server_id = context
        # Built-in monitors send a registered string watch id. Legacy/custom
        # monitors may omit it; once contexts exist, only a unique matching
        # context is safe to bind. With no contexts, the compatibility helper
        # above intentionally preserves the historical single-page fallback.
        watch_id = self._monitor_watch_id_for_event(event, context)
        if watch_id is None:
            return
        is_checkpoint = isinstance(event.task_id, str) and event.task_id.startswith("_ckpt_")
        if is_checkpoint:
            # A newer remote snapshot supersedes any scheduled retry of an
            # older one. If the watcher gate is busy, _sync coalesces this
            # newest event into the normal pending slot.
            self._clear_checkpoint_retry(watch_id)
            self._pending_checkpoint_events.pop(watch_id, None)
            self._sync_checkpoint_progress(event, workspace, watch_id)
            return
        # Real DONE/RUNNING path — debounce before refresh + download.
        has_done = event.exit_code is not None
        if has_done:
            # A DONE-triggered full refresh has priority over any older
            # checkpoint retry or queued lightweight sync for this watcher.
            self._clear_checkpoint_retry(watch_id)
            self._pending_checkpoint_events.pop(watch_id, None)
        if watch_id in self._pending_task_events:
            state = self._pending_task_events[watch_id]
            state["has_done"] = state["has_done"] or has_done
        else:
            self._pending_task_events[watch_id] = {
                "workspace": workspace,
                "run_id": run_id,
                "server_id": server_id,
                "has_done": has_done,
            }
        # Start or restart debounce timer (1000ms)
        if watch_id in self._task_done_timers:
            self._task_done_timers[watch_id].start(1000)
        else:
            self._arm_task_done_timer(watch_id)

    def _flush_task_done(self, watch_id: str):
        """Execute debounced refresh for a run after the quiet window."""
        state = self._pending_task_events.get(watch_id)
        if state is None:
            self._discard_task_done_timer(watch_id)
            return
        # A monitor signal can arrive while the prior refresh/download worker
        # still owns this watcher.  Keep the coalesced state intact; its
        # finished handler will retry once the owner releases the gate.
        if watch_id in self._in_progress:
            return
        self._pending_task_events.pop(watch_id, None)
        self._discard_task_done_timer(watch_id)
        workspace = state.get("workspace", self._workspace())
        run_id = state.get("run_id", watch_id)
        server_id = state["server_id"]
        self._monitor_controller.ensure_context(watch_id, MonitorContext.create(workspace, run_id, server_id))
        self._in_progress.add(watch_id)
        has_done = state["has_done"]

        def _run():
            record = self._runtime.load_run(workspace, run_id)
            patterns = self._get_download_patterns(record)
            outcome = self._execute_refresh_use_case(record, patterns, download=has_done)
            if outcome.errors:
                return tr(
                    "Automatic refresh failed: {errors}",
                    self._language,
                    errors="; ".join(outcome.errors),
                )
            if has_done and outcome.transfer_records:
                return tr(
                    "Run complete; results downloaded: {run_id}",
                    self._language,
                    run_id=run_id,
                )
            return None

        class _FakeEvent:
            pass

        evt = _FakeEvent()
        evt.run_id = run_id
        evt.server_id = server_id
        evt.watch_id = watch_id

        from ..workers import BackgroundWorker

        try:
            w = BackgroundWorker(_run)
        except Exception as error:
            self._rollback_monitor_refresh_start(watch_id, state, error)
            return

        w.result.connect(lambda message: (self._status_cb(message) if message and not self._shutting_down else None))
        w.error.connect(
            lambda error: (
                self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error))
                if not self._shutting_down
                else None
            )
        )
        w.finished.connect(lambda: self._on_monitor_refresh_done(evt))
        w.finished.connect(lambda: self._finish_monitor_refresh(watch_id))
        w.finished.connect(lambda: self._bg_workers.remove(w) if w in self._bg_workers else None)
        w.finished.connect(w.deleteLater)
        self._bg_workers.append(w)
        try:
            w.start()
        except Exception as error:
            if w in self._bg_workers:
                self._bg_workers.remove(w)
            try:
                w.stop_safely(3000)
            except Exception:
                _logger.debug(
                    "Failed to stop monitor refresh worker after start failure",
                    exc_info=True,
                )
            w.deleteLater()
            self._rollback_monitor_refresh_start(watch_id, state, error)

    def _arm_task_done_timer(self, watch_id: str, delay_ms: int = 1000) -> None:
        timer = self._task_done_timers.get(watch_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda key=watch_id: self._flush_task_done(key))
            self._task_done_timers[watch_id] = timer
        timer.start(delay_ms)

    def _rollback_monitor_refresh_start(self, watch_id: str, state: dict, error: Exception) -> None:
        """Restore coalesced work when a refresh worker cannot be started."""
        self._in_progress.discard(watch_id)
        if self._shutting_down or self._monitor_controller.get_context(watch_id) is None:
            return
        pending = self._pending_task_events.get(watch_id)
        if pending is None:
            self._pending_task_events[watch_id] = state
        else:
            pending["has_done"] = pending["has_done"] or state["has_done"]
        self._arm_task_done_timer(watch_id)
        self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error))

    def _discard_task_done_timer(self, watch_id: str) -> None:
        """Stop and forget the debounce timer owned by one monitor watcher."""
        timer = self._task_done_timers.pop(watch_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _finish_monitor_refresh(self, watch_id: str) -> None:
        """Release one monitor worker and flush exactly one coalesced follow-up batch."""
        self._release_monitor_refresh_gate(watch_id)

    def _release_monitor_refresh_gate(self, watch_id: str) -> None:
        """Release a watcher gate and replay pending monitor state when it is still live."""
        self._in_progress.discard(watch_id)
        if self._shutting_down or self._monitor_controller.get_context(watch_id) is None:
            return
        if watch_id in self._pending_task_events:
            self._flush_task_done(watch_id)
            return
        checkpoint = self._pending_checkpoint_events.pop(watch_id, None)
        if checkpoint is not None:
            event, workspace = checkpoint
            if watch_id in self._checkpoint_retry_attempts:
                self._sync_checkpoint_progress(event, workspace, watch_id, _is_retry=True)
            else:
                self._sync_checkpoint_progress(event, workspace, watch_id)

    def _clear_checkpoint_retry(self, watch_id: str, *, reset_attempts: bool = True) -> None:
        """Cancel one watcher's retry without disturbing other workspaces."""
        timer = self._checkpoint_retry_timers.pop(watch_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        self._checkpoint_retry_events.pop(watch_id, None)
        if reset_attempts:
            self._checkpoint_retry_attempts.pop(watch_id, None)

    def _schedule_checkpoint_retry(self, event, workspace: Path, watch_id: str) -> None:
        """Retain the consumed checkpoint event and retry with bounded backoff."""
        if self._shutting_down or self._monitor_controller.get_context(watch_id) is None:
            return
        # A newer queued checkpoint supersedes the event whose worker just
        # failed. A terminal DONE refresh also wins, but an ordinary RUNNING
        # refresh does not carry checkpoint files and must preserve the retry.
        pending_task = self._pending_task_events.get(watch_id)
        if watch_id in self._pending_checkpoint_events or (pending_task is not None and pending_task["has_done"]):
            self._clear_checkpoint_retry(watch_id)
            return
        self._checkpoint_retry_events[watch_id] = (event, workspace)
        attempt = self._checkpoint_retry_attempts.get(watch_id, 0) + 1
        self._checkpoint_retry_attempts[watch_id] = attempt
        exponent = min(attempt - 1, 30)
        delay = min(CHECKPOINT_RETRY_BASE_MS * (2**exponent), CHECKPOINT_RETRY_MAX_MS)
        timer = self._checkpoint_retry_timers.get(watch_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda key=watch_id: self._run_checkpoint_retry(key))
            self._checkpoint_retry_timers[watch_id] = timer
        else:
            timer.stop()
        timer.start(delay)

    def _run_checkpoint_retry(self, watch_id: str) -> None:
        """Run one scheduled retry, or merge it into the busy watcher gate."""
        timer = self._checkpoint_retry_timers.pop(watch_id, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
        retry = self._checkpoint_retry_events.pop(watch_id, None)
        if retry is None or self._shutting_down or self._monitor_controller.get_context(watch_id) is None:
            self._checkpoint_retry_attempts.pop(watch_id, None)
            return
        event, workspace = retry
        if watch_id in self._pending_task_events:
            # The ordinary RUNNING refresh cannot synchronize progress files,
            # so queue this retry behind it instead of consuming the retry.
            self._pending_checkpoint_events[watch_id] = (event, workspace)
            if watch_id not in self._in_progress:
                self._flush_task_done(watch_id)
            return
        if watch_id in self._in_progress:
            self._pending_checkpoint_events[watch_id] = (event, workspace)
            return
        self._sync_checkpoint_progress(event, workspace, watch_id, _is_retry=True)

    def _sync_checkpoint_progress(
        self,
        event,
        workspace: Path,
        watch_id: str,
        *,
        _is_retry: bool = False,
    ) -> None:
        """Transfer declared progress files off-thread before rereading them."""
        if not _is_retry:
            self._clear_checkpoint_retry(watch_id)
        run_id = event.run_id
        if watch_id in self._in_progress:
            # Keep only the newest checkpoint signal: each sync reads the
            # current remote files, so multiple signals require one follow-up.
            self._pending_checkpoint_events[watch_id] = (event, workspace)
            return
        self._in_progress.add(watch_id)

        def _run():
            record = self._runtime.load_run(workspace, run_id)
            outcome = self._execute_progress_use_case(record)
            if outcome.errors:
                raise RuntimeError("; ".join(outcome.errors))
            return outcome

        from ..workers import BackgroundWorker

        try:
            worker = BackgroundWorker(_run)
        except Exception as error:
            if not self._shutting_down:
                self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error))
                self._schedule_checkpoint_retry(event, workspace, watch_id)
            self._release_monitor_refresh_gate(watch_id)
            return

        failed = False

        def _error(error):
            nonlocal failed
            if failed:
                return
            failed = True
            if not self._shutting_down:
                self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error))
                self._schedule_checkpoint_retry(event, workspace, watch_id)

        worker.error.connect(_error)

        def _finished():
            if not failed:
                self._clear_checkpoint_retry(watch_id)
            self._release_monitor_refresh_gate(watch_id)
            if worker in self._bg_workers:
                self._bg_workers.remove(worker)
            if not failed and not self._shutting_down and self._workspace() == workspace:
                self.refresh_run_list()
            record = self._selected_record() if not failed and not self._shutting_down else None
            if record is not None:
                self._analyze_cache.clear()
                self._detail_cache.clear()
                self._preview_timer.start()

        worker.finished.connect(_finished)
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        try:
            worker.start()
        except Exception as error:
            if worker in self._bg_workers:
                self._bg_workers.remove(worker)
            try:
                worker.stop_safely(3000)
            except Exception:
                _logger.debug(
                    "Failed to stop checkpoint worker after start failure",
                    exc_info=True,
                )
            worker.deleteLater()
            if not self._shutting_down:
                self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error))
                self._schedule_checkpoint_retry(event, workspace, watch_id)
            self._release_monitor_refresh_gate(watch_id)

    def _on_monitor_refresh_done(self, event):
        if self._shutting_down:
            return
        context = self._monitor_context_for_event(event)
        if context is None:
            return
        workspace, run_id, server_id = context
        if self._workspace() == workspace:
            self.refresh_run_list()
        try:
            updated = self._runtime.load_run(workspace, run_id)
            if (
                updated.status_summary.get("submitting", 0) == 0
                and updated.status_summary.get("running", 0) == 0
                and updated.status_summary.get("submitted", 0) == 0
            ):
                watch_id = self._monitor_watch_id_for_event(event, context)
                try:
                    if watch_id is not None:
                        self._monitor_controller.unsubscribe(
                            watch_id,
                            MonitorContext.create(workspace, run_id, server_id),
                        )
                finally:
                    self._retire_monitor_watch(watch_id)
        except Exception:
            _logger.exception("Failed to update monitor refresh state for %s", run_id)

    def _retire_monitor_watch(self, watch_id: object) -> None:
        """Forget a terminal watcher before any queued late event can reuse it."""
        if not isinstance(watch_id, str):
            return
        self._monitor_controller.remove_context(watch_id)
        self._pending_task_events.pop(watch_id, None)
        self._pending_checkpoint_events.pop(watch_id, None)
        self._clear_checkpoint_retry(watch_id)
        self._discard_task_done_timer(watch_id)
        self._in_progress.discard(watch_id)

    def on_activated(self):
        settings = GuiSettingsStore().load()
        self._language = settings.language
        self._refresh_timer.setInterval(settings.auto_refresh_interval * 1000)
        self._refresh_timer.stop()
        self._refresh_overview_timer.start()
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

        try:
            start_context_worker(
                self,
                target=_recover,
                registry_attr="_bg_workers",
                on_result=self._apply_startup_recovery,
                on_error=self._apply_startup_recovery_error,
                on_finished=self._finish_startup_recovery,
            )
        except Exception as exc:
            # Worker creation can fail synchronously (for example while Qt is
            # shutting down or when the thread factory rejects a new worker).
            # Keep the page and MainWindow recovery gate from being stuck in
            # the running state forever; this mirrors the asynchronous error
            # path and still leaves a visible diagnostic for the user.
            self._recovery_running = False
            self._recovery_complete = True
            if not self._shutting_down:
                self._apply_startup_recovery_error(str(exc))
                self.startup_recovery_finished.emit()

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

    def apply_language(self, language: str, *, refresh: bool = True):
        """Translate the page and optionally refresh its run list.

        Refreshing the run list opens the local runs database.  Callers that
        are still constructing the main window can pass ``refresh=False``
        to keep startup independent of an unavailable or malformed database;
        normal page activation and explicit language changes retain the
        historical refresh behaviour.
        """
        self._language = language
        self._retry_feedback.set_idle_text(tr("Retry Failed", language))
        self._stop_feedback.set_idle_text(tr("Stop Task", language))
        self._retry_download_feedback.set_idle_text(tr("Retry Download", language))
        self._delete_feedback.set_idle_text(tr("Delete", language))
        self.confirm_submitted_btn.setText(tr("Confirm Submitted", language))
        self.abandon_submit_btn.setText(tr("Abandon Submit", language))
        self.result_label.setText(tr("Result Preview", language))
        self._overview_title.setText(tr("Runs overview:", language))
        self.activity_log_label.setText(tr("Activity log", language))
        self.clear_log_btn.setText(tr("Clear Log", language))
        self.search_edit.setPlaceholderText(tr("Search runs", language))
        self.refresh_btn.setText(tr("Refresh", language))
        self._populate_static_filters()
        self._populate_record_filters()
        self._set_activity_log_expanded(self.activity_log_toggle.isChecked())
        self._set_headers()
        if refresh:
            self.refresh_run_list()
        # Phase 11.1 — F5 fix. Forward language to the result detail
        # pane so its placeholder text re-translates on the fly.
        if hasattr(self, "detail_pane") and self.detail_pane is not None:
            self.detail_pane.apply_language(language)
        self._update_empty_state()
        self._refresh_status_overview()

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
                _logger.exception("Failed to append activity log entry")
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
        if text == self._last_activity_message:
            return
        self._last_activity_message = text
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
                _logger.exception("Failed to write to log sink")

    def _clear_activity_log(self) -> None:
        """Clear the visible log lines. Sink log and status bar are untouched."""
        if hasattr(self, "_log_view") and self._log_view is not None:
            self._log_view.clear()
            self._last_activity_message = None

    def set_submit_warnings(self, warnings: list[str]) -> None:
        """Show non-fatal submission advisories without persisting them."""
        cleaned = [str(warning).strip() for warning in warnings if str(warning).strip()]
        if not cleaned:
            self._submit_warning_banner.clear()
            self._submit_warning_banner.setToolTip("")
            self._submit_warning_banner.setVisible(False)
            return
        self._submit_warning_banner.setText(tr("{n} submission warnings", self._language, n=len(cleaned)))
        self._submit_warning_banner.setToolTip("\n".join(cleaned))
        self._submit_warning_banner.setVisible(True)

    def _set_headers(self):
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                tr("Run ID", self._language),
                tr("Server", self._language),
                tr("Remote Dir", self._language),
                tr("Status", self._language),
                tr("Workflow", self._language),
                tr("Command", self._language),
                tr("Created At", self._language),
            ]
        )

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

    def _queue_gui(self, callback: Callable[..., Any], *args: Any) -> None:
        """Route worker payloads through the page-owned GUI dispatcher."""
        self._gui_dispatcher.post(callback, *args)

    def _refresh_all(self):
        intent = self._actions.begin("refresh", shutting_down=self._shutting_down)
        if intent is None:
            return
        try:
            self.refresh_run_list()
            self._actions.finish(intent)
            row = self.table.currentRow()
            if row >= 0:
                self._refresh_status()
        finally:
            self._actions.finish(intent)

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
        self._remember_run_selection()
        previous_selection = self._current_run_id()
        try:
            query_result = self._run_query.list_runs(Path(workspace))
            runs = list(query_result.records)
        except Exception as exc:
            # Page activation is a user-facing navigation action.  A locked,
            # unavailable, or future-version local run database must not
            # terminate the Qt process from the deferred activation timer.
            _logger.exception("Failed to load run records")
            self._run_records = []
            self._filtered_records = []
            self._run_snapshots = ()
            self._populate_record_filters()
            self._render_run_rows([], None, set())
            self._refresh_status_overview()
            self._update_uncertain_actions()
            self._update_empty_state()
            self._status_cb(
                tr(
                    "Could not load run records: {error}",
                    self._language,
                    error=str(exc),
                )
            )
            return
        self._run_records = runs
        self._run_snapshots = query_result.snapshots
        self._prune_inactive_monitor_watches(Path(workspace), runs)
        self._populate_record_filters()
        # A freshly-set current_batch_id (new submission) jumps to that run;
        # otherwise keep the user's manual selection across refreshes.
        batch_id = getattr(self.state, "current_batch_id", None)
        available_ids = {record.run_id for record in runs}
        # Selection policy is independent of Qt row indexes: prune missing
        # IDs, apply a new batch exactly once, and otherwise keep manual
        # selection across list rebuilds.
        self._selection_state.remember(self._selected_run_ids(), previous_selection)
        selected_run_id = self._selection_state.reconcile(available_ids, batch_id)
        selected_ids = set(self._selection_state.snapshot().selected_ids)
        snapshots = self._query_snapshots_for_records()
        matched_snapshot_ids = {
            id(snapshot)
            for snapshot in filter_run_snapshots(
                snapshots,
                self._filter_spec_from_controls(),
            )
        }
        self._filtered_records = [
            record for record, snapshot in zip(runs, snapshots, strict=True) if id(snapshot) in matched_snapshot_ids
        ]
        self._render_run_rows(self._filtered_records, selected_run_id, selected_ids)
        self._refresh_status_overview()
        self._update_uncertain_actions()
        if len(runs) != self._last_reported_run_count:
            self._status_cb(tr("Run records: {n}", self._language, n=len(runs)))
            self._last_reported_run_count = len(runs)
        self.last_updated_label.setText(
            tr(
                "Last updated: {time}",
                self._language,
                time=datetime.now().strftime("%H:%M:%S"),
            )
        )
        self.last_updated_label.setToolTip(datetime.now().isoformat(timespec="seconds"))
        # Phase 2.1: toggle the empty-state hint whenever the run list
        # is refreshed. The hint lives outside the splitter so this only
        # affects the layout above the run table.
        self._update_empty_state()

    def _current_run_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return item.text() if item else None

    def _workspace(self) -> Path:
        return Path(self.state.current_project_root or Path.cwd())

    def _coordinator_for(self, workspace: Path) -> RunCoordinator:
        return self._runtime.coordinator(
            workspace,
            factory=self._coordinator_factory,
            session_pool=self._session_pool,
        )

    def _execute_refresh_use_case(self, record, patterns: list[str], *, download: bool):
        return self._runtime.refresh_run(
            self._result_workspace(record),
            record.run_id,
            patterns,
            download=download,
            server_id=record.server_id,
            resolver=self._coordinator_for,
            client_factory=self._client_factory,
        )

    def _execute_download_use_case(self, record, patterns: list[str]):
        return self._runtime.download_run(
            self._result_workspace(record),
            record.run_id,
            patterns,
            server_id=record.server_id,
            resolver=self._coordinator_for,
            client_factory=self._client_factory,
        )

    def _client_for(self, record: RunRecord) -> SSHConfFlowClient:
        coordinator = self._coordinator_for(self._result_workspace(record))
        return self._runtime.client(
            coordinator,
            record.server_id,
            factory=self._client_factory,
        )

    def _execute_progress_use_case(self, record):
        return self._runtime.sync_progress(
            self._result_workspace(record),
            record.run_id,
            resolver=self._coordinator_for,
        )

    def _result_workspace(self, record: RunRecord) -> Path:
        """Resolve the workspace for a run record's results."""
        return resolve_result_workspace(getattr(record, "local_dir", ""), self._workspace())

    def _load_tasks(self, record: RunRecord):
        try:
            tasks = self._runtime.load_tasks(self._result_workspace(record), record.run_id)
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
        """Return the run-owned directory used by ``RunService`` downloads."""
        return self._result_workspace(record) / "results" / record.run_id

    def _legacy_results_directory(self, record: RunRecord, base: Path | None = None) -> Path:
        root = base if base is not None else self._result_workspace(record)
        return root / "results" / record.run_id

    @staticmethod
    def _has_result_workspace_binding(record: RunRecord) -> bool:
        """Whether a record owns a specific local workspace for its results."""
        return has_workspace_binding(getattr(record, "local_dir", ""))

    def _result_search_directories(self, record: RunRecord, bases: list[Path]) -> list[Path]:
        """Return result locations without mixing bound runs with other workspaces.

        New records persist ``local_dir`` and all downloads for those records
        belong in that workspace's ``results/<run-id>`` directory.  Legacy
        records have no such binding, so retain their former root-directory
        fallback only after trying every run-owned directory first.
        """
        paths = resolve_run_artifacts(
            record.run_id,
            getattr(record, "local_dir", ""),
            self._result_workspace(record),
            candidate_roots=bases,
        )
        return list(paths.search_dirs)

    def _artifact_paths(self, record: RunRecord, *, default_local_folder: str | None = None):
        return resolve_run_artifacts(
            record.run_id,
            getattr(record, "local_dir", ""),
            self._workspace(),
            default_local_folder=default_local_folder,
        )

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
        return self._runtime.load_run(self._workspace(), item.text())

    def _on_run_selected(self, row, col, prev_row, prev_col):
        """Debounce selection so rapid scrolling doesn't parse files per row."""
        self._update_uncertain_actions()
        self._update_action_buttons()
        valid = row >= 0 and self._selected_record() is not None
        self._set_results_visible(valid)
        if valid:
            self._preview_timer.start()

    def _update_action_buttons(self) -> None:
        record = self._selected_record()
        no_selection = tr("Select a run to enable this action", self._language)

        def configure(button: QPushButton, enabled: bool, disabled_reason: str) -> None:
            button.setEnabled(enabled)
            tooltip = "" if enabled else disabled_reason
            button.setToolTip(tooltip)
            feedback = {
                self.retry_btn: self._retry_feedback,
                self.stop_btn: self._stop_feedback,
                self.retry_dl_btn: self._retry_download_feedback,
                self.delete_btn: self._delete_feedback,
            }.get(button)
            if feedback is not None:
                feedback._idle_tooltip = tooltip

        if record is None:
            for button in (
                self.retry_btn,
                self.stop_btn,
                self.retry_dl_btn,
                self.delete_btn,
            ):
                configure(button, False, no_selection)
            return
        summary = record.status_summary
        configure(
            self.retry_btn,
            bool(summary.get("failed", 0)),
            tr("This run has no failed tasks", self._language),
        )
        configure(
            self.stop_btn,
            any(summary.get(key, 0) for key in _ACTIVE_STATUSES),
            tr("This run has no active tasks", self._language),
        )
        configure(
            self.retry_dl_btn,
            bool(summary.get("remote_completed", 0)),
            tr("This run has no tasks awaiting download", self._language),
        )
        configure(self.delete_btn, True, "")

    def _update_uncertain_actions(self) -> None:
        record = self._selected_record()
        enabled = bool(
            not self._remote_mutation_running
            and record
            and record.status_summary.get("uncertain", 0)
            and self._selected_uncertain_task_ids()
        )
        self.confirm_submitted_btn.setVisible(enabled)
        self.abandon_submit_btn.setVisible(enabled)
        self.confirm_submitted_btn.setEnabled(enabled)
        self.abandon_submit_btn.setEnabled(enabled)

    def _refresh_status_overview(self, runs: list[RunRecord] | None = None) -> None:
        """Show run count separately from exhaustive aggregate task totals."""
        if not hasattr(self, "_overview_label"):
            return
        records = self._run_records if runs is None else runs
        visible_count = len(self._filtered_records) if runs is None else len(records)
        totals = {"active": 0, "completed": 0, "failed": 0, "other": 0}
        for record in records:
            for status, count in record.status_summary.items():
                if status in _ACTIVE_STATUSES:
                    totals["active"] += count
                elif status in _COMPLETED_STATUSES:
                    totals["completed"] += count
                elif status == "failed":
                    totals["failed"] += count
                else:
                    totals["other"] += count
        run_text = tr(
            "Runs: {visible} of {total}",
            self._language,
            visible=visible_count,
            total=len(records),
        )
        task_parts = [
            f"{tr('Active', self._language)} {totals['active']}",
            f"{tr('Completed', self._language)} {totals['completed']}",
            f"{tr('Failed', self._language)} {totals['failed']}",
            f"{tr('Other', self._language)} {totals['other']}",
        ]
        text = f"{run_text} · {tr('Tasks:', self._language)} " + " · ".join(task_parts)
        self._overview_label.setText(text)
        self._overview_label.setAccessibleName(text)

    def _selected_uncertain_task_ids(self) -> list[str]:
        selected_rows = sorted({index.row() for index in self.result_table.selectedIndexes()})
        if not selected_rows:
            return []
        task_ids: list[str] = []
        for row in selected_rows:
            item = self.result_table.item(row, 0)
            data = item.data(Qt.UserRole) if item is not None else None
            if isinstance(data, dict):
                if data.get("kind") != "uncertain" or data.get("status") != "uncertain":
                    return []
                task_id = data.get("task_id")
                if not task_id:
                    return []
                task_ids.append(str(task_id))
                continue
            if not isinstance(data, tuple) or len(data) != 2 or data[1] != "uncertain":
                return []
            task_ids.append(str(data[0]))
        return task_ids

    def _build_preview_request(self, record: RunRecord) -> PreviewRequest:
        """Freeze all page/record state before starting the preview worker."""
        default_folder = None
        if not self._has_result_workspace_binding(record):
            default_folder = GuiSettingsStore().load().default_local_folder
        artifacts = self._artifact_paths(
            record,
            default_local_folder=str(default_folder) if default_folder else None,
        )
        raw_tasks = self._load_tasks(record)
        tasks = tuple(UncertainTaskPayload.from_task(task) for task in (raw_tasks or ()))
        summary = getattr(record, "status_summary", {})
        uncertain = isinstance(summary, Mapping) and bool(summary.get("uncertain", 0))
        workflow_kind = getattr(record, "workflow_kind", "")
        workflow_kind = getattr(workflow_kind, "value", workflow_kind)
        return PreviewRequest(
            run_id=str(getattr(record, "run_id", "")),
            result_dirs=tuple(artifacts.search_dirs),
            download_dir=artifacts.download_dir,
            workflow_kind=str(workflow_kind or ""),
            progress_dir=_run_progress_dir(record),
            tasks=tasks,
            uncertain=uncertain,
            auto_analysis_label=tr("Result Preview - Auto Analysis", self._language),
            local_files_label=tr("Result Preview - Local Files", self._language),
            tsv_label=tr("Result Preview", self._language),
            file_too_large_label=tr("File too large for preview", self._language),
            parse_error_label=tr("Parse Error", self._language),
            ok_label=tr("OK", self._language),
        )

    def _render_selected_preview(self):
        """Render the preview for the settled selection (called after debounce)."""
        record = self._selected_record()
        if record is None:
            self.result_table.setRowCount(0)
            return
        request = self._build_preview_request(record)
        self._preview_request_id += 1
        request_id = self._preview_request_id

        def _run(_ctx: WorkerContext, request: PreviewRequest = request):
            return build_preview_payload(request)

        def _done(payload):
            if request_id != self._preview_request_id:
                return
            self._apply_result_preview(payload)

        start_context_worker(
            self,
            target=_run,
            registry_attr="_bg_workers",
            on_result=lambda payload: self._queue_gui(_done, payload),
            on_error=lambda error: self._queue_gui(
                self._status_cb,
                tr("Preview failed: {e}", self._language, e=error.splitlines()[0]),
            ),
        )

    def _collect_result_preview(self, record: RunRecord) -> PreviewPayload:
        from ...services.gui_settings import GuiSettingsStore

        tasks = tuple(self._load_tasks(record))
        workflow_kind = getattr(record, "workflow_kind", None)
        if getattr(record, "status_summary", {}).get("uncertain", 0):
            return PreviewPayload(kind="uncertain", tasks=tasks, workflow_kind=workflow_kind)
        workspace = self._result_workspace(record)
        candidates = [workspace]
        default_folder = None
        if not self._has_result_workspace_binding(record):
            default_folder = GuiSettingsStore().load().default_local_folder
            if default_folder and Path(default_folder) != workspace:
                candidates.append(Path(default_folder))
            gui_ws = self._workspace()
            if gui_ws != workspace and gui_ws not in candidates:
                candidates.append(gui_ws)
        result_dirs = list(
            self._artifact_paths(
                record,
                default_local_folder=str(default_folder) if default_folder else None,
            ).search_dirs
        )

        is_confflow = getattr(record, "workflow_kind", None) in {
            WorkflowKind.confflow,
            WorkflowKind.dag,
        }
        if is_confflow:
            best_dir = None
            fallback_dir = None
            for result_dir in result_dirs:
                if result_dir.exists():
                    if _confflow_result_dir_has_summary(
                        record,
                        result_dir,
                        tasks,
                    ):
                        best_dir = result_dir
                        break
                    if fallback_dir is None:
                        fallback_dir = result_dir
            return PreviewPayload(
                kind="confflow",
                run_id=record.run_id,
                result_dir=best_dir or fallback_dir or self._download_directory(record),
                tasks=tasks,
                progress_dir=_run_progress_dir(record),
                workflow_kind=workflow_kind,
            )

        for result_dir in result_dirs:
            if result_dir.exists():
                rows = self._auto_analyze(result_dir)
                if rows:
                    return PreviewPayload(
                        kind="analysis",
                        rows=tuple(tuple(row) for row in rows),
                        label=tr("Result Preview - Auto Analysis", self._language),
                        tasks=self._preview_task_snapshots(record, result_dir, rows, tasks),
                        workspace=result_dir,
                        workflow_kind=workflow_kind,
                    )

        for result_dir in result_dirs:
            needs_refresh = False

            def _mark_needs_refresh() -> None:
                nonlocal needs_refresh
                needs_refresh = True

            rows = self._analyze_workspace_files(record, result_dir, on_changed=_mark_needs_refresh)
            if rows:
                return PreviewPayload(
                    kind="analysis",
                    rows=tuple(tuple(row) for row in rows),
                    label=tr("Result Preview - Local Files", self._language),
                    stale=needs_refresh,
                    tasks=self._preview_task_snapshots(record, result_dir, rows, tasks),
                    workspace=result_dir,
                    workflow_kind=workflow_kind,
                )

        tsv = choose_existing_artifact(result_dirs, ("final_results.tsv", "analysis_preview.tsv"), minimum_bytes=30)
        if tsv is not None:
            return PreviewPayload(
                kind="tsv",
                artifact_path=tsv,
                label=f"{tr('Result Preview', self._language)} - {tsv.name}",
            )

        return PreviewPayload(kind="empty")

    def _preview_task_snapshots(
        self,
        record: RunRecord,
        result_dir: Path,
        rows: list[list[str]],
        tasks: tuple[Any, ...],
    ) -> tuple[UncertainTaskPayload, ...]:
        """Copy only task fields needed to render and open preview details."""
        by_id = {str(getattr(task, "task_id", "")): task for task in tasks}
        snapshots: list[UncertainTaskPayload] = []
        for row in rows:
            task_id = str(row[COL_TASK]) if row else ""
            source = by_id.get(task_id)
            if source is None:
                source = {
                    "task_id": task_id,
                    "status": "downloaded",
                    "remote_task_files": (),
                }
            snapshots.append(UncertainTaskPayload.from_task(source, task_dir=result_dir / task_id))
        return tuple(snapshots)

    def _apply_result_preview(self, payload, *, record: RunRecord | None = None) -> None:
        legacy_record = None
        if not isinstance(payload, PreviewPayload) and payload and payload[0] == "confflow":
            # Keep direct calls from older page integrations working.  Normal
            # worker results are already a PreviewPayload and never retain the
            # legacy record.
            legacy_record = payload[1]
        frozen = PreviewPayload.from_legacy(payload)
        kind = frozen.kind
        if kind == "uncertain":
            self._show_uncertain_tasks(list(frozen.tasks))
        elif kind == "confflow":
            if isinstance(payload, PreviewPayload):
                if frozen.run_id and frozen.result_dir is not None:
                    self._show_confflow_batch_results(
                        frozen.run_id,
                        frozen.result_dir,
                        tasks=frozen.tasks,
                        progress_dir=frozen.progress_dir,
                    )
            else:
                active_record = record or legacy_record or self._selected_record()
                if active_record is not None and frozen.result_dir is not None:
                    self._show_confflow_batch_results(active_record, frozen.result_dir)
        elif kind == "analysis":
            self.result_text.setVisible(False)
            self._show_analysis_rows(
                [list(row) for row in frozen.rows],
                tasks=frozen.tasks,
                workspace=frozen.workspace,
            )
            self._set_parsed_results_label(frozen.label)
            if frozen.stale:
                self.refresh_run_list()
        elif kind == "tsv":
            if frozen.artifact_path is None:
                return
            self._load_tsv(frozen.artifact_path)
            self.result_text.setVisible(False)
            self.result_label.setText(frozen.label)
        else:
            self.result_label.setText(tr("No results downloaded yet", self._language))
            self.result_text.setVisible(False)
            self.result_table.setRowCount(0)

    def _show_uncertain_tasks(self, tasks) -> None:
        self.result_table.clearSelection()
        self.result_table.setColumnCount(3)
        self.result_table.setHorizontalHeaderLabels(
            [
                tr("Task", self._language),
                tr("Status", self._language),
                tr("Error", self._language),
            ]
        )
        self.result_table.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            task_id = str(getattr(task, "task_id", ""))
            raw_status = getattr(task, "status", "")
            status = str(getattr(raw_status, "value", raw_status))
            error_message = str(getattr(task, "error_message", "") or "")
            values = (task_id, tr(status.title(), self._language), error_message)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(
                        Qt.UserRole,
                        {
                            "kind": "uncertain",
                            "task_id": task_id,
                            "status": status,
                            "error": error_message,
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
        default_folder = None
        if not self._has_result_workspace_binding(record):
            default_folder = GuiSettingsStore().load().default_local_folder
            if default_folder and Path(default_folder) != workspace:
                candidates.append(Path(default_folder))
            gui_ws = self._workspace()
            if gui_ws != workspace and gui_ws not in candidates:
                candidates.append(gui_ws)
        result_dirs = list(
            self._artifact_paths(
                record,
                default_local_folder=str(default_folder) if default_folder else None,
            ).search_dirs
        )

        # Detect workflow batches from the persisted workflow kind
        is_confflow = getattr(record, "workflow_kind", None) in {
            WorkflowKind.confflow,
            WorkflowKind.dag,
        }
        if is_confflow:
            best_dir = None
            fallback_dir = None
            for result_dir in result_dirs:
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
        for result_dir in result_dirs:
            if result_dir.exists():
                rows = self._auto_analyze(result_dir)
                if rows:
                    self.result_text.setVisible(False)
                    self._show_analysis_rows(rows)
                    self._set_parsed_results_label(tr("Result Preview — Auto Analysis", self._language))
                    return

        # Legacy records may still have results directly in their workspace root.
        for result_dir in result_dirs:
            rows = self._analyze_workspace_files(record, result_dir)
            if rows:
                self._show_analysis_rows(rows)
                self._set_parsed_results_label(tr("Result Preview — Local Files", self._language))
                return

        # Last resort: read existing TSV
        tsv = choose_existing_artifact(result_dirs, ("final_results.tsv", "analysis_preview.tsv"), minimum_bytes=30)
        if tsv is not None:
            self._load_tsv(tsv)
            self.result_text.setVisible(False)
            self.result_label.setText(f"{tr('Result Preview', self._language)} — {tsv.name}")
            return

        self.result_label.setText(tr("No results downloaded yet", self._language))
        self.result_text.setVisible(False)
        self.result_table.setRowCount(0)

    def _analyze_workspace_files(self, record: RunRecord, workspace: Path, *, on_changed=None) -> list[list[str]]:
        """Analyze output files directly from workspace if they exist locally."""
        from ...core.lifecycle import TaskStatus
        from ...core.parsers.gaussian import (
            diagnose_gaussian_result,
            parse_gaussian_log,
        )
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
                    rows.append(
                        _placeholder_analysis_row(
                            task.task_id,
                            log_file.name,
                            "Gaussian",
                            tr("File too large for preview", self._language),
                        )
                    )
                else:
                    try:
                        r = parse_gaussian_log(log_file)
                        rows.append(
                            _analysis_row(
                                task.task_id,
                                log_file.name,
                                "Gaussian",
                                r,
                                diagnose_gaussian_result(r),
                                self._language,
                            )
                        )
                    except Exception:
                        _logger.exception("Failed to parse Gaussian log: %s", log_file)
                        rows.append(
                            _placeholder_analysis_row(
                                task.task_id,
                                log_file.name,
                                "Gaussian",
                                tr("Parse Error", self._language),
                            )
                        )
            # Check .out (ORCA)
            out_file = workspace / f"{stem}.out"
            if out_file.is_file():
                if _too_large_for_preview(out_file):
                    rows.append(
                        _placeholder_analysis_row(
                            task.task_id,
                            out_file.name,
                            "ORCA",
                            tr("File too large for preview", self._language),
                        )
                    )
                else:
                    try:
                        ro = parse_orca_out(out_file)
                        rows.append(
                            _analysis_row(
                                task.task_id,
                                out_file.name,
                                "ORCA",
                                ro,
                                diagnose_orca_result(ro),
                                self._language,
                            )
                        )
                    except Exception:
                        _logger.exception("Failed to parse ORCA output: %s", out_file)
                        rows.append(
                            _placeholder_analysis_row(
                                task.task_id,
                                out_file.name,
                                "ORCA",
                                tr("Parse Error", self._language),
                            )
                        )
        return rows

    def _auto_analyze(self, result_dir: Path) -> list[list[str]]:
        """Auto-detect and parse Gaussian/ORCA output files matching task stem."""
        key = str(result_dir)
        sig = _dir_parse_signature(result_dir)
        cached = self._analyze_cache.get(key)
        if cached is not None and cached[0] == sig:
            return cached[1]
        from ...core.parsers.gaussian import (
            diagnose_gaussian_result,
            parse_gaussian_log,
        )
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
                    rows.append(
                        _placeholder_analysis_row(
                            stem,
                            log_file.name,
                            "Gaussian",
                            tr("File too large for preview", self._language),
                        )
                    )
                else:
                    try:
                        r = parse_gaussian_log(log_file)
                        rows.append(
                            _analysis_row(
                                stem,
                                log_file.name,
                                "Gaussian",
                                r,
                                diagnose_gaussian_result(r),
                                self._language,
                            )
                        )
                    except Exception:
                        _logger.exception("Failed to parse Gaussian log: %s", log_file)
                        rows.append(
                            _placeholder_analysis_row(
                                stem,
                                log_file.name,
                                "Gaussian",
                                tr("Parse Error", self._language),
                            )
                        )
            # ORCA .out
            out_file = task_dir / f"{stem}.out"
            if out_file.is_file():
                if _too_large_for_preview(out_file):
                    rows.append(
                        _placeholder_analysis_row(
                            stem,
                            out_file.name,
                            "ORCA",
                            tr("File too large for preview", self._language),
                        )
                    )
                else:
                    try:
                        ro = parse_orca_out(out_file)
                        rows.append(
                            _analysis_row(
                                stem,
                                out_file.name,
                                "ORCA",
                                ro,
                                diagnose_orca_result(ro),
                                self._language,
                            )
                        )
                    except Exception:
                        _logger.exception("Failed to parse ORCA output: %s", out_file)
                        rows.append(
                            _placeholder_analysis_row(
                                stem,
                                out_file.name,
                                "ORCA",
                                tr("Parse Error", self._language),
                            )
                        )
        self._analyze_cache[key] = (sig, rows)
        return rows

    def _show_confflow_batch_results(
        self,
        record_or_run_id,
        result_dir: Path,
        *,
        tasks: tuple[UncertainTaskPayload, ...] | None = None,
        progress_dir: Path | None = None,
    ):
        """Display per-molecule ConfFlow summary table using manifest as authority."""
        from ...core.lifecycle import TaskStatus
        from ...services.confflow_results import ParseState, load_summary_result

        headers = [
            "Molecule",
            "Status",
            "Conformers (in→out)",
            "Duration (s)",
            "Steps",
            "Progress",
        ]
        rows: list[list[str]] = []

        if isinstance(record_or_run_id, str):
            tasks = tuple(tasks or ())
        else:
            tasks = tuple(tasks) if tasks is not None else tuple(self._load_tasks(record_or_run_id))
            if progress_dir is None:
                progress_dir = _run_progress_dir(record_or_run_id)

        if tasks:
            for task in tasks:
                mol_name = task.task_id
                summary_file = _confflow_summary_file(result_dir, mol_name)
                status = str(
                    getattr(
                        getattr(task, "status", ""),
                        "value",
                        getattr(task, "status", ""),
                    )
                )
                if status in (TaskStatus.downloaded.value, TaskStatus.analyzed.value) and summary_file.exists():
                    try:
                        parsed = load_summary_result(summary_file)
                        if parsed.state is ParseState.MALFORMED:
                            rows.append([mol_name, "\u26a0 Parse Error", "", "", "", ""])
                            continue
                        if parsed.state is ParseState.MISSING or parsed.summary is None:
                            rows.append([mol_name, "\u2717 Missing", "", "", "", ""])
                            continue
                        s = parsed.summary
                        steps = (
                            ", ".join(f"{k}={v}" for k, v in s.step_status_counts.items())
                            if s.step_status_counts
                            else ""
                        )
                        progress = _step_progress_text(result_dir, mol_name, progress_dir)
                        rows.append(
                            [
                                mol_name,
                                "✓ Done",
                                f"{s.initial_conformers}→{s.final_conformers}",
                                f"{s.total_duration_seconds:.1f}",
                                steps,
                                progress,
                            ]
                        )
                    except Exception:
                        _logger.exception("Failed to load ConfFlow summary: %s", summary_file)
                        rows.append([mol_name, "⚠ Parse Error", "", "", "", ""])
                elif status == TaskStatus.failed.value:
                    reason = f" ({task.error_message})" if task.error_message else ""
                    rows.append([mol_name, f"✗ Failed{reason}", "", "", "", ""])
                elif status == TaskStatus.remote_completed.value:
                    reason = f" ({task.error_message})" if task.error_message else ""
                    rows.append([mol_name, f"⚠ Download Failed{reason}", "", "", "", ""])
                elif status in (
                    TaskStatus.submitting.value,
                    TaskStatus.submitted.value,
                    TaskStatus.running.value,
                ):
                    label = "Running" if status == TaskStatus.running.value else "Pending"
                    progress = _step_progress_text(result_dir, mol_name, progress_dir)
                    rows.append([mol_name, f"⏳ {label}", "", "", "", progress])
                else:
                    rows.append([mol_name, "✗ Missing", "", "", "", ""])
        else:
            # Fallback: scan local directories if no manifest
            if result_dir.exists():
                for task_dir in sorted(d for d in result_dir.iterdir() if d.is_dir()):
                    mol_name = (
                        task_dir.name.removesuffix(WORK_DIR_SUFFIX)
                        if task_dir.name.endswith(WORK_DIR_SUFFIX)
                        else task_dir.name
                    )
                    summary_file = _confflow_summary_file(result_dir, mol_name)
                    if summary_file.exists():
                        try:
                            parsed = load_summary_result(summary_file)
                            if parsed.state is ParseState.MALFORMED:
                                rows.append([mol_name, "\u26a0 Parse Error", "", "", "", ""])
                                continue
                            if parsed.state is ParseState.MISSING or parsed.summary is None:
                                rows.append([mol_name, "\u2717 Missing", "", "", "", ""])
                                continue
                            s = parsed.summary
                            steps = (
                                ", ".join(f"{k}={v}" for k, v in s.step_status_counts.items())
                                if s.step_status_counts
                                else ""
                            )
                            progress = _step_progress_text(result_dir, mol_name)
                            rows.append(
                                [
                                    mol_name,
                                    "✓ Done",
                                    f"{s.initial_conformers}→{s.final_conformers}",
                                    f"{s.total_duration_seconds:.1f}",
                                    steps,
                                    progress,
                                ]
                            )
                        except Exception:
                            _logger.exception("Failed to load ConfFlow summary: %s", summary_file)
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
            self.detail_pane.setVisible(False)
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
        self.detail_pane.setVisible(False)

    def _show_uncertain_row_detail(self, task_id: str, status: str | None, error: str | None) -> None:
        self.detail_pane.setVisible(True)
        self.detail_pane.title_label.setText(task_id)
        status_text = tr("Uncertain", self._language) if (not status or status.lower() == "uncertain") else status
        if error:
            self.detail_pane.status_label.setText(f"⚠ {status_text}: {error}")
            self.detail_pane.status_label.setStyleSheet(f"font-weight: 600; color: {Colors.WARNING};")
            self.detail_pane.error_value.setText(error)
            self.detail_pane.error_value.setVisible(True)
        else:
            self.detail_pane.status_label.setText(str(status_text))
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
        self.detail_pane.geometry_view.setPlainText(tr("(uncertain task — no parsed output)", self._language))

    def _render_detail_for_task(self, task_id: str, task, workspace: Path | None) -> None:
        """Resolve a parser output file and render the parsed result to the pane.

        Tries cache first; on miss, calls ``_resolve_output_path`` and the
        appropriate parser. The parser calls are monkeypatched in unit tests
        to avoid spawning the (slow, license-bound) real Gaussian.
        """
        from ...core.parsers.gaussian import parse_gaussian_log
        from ...core.parsers.orca import parse_orca_out

        self.detail_pane.setVisible(True)
        output_path = _resolve_output_path(task, workspace)
        if output_path is None:
            self.detail_pane.title_label.setText(task_id)
            self.detail_pane.status_label.setText(tr("Output file not found", self._language))
            # Use the saturated failure red (#b91c1c) instead of the
            # primary-brand ERROR (#ef4444) so the message reads as a
            # warning even when the page chrome uses the primary colour.
            # Same colour is asserted by
            # test_render_detail_for_task_handles_missing_output.
            self.detail_pane.status_label.setStyleSheet("font-weight: 600; color: #b91c1c;")
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
                self.detail_pane.setVisible(False)
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
            if (
                hasattr(cached, "total_energy_au")
                or "Total" in type(cached).__name__
                or cached.__class__.__name__ == "OrcaResult"
            ):
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
        headers = [
            tr("Task", self._language),
            tr("File", self._language),
            tr("Program", self._language),
            tr("Energy(Hartree)", self._language),
            "Gibbs(Hartree)",
            "ZPE(Hartree)",
            tr("Imag.Freq", self._language),
            tr("Diagnosis", self._language),
        ]
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
        if self._shutting_down:
            return
        # The same 15-second cycle also retries watcher construction that
        # failed during activation. RunMonitor.watch is idempotent for live
        # watcher ids, so healthy runs do not gain duplicate threads.
        self._start_monitoring()
        if getattr(self, "_auto_refresh_running", False):
            return
        workspace = self._workspace()
        runs = list(self._run_query.list_runs(workspace).records)
        active = [
            r
            for r in runs
            if r.status_summary.get("submitting", 0) > 0
            or r.status_summary.get("submitted", 0) > 0
            or r.status_summary.get("running", 0) > 0
        ]
        # Include remote_completed runs that haven't permanently failed download
        needs_download = [
            r
            for r in runs
            if r not in active
            and r.status_summary.get("remote_completed", 0) > 0
            and not getattr(self, "_download_backoff", {}).get(
                self._monitor_identity(workspace, r.run_id, r.server_id), 0
            )
            > 2
        ]
        # Skip runs the monitor-driven flush is already handling.
        active = [
            r for r in active if self._monitor_identity(workspace, r.run_id, r.server_id) not in self._in_progress
        ]
        needs_download = [
            r
            for r in needs_download
            if self._monitor_identity(workspace, r.run_id, r.server_id) not in self._in_progress
        ]
        if not active and not needs_download:
            return

        self._auto_refresh_running = True
        claimed = [self._monitor_identity(workspace, r.run_id, r.server_id) for r in [*active, *needs_download]]
        self._in_progress.update(claimed)
        backoff = getattr(self, "_download_backoff", {})

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
                    key = self._monitor_identity(workspace, record.run_id, record.server_id)
                    dl_failures[key] = backoff.get(key, 0) + 1
                else:
                    downloaded.append(record.run_id)
                    dl_failures[self._monitor_identity(workspace, record.run_id, record.server_id)] = 0
            return downloaded, errors, dl_failures

        from ..workers import BackgroundWorker

        def _rollback_start(error: Exception, worker=None) -> None:
            self._auto_refresh_running = False
            for watch_id in claimed:
                self._release_monitor_refresh_gate(watch_id)
            if worker is not None:
                if worker in self._bg_workers:
                    self._bg_workers.remove(worker)
                try:
                    worker.stop_safely(3000)
                except Exception:
                    _logger.debug(
                        "Failed to stop auto-refresh worker after start failure",
                        exc_info=True,
                    )
                worker.deleteLater()
            if not self._shutting_down:
                self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error))

        try:
            worker = BackgroundWorker(_run)
        except Exception as error:
            _rollback_start(error)
            return

        def _report(result):
            if self._shutting_down:
                return
            downloaded, errors, dl_failures = result
            if not hasattr(self, "_download_backoff"):
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
                    tr(
                        "Automatic refresh failed: {errors}",
                        self._language,
                        errors="; ".join(errors),
                    )
                )

        worker.result.connect(_report)
        worker.error.connect(
            lambda error: (
                self._status_cb(tr("Automatic refresh failed: {e}", self._language, e=error))
                if not self._shutting_down
                else None
            )
        )

        def _on_done():
            self._auto_refresh_running = False
            for watch_id in claimed:
                self._release_monitor_refresh_gate(watch_id)
            if worker in self._bg_workers:
                self._bg_workers.remove(worker)
            if not self._shutting_down:
                self.refresh_run_list()

        worker.finished.connect(_on_done)
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        try:
            worker.start()
        except Exception as error:
            _rollback_start(error, worker)

    def _refresh_status(self):
        if self._manual_refresh_running:
            return
        record = self._selected_record()
        if record is None:
            return
        intent = self._actions.begin(
            "refresh_status",
            (record.run_id,),
            workspace=self._workspace(),
            shutting_down=self._shutting_down,
        )
        if intent is None:
            return
        self._manual_refresh_running = True
        self._active_refresh_action_intent = intent

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

        try:
            worker = BackgroundWorker(_run)
        except Exception as error:
            self._manual_refresh_running = False
            self._actions.finish(self._active_refresh_action_intent)
            self._active_refresh_action_intent = None
            self._status_cb(tr("Refresh failed: {e}", self._language, e=error))
            return

        worker.result.connect(lambda msg: self._queue_gui(self._status_cb, msg) if msg else None)
        worker.error.connect(
            lambda e: self._queue_gui(
                self._status_cb,
                tr("Refresh failed: {e}", self._language, e=e),
            )
        )

        def _finished() -> None:
            self._manual_refresh_running = False
            self._actions.finish(self._active_refresh_action_intent)
            self._active_refresh_action_intent = None
            self._queue_gui(self._on_refresh_done)

        worker.finished.connect(_finished)
        worker.finished.connect(lambda: (self._bg_workers.remove(worker) if worker in self._bg_workers else None))
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        try:
            worker.start()
        except Exception as error:
            self._manual_refresh_running = False
            self._actions.finish(self._active_refresh_action_intent)
            self._active_refresh_action_intent = None
            if worker in self._bg_workers:
                self._bg_workers.remove(worker)
            try:
                worker.stop_safely(3000)
            except Exception:
                _logger.debug(
                    "Failed to stop manual refresh worker after start failure",
                    exc_info=True,
                )
            worker.deleteLater()
            self._status_cb(tr("Refresh failed: {e}", self._language, e=error))

    def _on_refresh_done(self):
        if self._shutting_down:
            return
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
        if not self._begin_remote_mutation(action="retry", run_ids=(record.run_id,)):
            return
        try:
            workspace = self._result_workspace(record)
            outcome = self._runtime.retry_failed(
                workspace,
                record.run_id,
                coordinator=self._coordinator_for(workspace),
            )
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
        if getattr(self, "_retry_dl_running", False):
            return
        record = self._selected_record()
        if record is None:
            return
        if not record.status_summary.get("remote_completed", 0):
            self._status_cb(tr("No tasks awaiting download", self._language))
            return
        if not self._begin_remote_mutation(action="retry_download", run_ids=(record.run_id,)):
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

        try:
            worker = BackgroundWorker(_run)
        except Exception as exc:
            self._retry_dl_running = False
            self._finish_remote_mutation()
            self._retry_download_feedback.error(tr("Download failed", self._language))
            self._status_cb(tr("Download error: {e}", self._language, e=exc))
            return

        def _done(result):
            _recs, failures = result
            self.refresh_run_list()
            if failures:
                self._retry_download_feedback.error(tr("Partial: {n} failed", self._language, n=len(failures)))
                self._status_cb(tr("Download partial: {n} failed", self._language, n=len(failures)))
            else:
                self._retry_download_feedback.success(tr("Downloaded", self._language))
                self._status_cb(tr("Download complete", self._language))

        def _err(exc):
            self._retry_download_feedback.error(tr("Download failed", self._language))
            self._status_cb(tr("Download error: {e}", self._language, e=exc))

        def _finished():
            self._retry_dl_running = False
            self._finish_remote_mutation()

        worker.result.connect(lambda result: self._queue_gui(_done, result))
        worker.error.connect(lambda exc: self._queue_gui(_err, exc))
        if not hasattr(self, "_bg_workers"):
            self._bg_workers = []
        worker.finished.connect(
            lambda: self._queue_gui(lambda: (self._bg_workers.remove(worker) if worker in self._bg_workers else None))
        )
        worker.finished.connect(lambda: self._queue_gui(_finished))
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        try:
            worker.start()
        except Exception as exc:
            self._retry_dl_running = False
            self._finish_remote_mutation()
            if worker in self._bg_workers:
                self._bg_workers.remove(worker)
            try:
                worker.stop_safely(3000)
            except Exception:
                _logger.debug(
                    "Failed to stop retry-download worker after start failure",
                    exc_info=True,
                )
            worker.deleteLater()
            self._retry_download_feedback.error(tr("Download failed", self._language))
            self._status_cb(tr("Download error: {e}", self._language, e=exc))

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
        if not self._begin_remote_mutation(action="rerun", run_ids=(record.run_id,)):
            return
        try:
            workspace = self._result_workspace(record)
            outcome = self._runtime.rerun(
                workspace,
                record.run_id,
                coordinator=self._coordinator_for(workspace),
            )
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
            self,
            tr("Compare Selected", self._language),
            tr("Analysis profile:", self._language),
            profiles,
            default_idx,
            False,
        )
        if not ok:
            return
        energy_field = "final_energy" if profile.startswith("orca") else "scf_energy"
        workspace = self._workspace()

        def _run():
            from ...services.comparison import compare_runs

            # Freeze the display projection before the worker emits its
            # result.  The Qt page must not receive the mutable RunComparison
            # service value across the thread boundary.
            return ComparePayload.from_comparison(
                compare_runs(workspace, run_ids, energy_field=energy_field, profile_name=profile)
            )

        from ..workers import BackgroundWorker

        worker = BackgroundWorker(_run)
        worker.result.connect(lambda comparison: self._queue_gui(self._show_comparison_rows, comparison))
        worker.error.connect(
            lambda e: self._queue_gui(
                self._status_cb,
                tr("Compare failed: {e}", self._language, e=e),
            )
        )
        worker.finished.connect(lambda: (self._bg_workers.remove(worker) if worker in self._bg_workers else None))
        worker.finished.connect(worker.deleteLater)
        self._bg_workers.append(worker)
        worker.start()

    def _show_comparison_rows(self, comparison: ComparePayload | Any):
        if self._shutting_down:
            return
        # Legacy direct callers may still provide RunComparison; worker
        # callbacks always provide the immutable ComparePayload.
        payload = comparison if isinstance(comparison, ComparePayload) else ComparePayload.from_comparison(comparison)
        if not payload.rows:
            self._set_parsed_results_label(
                tr("Cross-run Comparison", self._language) + " - " + tr("No comparable results", self._language)
            )
            self.result_text.setVisible(False)
            self.result_table.setRowCount(0)
            return
        headers = payload.headers
        self.result_text.setVisible(False)
        self.result_table.setColumnCount(len(headers))
        self.result_table.setHorizontalHeaderLabels(list(headers))
        self.result_table.setRowCount(len(payload.rows))
        for r, row in enumerate(payload.rows):
            for c, value in enumerate(row):
                self.result_table.setItem(r, c, QTableWidgetItem(value))
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._set_parsed_results_label(tr("Cross-run Comparison", self._language) + f" ({len(payload.rows)})")

    def _stop_run(self):
        record = self._selected_record()
        if record is None:
            return
        if not self._begin_remote_mutation(action="cancel", run_ids=(record.run_id,)):
            return
        if (
            QMessageBox.question(
                self,
                tr("Stop", self._language),
                tr("Stop run {run_id}?", self._language, run_id=record.run_id),
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            self._finish_remote_mutation()
            return
        self._stop_feedback.pending(tr("Stopping...", self._language))

        def _run(_ctx: WorkerContext):
            return self._runtime.cancel_run(
                self._result_workspace(record),
                record.run_id,
                server_id=record.server_id,
                resolver=self._coordinator_for,
                client_factory=self._client_factory,
            )

        try:
            start_context_worker(
                self,
                target=_run,
                registry_attr="_bg_workers",
                on_result=lambda result: self._queue_gui(self._on_stop_done, record.run_id, result),
                on_error=lambda error: self._queue_gui(self._on_stop_error, error),
                on_finished=lambda: self._queue_gui(self._finish_remote_mutation),
            )
        except Exception as exc:
            self._finish_remote_mutation()
            self._on_stop_error(exc)

    def _on_stop_error(self, exc: Exception | str):
        self._stop_feedback.error(tr("Stop failed", self._language))
        self._status_cb(tr("Stop failed: {e}", self._language, e=exc))

    def _on_stop_done(self, run_id: str, result: tuple[int, list[str]]):
        changed, errors = result
        intent = self._active_action_intent
        if intent is not None:
            outcome = self._actions.outcome(intent, changed_count=changed, errors=errors)
            errors = list(outcome.errors)
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
        if (
            QMessageBox.question(
                self,
                tr("Uncertain", self._language),
                prompt,
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        if not self._begin_remote_mutation(action="uncertain", run_ids=(record.run_id,)):
            return
        workspace = self._result_workspace(record)

        def _run():
            if confirm:
                return self._runtime.confirm_submitted(
                    workspace,
                    record.run_id,
                    task_ids,
                    resolver=self._coordinator_for,
                )
            return self._runtime.abandon_submit(
                workspace,
                record.run_id,
                task_ids,
                resolver=self._coordinator_for,
            )

        def _done(outcome):
            self.refresh_run_list()
            self._update_uncertain_actions()
            if outcome.errors:
                self._status_cb("; ".join(outcome.errors))
            else:
                self._status_cb(f"{action.title()}ed {outcome.changed_count} uncertain task(s)")

        start_context_worker(
            self,
            target=lambda _ctx: _run(),
            registry_attr="_bg_workers",
            on_result=_done,
            on_error=lambda error: self._status_cb(f"{action.title()} uncertain tasks failed: {error}"),
            on_finished=self._finish_remote_mutation,
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
        msg = (
            tr("Delete {n} run records?", self._language, n=len(run_ids))
            if len(run_ids) > 1
            else tr("Delete run {run_id} record?", self._language, run_id=run_ids[0])
        )
        if (
            QMessageBox.question(
                self,
                tr("Delete", self._language),
                msg,
                QMessageBox.Yes | QMessageBox.No,
            )
            != QMessageBox.Yes
        ):
            return
        workspace = self._workspace()
        if not self._begin_remote_mutation(action="delete", run_ids=tuple(run_ids)):
            return
        self._delete_feedback.pending(tr("Deleting...", self._language))
        deleted_run_ids: list[str] = []

        def _run(_ctx: WorkerContext):
            deleted = 0
            errors: list[str] = []
            for rid in run_ids:
                try:
                    record = self._runtime.load_run(workspace, rid)
                    record_workspace = self._result_workspace(record)
                    outcome = self._runtime.delete_run(
                        record_workspace,
                        rid,
                        coordinator=self._coordinator_for(record_workspace),
                    )
                    if outcome.errors:
                        errors.extend(f"{rid}: {error}" for error in outcome.errors)
                    else:
                        deleted += 1
                        deleted_run_ids.append(rid)
                except Exception as exc:
                    errors.append(f"{rid}: {exc}")
            return deleted, errors, tuple(deleted_run_ids)

        def _done(result):
            if len(result) == 3:
                deleted, errors, completed_run_ids = result
            else:
                # Keep direct test/fake-worker invocations compatible with
                # the legacy two-item worker payload.
                deleted, errors = result
                completed_run_ids = tuple(deleted_run_ids)
            intent = self._active_action_intent
            if intent is None:
                return
            outcome = self._actions.outcome(
                intent,
                changed_count=deleted,
                errors=errors,
                completed_run_ids=completed_run_ids,
            )
            self._retire_monitor_watches_for_runs(workspace, set(outcome.retired_watch_run_ids))
            self.refresh_run_list()
            if outcome.errors:
                self._delete_feedback.error(tr("Delete failed", self._language))
                self._status_cb(tr("Delete failed", self._language) + f": {'; '.join(outcome.errors)}")
            else:
                self._delete_feedback.success(tr("Deleted {n}", self._language, n=deleted))
                self._status_cb(tr("Deleted: {n} records", self._language, n=deleted))

        def _error(error: Exception | str):
            self._delete_feedback.error(tr("Delete failed", self._language))
            self._status_cb(tr("Delete failed", self._language) + f": {error}")

        try:
            start_context_worker(
                self,
                target=_run,
                registry_attr="_bg_workers",
                on_result=lambda result: self._queue_gui(_done, result),
                on_error=lambda error: self._queue_gui(_error, error),
                on_finished=lambda: self._queue_gui(self._finish_remote_mutation),
            )
        except Exception:
            self._finish_remote_mutation()
            raise

    def _submit_record(
        self,
        run_id: str,
        *,
        feedback: ButtonFeedback | None = None,
        mutation_owned: bool = False,
    ):
        if not mutation_owned and not self._begin_remote_mutation(action="submit", run_ids=(run_id,)):
            if feedback is not None:
                feedback.error(tr("Retry failed", self._language))
            return False
        workspace = self._workspace()

        def _run(_ctx: WorkerContext):
            _handle, outcome = self._runtime.submit_run(
                workspace,
                run_id,
                resolver=self._coordinator_for,
                client_factory=self._client_factory,
            )
            if outcome.errors or not outcome.submit_results:
                raise RuntimeError("; ".join(outcome.errors) or "submit returned no result")
            return outcome.submit_results[0]

        try:
            start_context_worker(
                self,
                target=_run,
                registry_attr="_bg_workers",
                on_result=lambda result: self._queue_gui(
                    lambda payload: self._on_submit_done(payload, feedback=feedback),
                    result,
                ),
                on_error=lambda error: self._queue_gui(
                    lambda payload: self._on_submit_error(payload, feedback=feedback),
                    error,
                ),
                on_finished=lambda: self._queue_gui(self._finish_remote_mutation),
            )
        except Exception:
            self._finish_remote_mutation()
            raise
        return True

    def _begin_remote_mutation(
        self,
        *,
        action: str = "mutation",
        run_ids: tuple[str, ...] = (),
    ) -> bool:
        if self._shutting_down or self._remote_mutation_running:
            if not self._shutting_down:
                self._status_cb(tr("Remote operation already in progress", self._language))
            return False
        intent = self._actions.begin(
            action,
            run_ids,
            workspace=self._workspace(),
            shutting_down=self._shutting_down,
        )
        if intent is None:
            if not self._shutting_down:
                self._status_cb(tr("Remote operation already in progress", self._language))
            return False
        self._active_action_intent = intent
        self._remote_mutation_running = True
        return True

    def _finish_remote_mutation(self) -> None:
        self._actions.finish(self._active_action_intent)
        self._active_action_intent = None
        self._remote_mutation_running = False
        if hasattr(self, "confirm_submitted_btn") and not self._shutting_down:
            self._update_uncertain_actions()

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
            f"{tr('Remote logs', self._language)}:\n  {remote_dir}/.jobdesk_submit.log\n  {remote_dir}/.jobdesk_submit.err"
        )
        self.result_text.setVisible(True)

    def _show_paths(self):
        record = self._selected_record()
        if record is None:
            return
        results_dir = self._download_directory(record)
        self.result_text.setPlainText(
            f"{tr('Run directory', self._language)}: {record.run_dir}\n"
            f"Database: {record.run_dir.parent / 'jobdesk.db'}\n"
            f"{tr('Results directory', self._language)}: {results_dir}"
        )
        self.result_text.setVisible(True)

    def shutdown(self):
        self._shutting_down = True
        self._gui_dispatcher.close()
        self._finish_remote_mutation()
        self._preview_request_id += 1
        self._refresh_timer.stop()
        self._refresh_overview_timer.stop()
        self._preview_timer.stop()
        self._activation_timer.stop()
        for timer in self._task_done_timers.values():
            timer.stop()
        self._task_done_timers.clear()
        for timer in self._checkpoint_retry_timers.values():
            timer.stop()
        self._checkpoint_retry_timers.clear()
        self._checkpoint_retry_events.clear()
        self._checkpoint_retry_attempts.clear()
        self._pending_task_events.clear()
        self._pending_checkpoint_events.clear()
        self._monitor_controller.close()
        for w in list(getattr(self, "_bg_workers", [])):
            w.stop_safely(3000)
        w = getattr(self, "_worker", None)
        if w and hasattr(w, "stop_safely"):
            w.stop_safely(3000)
        if self._owns_session_pool:
            self._runtime.close(session_pool=self._session_pool)


def _too_large_for_preview(path: Path) -> bool:
    return is_preview_too_large(path, max_bytes=MAX_PREVIEW_FILE_BYTES)


def _confflow_summary_file(result_dir: Path, mol_name: str) -> Path:
    candidates = [
        result_dir / f"{mol_name}{WORK_DIR_SUFFIX}" / RUN_SUMMARY_FILE,
        result_dir / mol_name / f"{mol_name}{WORK_DIR_SUFFIX}" / RUN_SUMMARY_FILE,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _confflow_step_stats_file(result_dir: Path, mol_name: str) -> Path:
    """Locate the workflow stats file next to a run summary if present."""
    candidates = [
        result_dir / f"{mol_name}{WORK_DIR_SUFFIX}" / WORKFLOW_STATS_FILE,
        result_dir / mol_name / f"{mol_name}{WORK_DIR_SUFFIX}" / WORKFLOW_STATS_FILE,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _confflow_workflow_state_file(result_dir: Path, mol_name: str) -> Path | None:
    """Locate the workflow state file (v1.3.0+ atomic checkpoint).

    Returns the path if found, otherwise None. Callers should fall back to
    the workflow stats file when this returns None.
    """
    candidates = [
        result_dir / f"{mol_name}{WORK_DIR_SUFFIX}" / WORKFLOW_STATE_FILE,
        result_dir / mol_name / f"{mol_name}{WORK_DIR_SUFFIX}" / WORKFLOW_STATE_FILE,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _run_progress_dir(record) -> Path | None:
    """Return the record-owned live-checkpoint directory when available."""
    run_dir = getattr(record, "run_dir", None)
    if not isinstance(run_dir, (str, os.PathLike)):
        return None
    return Path(run_dir) / "progress"


def _step_progress_text(result_dir: Path, mol_name: str, progress_dir: Path | None = None) -> str:
    """Render a short step-progress string for the Runs page table.

    Prefers the v1.3.0+ atomic workflow state file when available, falling
    back to the workflow stats file for older runs. File names are sourced
    from :mod:`jobdesk_app.core.confflow_contract`.
    """
    from ...services.confflow_results import (
        format_step_progress,
        load_step_progress,
        load_workflow_state_progress,
    )

    def _from_directory(directory: Path) -> str:
        state_file = _confflow_workflow_state_file(directory, mol_name)
        if state_file is not None:
            formatted = format_step_progress(load_workflow_state_progress(state_file))
            if formatted:
                return formatted
        return format_step_progress(load_step_progress(_confflow_step_stats_file(directory, mol_name)))

    # Live checkpoints are stored under the managed run directory.  When
    # absent or empty, preserve the full state-then-stats fallback against
    # downloaded results for completed and legacy runs.
    if progress_dir is not None:
        formatted = _from_directory(progress_dir)
        if formatted:
            return formatted
    return _from_directory(result_dir)


def _confflow_result_dir_has_summary(record, result_dir: Path, tasks=None) -> bool:
    from ...core.lifecycle import TaskStatus

    if tasks is not None:
        return any(
            task.status in (TaskStatus.downloaded, TaskStatus.analyzed)
            and _confflow_summary_file(result_dir, task.task_id).exists()
            for task in tasks
        )
    return any(result_dir.rglob(RUN_SUMMARY_FILE)) if result_dir.exists() else False


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


def _analysis_row(
    task_id: str,
    file_name: str,
    program: str,
    result,
    diagnosis: str | None,
    language: str,
) -> list[str]:
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
