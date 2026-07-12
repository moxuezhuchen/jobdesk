from __future__ import annotations

import hashlib
import posixpath
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...config.servers import load_servers
from ...core.file_transfer import OverwritePolicy
from ...core.submit_payload import InputSource
from ...core.transfer import TransferStatus
from ...services.external_terminal import build_terminal_launch, launch_terminal
from ...services.file_transfer_service import FileTransferService
from ...services.gui_settings import GuiSettingsStore
from ...services.run_service import RunService
from ..button_feedback import ButtonFeedback, ButtonRole, apply_button_role
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client
from ..widgets import EmptyStateHint
from ..worker_utils import WorkerContext, start_context_worker, start_tracked_worker
from ..workers import BackgroundWorker
from .file_transfer_helpers import (
    collect_remote_delete_roots,
    connection_status_text,
    default_remote_dir_for_server,
    file_table_headers,
    format_file_size,
    format_modified_time,
    format_queue_summary,
    format_remote_size,
    format_selection_summary,
    local_parent_row,
    local_table_row,
    normalize_remote_path,
    remote_child_path,
    remote_parent_row,
    remote_table_row,
)
from .file_transfer_widgets import (
    _clamp_column_widths,
    _ConnectedSFTP,
    _default_column_widths,
    _FileTable,
    _load_rows,
    _setup_table,
)

CONTROL_HEIGHT = 38
RENAME_DIALOG_MIN_WIDTH = 460
RENAME_DIALOG_INPUT_MIN_WIDTH = 380
TRANSFER_PROGRESS_HEIGHT = 30
TRANSFER_PROGRESS_MIN_WIDTH = 320
TRANSFER_PROGRESS_MAX_WIDTH = 560
RENAME_ON_SELECTED_CLICK_DELAY_MS = 700
REMOTE_EDIT_POLL_INTERVAL_MS = 1500


@dataclass
class _RemoteEditSession:
    remote_path: str
    local_path: Path
    uploaded_signature: str
    uploading_signature: str | None = None


class ConfigUnreadable(Exception):
    """Raised when the user's existing config file cannot be parsed.

    The Files page "Import sample" button is the most common way a
    user recovers from a broken servers.yaml -- they hit it because
    the empty-state hint is up. The original file is therefore the
    user's best chance to repair whatever is wrong (typo, half-
    written crash, encoding glitch). Overwriting it with a sample
    turns a recoverable failure into a permanent loss, so we raise
    this exception instead and let the caller show a clear error.

    Attributes:
        path: Path to the file we refused to overwrite.
        cause: The original parse failure (yaml.YAMLError or any
            non-mapping root). Surfaced verbatim in the dialog so the
            user can act on the actual error.
    """

    def __init__(self, path: Path, cause: BaseException) -> None:
        super().__init__(
            f"servers.yaml at {path} could not be parsed: {cause}"
        )
        self.path = path
        self.cause = cause


def _format_transfer_speed(bytes_per_second: float) -> str:
    if bytes_per_second >= 1024 * 1024:
        return f"{bytes_per_second / 1024 / 1024:.1f} MB/s"
    if bytes_per_second >= 1024:
        return f"{bytes_per_second / 1024:.0f} KB/s"
    return f"{bytes_per_second:.0f} B/s"


def _load_existing_servers_data(path: Path) -> dict:
    """Read ``path`` and return the existing mapping root, with guards.

    Returns an empty dict when ``path`` does not exist. Raises
    :class:`ConfigUnreadable` when the file exists but cannot be
    parsed (or its top level is not a mapping) -- the caller is
    responsible for surfacing the error to the user, but the file
    on disk is NOT modified by this function.

    Review-fix: extracted from ``FileTransferPage._import_sample_servers_yaml``
    so tests can drive it without instantiating a full QWidget page.
    """
    import yaml

    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8")
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        # Preserve the broken file exactly as it was; surface a clear
        # error rather than overwrite it with a sample.
        raise ConfigUnreadable(path, exc) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        # The file parses (it's YAML) but the top-level isn't a mapping
        # -- e.g. someone wrote a list or a scalar. Same data-safety
        # rule: do not silently overwrite.
        raise ConfigUnreadable(
            path,
            ValueError(
                f"servers.yaml top-level is {type(loaded).__name__}, "
                "expected a mapping"
            ),
        )
    return loaded


class FileTransferPage(QWidget):
    runs_submitted = Signal(list)
    use_as_input_received = Signal(list)  # list[InputSource]
    # Phase 2.0: emitted when the user clicks the Files-page [Submit] button.
    # MainWindow opens the SubmitDialog and forwards the resulting
    # SubmitPayload to the use case.
    submit_requested_with_files = Signal(list)  # list[InputSource]
    # Phase 2.1: emitted when the empty-state hint asks the shell to switch
    # to Settings (or any other page that wants to handle nav-up requests).
    # MainWindow wires this in a later phase; pages are responsible only for
    # raising the signal — never for calling a navigator directly.
    open_settings_requested = Signal()

    def __init__(self, state, log_cb, status_cb, error_cb, coordinator_factory=None):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._error_cb = error_cb
        self._coordinator_factory = coordinator_factory
        self._servers = {}
        self._service: FileTransferService | None = None
        self._connected_server_id: str | None = None
        self._connected_server = None
        self._gui_settings = GuiSettingsStore().load()
        self._language = self._gui_settings.language
        self._remote_list_request_id = 0
        self._remote_list_fallbacks: list[str] = []
        self._server_remote_dirs: dict[str, str] = {}
        self._background_workers = []
        self._shutting_down = False
        self._local_refresh_request_id = 0
        self._local_poll_running = False
        self._initialized = False
        self._remote_edit_sessions: dict[str, _RemoteEditSession] = {}
        self._pending_click_rename: tuple[str, int] | None = None
        self._last_file_selection_side: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # -- Phase 2.1: empty-state hints (no server / connected-but-empty) --
        # Both start hidden; visibility is toggled in on_activated once the
        # page knows whether a remote service is connected and the current
        # remote directory has any entries.
        self._no_server_hint = EmptyStateHint(
            title_key="No server connected",
            body_key="Add a Linux SSH server from the Settings tab to browse and transfer files.",
            action_texts=(
                ("open_settings", "Open Settings"),
                ("import_sample", "Import sample servers.yaml"),
            ),
            language=self._language,
            parent=self,
        )
        self._no_server_hint.action_requested.connect(self._on_no_server_action)
        self._no_server_hint.setVisible(False)
        layout.addWidget(self._no_server_hint)

        self._empty_dir_hint = EmptyStateHint(
            title_key="Browse a remote directory",
            body_key=(
                "Pick a folder on the right, then drop .xyz / .gjf / .inp "
                "files into the input list below."
            ),
            action_texts=(("refresh", "Refresh"),),
            language=self._language,
            parent=self,
        )
        self._empty_dir_hint.action_requested.connect(self._on_empty_dir_action)
        self._empty_dir_hint.setVisible(False)
        layout.addWidget(self._empty_dir_hint)

        self._apply_default_local_folder()
        self.local_path_btn = QPushButton(str(self.state.current_project_root or Path.cwd()))
        self.local_path_btn.setToolTip(self.local_path_btn.text())
        self.local_path_btn.setStyleSheet("text-align: left; padding: 0 8px;")
        self.local_path_btn.clicked.connect(self._choose_local_folder)
        self.server_combo = QComboBox()
        self.server_combo.setMinimumWidth(120)
        self.server_combo.setMaximumWidth(200)
        self.server_label = QLabel(tr("Server:", self._language))
        self.server_combo.currentIndexChanged.connect(self._auto_connect_selected_server)
        self.connection_label = QLabel(connection_status_text(None, False, language=self._language))
        self.connection_label.setMinimumWidth(80)
        self.connection_label.setMaximumWidth(180)
        self.connection_label.setVisible(False)
        self.remote_path = QLineEdit(self._gui_settings.default_remote_dir)
        self.remote_path.setMinimumWidth(80)
        self.remote_path.returnPressed.connect(self._refresh_remote)
        for label in (self.server_label,):
            label.setFixedHeight(36)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._normalize_control_heights(
            self.local_path_btn,
            self.server_combo,
            self.remote_path,
        )

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setToolTip(tr("Refresh", self._language))
        self.refresh_btn.clicked.connect(self._refresh_all)
        self._normalize_control_heights(self.refresh_btn)
        self.open_terminal_btn = QPushButton(tr("Open Terminal Here", self._language))
        self.open_terminal_btn.clicked.connect(self._open_terminal_here)
        self._normalize_control_heights(self.open_terminal_btn)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        splitter.setMinimumWidth(0)
        self.local_table = _FileTable("local")
        self.remote_table = _FileTable("remote")
        self.local_table.setMinimumWidth(120)
        self.remote_table.setMinimumWidth(160)
        self.local_table.setMinimumHeight(180)
        self.remote_table.setMinimumHeight(180)
        self.local_table.setAlternatingRowColors(True)
        self.remote_table.setAlternatingRowColors(True)
        self.local_table.setSortingEnabled(True)
        self.remote_table.setSortingEnabled(True)
        self.local_table.drop_files.connect(self._download_dropped_remote_paths)
        self.local_table.copy_local_files.connect(self._copy_dropped_local_paths)
        self.local_table.move_local_files.connect(self._move_local_paths_into_directory)
        self.remote_table.drop_files.connect(self._upload_dropped_local_paths)
        self.remote_table.move_remote_files.connect(self._move_remote_paths_into_directory)
        self.local_table.selected_item_clicked.connect(
            lambda item: self._schedule_selected_click_rename("local", item)
        )
        self.remote_table.selected_item_clicked.connect(
            lambda item: self._schedule_selected_click_rename("remote", item)
        )
        _setup_table(self.local_table, self._translated_table_headers("local"), hidden_columns=[3, 4])
        _setup_table(self.remote_table, self._translated_table_headers("remote"), hidden_columns=[4, 5])
        self.local_table.bind_column_widths("files.local", _clamp_column_widths("files.local", _default_column_widths("files.local")))
        self.remote_table.bind_column_widths("files.remote", _clamp_column_widths("files.remote", _default_column_widths("files.remote")))
        self.local_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.remote_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.local_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.local_table.customContextMenuRequested.connect(self._local_context_menu)
        self.remote_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_table.customContextMenuRequested.connect(self._remote_context_menu)
        self.local_table.itemDoubleClicked.connect(self._open_local_item)
        self.remote_table.itemDoubleClicked.connect(self._open_remote_item)
        self.local_table.key_delete.connect(self._delete_local)
        self.local_table.key_enter.connect(self._enter_local)
        self.local_table.key_rename.connect(lambda: self._rename_from_key("local"))
        self.remote_table.key_delete.connect(self._delete_remote)
        self.remote_table.key_enter.connect(self._enter_remote)
        self.remote_table.key_rename.connect(lambda: self._rename_from_key("remote"))
        self._click_rename_timer = QTimer(self)
        self._click_rename_timer.setSingleShot(True)
        self._click_rename_timer.setInterval(RENAME_ON_SELECTED_CLICK_DELAY_MS)
        self._click_rename_timer.timeout.connect(self._trigger_selected_click_rename)
        self._remote_edit_timer = QTimer(self)
        self._remote_edit_timer.setInterval(REMOTE_EDIT_POLL_INTERVAL_MS)
        self._remote_edit_timer.timeout.connect(self._check_remote_edit_sessions)
        local_pane = QWidget()
        local_pane.setMinimumWidth(160)
        local_pane_layout = QVBoxLayout(local_pane)
        local_pane_layout.setContentsMargins(0, 0, 0, 0)
        local_pane_layout.setSpacing(4)
        local_header_widget = QWidget()
        local_header_widget.setObjectName("LocalHeader")
        local_header_widget.setStyleSheet(
            "#LocalHeader { background: #dfe7f0; border: 1px solid #9aaec4;"
            " border-radius: 3px; border-top-right-radius: 0; border-bottom-right-radius: 0; }"
        )
        local_header_widget.setFixedHeight(52)
        local_header = QHBoxLayout(local_header_widget)
        local_header.setContentsMargins(8, 0, 8, 0)
        local_header.setSpacing(12)
        local_header.addWidget(self.local_path_btn, 1)
        local_header.addWidget(self.refresh_btn, 0)
        local_pane_layout.addWidget(local_header_widget)
        local_pane_layout.addWidget(self.local_table, 1)

        remote_pane = QWidget()
        remote_pane.setMinimumWidth(180)
        remote_pane_layout = QVBoxLayout(remote_pane)
        remote_pane_layout.setContentsMargins(0, 0, 0, 0)
        remote_pane_layout.setSpacing(4)
        remote_header_widget = QWidget()
        remote_header_widget.setObjectName("RemoteHeader")
        remote_header_widget.setStyleSheet(
            "#RemoteHeader { background: #dfe7f0; border: 1px solid #9aaec4;"
            " border-radius: 3px; border-top-left-radius: 0; border-bottom-left-radius: 0; }"
            " #RemoteHeader QLineEdit, #RemoteHeader QComboBox {"
            " background: #f7f9fc; border: 1px solid #9aaec4; border-radius: 3px;"
            " padding: 0 8px; min-height: 38px; max-height: 38px; }"
            " #RemoteHeader QLabel { background: transparent; }"
        )
        remote_header_widget.setFixedHeight(52)
        remote_header = QHBoxLayout(remote_header_widget)
        remote_header.setContentsMargins(8, 0, 8, 0)
        remote_header.setSpacing(12)
        remote_header.addWidget(self.server_label, 0)
        remote_header.addWidget(self.server_combo, 0)
        remote_header.addWidget(self.connection_label)
        remote_header.addWidget(self.remote_path, 1)
        remote_header.addWidget(self.open_terminal_btn, 0)
        remote_pane_layout.addWidget(remote_header_widget)
        remote_pane_layout.addWidget(self.remote_table, 1)

        splitter.addWidget(local_pane)
        splitter.addWidget(remote_pane)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 620])

        # Progress bar — surfaces transfer progress (upload/download).  Lives
        # outside the splitter so it doesn't steal vertical space from the
        # file tables.  Hidden by default; flipped on by _start_transfer_worker.
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumWidth(TRANSFER_PROGRESS_MIN_WIDTH)
        self.progress_bar.setMaximumWidth(TRANSFER_PROGRESS_MAX_WIDTH)
        self.progress_bar.setMinimumHeight(TRANSFER_PROGRESS_HEIGHT)
        self.progress_bar.setMaximumHeight(TRANSFER_PROGRESS_HEIGHT)
        self.progress_bar.setTextVisible(True)
        progress_row = QHBoxLayout()
        progress_row.setContentsMargins(0, 0, 0, 0)
        progress_row.setSpacing(0)
        progress_row.addStretch()
        progress_row.addWidget(self.progress_bar)
        progress_wrap = QWidget()
        progress_wrap.setLayout(progress_row)
        progress_wrap.setContentsMargins(0, 0, 0, 0)

        # Phase 2.0: primary [Submit] button — must exist before the
        # action_row below adds it to the layout.
        self.submit_btn = QPushButton(tr("Submit (selected files)", self._language))
        self.submit_btn.setObjectName("FilesSubmitBtn")
        apply_button_role(self.submit_btn, ButtonRole.PRIMARY_ACTION)
        self.submit_btn.setEnabled(False)
        self._normalize_control_heights(self.submit_btn)
        self.submit_btn.clicked.connect(self._on_submit_clicked)

        # Phase 2.0: action row surfaces the selection summary + Submit.
        self.selection_label = QLabel(format_selection_summary(0, 0, self._language))
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.addWidget(self.selection_label, 1)
        action_row.addWidget(self.submit_btn, 0)
        action_wrap = QWidget()
        action_wrap.setLayout(action_row)
        action_wrap.setContentsMargins(0, 0, 0, 0)

        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setHandleWidth(8)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.addWidget(splitter)
        main_splitter.addWidget(progress_wrap)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 0)
        main_splitter.setSizes([100, 0])
        layout.addWidget(main_splitter, 1)
        layout.addWidget(action_wrap, 0)

        self._refresh_feedback = ButtonFeedback(self.refresh_btn, role=ButtonRole.REFRESH_ACTION)
        self._terminal_feedback = ButtonFeedback(self.open_terminal_btn, role=ButtonRole.INSTANT_ACTION)

        # (Phase 2.0 Files-page [Submit] button is created earlier in __init__
        # so the action row above can pick it up.)

        self._load_servers()

        # Local directory auto-refresh via polling (QFileSystemWatcher doesn't
        # detect writes from WSL /mnt/c/, so we poll instead)
        self._local_poll_timer = QTimer(self)
        self._local_poll_timer.setInterval(2000)
        self._local_poll_timer.timeout.connect(self._check_local_changes)
        self._local_poll_snapshot: dict[str, float] = {}
        self._local_poll_timer.start()

        self._refresh_local()
        self._connect_selection_signals()
        self._allow_width_shrink()
        self._normalize_all_control_heights()

        if self._gui_settings.auto_connect:
            QTimer.singleShot(0, self._auto_connect_selected_server)

    def _choose_local_folder(self):
        path = QFileDialog.getExistingDirectory(self, tr("Select local directory", self._language))
        if not path:
            return
        self.state.current_project_root = Path(path)
        self.local_path_btn.setText(path)
        self.local_path_btn.setToolTip(path)
        self._save_last_local_folder(Path(path))
        self._refresh_local()

    def on_activated(self):
        self._gui_settings = GuiSettingsStore().load()
        self.apply_language(self._gui_settings.language)

        first_run = not self._initialized
        if first_run:
            self._initialized = True
            self._apply_default_local_folder()
            local_root = str(self.state.current_project_root or Path.cwd())
            self.local_path_btn.setText(local_root)
            self.local_path_btn.setToolTip(local_root)
            if self._gui_settings.last_remote_dirs:
                self._server_remote_dirs.update(self._gui_settings.last_remote_dirs)

        # Always reload server list, but block signals to prevent auto-connect
        self.server_combo.blockSignals(True)
        self._load_servers_inner()
        if first_run and self._gui_settings.last_server_id:
            idx = self.server_combo.findData(self._gui_settings.last_server_id)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)
        self.server_combo.blockSignals(False)

        if first_run:
            server_id = self.server_combo.currentData()
            if server_id:
                last_path = self._server_remote_dirs.get(server_id)
                if last_path:
                    self.remote_path.setText(last_path)
                self._auto_connect_selected_server()
        self._refresh_local()
        self._update_empty_state_visibility()

    def _update_empty_state_visibility(self) -> None:
        """Toggle the two empty-state hints based on connection status.

        Shown when there is no remote service. Hides itself once a
        service is available. The second hint (connected but empty dir)
        only shows once a service exists AND the remote table has 0 rows
        — we treat ``self.remote_table`` as the source of truth.
        """
        no_service = self._service is None
        self._no_server_hint.setVisible(no_service)
        has_service = self._service is not None
        empty_remote = (
            has_service and self.remote_table.rowCount() <= 1
        )  # row 0 may be the synthetic ".."
        self._empty_dir_hint.setVisible(has_service and empty_remote)

    def apply_language(self, language: str):
        self._language = language
        self.refresh_btn.setText("\u27f3 " + tr("Refresh", language))
        self.open_terminal_btn.setText(tr("Open Terminal Here", language))
        self.server_label.setText(tr("Server:", language))
        if hasattr(self, "submit_btn"):
            self.submit_btn.setText(tr("Submit (selected files)", language))
        self._refresh_feedback.set_idle_text(self.refresh_btn.text())
        self._terminal_feedback.set_idle_text(self.open_terminal_btn.text())
        self.local_table.setHorizontalHeaderLabels(self._translated_table_headers("local"))
        self.remote_table.setHorizontalHeaderLabels(self._translated_table_headers("remote"))
        self.connection_label.setText(connection_status_text(
            self._connected_server_id,
            self._service is not None,
            language=language,
        ))
        # -- Phase 2.1: retranslate empty-state hints --
        self._no_server_hint.apply_language(language)
        self._empty_dir_hint.apply_language(language)

    def _translated_table_headers(self, kind: str) -> list[str]:
        return [tr(header, self._language) for header in file_table_headers(kind)] + ["type", "path"]

    def _load_servers(self):
        """Reload servers (used by tab switches after first init)."""
        self.server_combo.blockSignals(True)
        self._load_servers_inner()
        self.server_combo.blockSignals(False)

    def _load_servers_inner(self):
        """Populate server_combo. Caller must handle signal blocking."""
        try:
            cfg = load_servers()
            self._servers = cfg.servers
            current = self.server_combo.currentData()
            self.server_combo.clear()
            for sid in sorted(self._servers):
                self.server_combo.addItem(sid, sid)
            if self._gui_settings.default_server_id:
                idx = self.server_combo.findData(self._gui_settings.default_server_id)
                if idx >= 0:
                    self.server_combo.setCurrentIndex(idx)
            if current:
                idx = self.server_combo.findData(current)
                if idx >= 0:
                    self.server_combo.setCurrentIndex(idx)
        except Exception as exc:
            self._servers = {}
            self._status_cb(f"No servers configured: {exc}")

    def _auto_connect_selected_server(self):
        if not self._gui_settings.auto_connect:
            self.connection_label.setText(tr("Auto connect disabled", self._language))
            return
        server_id = self.server_combo.currentData()
        if not server_id:
            self.connection_label.setText(connection_status_text(None, False, language=self._language))
            return
        if self._connected_server_id == server_id and self._service is not None:
            self.connection_label.setText(connection_status_text(server_id, True, language=self._language))
            return
        self._remember_current_remote_dir()
        server = self._servers.get(server_id)
        if server is not None:
            self.remote_path.setText(self._server_remote_dirs.get(server_id) or default_remote_dir_for_server(server))
        self._connect()

    def _remember_current_remote_dir(self):
        if self._connected_server_id:
            self._server_remote_dirs[self._connected_server_id] = normalize_remote_path(
                self.remote_path.text().strip() or "/"
            )

    def _connect(self):
        server_id = self.server_combo.currentData()
        if not server_id:
            self._status_cb("Select a server first")
            self.connection_label.setText(connection_status_text(None, False, language=self._language))
            return
        server = self._servers[server_id]
        if self._connected_server_id != server_id:
            self.remote_path.setText(self._server_remote_dirs.get(server_id) or default_remote_dir_for_server(server))

        def factory():
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            return _ConnectedSFTP(ssh, sftp)

        if self._service is not None:
            self._close_service_async(self._service)
        self._service = FileTransferService(
            factory,
            allowed_delete_roots=collect_remote_delete_roots(self._current_run_tasks()),
            persistent_session=True,
        )
        self._connected_server_id = server_id
        self._connected_server = server
        self.connection_label.setText(connection_status_text(server_id, True, language=self._language))
        self._refresh_remote()
        # Phase 2.1: refresh empty-state hints now that the connection
        # state flipped from "none" to "connected".
        self._update_empty_state_visibility()

    def _current_run_tasks(self):
        run_id = getattr(self.state, "current_batch_id", None)
        if not isinstance(run_id, str) or not run_id:
            return []
        workspace = Path(getattr(self.state, "current_project_root", None) or Path.cwd())
        try:
            return RunService(workspace).repository.load_tasks(run_id)
        except (KeyError, OSError):
            return []

    def _close_service_async(self, service: FileTransferService) -> None:
        worker = BackgroundWorker(service.close)
        self._keep_worker(worker)
        worker.start()

    def _apply_default_local_folder(self):
        # Prefer last-used folder over the static default
        folder = self._gui_settings.last_local_folder or self._gui_settings.default_local_folder
        if folder and Path(folder).exists():
            self.state.current_project_root = Path(folder)

    def _save_last_local_folder(self, path: Path) -> None:
        """Persist the current local folder so it survives restarts."""
        GuiSettingsStore().update(last_local_folder=str(path))

    @staticmethod
    def _build_local_rows(base: Path, hide_dot: bool) -> tuple[dict[str, float], list[list[str]], str | None]:
        snapshot: dict[str, float] = {}
        rows = []
        parent = local_parent_row(base)
        if parent is not None:
            rows.append(parent)
        try:
            children = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower(), p.name))
        except (PermissionError, OSError):
            return snapshot, rows, f"No permission to access: {base}"
        for child in children:
            if hide_dot and child.name.startswith("."):
                continue
            try:
                st = child.stat()
                snapshot[str(child)] = st.st_mtime_ns if hasattr(st, "st_mtime_ns") else st.st_mtime
                is_dir = child.is_dir()
                size = "" if is_dir else format_file_size(st.st_size)
                mtime = format_modified_time(st.st_mtime)
            except (PermissionError, OSError):
                continue
            rows.append(local_table_row(child.name, is_dir, size, str(child), mtime))
        return snapshot, rows, None

    def _check_local_changes(self):
        """Poll local directory for changes (handles WSL /mnt/c writes)."""
        if self._local_poll_running:
            return
        base = Path(self.state.current_project_root or Path.cwd())
        hide_dot = self._gui_settings.hide_dotfiles
        self._local_poll_running = True

        def _run(_ctx: WorkerContext):
            return self._build_local_rows(base, hide_dot)

        def _done(result):
            self._local_poll_running = False
            snapshot, rows, error = result
            if error:
                self._status_cb(error)
            if snapshot != self._local_poll_snapshot:
                self._local_poll_snapshot = snapshot
                self._load_local_rows(rows)

        def _error(_message: str):
            self._local_poll_running = False

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=_done,
            on_error=_error,
        )

    def _refresh_local(self):
        base = self.state.current_project_root or Path.cwd()
        snapshot, rows, error = self._build_local_rows(Path(base), self._gui_settings.hide_dotfiles)
        if error:
            self._status_cb(error)
        self._local_poll_snapshot = snapshot
        self._load_local_rows(rows)

    def _load_local_rows(self, rows: list[list[str]]) -> None:
        _load_rows(self.local_table, rows)
        self._update_selection_summary()

    def _refresh_local_async(self):
        base = Path(self.state.current_project_root or Path.cwd())
        hide_dot = self._gui_settings.hide_dotfiles
        self._local_refresh_request_id += 1
        request_id = self._local_refresh_request_id

        def _run(_ctx: WorkerContext):
            return self._build_local_rows(base, hide_dot)

        def _done(result):
            if request_id != self._local_refresh_request_id:
                return
            snapshot, rows, error = result
            if error:
                self._status_cb(error)
            self._local_poll_snapshot = snapshot
            self._load_local_rows(rows)

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=_done,
            on_error=lambda error: self._status_cb(f"Local refresh failed: {error.splitlines()[0]}"),
        )

    def _refresh_local_after_navigation(self):
        self._refresh_local()
        self.local_table.clearSelection()
        self.local_table.setCurrentCell(-1, -1)

    def _on_no_server_action(self, action_id: str) -> None:
        """Route the Files-page empty-state buttons.

        "open_settings" emits ``open_settings_requested`` so MainWindow can
        flip the sidebar (wired in the cross-page nav helper in main_window).
        "import_sample" merges a copy-paste-ready YAML snippet into the
        user's default ``servers.yaml`` so the empty Files page gets a
        real first server in one click, then refreshes the Files server
        combo / Settings table so the user sees the new entry immediately.
        """
        if action_id == "open_settings":
            self.open_settings_requested.emit()
            return
        if action_id == "import_sample":
            try:
                self._import_sample_servers_yaml()
            except ConfigUnreadable as exc:
                # Data-safety branch: never overwrite a broken file. Show
                # the original parse error inline so the user knows what
                # to fix.
                self._error_cb(
                    tr("Cannot import sample", self._language),
                    tr(
                        "{path} could not be parsed. Fix the file manually "
                        "(or move it aside) and try again.\n\n{err}",
                        self._language,
                        path=str(exc.path),
                        err=str(exc.cause),
                    ),
                )
            except Exception as exc:
                self._error_cb(
                    tr("Import sample failed", self._language),
                    str(exc),
                )
            return

    def _import_sample_servers_yaml(self) -> None:
        """Drop a working sample server into the user's servers.yaml.

        Generates a UNIQUE sample id (so re-running does not conflict),
        merges it into the existing servers dict if the file already
        exists, writes atomically, and refreshes the visible server
        list. The placeholder values are deliberately conservative
        (127.0.0.1, myuser) -- the user is expected to edit them in
        the Settings tab right after import.

        Review-fix (data safety): if the existing file cannot be parsed
        (syntax error, encoding problem, half-written crash recovery)
        or its top-level is not a mapping, we MUST NOT overwrite it.
        The original config is the user's best chance to repair
        whatever is wrong; clobbering it would turn a recoverable
        failure into a permanent loss. Instead we raise
        :class:`ConfigUnreadable` so the caller can show a clear error
        dialog pointing the user at the broken file path and
        recommending a manual edit. No YAML is written in that branch.
        """
        import yaml

        from ...config.servers import get_default_servers_path
        from ...core.atomic_write import atomic_write_text

        # Build a unique id so multiple clicks don't collide.
        base_id = "my_linux_box"
        sid = base_id
        suffix = 1
        path = get_default_servers_path()
        # Review-fix: pull the merge-data through a dedicated helper
        # that raises ``ConfigUnreadable`` for any unparseable file,
        # rather than swallowing parse errors and continuing with an
        # empty dict -- the latter would overwrite a broken config
        # the user is most likely trying to recover.
        data = _load_existing_servers_data(path)
        servers = data.setdefault("servers", {})
        while sid in servers:
            suffix += 1
            sid = f"{base_id}_{suffix}"

        servers[sid] = {
            "host": "127.0.0.1",
            "port": 22,
            "username": "myuser",
            "auth_method": "key",
            "key_path": "~/.ssh/id_ed25519",
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        )

        # Refresh the Files page's server_combo + Settings table so the
        # new entry shows up without restarting the app.
        self._load_servers()
        self._status_cb(
            tr(
                "Imported sample server '{sid}'. Edit host/key in Settings.",
                self._language,
                sid=sid,
            )
        )

    def _on_empty_dir_action(self, action_id: str) -> None:
        if action_id == "refresh":
            self._refresh_all()

    def _refresh_all(self):
        self._refresh_feedback.pending(tr("Refreshing...", self._language))
        self._refresh_local_async()
        self._refresh_remote()

    def _open_terminal_here(self):
        server_id = self._connected_server_id or self.server_combo.currentData()
        if not server_id:
            self._status_cb(tr("Select a server first", self._language))
            return
        server = self._connected_server if self._connected_server_id == server_id else self._servers.get(server_id)
        if server is None:
            self._status_cb(tr("Select a server first", self._language))
            return
        remote_dir = normalize_remote_path(self.remote_path.text().strip() or "/")
        self.remote_path.setText(remote_dir)
        try:
            launch = build_terminal_launch(
                server,
                remote_dir,
                temp_dir=Path(tempfile.gettempdir()) / "jobdesk_terminal",
            )
            launch_terminal(launch)
            self._terminal_feedback.success(tr("Opened", self._language))
            self._status_cb(tr("Terminal opened", self._language))
        except Exception as exc:
            self._terminal_feedback.error(tr("Open failed", self._language))
            self._status_cb(tr("Open terminal failed: {e}", self._language, e=exc))

    def _refresh_remote(self):
        if self._service is None:
            self._auto_connect_selected_server()
            if self._service is None and self.refresh_btn.property("feedbackState") == "pending":
                self._refresh_feedback.error(tr("Refresh failed", self._language))
            return
        self._remote_list_fallbacks = self._fallback_remote_dirs()
        self._refresh_remote_path(self.remote_path.text().strip() or "/")

    def _refresh_remote_path(self, remote_path: str):
        remote_dir = normalize_remote_path(self.remote_path.text().strip() or "/")
        if remote_path:
            remote_dir = normalize_remote_path(remote_path)
        self.remote_path.setText(remote_dir)
        self._remote_list_request_id += 1
        request_id = self._remote_list_request_id
        service = self._service

        def _run():
            return service.list_remote(remote_dir)

        self._status_cb(f"Listing remote: {remote_dir}")
        self.remote_worker = BackgroundWorker(_run)
        self.remote_worker.result.connect(lambda entries: self._on_remote_entries_loaded(request_id, remote_dir, entries))
        self.remote_worker.error.connect(lambda error: self._on_remote_list_error(request_id, error))
        self._keep_worker(self.remote_worker)
        self.remote_worker.start()

    def _fallback_remote_dirs(self) -> list[str]:
        server = self._connected_server
        candidates = [
            default_remote_dir_for_server(server) if server is not None else "",
            self._gui_settings.default_remote_dir,
            "/tmp",
            "/",
        ]
        current = normalize_remote_path(self.remote_path.text().strip() or "/")
        result = []
        for candidate in candidates:
            if not candidate:
                continue
            path = normalize_remote_path(candidate)
            if path != current and path not in result:
                result.append(path)
        return result

    def _on_remote_entries_loaded(self, request_id: int, remote_dir: str, entries):
        if request_id != self._remote_list_request_id:
            return
        if self._connected_server_id:
            self._server_remote_dirs[self._connected_server_id] = remote_dir
        rows = []
        parent = remote_parent_row(remote_dir)
        if parent is not None:
            rows.append(parent)
        hide_dot = self._gui_settings.hide_dotfiles
        rows.extend([
            remote_table_row(
                e.name,
                e.is_dir,
                format_remote_size(e.size_bytes, e.is_dir),
                format_modified_time(e.modified_at),
                e.permissions,
                e.path,
            )
            for e in entries
            if not (hide_dot and e.name.startswith("."))
        ])
        _load_rows(self.remote_table, rows)
        self._update_selection_summary()
        self.connection_label.setText(connection_status_text(self._connected_server_id, True, language=self._language))
        if self.refresh_btn.property("feedbackState") == "pending":
            self._refresh_feedback.success(tr("Refreshed", self._language))
        self._status_cb(f"Remote listed: {remote_dir} ({len(rows)} entries)")
        # Phase 2.1: re-evaluate empty-state hints now that the remote
        # table has just been (re)populated. Without this call, the
        # "connected but empty dir" hint never reappears once hidden.
        self._update_empty_state_visibility()

    def _on_remote_list_error(self, request_id: int, error: str):
        if request_id != self._remote_list_request_id:
            return
        if self._remote_list_fallbacks and _remote_list_error_allows_fallback(error):
            fallback = self._remote_list_fallbacks.pop(0)
            self._status_cb(f"Remote path missing, trying: {fallback}")
            self._refresh_remote_path(fallback)
            return
        self.connection_label.setText(connection_status_text(self._connected_server_id, False, error.splitlines()[0], self._language))
        if self.refresh_btn.property("feedbackState") == "pending":
            self._refresh_feedback.error(tr("Refresh failed", self._language))
        self._error_cb("Remote List Error", error.splitlines()[0])

    def _selected_local_path(self) -> Path | None:
        paths = self._selected_local_paths()
        return paths[0] if paths else None

    def _selected_local_paths(self) -> list[Path]:
        rows = sorted({idx.row() for idx in self.local_table.selectedIndexes()})
        if not rows and self.local_table.currentRow() >= 0:
            rows = [self.local_table.currentRow()]
        paths: list[Path] = []
        for row in rows:
            name_item = self.local_table.item(row, 0)
            if name_item and name_item.text() == "..":
                continue
            item = self.local_table.item(row, 4)
            if item:
                paths.append(Path(item.text()))
        return paths

    def _selected_remote_path(self) -> str | None:
        paths = self._selected_remote_paths()
        return paths[0] if paths else None

    def _selected_remote_paths(self) -> list[str]:
        rows = sorted({idx.row() for idx in self.remote_table.selectedIndexes()})
        if not rows and self.remote_table.currentRow() >= 0:
            rows = [self.remote_table.currentRow()]
        paths: list[str] = []
        for row in rows:
            name_item = self.remote_table.item(row, 0)
            if name_item and name_item.text() == "..":
                continue
            item = self.remote_table.item(row, 5)
            if item:
                paths.append(item.text())
        return paths

    def _delete_local(self):
        paths = self._selected_local_paths()
        if not paths:
            self._status_cb("Select a local file or folder")
            return
        message = "\n".join(str(path) for path in paths[:10])
        if len(paths) > 10:
            message += f"\n... {len(paths) - 10} more"
        if QMessageBox.question(
            self,
            "Delete Local Path",
            f"Delete local path(s)?\n{message}",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        def _run(_ctx: WorkerContext):
            for path in paths:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            return len(paths)

        def _on_done(count: int) -> None:
            self._status_cb(f"Deleted {count} local item(s)")
            self._refresh_local()

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=_on_done,
            on_error=lambda error: self._error_cb("Delete Local Error", error),
        )

    def _selected_remote_entries(self) -> tuple[list[str], list[str]]:
        files: list[str] = []
        dirs: list[str] = []
        rows = sorted({idx.row() for idx in self.remote_table.selectedIndexes()})
        if not rows and self.remote_table.currentRow() >= 0:
            rows = [self.remote_table.currentRow()]
        for row in rows:
            kind_item = self.remote_table.item(row, 4)
            name_item = self.remote_table.item(row, 0)
            path_item = self.remote_table.item(row, 5)
            if not kind_item or not path_item or (name_item and name_item.text() == ".."):
                continue
            if kind_item.text() == "dir":
                dirs.append(path_item.text())
            else:
                files.append(path_item.text())
        return files, dirs

    def _selected_row_count(self, table: QTableWidget) -> int:
        rows = {idx.row() for idx in table.selectedIndexes()}
        return len(rows)

    def _update_selection_summary(self):
        if hasattr(self, "selection_label"):
            self.selection_label.setText(format_selection_summary(
                self._selected_row_count(self.local_table),
                self._selected_row_count(self.remote_table),
                self._language,
            ))
        if hasattr(self, "submit_btn"):
            n_local = self._selected_row_count(self.local_table)
            n_remote = self._selected_row_count(self.remote_table)
            self.submit_btn.setEnabled((n_local + n_remote) > 0)

    def _on_submit_clicked(self) -> None:
        """Open :class:`SubmitDialog` with the currently selected sources.

        Prefer remote selections when the user is connected (skips the
        upload step entirely); fall back to local selections.
        """
        local_paths = self._selected_paths_for_side("local")
        remote_paths = self._selected_paths_for_side("remote")
        if remote_paths:
            sources = self._build_input_sources(remote_paths, side="remote")
        elif local_paths:
            sources = self._build_input_sources(local_paths, side="local")
        else:
            return
        self.submit_requested_with_files.emit(list(sources))

    def _connect_selection_signals(self):
        self.local_table.itemSelectionChanged.connect(self._on_local_selection_changed)
        self.remote_table.itemSelectionChanged.connect(self._on_remote_selection_changed)

    def _on_local_selection_changed(self):
        if self._selected_row_count(self.local_table):
            self._last_file_selection_side = "local"
        self._update_selection_summary()

    def _on_remote_selection_changed(self):
        if self._selected_row_count(self.remote_table):
            self._last_file_selection_side = "remote"
        self._update_selection_summary()

    def _local_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction(tr("Upload ->", self._language), self._upload_selected)
        menu.addAction(tr("Refresh", self._language), self._refresh_local_async)
        menu.addSeparator()
        menu.addAction(tr("New Folder", self._language), self._mkdir_local)
        menu.addAction(tr("New File", self._language), self._new_file_local)
        menu.addAction(tr("Rename", self._language), self._rename_local)
        menu.addAction(tr("Delete", self._language), self._delete_local)
        self._maybe_add_use_as_input(menu, side="local")
        self._add_viewer_submenu(menu, local=True)
        menu.exec(self.local_table.viewport().mapToGlobal(pos))

    def _remote_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction(tr("<- Download", self._language), self._download_selected)
        menu.addAction(tr("Refresh", self._language), self._refresh_remote)
        menu.addSeparator()
        menu.addAction(tr("New Folder", self._language), self._mkdir_remote)
        menu.addAction(tr("New File", self._language), self._new_file_remote)
        menu.addAction(tr("Rename", self._language), self._rename_remote)
        menu.addAction(tr("Delete", self._language), self._delete_remote)
        menu.addSeparator()
        menu.addAction(tr("Preview", self._language), self._preview_remote)
        self._maybe_add_use_as_input(menu, side="remote")
        self._add_viewer_submenu(menu, local=False)
        menu.exec(self.remote_table.viewport().mapToGlobal(pos))

    def _add_viewer_submenu(self, menu: QMenu, local: bool):
        from ...core.viewer import list_available_viewers
        viewers = list_available_viewers()
        if not viewers:
            return
        sub = menu.addMenu(tr("Open in Viewer", self._language))
        for name, exe in sorted(viewers.items()):
            if local:
                sub.addAction(name, lambda _exe=exe: self._open_local_in_viewer(_exe))
            else:
                sub.addAction(name, lambda _exe=exe: self._open_remote_in_viewer(_exe))

    # Use as input → Submit (cross-page push)

    def _maybe_add_use_as_input(self, menu: QMenu, *, side: str) -> None:
        """Right-click helper: add "Use as input → Submit" items for eligible selections.

        The Submit page (``SubmitPage.push_sources``) consumes the emitted
        ``InputSource`` list.  We always offer "Use as input → Submit"; the
        "Send to ConfFlow → Submit" entry appears only when every selected
        path is ``.xyz`` so the user can opt into workflow generation.
        """
        paths = self._selected_paths_for_side(side)
        if not paths:
            return
        sources = self._build_input_sources(paths, side=side)
        if not sources:
            return
        kinds = {source.kind for source in sources}
        menu.addSeparator()
        if kinds == {"xyz"}:
            menu.addAction(
                tr("Use as input → Submit", self._language),
                lambda _s=sources: self.use_as_input_received.emit(list(_s)),
            )
            menu.addAction(
                tr("Send to ConfFlow → Submit", self._language),
                lambda _s=sources: self.use_as_input_received.emit(list(_s)),
            )
        elif kinds & {"gjf", "inp"}:
            menu.addAction(
                tr("Use as input → Submit", self._language),
                lambda _s=sources: self.use_as_input_received.emit(list(_s)),
            )
        else:
            # Mixed selection — show both labels; Submit page filters by kind.
            menu.addAction(
                tr("Use as input → Submit", self._language),
                lambda _s=sources: self.use_as_input_received.emit(list(_s)),
            )
            menu.addAction(
                tr("Send to ConfFlow → Submit", self._language),
                lambda _s=sources: self.use_as_input_received.emit(list(_s)),
            )

    def _selected_paths_for_side(self, side: str) -> list[str]:
        """Return currently selected file paths for ``side`` (``"local"`` / ``"remote"``)."""
        if side == "local":
            files, _dirs = self._selected_local_entries()
            return files
        files, _dirs = self._selected_remote_entries()
        return files

    @staticmethod
    def _build_input_sources(paths: list[str], *, side: str) -> list[InputSource]:
        """Wrap ``paths`` as :class:`InputSource` instances.

        ``kind`` is inferred from the file suffix (``.gjf`` → ``"gjf"``,
        ``.inp`` → ``"inp"``, otherwise ``"xyz"``).  Unknown suffixes are
        treated as ``"xyz"`` so the Submit page's kind filter still routes
        them sensibly.
        """
        suffix_map = {".gjf": "gjf", ".inp": "inp"}
        sources: list[InputSource] = []
        for raw in paths:
            p = Path(raw)
            kind = suffix_map.get(p.suffix.lower(), "xyz")
            sources.append(InputSource(path=p, side=side, kind=kind))  # type: ignore[arg-type]
        return sources

    # Open in Viewer

    def _open_local_in_viewer(self, exe: str):
        row = self.local_table.currentRow()
        path_item = self.local_table.item(row, 4) if row >= 0 else None
        if path_item is None:
            return
        from ...core.viewer import open_in_viewer
        open_in_viewer(path_item.text(), custom_path=exe)

    def _open_remote_in_viewer(self, exe: str):
        """Download remote file to temp, open in viewer."""
        row = self.remote_table.currentRow()
        path_item = self.remote_table.item(row, 5) if row >= 0 else None
        if path_item is None or self._service is None:
            return
        remote_path = path_item.text()
        import tempfile
        suffix = Path(remote_path).suffix or ".tmp"
        f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        f.close()
        tmp = Path(f.name)
        service = self._service

        def _download(_ctx: WorkerContext):
            service.download_path(remote_path, str(tmp))
            return tmp

        def _open(path: Path):
            from ...core.viewer import open_in_viewer
            open_in_viewer(path, custom_path=exe)
            self._status_cb(f"Opened in viewer: {Path(remote_path).name}")

        start_context_worker(
            self,
            target=_download,
            registry_attr="_background_workers",
            on_result=_open,
            on_error=lambda error: self._status_cb(f"Download failed: {error.splitlines()[0]}"),
        )

    def _remote_target_for_local(self, local_path: Path) -> str:
        return remote_child_path(self.remote_path.text().strip() or "/", local_path.name)

    def _open_local_item(self, item):
        self._cancel_selected_click_rename()
        row = item.row()
        kind_item = self.local_table.item(row, 3)
        path_item = self.local_table.item(row, 4)
        if not kind_item or not path_item:
            return
        path = Path(path_item.text())
        if kind_item.text() == "dir":
            self.state.current_project_root = path
            self.local_path_btn.setText(str(path))
            self.local_path_btn.setToolTip(str(path))
            self._save_last_local_folder(path)
            self._refresh_local_after_navigation()
            return
        self._open_in_text_editor(path)

    def _open_remote_item(self, item):
        self._cancel_selected_click_rename()
        row = item.row()
        kind_item = self.remote_table.item(row, 4)
        path_item = self.remote_table.item(row, 5)
        if not kind_item or not path_item:
            return
        if kind_item.text() == "dir":
            self.remote_path.setText(path_item.text())
            self._refresh_remote()
            self.remote_table.clearSelection()
            self.remote_table.setCurrentCell(-1, -1)
        else:
            self.remote_table.setCurrentCell(row, 0)
            self._open_remote_file_in_editor(path_item.text())

    def _enter_local(self):
        item = self.local_table.currentItem()
        if item:
            self._open_local_item(item)

    def _enter_remote(self):
        item = self.remote_table.currentItem()
        if item:
            self._open_remote_item(item)

    def _rename_from_key(self, role: str) -> None:
        self._cancel_selected_click_rename()
        self._last_file_selection_side = role
        table = self.local_table if role == "local" else self.remote_table
        if self._selected_row_count(table) != 1:
            self._status_cb("Select exactly one file or folder to rename")
            return
        if role == "local":
            self._rename_local()
        else:
            self._rename_remote()

    def _schedule_selected_click_rename(self, role: str, item) -> None:
        self._last_file_selection_side = role
        name_item = item.tableWidget().item(item.row(), 0)
        if name_item is None or name_item.text() == "..":
            self._cancel_selected_click_rename()
            return
        self._pending_click_rename = (role, item.row())
        self._click_rename_timer.start()

    def _cancel_selected_click_rename(self) -> None:
        self._click_rename_timer.stop()
        self._pending_click_rename = None

    def _trigger_selected_click_rename(self) -> None:
        pending = self._pending_click_rename
        self._pending_click_rename = None
        if pending is None:
            return
        role, row = pending
        table = self.local_table if role == "local" else self.remote_table
        item = table.item(row, 0)
        if (
            item is None
            or item.text() == ".."
            or not item.isSelected()
            or row != table.currentRow()
            or self._selected_row_count(table) != 1
        ):
            return
        table.setCurrentCell(row, 0)
        if role == "local":
            self._rename_local()
        else:
            self._rename_remote()

    def _open_remote_file_in_editor(self, remote_path: str):
        """Download a remote file to a temp directory and open it in the configured editor."""
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        name = Path(remote_path).name
        tmp_file = _remote_edit_temp_path(remote_path, self._connected_server_id)
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        service = self._service
        assert service is not None

        def _download(_ctx: WorkerContext):
            from ...core.file_transfer import OverwritePolicy
            service.download_path(remote_path, str(tmp_file), OverwritePolicy.overwrite)
            return tmp_file

        def _on_done(path):
            if self._open_in_text_editor(path):
                self._register_remote_edit_session(remote_path, Path(path))
                self._status_cb(f"Opened: {name}")

        start_context_worker(
            self,
            target=_download,
            registry_attr="_background_workers",
            on_result=_on_done,
            on_error=lambda error: self._status_cb(f"Download failed: {error.splitlines()[0]}"),
        )
        self._status_cb(f"Downloading {name}...")

    def _open_in_text_editor(self, path: str | Path) -> bool:
        editor = self._gui_settings.text_editor_path or "notepad.exe"
        try:
            subprocess.Popen([editor, str(path)])
        except Exception as exc:
            self._error_cb("Open File Error", str(exc))
            return False
        return True

    def _register_remote_edit_session(self, remote_path: str, local_path: Path) -> None:
        local_path = Path(local_path)
        self._remote_edit_sessions[str(local_path)] = _RemoteEditSession(
            remote_path=remote_path,
            local_path=local_path,
            uploaded_signature=_file_signature(local_path),
        )
        if not self._remote_edit_timer.isActive():
            self._remote_edit_timer.start()

    def _check_remote_edit_sessions(self) -> None:
        if not self._remote_edit_sessions:
            self._remote_edit_timer.stop()
            return
        for key, session in list(self._remote_edit_sessions.items()):
            if not session.local_path.exists():
                self._remote_edit_sessions.pop(key, None)
                continue
            signature = _file_signature(session.local_path)
            if signature == session.uploaded_signature:
                continue
            if signature == session.uploading_signature:
                continue
            self._upload_remote_edit_session(session, signature)
        if not self._remote_edit_sessions:
            self._remote_edit_timer.stop()

    def _upload_remote_edit_session(self, session: _RemoteEditSession, signature: str | None = None) -> None:
        if self._service is None:
            self._error_cb("Upload Remote Edit Error", "Connect to a server first")
            return
        upload_signature = signature or _file_signature(session.local_path)
        session.uploading_signature = upload_signature
        service = self._service
        local_path = session.local_path
        remote_path = session.remote_path
        session_key = str(local_path)

        def _run(_ctx: WorkerContext):
            records = service.upload_path(local_path, remote_path, OverwritePolicy.overwrite)
            _raise_if_upload_failed(records, remote_path)
            return session_key, upload_signature, remote_path

        def _done(result):
            key, completed_signature, completed_remote_path = result
            current = self._remote_edit_sessions.get(key)
            if current is None:
                return
            if current.uploading_signature == completed_signature:
                current.uploaded_signature = completed_signature
                current.uploading_signature = None
            self._status_cb(f"Uploaded remote edit: {completed_remote_path}")
            self._refresh_remote()

        def _error(error: str):
            current = self._remote_edit_sessions.get(session_key)
            if current is not None and current.uploading_signature == upload_signature:
                current.uploading_signature = None
            self._error_cb("Upload Remote Edit Error", error.splitlines()[0])

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=_done,
            on_error=_error,
        )

    def _download_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file or folder")
            return
        local_base = self.state.current_project_root or Path.cwd()
        target = Path(local_base) / Path(remote_path).name
        service = self._service

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))
            rec = service.download_path(
                remote_path, target,
                OverwritePolicy.overwrite,
                progress_callback=_progress,
            )
            return rec if isinstance(rec, list) else [rec]

        self._start_transfer_worker(_run, "Download", self._refresh_local)

    def _upload_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        local_path = self._selected_local_path()
        if local_path is None:
            self._status_cb("Select a local file or folder")
            return
        remote_target = self._remote_target_for_local(local_path)
        service = self._service

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))
            rec = service.upload_path(
                local_path, remote_target,
                OverwritePolicy.overwrite,
                progress_callback=_progress,
            )
            return rec if isinstance(rec, list) else [rec]

        self._start_transfer_worker(_run, "Upload", self._refresh_remote)

    def _start_transfer_worker(self, run_fn_or_worker, label: str, on_done_refresh):
        started_at = time.monotonic()
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setFormat(f"{label}: %p%")
        self.progress_bar.setVisible(True)

        def _on_progress(done, total):
            elapsed = max(time.monotonic() - started_at, 0.001)
            speed = _format_transfer_speed(done / elapsed)
            if total > 0:
                self.progress_bar.setValue(int(done * 100 / total))
                self.progress_bar.setFormat(
                    f"{label}: {done // 1024}K / {total // 1024}K @ {speed}"
                )
            else:
                self.progress_bar.setMaximum(0)  # indeterminate
                self.progress_bar.setFormat(f"{label}: {done // 1024}K @ {speed}")

        def _on_done(records):
            self.progress_bar.setVisible(False)
            self.progress_bar.setMaximum(100)
            if not isinstance(records, list):
                records = [records]
            self._status_cb(format_queue_summary([r.status for r in records], self._language))
            on_done_refresh()

        def _on_error(msg):
            self.progress_bar.setVisible(False)
            self.progress_bar.setMaximum(100)
            self._error_cb(f"{label} Error", msg)

        if hasattr(run_fn_or_worker, "start"):
            start_tracked_worker(
                self,
                run_fn_or_worker,
                registry_attr="_background_workers",
                on_progress=_on_progress,
                on_result=_on_done,
                on_error=_on_error,
            )
        else:
            start_context_worker(
                self,
                target=run_fn_or_worker,
                registry_attr="_background_workers",
                on_progress=_on_progress,
                on_result=_on_done,
                on_error=_on_error,
            )
        self._status_cb(f"{label} started")

    def _upload_dropped_local_paths(self, paths: list[str]):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        service = self._service
        remote_dir = self.remote_path.text().strip() or "/"

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            records = []
            for path_text in paths:
                local_path = Path(path_text)
                if not local_path.exists():
                    continue
                target = remote_child_path(remote_dir, local_path.name)
                result = service.upload_path(
                    local_path,
                    target,
                    OverwritePolicy.skip_same_size,
                    progress_callback=_progress,
                )
                records.extend(result if isinstance(result, list) else [result])
            return records

        self._start_transfer_worker(_run, "Upload", self._refresh_remote)

    def _download_dropped_remote_paths(self, paths: list[str]):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        service = self._service
        local_base = self.state.current_project_root or Path.cwd()

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            records = []
            for remote_path in paths:
                result = service.download_path(
                    remote_path,
                    Path(local_base) / Path(remote_path).name,
                    OverwritePolicy.overwrite,
                    progress_callback=_progress,
                )
                records.extend(result if isinstance(result, list) else [result])
            return records

        self._start_transfer_worker(_run, "Download", self._refresh_local)

    def _copy_dropped_local_paths(self, paths: list[str]):
        local_base = Path(self.state.current_project_root or Path.cwd())
        copied: list[Path] = []
        failures: list[str] = []
        for path_text in paths:
            source = Path(path_text)
            if not source.exists():
                failures.append(f"Source path does not exist: {source}")
                continue
            destination = local_base / source.name
            try:
                if source.resolve() == destination.resolve():
                    failures.append(f"Source is already in this directory: {source.name}")
                    continue
                if destination.exists():
                    failures.append(f"Destination already exists: {destination.name}")
                    continue
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
                copied.append(destination)
            except Exception as exc:
                failures.append(f"{source.name}: {exc}")
        if copied:
            self._refresh_local()
            self._status_cb(f"Copied {len(copied)} local path(s)")
        if failures:
            self._error_cb("Drop Copy Error", "\n".join(failures))

    def _move_local_paths_into_directory(self, paths: list[str], target_dir_text: str):
        target_dir = Path(target_dir_text)
        moved: list[Path] = []
        failures: list[str] = []
        if not target_dir.is_dir():
            self._error_cb("Move Error", f"Target directory does not exist: {target_dir}")
            return
        target_resolved = target_dir.resolve()
        for path_text in paths:
            source = Path(path_text)
            if not source.exists():
                failures.append(f"Source path does not exist: {source}")
                continue
            destination = target_dir / source.name
            source_resolved = source.resolve()
            try:
                if source_resolved == destination.resolve():
                    failures.append(f"Source is already in this directory: {source.name}")
                    continue
                if source.is_dir() and (
                    target_resolved == source_resolved or source_resolved in target_resolved.parents
                ):
                    failures.append(f"Cannot move directory into itself: {source.name}")
                    continue
                if destination.exists():
                    failures.append(f"Destination already exists: {destination.name}")
                    continue
                shutil.move(str(source), str(destination))
                moved.append(destination)
            except Exception as exc:
                failures.append(f"{source.name}: {exc}")
        if moved:
            self._refresh_local()
            self._status_cb(f"Moved {len(moved)} local path(s)")
        if failures:
            self._error_cb("Move Error", "\n".join(failures))

    def _move_remote_paths_into_directory(self, paths: list[str], target_dir_text: str):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        target_dir = normalize_remote_path(target_dir_text)
        moved = 0
        failures: list[str] = []
        for path_text in paths:
            source = normalize_remote_path(path_text)
            destination = remote_child_path(target_dir, posixpath.basename(source))
            if destination == source:
                failures.append(f"Source is already in this directory: {posixpath.basename(source)}")
                continue
            if target_dir == source or target_dir.startswith(source.rstrip("/") + "/"):
                failures.append(f"Cannot move directory into itself: {posixpath.basename(source)}")
                continue
            try:
                self._service.rename_remote(source, destination)
                moved += 1
            except Exception as exc:
                failures.append(f"{posixpath.basename(source)}: {exc}")
        if moved:
            self._refresh_remote()
            self._status_cb(f"Moved {moved} remote path(s)")
        if failures:
            self._error_cb("Move Error", "\n".join(failures))

    def _mkdir_local(self):
        name, ok = self._prompt_new_folder_name(
            tr("New Folder", self._language),
            tr("Folder name:", self._language),
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if "/" in name or "\\" in name or name in (".", ".."):
            self._error_cb("Invalid Name", "Name cannot contain path separators or '..'")
            return
        base = self.state.current_project_root or Path.cwd()
        new_dir = Path(base) / name
        try:
            new_dir.mkdir(parents=True, exist_ok=False)
            self._refresh_local()
        except Exception as exc:
            self._error_cb("Mkdir Error", str(exc))

    def _new_file_local(self):
        name, ok = QInputDialog.getText(self, tr("New File", self._language), tr("File name:", self._language))
        if not ok or not name.strip():
            return
        name = name.strip()
        if "/" in name or "\\" in name or name in (".", ".."):
            self._error_cb("Invalid Name", "Name cannot contain path separators or '..'")
            return
        base = self.state.current_project_root or Path.cwd()
        new_file = Path(base) / name
        try:
            new_file.touch(exist_ok=False)
            self._refresh_local()
            self._open_in_text_editor(new_file)
        except Exception as exc:
            self._error_cb("New File Error", str(exc))

    def _new_file_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        name, ok = QInputDialog.getText(self, tr("New File", self._language), tr("File name:", self._language))
        if not ok or not name.strip():
            return
        base = self.remote_path.text().strip().rstrip("/") or "/"
        remote_file = f"{base}/{name.strip()}" if base != "/" else f"/{name.strip()}"
        import tempfile
        f = tempfile.NamedTemporaryFile(suffix=Path(name).suffix or ".tmp", delete=False)
        f.close()
        tmp = Path(f.name)
        try:
            tmp.write_bytes(b"")
            self._service.upload_path(tmp, remote_file)
            self._refresh_remote()
        except Exception as exc:
            self._error_cb("New File Error", str(exc))
        finally:
            tmp.unlink(missing_ok=True)

    def _mkdir_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        name, ok = self._prompt_new_folder_name("New Remote Folder", "Folder name:")
        if not ok or not name.strip():
            return
        base = self.remote_path.text().strip().rstrip("/") or "/"
        remote_dir = f"{base}/{name.strip()}" if base != "/" else f"/{name.strip()}"
        try:
            self._service.mkdir_remote(remote_dir)
            self._refresh_remote()
        except Exception as exc:
            self._error_cb("Mkdir Error", str(exc))

    def _preview_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file")
            return
        service = self._service

        def _run(_ctx: WorkerContext):
            return service.preview_remote_text(remote_path)

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=lambda text: QMessageBox.information(self, remote_path, text[:4000]),
            on_error=lambda error: self._error_cb("Preview Error", error),
        )

    def _rename_name(self, name: str) -> str | None:
        name = name.strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            self._error_cb("Invalid Name", "Name cannot contain path separators, '.' or '..'")
            return None
        return name

    def _build_name_input_dialog(self, title: str, label: str, text: str) -> QInputDialog:
        dialog = QInputDialog(self)
        dialog.setInputMode(QInputDialog.TextInput)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextValue(text)
        dialog.setMinimumWidth(RENAME_DIALOG_MIN_WIDTH)
        input_field = dialog.findChild(QLineEdit)
        if input_field is not None:
            input_field.setMinimumWidth(RENAME_DIALOG_INPUT_MIN_WIDTH)
            input_field.selectAll()
        dialog.resize(RENAME_DIALOG_MIN_WIDTH, dialog.sizeHint().height())
        return dialog

    def _build_rename_dialog(self, title: str, label: str, text: str) -> QInputDialog:
        return self._build_name_input_dialog(title, label, text)

    def _prompt_rename_name(self, title: str, label: str, text: str) -> tuple[str, bool]:
        dialog = self._build_rename_dialog(title, label, text)
        ok = dialog.exec() == QDialog.Accepted
        return dialog.textValue(), ok

    def _prompt_new_folder_name(self, title: str, label: str) -> tuple[str, bool]:
        dialog = self._build_name_input_dialog(title, label, "")
        ok = dialog.exec() == QDialog.Accepted
        return dialog.textValue(), ok

    def _rename_local(self):
        local_path = self._selected_local_path()
        if local_path is None:
            self._status_cb("Select a local file or folder")
            return
        new_name, ok = self._prompt_rename_name("Rename Local Path", "New name:", local_path.name)
        if not ok:
            return
        new_name = self._rename_name(new_name)
        if new_name is None:
            return
        new_path = local_path.with_name(new_name)
        if new_path == local_path:
            return
        if new_path.exists():
            self._error_cb("Rename Error", f"Destination already exists: {new_name}")
            return
        try:
            local_path.rename(new_path)
            self._refresh_local()
        except Exception as exc:
            self._error_cb("Rename Error", str(exc))

    def _rename_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file or folder")
            return
        new_name, ok = self._prompt_rename_name("Rename Remote Path", "New name:", Path(remote_path).name)
        if not ok:
            return
        new_name = self._rename_name(new_name)
        if new_name is None:
            return
        parent = remote_path.rsplit("/", 1)[0] or "/"
        new_path = f"{parent}/{new_name}" if parent != "/" else f"/{new_name}"
        try:
            self._service.rename_remote(remote_path, new_path)
            self._refresh_remote()
        except Exception as exc:
            self._error_cb("Rename Error", str(exc))

    def _delete_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_paths = self._selected_remote_paths()
        if not remote_paths:
            self._status_cb("Select a remote file or folder")
            return
        current_dir = (self.remote_path.text().strip() or "/").rstrip("/") or "/"
        # Reject deletion when browsing a dangerous top-level directory
        _dangerous_tops = {"/", "/root", "/home"}
        if current_dir in _dangerous_tops:
            self._error_cb("Delete Error", f"Cannot delete items at top-level directory: {current_dir}")
            return
        # Filter out parent entries and paths outside current dir
        valid_paths = []
        for p in remote_paths:
            if p == current_dir or not p.startswith(current_dir + "/"):
                continue
            valid_paths.append(p)
        if not valid_paths:
            self._error_cb("Delete Error", "Selected path(s) cannot be deleted from this location")
            return
        message = "\n".join(valid_paths[:10])
        if len(valid_paths) > 10:
            message += f"\n... {len(valid_paths) - 10} more"
        if QMessageBox.question(
            self,
            "Delete Remote Path",
            f"Delete remote path(s)?\n{message}",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        service = self._service

        def _run(_ctx: WorkerContext):
            for remote_path in valid_paths:
                service.delete_remote(
                    remote_path,
                    recursive=True,
                    extra_allowed_roots=[current_dir],
                )
            return len(valid_paths)

        def _on_done(count: int) -> None:
            self._status_cb(f"Deleted {count} remote item(s)")
            self._refresh_remote()

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=_on_done,
            on_error=lambda error: self._error_cb("Delete Error", error),
        )

    def _selected_local_entries(self) -> tuple[list[str], list[str]]:
        """Return (files, dirs) of selected local paths."""
        files: list[str] = []
        dirs: list[str] = []
        rows = sorted({idx.row() for idx in self.local_table.selectedIndexes()})
        if not rows and self.local_table.currentRow() >= 0:
            rows = [self.local_table.currentRow()]
        for row in rows:
            kind_item = self.local_table.item(row, 3)
            name_item = self.local_table.item(row, 0)
            path_item = self.local_table.item(row, 4)
            if not kind_item or not path_item or (name_item and name_item.text() == ".."):
                continue
            if kind_item.text() == "dir":
                dirs.append(path_item.text())
            else:
                files.append(path_item.text())
        return files, dirs

    def _on_runs_done(self, results):
        for result in results:
            self._log(f"Run submitted: {result.batch_id}, tasks={result.submitted_task_count}, errors={len(result.errors)}")
            for error in result.errors:
                self._log(f"  {error}")
        self._status_cb(f"Submitted {len(results)} run(s)")
        self.runs_submitted.emit([result.batch_id for result in results if not result.errors])

    def _allow_width_shrink(self):
        for widget in (
            self.local_path_btn,
            self.connection_label,
            self.remote_path,
        ):
            policy = widget.sizePolicy()
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Ignored, policy.verticalPolicy())
        for widget in (
            self.server_combo,
        ):
            policy = widget.sizePolicy()
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Preferred, policy.verticalPolicy())
            policy = widget.sizePolicy()
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Preferred, policy.verticalPolicy())

    def _normalize_control_heights(self, *widgets):
        for widget in widgets:
            widget.setMinimumHeight(CONTROL_HEIGHT)
            widget.setMaximumHeight(CONTROL_HEIGHT)
            widget.setFixedHeight(CONTROL_HEIGHT)
            widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(), QSizePolicy.Fixed)

    def _normalize_all_control_heights(self):
        self._normalize_control_heights(
            self.local_path_btn,
            self.server_combo,
            self.remote_path,
        )

    def shutdown(self):
        self._shutting_down = True
        # Ignore results from remote-list workers that finish during teardown.
        self._remote_list_request_id += 1
        self._local_refresh_request_id += 1
        if hasattr(self, "_local_poll_timer"):
            self._local_poll_timer.stop()
        if hasattr(self, "_remote_edit_timer"):
            self._remote_edit_timer.stop()
        dirty_remote_edits = self._dirty_remote_edit_sessions()
        if dirty_remote_edits:
            details = "\n".join(
                f"{session.local_path} -> {session.remote_path}"
                for session in dirty_remote_edits[:10]
            )
            if len(dirty_remote_edits) > 10:
                details += f"\n... {len(dirty_remote_edits) - 10} more"
            self._error_cb(
                "Unsaved Remote Edits",
                "Remote edit temporary files have changes that were not uploaded:\n" + details,
            )
        try:
            self._remember_current_remote_dir()
            store = GuiSettingsStore()
            current = store.load()
            new_server_id = self._connected_server_id or self.server_combo.currentData() or ""
            new_remote_dirs = {**dict(current.last_remote_dirs or {}), **self._server_remote_dirs}
            store.update(last_server_id=new_server_id, last_remote_dirs=new_remote_dirs)
        except OSError:
            pass
        finally:
            workers = list(self._background_workers)
            worker = getattr(self, "worker", None)
            if worker is not None and worker not in workers:
                workers.append(worker)
            for worker in workers:
                if hasattr(worker, "stop_safely"):
                    worker.stop_safely(3000)
                elif hasattr(worker, "isRunning") and worker.isRunning():
                    worker.quit()
                    worker.wait(3000)
            if self._service is not None:
                self._service.close()
                self._service = None

    def _keep_worker(self, worker):
        self._background_workers.append(worker)
        worker.finished.connect(lambda: self._background_workers.remove(worker) if worker in self._background_workers else None)
        if hasattr(worker, "deleteLater"):
            worker.finished.connect(worker.deleteLater)

    def _dirty_remote_edit_sessions(self) -> list[_RemoteEditSession]:
        dirty = []
        for session in self._remote_edit_sessions.values():
            if session.local_path.exists() and _file_signature(session.local_path) != session.uploaded_signature:
                dirty.append(session)
        return dirty


def _remote_list_error_allows_fallback(error: str) -> bool:
    first_line = (error.splitlines()[0] if error else "").lower()
    return (
        "filenotfounderror" in first_line
        or "errno 2" in first_line
        or "errno 20" in first_line
        or "no such file" in first_line
        or "no such directory" in first_line
        or "not a directory" in first_line
    )


def _file_signature(path: Path) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError:
        return "missing"
    return hashlib.sha256(data).hexdigest()


def _remote_edit_temp_path(remote_path: str, server_id: str | None) -> Path:
    name = Path(remote_path).name or "remote-file"
    key = f"{server_id or ''}\0{remote_path}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return Path(tempfile.gettempdir()) / "jobdesk_remote_edit" / digest / name


def _raise_if_upload_failed(records, remote_path: str) -> None:
    items = records if isinstance(records, list) else [records]
    for item in items:
        if getattr(item, "status", None) == TransferStatus.failed:
            reason = getattr(item, "reason", "") or "upload failed"
            raise RuntimeError(f"upload failed for {remote_path}: {reason}")


def _submit_result_errors(results) -> list[str]:
    errors: list[str] = []
    for result in results or []:
        batch_id = getattr(result, "batch_id", "run")
        for error in getattr(result, "errors", []) or []:
            errors.append(f"{batch_id}: {error}")
    return errors
