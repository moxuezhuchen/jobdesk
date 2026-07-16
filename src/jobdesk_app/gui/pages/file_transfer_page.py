from __future__ import annotations

import subprocess
import tempfile
import time
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

from ...config.servers import (
    load_servers,  # noqa: F401  re-exported for tests that monkeypatch the symbol on this module
)
from ...services.external_terminal import build_terminal_launch, launch_terminal
from ...services.file_transfer_service import FileTransferService
from ...services.gui_settings import GuiSettingsStore
from ...services.run_service import RunService
from ..button_feedback import ButtonFeedback, ButtonRole, apply_button_role
from ..design.components import StatusChip
from ..design.tokens import Colors, Metrics, Radius
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client
from ..widgets import EmptyStateHint
from ..worker_utils import WorkerContext, start_context_worker, start_tracked_worker
from ..workers import BackgroundWorker
from .file_transfer_config import ConfigUnreadable, load_existing_servers_data
from .file_transfer_config import _load_existing_servers_data as _load_existing_servers_data
from .file_transfer_connections import ConnectionsCoordinator
from .file_transfer_helpers import (
    _remote_list_error_allows_fallback,
    build_input_sources,
    collect_remote_delete_roots,
    connection_status_text,
    default_remote_dir_for_server,
    file_table_headers,
    format_modified_time,
    format_remote_size,
    format_selection_summary,
    normalize_remote_path,
    remote_child_path,
    remote_parent_row,
    remote_table_row,
)
from .file_transfer_local_navigator import LocalNavigator
from .file_transfer_operations import FileOperations
from .file_transfer_remote_edit import RemoteEditSessionManager
from .file_transfer_runner import TransferRunner
from .file_transfer_tables import _RemoteEditSession
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
        self._connections = ConnectionsCoordinator(
            status_cb=status_cb,
            log_cb=log_cb,
            create_ssh=create_ssh_client,
            create_sftp=create_sftp_client,
            run_tasks_provider=self._current_run_tasks,
        )
        self._gui_settings = GuiSettingsStore().load()
        self._language = self._gui_settings.language
        self._remote_list_request_id = 0
        self._remote_list_fallbacks: list[str] = []
        self._server_remote_dirs: dict[str, str] = {}
        self._background_workers = []
        self._shutting_down = False
        self._local_refresh_request_id = 0
        self._local_poll_snapshot: dict[str, float] = {}
        self._local_navigator = LocalNavigator(
            root_provider=lambda: self.state.current_project_root,
            hide_dot_provider=lambda: self._gui_settings.hide_dotfiles,
            log_provider=lambda: self._status_cb,
            on_rows_loaded=self._load_local_rows,
            worker_registry_attr="_background_workers",
        )
        self._local_navigator.set_root_provider(self._apply_local_root)
        self._remote_edit_manager = RemoteEditSessionManager(
            service_provider=lambda: self._service,
            settings_provider=lambda: self._gui_settings,
            server_id_provider=lambda: self._connected_server_id,
            on_status=lambda message: self._status_cb(message),
            on_error=lambda title, message: self._error_cb(title, message),
            on_refresh_remote=lambda: self._refresh_remote(),
            start_worker=lambda owner, **kwargs: start_context_worker(owner, **kwargs),
            process_launcher=lambda args: subprocess.Popen(args),
        )
        self._initialized = False
        self._remote_edit_sessions: dict[str, _RemoteEditSession] = {}
        self._pending_click_rename: tuple[str, int] | None = None
        self._last_file_selection_side: str | None = None
        layout = QVBoxLayout(self)
        # Phase 18 visual cleanup: standardise the page padding with the
        # other three pages so the chrome matches. The previous (10, 10,
        # 10, 10) left the page content butting up against the sidebar.
        layout.setContentsMargins(
            Metrics.PAGE_PADDING,
            Metrics.PAGE_PADDING - 4,
            Metrics.PAGE_PADDING,
            Metrics.PAGE_PADDING - 4,
        )
        layout.setSpacing(12)

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

        self._local_navigator.apply_default_local_folder(self._gui_settings)
        self.local_path_btn = QPushButton(str(self.state.current_project_root or Path.cwd()))
        self.local_path_btn.setToolTip(self.local_path_btn.text())
        self.local_path_btn.setStyleSheet("text-align: left; padding: 0 8px;")
        self.local_path_btn.clicked.connect(self._choose_local_folder)
        self.server_combo = QComboBox()
        self.server_combo.setMinimumWidth(120)
        self.server_combo.setMaximumWidth(200)
        self.server_label = QLabel(tr("Server:", self._language))
        self.server_combo.currentIndexChanged.connect(self._auto_connect_selected_server)
        self.connection_label = StatusChip(
            connection_status_text(None, False, language=self._language),
            state="neutral",
        )
        self.connection_label.setMinimumWidth(80)
        self.connection_label.setMaximumWidth(220)
        # Keep the connection state visible before a server is selected;
        # the neutral chip makes the current state discoverable without
        # relying on a transient status-bar message.
        self.connection_label.setVisible(True)
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
        self.local_table.copy_local_files.connect(lambda paths: self._file_operations.copy_dropped_local_paths(paths))
        self.local_table.move_local_files.connect(lambda paths, target: self._file_operations.move_local_paths_into_directory(paths, target))
        self.remote_table.drop_files.connect(self._upload_dropped_local_paths)
        self.remote_table.move_remote_files.connect(lambda paths, target: self._file_operations.move_remote_paths_into_directory(paths, target))
        self.local_table.selected_item_clicked.connect(
            lambda item: self._schedule_selected_click_rename("local", item)
        )
        self.remote_table.selected_item_clicked.connect(
            lambda item: self._schedule_selected_click_rename("remote", item)
        )
        _setup_table(self.local_table, [tr(h, self._language) for h in file_table_headers("local")] + ["type", "path"], hidden_columns=[3, 4])
        _setup_table(self.remote_table, [tr(h, self._language) for h in file_table_headers("remote")] + ["type", "path"], hidden_columns=[4, 5])
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
            f"#LocalHeader {{ background: {Colors.CARD_BG}; border: 1px solid {Colors.BORDER}; "
            f"border-radius: {Radius.MD}px; border-top-right-radius: 0; border-bottom-right-radius: 0; }}"
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
            f"#RemoteHeader {{ background: {Colors.CARD_BG}; border: 1px solid {Colors.BORDER}; "
            f"border-radius: {Radius.MD}px; border-top-left-radius: 0; border-bottom-left-radius: 0; }} "
            f" #RemoteHeader QLineEdit, #RemoteHeader QComboBox {{"
            f" background: {Colors.BG_SURFACE}; border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; "
            f"padding: 0 10px; min-height: 36px; max-height: 36px; }} "
            f" #RemoteHeader QLabel {{ background: transparent; }}"
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
        self._transfer_runner = TransferRunner(
            owner=self,
            progress_bar=self.progress_bar,
            service_provider=lambda: self._service,
            language_provider=lambda: self._language,
            worker_registry=self._background_workers,
            on_status=lambda message: self._status_cb(message),
            on_error=lambda title, message: self._error_cb(title, message),
            on_refresh_local=lambda: self._refresh_local(),
            on_refresh_remote=lambda: self._refresh_remote(),
            run_transfer=lambda run_fn, label, refresh: self._start_transfer_worker(
                run_fn, label, refresh
            ),
            start_context=lambda owner, **kwargs: start_context_worker(owner, **kwargs),
            start_tracked=lambda owner, worker, **kwargs: start_tracked_worker(
                owner, worker, **kwargs
            ),
            clock=lambda: time.monotonic(),
            show_preview=lambda parent, title, text: QMessageBox.information(
                parent, title, text
            ),
        )
        self._file_operations = FileOperations(
            service_provider=lambda: self._service,
            local_root_provider=lambda: self.state.current_project_root,
            language_provider=lambda: self._language,
            on_status=lambda message: self._status_cb(message),
            on_error=lambda title, message: self._error_cb(title, message),
            on_refresh_local=lambda: self._refresh_local(),
            on_refresh_remote=lambda: self._refresh_remote(),
            prompt_new_name=lambda title, label, text: self._prompt_rename_name(
                title, label, text
            ),
            prompt_new_folder=lambda title, label: self._prompt_new_folder_name(
                title, label
            ),
            prompt_text=lambda title, label: QInputDialog.getText(self, title, label),
            ask_confirm=lambda title, body: QMessageBox.question(
                self, title, body, QMessageBox.Yes | QMessageBox.No
            )
            == QMessageBox.Yes,
            open_editor=lambda path: self._remote_edit_manager.open_in_text_editor(Path(path)),
            start_worker=lambda target, on_result, on_error: start_context_worker(
                self,
                target=target,
                registry_attr="_background_workers",
                on_result=on_result,
                on_error=on_error,
            ),
            remote_dir_provider=lambda: self.remote_path.text().strip() or "/",
        )
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
        self._local_poll_timer.timeout.connect(lambda: self._local_navigator.check_local_changes(self))
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
        self._apply_local_root(Path(path))
        self._local_navigator.save_last_local_folder(Path(path))
        self._refresh_local()

    def _apply_local_root(self, path: Path) -> None:
        """Mutate ``state.current_project_root`` and the local-path button."""
        self.state.current_project_root = path
        if not hasattr(self, "local_path_btn"):
            return
        self.local_path_btn.setText(str(path))
        self.local_path_btn.setToolTip(str(path))

    def on_activated(self):
        self._gui_settings = GuiSettingsStore().load()
        self.apply_language(self._gui_settings.language)

        first_run = not self._initialized
        if first_run:
            self._initialized = True
            self._local_navigator.apply_default_local_folder(self._gui_settings)
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
        self.local_table.setHorizontalHeaderLabels([tr(h, self._language) for h in file_table_headers("local")] + ["type", "path"])
        self.remote_table.setHorizontalHeaderLabels([tr(h, self._language) for h in file_table_headers("remote")] + ["type", "path"])
        self.connection_label.setText(connection_status_text(
            self._connected_server_id,
            self._service is not None,
            language=language,
        ))
        self.connection_label.set_state("success" if self._service is not None else "neutral")
        # -- Phase 2.1: retranslate empty-state hints --
        self._no_server_hint.apply_language(language)
        self._empty_dir_hint.apply_language(language)

    def _load_servers(self):
        """Reload servers (used by tab switches after first init)."""
        self.server_combo.blockSignals(True)
        self._load_servers_inner()
        self.server_combo.blockSignals(False)

    def _load_servers_inner(self):
        """Populate server_combo. Caller must handle signal blocking."""
        servers = self._connections.load_servers()
        self._servers = servers
        current = self.server_combo.currentData()
        self.server_combo.clear()
        for sid in sorted(servers):
            self.server_combo.addItem(sid, sid)
        if self._gui_settings.default_server_id:
            idx = self.server_combo.findData(self._gui_settings.default_server_id)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)
        if current:
            idx = self.server_combo.findData(current)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)

    def _auto_connect_selected_server(self):
        if not self._gui_settings.auto_connect:
            self._set_connection_status(tr("Auto connect disabled", self._language), state="warning")
            return
        server_id = self.server_combo.currentData()
        if not server_id:
            self._set_connection_status(
                connection_status_text(None, False, language=self._language),
                state="neutral",
            )
            return
        if self._connected_server_id == server_id and self._service is not None:
            self._set_connection_status(
                connection_status_text(server_id, True, language=self._language),
                state="success",
            )
            return
        self._remember_current_remote_dir()
        server = self._servers.get(server_id)
        if server is not None:
            self.remote_path.setText(self._server_remote_dirs.get(server_id) or default_remote_dir_for_server(server))
        self._connect()

    def _set_connection_status(self, text: str, *, state: str = "neutral") -> None:
        """Set ``connection_label`` text + chip colour in one call.

        Phase 18 visual cleanup: the previous version used a plain
        ``QLabel`` so connection state had to be read from the verb
        ("Connected to jobdesk-centos" vs "Disconnected"). The chip
        now carries an explicit visual state (success / warning /
        error / neutral) so the page reads at a glance.
        """
        if not hasattr(self, "connection_label"):
            return
        self.connection_label.set_state(state)
        self.connection_label.setText(text)

    def _remember_current_remote_dir(self):
        if self._connected_server_id:
            self._server_remote_dirs[self._connected_server_id] = normalize_remote_path(
                self.remote_path.text().strip() or "/"
            )

    def _connect(self):
        server_id = self.server_combo.currentData()
        if not server_id:
            self._status_cb("Select a server first")
            self._set_connection_status(
                connection_status_text(None, False, language=self._language),
                state="neutral",
            )
            return
        server = self._servers[server_id]
        if self._connected_server_id != server_id:
            self.remote_path.setText(self._server_remote_dirs.get(server_id) or default_remote_dir_for_server(server))

        if self._service is not None:
            self._close_service_async(self._service)
        service = FileTransferService(
            self._build_service_factory(server),
            allowed_delete_roots=collect_remote_delete_roots(self._current_run_tasks()),
            persistent_session=True,
        )
        self._service = service
        self._connections.set_server(server_id, server, service)
        self._connected_server_id = server_id
        self._connected_server = server
        self._set_connection_status(
            connection_status_text(server_id, True, language=self._language),
            state="success",
        )
        self._refresh_remote()
        # Phase 2.1: refresh empty-state hints now that the connection
        # state flipped from "none" to "connected".
        self._update_empty_state_visibility()

    def _build_service_factory(self, server):
        """Build a FileTransferService factory that opens (ssh, sftp) for ``server``.

        Factored out so ``_connect`` can pass it through the coordinator's
        runner path without leaking the page's create_* helpers.
        """
        def factory():
            ssh = create_ssh_client(server)
            ssh.connect()
            sftp = create_sftp_client(ssh)
            return _ConnectedSFTP(ssh, sftp)
        return factory

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
        self._transfer_runner.keep_worker(worker)
        worker.start()

    def _refresh_local(self):
        # Use the navigator's pure ``scan`` helper so test fixtures that
        # patch ``file_page._status_cb`` after construction keep working.
        snapshot, rows, error = self._local_navigator.scan()
        if error:
            self._status_cb(error)
        self._local_poll_snapshot = snapshot
        self._local_navigator._snapshot = snapshot
        self._load_local_rows(rows)

    def _load_local_rows(self, rows: list[list[str]]) -> None:
        _load_rows(self.local_table, rows)
        # Mirror the navigator's snapshot so test fixtures that read
        # ``file_page._local_poll_snapshot`` stay in sync.
        self._local_poll_snapshot = self._local_navigator.last_poll_snapshot
        self._update_selection_summary()

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
        data = load_existing_servers_data(path)
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
        self._refresh_local()
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
        self._transfer_runner.keep_worker(self.remote_worker)
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
        self._set_connection_status(
            connection_status_text(self._connected_server_id, True, language=self._language),
            state="success",
        )
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
        self._set_connection_status(
            connection_status_text(self._connected_server_id, False, error.splitlines()[0], self._language),
            state="error",
        )
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
        self._file_operations.delete_local(paths)

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
            sources = build_input_sources(remote_paths, side="remote")
        elif local_paths:
            sources = build_input_sources(local_paths, side="local")
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
        menu.addAction(tr("Refresh", self._language), self._refresh_local)
        menu.addSeparator()
        menu.addAction(tr("New Folder", self._language), self._file_operations.mkdir_local)
        menu.addAction(tr("New File", self._language), self._file_operations.new_file_local)
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
        menu.addAction(tr("New Folder", self._language), self._file_operations.mkdir_remote)
        menu.addAction(tr("New File", self._language), self._file_operations.new_file_remote)
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
        sources = build_input_sources(paths, side=side)
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
            self._local_navigator.save_last_local_folder(path)
            self._refresh_local_after_navigation()
            return
        self._remote_edit_manager.open_in_text_editor(Path(path))

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
        self._remote_edit_manager.open_remote_file(
            self,
            remote_path,
            on_opened=lambda path: self._register_remote_edit_session(remote_path, path),
            open_in_editor=lambda path: self._remote_edit_manager.open_in_text_editor(Path(path)),
        )

    def _register_remote_edit_session(self, remote_path: str, local_path: Path) -> None:
        self._remote_edit_manager.register_session(remote_path, local_path)
        # Mirror the manager's session dict into the page attribute so
        # test fixtures that read ``file_page._remote_edit_sessions`` keep
        # working.
        self._remote_edit_sessions = self._remote_edit_manager._sessions
        if hasattr(self, "_remote_edit_timer") and not self._remote_edit_timer.isActive():
            self._remote_edit_timer.start()

    def _check_remote_edit_sessions(self) -> None:
        self._remote_edit_manager.tick(self)
        self._remote_edit_sessions = self._remote_edit_manager._sessions
        if not self._remote_edit_sessions and hasattr(self, "_remote_edit_timer"):
            self._remote_edit_timer.stop()

    def _download_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file or folder")
            return
        local_base = self.state.current_project_root or Path.cwd()
        self._transfer_runner.download_selected(remote_path, Path(local_base))

    def _upload_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        local_path = self._selected_local_path()
        if local_path is None:
            self._status_cb("Select a local file or folder")
            return
        remote_target = remote_child_path(self.remote_path.text().strip() or "/", local_path.name)
        self._transfer_runner.upload_selected(local_path, remote_target)

    def _start_transfer_worker(self, run_fn_or_worker, label: str, on_done_refresh):
        self._transfer_runner.start_worker(run_fn_or_worker, label, on_done_refresh)

    def _upload_dropped_local_paths(self, paths: list[str]):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_dir = self.remote_path.text().strip() or "/"
        self._transfer_runner.upload_dropped_local_paths(
            paths,
            remote_dir,
            self._refresh_remote,
        )

    def _download_dropped_remote_paths(self, paths: list[str]):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        local_base = self.state.current_project_root or Path.cwd()
        self._transfer_runner.download_dropped_remote_paths(
            paths,
            Path(local_base),
            self._refresh_local,
        )

    def _preview_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file")
            return
        self._transfer_runner.preview_remote(remote_path, self)

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

    def _prompt_rename_name(self, title: str, label: str, text: str) -> tuple[str, bool]:
        dialog = self._build_name_input_dialog(title, label, text)
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
        self._file_operations.rename_local(local_path)

    def _rename_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file or folder")
            return
        self._file_operations.rename_remote(remote_path)

    def _delete_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_paths = self._selected_remote_paths()
        if not remote_paths:
            self._status_cb("Select a remote file or folder")
            return
        current_dir = self.remote_path.text().strip() or "/"
        self._file_operations.delete_remote(remote_paths, current_dir)

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
                self._connections._service = self._service
                self._connections._connected_server_id = None
                self._connections._connected_server = None
                self._connections.teardown()
                self._service = None

    def _dirty_remote_edit_sessions(self) -> list[_RemoteEditSession]:
        # Delegate to RemoteEditSessionManager so the dirty-tracking logic
        # stays in one place. The page still owns the ``_remote_edit_timer``
        # ``QTimer`` that drives this check.
        self._remote_edit_sessions = self._remote_edit_manager._sessions
        return self._remote_edit_manager.dirty_sessions
