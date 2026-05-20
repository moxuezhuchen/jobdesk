from __future__ import annotations

import posixpath
import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QComboBox, QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QInputDialog, QMessageBox, QTextEdit, QSpinBox, QAbstractItemView,
    QFileDialog, QSizePolicy, QGridLayout, QAbstractSpinBox, QMenu,
    QProgressBar,
)
from PySide6.QtCore import Qt, QTimer, QMimeData, QUrl, Signal
from PySide6.QtGui import QDrag

from ...config.servers import load_servers
from ...core.file_transfer import OverwritePolicy
from ...core.manifest import Manifest
from ...core.run import RunMode, RunSource, RunSpec, build_run_plan, chunk_sources
from ...core.transfer import TransferStatus
from ...services.file_transfer_service import FileTransferService
from ...services.gui_settings import GuiSettingsStore
from ...services.run_profiles import RunProfileStore
from ...services.run_service import RunService
from ..i18n import tr
from ..session import create_sftp_client, create_ssh_client


CONTROL_HEIGHT = 44


def format_file_size(size: int | None) -> str:
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_remote_size(size: int | None, is_dir: bool) -> str:
    if is_dir:
        return ""
    return format_file_size(size)


def format_modified_time(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def table_resize_mode_name() -> str:
    return "Interactive"


def format_queue_summary(statuses: list[TransferStatus], language: str = "en") -> str:
    transferred = sum(1 for s in statuses if s == TransferStatus.transferred)
    skipped = sum(1 for s in statuses if s == TransferStatus.skipped)
    failed = sum(1 for s in statuses if s == TransferStatus.failed)
    return tr(
        "Queue {transferred} ok | {skipped} skip | {failed} fail",
        language,
        transferred=transferred,
        skipped=skipped,
        failed=failed,
    )


def build_file_button_reasons(local_selected: bool, remote_selected: bool, connected: bool) -> dict[str, str]:
    return {
        "upload": "" if local_selected else "Select a local file or folder",
        "download": "" if connected and remote_selected else (
            "Connect to a server first" if not connected else "Select a remote file or folder"
        ),
        "preview": "" if connected and remote_selected else (
            "Connect to a server first" if not connected else "Select a remote file"
        ),
    }


def collect_remote_delete_roots(manifest_path: Path | None) -> list[str]:
    if manifest_path is None or not Path(manifest_path).exists():
        return []
    roots: set[str] = set()
    for task in Manifest.read(Path(manifest_path)):
        if task.remote_work_dir and task.batch_id:
            roots.add(f"{task.remote_work_dir.rstrip('/')}/{task.batch_id}")
    return sorted(roots)


def run_button_reason(connected: bool, selected_count: int, command_template: str) -> str:
    if not connected:
        return "Connect to a server first"
    if selected_count <= 0:
        return "Select remote files or directories"
    if not command_template.strip():
        return "Enter a command template"
    return ""


def format_command_preview_rows(
    remote_paths: list[str],
    remote_dirs: list[str],
    remote_dir: str,
    command_template: str,
    run_mode: str,
    max_preview: int = 10,
) -> list[str]:
    mode = RunMode(run_mode)
    sources = [RunSource(path=p, is_dir=False) for p in remote_paths]
    sources.extend(RunSource(path=p, is_dir=True) for p in remote_dirs)
    plan = build_run_plan(RunSpec(
        server_id="preview",
        remote_dir=remote_dir,
        command_template=command_template,
        max_parallel=1,
        mode=mode,
        sources=sources,
    ), run_id="preview")
    return [f"{task.task_id}: {task.command}" for task in plan.tasks[:max_preview]]


def choose_chunks_to_submit(chunks: list, submit_mode: str) -> list:
    if submit_mode == "create_only":
        return []
    if submit_mode == "first_batch":
        return chunks[:1]
    return chunks


def choose_delete_scope(local_count: int, remote_count: int, focused_pane: str) -> str:
    if focused_pane == "local" and local_count > 0:
        return "local"
    if focused_pane == "remote" and remote_count > 0:
        return "remote"
    if remote_count > 0:
        return "remote"
    if local_count > 0:
        return "local"
    return ""


def default_remote_dir_for_server(server) -> str:
    username = (getattr(server, "username", "") or "").strip()
    if username == "root":
        return "/root"
    if username:
        return f"/home/{username}"
    return "/"


def remote_child_path(remote_dir: str, name: str) -> str:
    base = normalize_remote_path(remote_dir)
    child = name.strip("/")
    if not child:
        return base
    return normalize_remote_path(posixpath.join(base, child))


def remote_parent_path(remote_dir: str) -> str:
    path = normalize_remote_path(remote_dir)
    if path == "/":
        return "/"
    parent = posixpath.dirname(path.rstrip("/"))
    return parent or "/"


def local_parent_row(local_dir: str | Path) -> list[str] | None:
    path = Path(local_dir).resolve()
    parent = path.parent
    if parent == path:
        return None
    return local_table_row("..", True, "", str(parent))


def remote_parent_row(remote_dir: str) -> list[str] | None:
    path = normalize_remote_path(remote_dir)
    if path == "/":
        return None
    return remote_table_row("..", True, "", "", "", remote_parent_path(path))


def normalize_remote_path(remote_dir: str) -> str:
    path = (remote_dir or "/").replace("\\", "/").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    normalized = posixpath.normpath(path)
    return "/" if normalized == "." else normalized


def breadcrumb_parts(remote_dir: str) -> list[tuple[str, str]]:
    path = normalize_remote_path(remote_dir)
    parts = [("/", "/")]
    if path == "/":
        return parts
    current = ""
    for part in path.strip("/").split("/"):
        current = f"{current}/{part}" if current else f"/{part}"
        parts.append((part, current))
    return parts


def connection_status_text(server_id: str | None, connected: bool, error: str = "", language: str = "en") -> str:
    if error:
        return f"Connection failed: {error}"
    if not server_id:
        return tr("No server selected", language)
    key = "Connected: {server_id}" if connected else "Connecting: {server_id}"
    return tr(key, language, server_id=server_id)


def file_action_labels() -> dict[str, str]:
    return {
        "up": "Up",
        "home": "Home",
        "refresh_local": "Refresh Local",
        "refresh_remote": "Refresh Remote",
        "upload": "Upload ->",
        "download": "<- Download",
        "mkdir": "New Folder",
        "rename": "Rename",
        "delete": "Delete",
        "preview": "Preview",
    }


def file_table_headers(kind: str) -> list[str]:
    if kind == "remote":
        return ["name", "size", "modified", "permissions"]
    return ["name", "size", "modified"]


def files_layout_row_counts() -> dict[str, int]:
    return {
        "top_toolbar_rows": 1,
        "action_rows": 1,
        "run_rows": 3,
    }


def local_table_row(name: str, is_dir: bool, size: str, path: str, modified: str = "") -> list[str]:
    return [name, size, modified, "dir" if is_dir else "file", path]


def remote_table_row(name: str, is_dir: bool, size: str, modified: str, permissions: str, path: str) -> list[str]:
    return [name, size, modified, permissions, "dir" if is_dir else "file", path]


def format_selection_summary(local_count: int, remote_count: int, language: str = "en") -> str:
    return tr(
        "Local {local_count} | Remote {remote_count}",
        language,
        local_count=local_count,
        remote_count=remote_count,
    )


class FileTransferPage(QWidget):
    def __init__(self, state, log_cb, status_cb, error_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._error_cb = error_cb
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
        self._initialized = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

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

        self.refresh_btn = QPushButton("⟳ " + tr("Refresh", self._language))
        self.refresh_btn.setToolTip(tr("Refresh", self._language))
        self.refresh_btn.clicked.connect(self._refresh_all)
        self._normalize_control_heights(self.refresh_btn)

        main_splitter = QSplitter(Qt.Vertical)
        main_splitter.setHandleWidth(8)

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
        self.local_table.drop_files.connect(self._upload_dropped_local_paths)
        self.remote_table.drop_files.connect(self._download_dropped_remote_paths)
        _setup_table(self.local_table, self._translated_table_headers("local"), hidden_columns=[3, 4])
        _setup_table(self.remote_table, self._translated_table_headers("remote"), hidden_columns=[4, 5])
        self._restore_column_widths(self.local_table, "files.local")
        self._restore_column_widths(self.remote_table, "files.remote")
        self.local_table.horizontalHeader().sectionResized.connect(lambda *_: self._save_column_widths(self.local_table, "files.local"))
        self.remote_table.horizontalHeader().sectionResized.connect(lambda *_: self._save_column_widths(self.remote_table, "files.remote"))
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
        self.remote_table.key_delete.connect(self._delete_remote)
        self.remote_table.key_enter.connect(self._enter_remote)
        local_pane = QWidget()
        local_pane.setMinimumWidth(160)
        local_pane_layout = QVBoxLayout(local_pane)
        local_pane_layout.setContentsMargins(0, 0, 0, 0)
        local_pane_layout.setSpacing(4)
        local_header_widget = QWidget()
        local_header_widget.setObjectName("LocalHeader")
        local_header_widget.setStyleSheet(
            "#LocalHeader { background: #e2e8f0; border: 1px solid #cbd5e1;"
            " border-radius: 6px; border-top-right-radius: 0; border-bottom-right-radius: 0; }"
            " #LocalHeader QPushButton { background: #cbd5e1; border: 1px solid #94a3b8;"
            " padding: 0 8px; border-radius: 4px; min-height: 44px; max-height: 44px; }"
            " #LocalHeader QPushButton:pressed { background: #93c5fd; border-color: #3b82f6; }"
        )
        local_header_widget.setFixedHeight(60)
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
            "#RemoteHeader { background: #e2e8f0; border: 1px solid #cbd5e1;"
            " border-radius: 6px; border-top-left-radius: 0; border-bottom-left-radius: 0; }"
            " #RemoteHeader QPushButton { background: #cbd5e1; border: 1px solid #94a3b8;"
            " padding: 0 8px; border-radius: 4px; min-height: 44px; max-height: 44px; }"
            " #RemoteHeader QPushButton:pressed { background: #93c5fd; border-color: #3b82f6; }"
            " #RemoteHeader QLineEdit, #RemoteHeader QComboBox {"
            " background: #cbd5e1; border: 1px solid #94a3b8; border-radius: 4px;"
            " padding: 0 8px; min-height: 44px; max-height: 44px; }"
            " #RemoteHeader QLabel { background: transparent; }"
        )
        remote_header_widget.setFixedHeight(60)
        remote_header = QHBoxLayout(remote_header_widget)
        remote_header.setContentsMargins(8, 0, 8, 0)
        remote_header.setSpacing(12)
        remote_header.addWidget(self.server_label, 0)
        remote_header.addWidget(self.server_combo, 0)
        remote_header.addWidget(self.connection_label)
        remote_header.addWidget(self.remote_path, 1)
        remote_pane_layout.addWidget(remote_header_widget)
        remote_pane_layout.addWidget(self.remote_table, 1)

        splitter.addWidget(local_pane)
        splitter.addWidget(remote_pane)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([500, 620])
        main_splitter.addWidget(splitter)

        run_panel = QWidget()
        run_panel.setObjectName("RunPanel")
        run_panel.setStyleSheet(
            "#RunPanel { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; }"
            " #RunPanel QPushButton { background: #cbd5e1; border: 1px solid #94a3b8;"
            " padding: 0 16px; border-radius: 4px; min-height: 44px; max-height: 44px; }"
            " #RunPanel QPushButton:pressed { background: #93c5fd; border-color: #3b82f6; }"
            " #RunPanel QLineEdit, #RunPanel QComboBox, #RunPanel QSpinBox {"
            " background: #cbd5e1; border: 1px solid #94a3b8; border-radius: 4px;"
            " padding: 0 8px; min-height: 44px; max-height: 44px; }"
            " #RunPanel QLabel { background: transparent; }"
        )
        run_panel.setMinimumHeight(110)
        run_layout = QVBoxLayout(run_panel)
        run_layout.setContentsMargins(16, 8, 16, 8)
        run_layout.setSpacing(4)

        command_row = QHBoxLayout()
        command_row.setSpacing(6)
        self.command_label = QLabel(tr("Command:", self._language))
        command_row.addWidget(self.command_label)
        self.command_edit = QComboBox()
        self.command_edit.setEditable(True)
        self.command_edit.setInsertPolicy(QComboBox.NoInsert)
        self._load_command_history()
        self.command_edit.setCurrentText(self._gui_settings.command_template)
        command_row.addWidget(self.command_edit, 1)
        self.preview_commands_btn = QPushButton(tr("Preview Commands", self._language))
        self.preview_commands_btn.clicked.connect(self._preview_run_commands)
        command_row.addWidget(self.preview_commands_btn)
        run_layout.addLayout(command_row)

        run_options_row = QHBoxLayout()
        run_options_row.setSpacing(6)
        self.run_mode_label = QLabel(tr("Run mode:", self._language))
        run_options_row.addWidget(self.run_mode_label)
        self.run_mode_combo = QComboBox()
        self._populate_run_mode_combo()
        run_options_row.addWidget(self.run_mode_combo)
        self.max_parallel_label = QLabel(tr("Max parallel:", self._language))
        run_options_row.addWidget(self.max_parallel_label)
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.max_parallel_spin.setRange(1, 9999)
        self.max_parallel_spin.setValue(self._gui_settings.max_parallel)
        run_options_row.addWidget(self.max_parallel_spin)
        self.run_btn = QPushButton(tr("Run Selected", self._language))
        self.run_btn.clicked.connect(self._run_selected)
        run_options_row.addWidget(self.run_btn)
        self.create_only_btn = QPushButton(tr("Create tasks only", self._language))
        self.create_only_btn.clicked.connect(self._create_only)
        run_options_row.addWidget(self.create_only_btn)
        run_options_row.addStretch()
        run_layout.addLayout(run_options_row)

        self.command_preview = QTextEdit()
        self.command_preview.setReadOnly(True)
        self.command_preview.setMinimumHeight(0)
        self.command_preview.setMaximumHeight(90)
        self.command_preview.setVisible(False)
        run_layout.addWidget(self.command_preview)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMaximumHeight(18)
        self.progress_bar.setTextVisible(True)
        run_layout.addWidget(self.progress_bar)
        main_splitter.addWidget(run_panel)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setCollapsible(1, False)
        main_splitter.setStretchFactor(0, 8)
        main_splitter.setStretchFactor(1, 2)
        main_splitter.setSizes([620, 100])
        layout.addWidget(main_splitter, 1)

        self._load_servers()
        self._refresh_local()
        self._connect_selection_signals()
        self._allow_width_shrink()
        self._normalize_all_control_heights()
        if self._gui_settings.auto_connect:
            QTimer.singleShot(0, self._auto_connect_selected_server)

    def _choose_local_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select Local Folder")
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
        self._apply_gui_settings_no_folder()

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

    def apply_language(self, language: str):
        self._language = language
        self.server_label.setText(tr("Server:", language))
        self.command_label.setText(tr("Command:", language))
        self.preview_commands_btn.setText(tr("Preview Commands", language))
        self.run_mode_label.setText(tr("Run mode:", language))
        self.max_parallel_label.setText(tr("Max parallel:", language))
        self.run_btn.setText(tr("Run Selected", language))
        self.create_only_btn.setText(tr("Create tasks only", language))
        self.local_table.setHorizontalHeaderLabels(self._translated_table_headers("local"))
        self.remote_table.setHorizontalHeaderLabels(self._translated_table_headers("remote"))
        self._populate_run_mode_combo()
        self.connection_label.setText(connection_status_text(
            self._connected_server_id,
            self._service is not None,
            language=language,
        ))

    def _translated_table_headers(self, kind: str) -> list[str]:
        return [tr(header, self._language) for header in file_table_headers(kind)] + ["type", "path"]

    def _populate_run_mode_combo(self):
        current = self.run_mode_combo.currentData() if hasattr(self, "run_mode_combo") else RunMode.selected_files.value
        self.run_mode_combo.blockSignals(True)
        self.run_mode_combo.clear()
        self.run_mode_combo.addItem(tr("Selected files", self._language), RunMode.selected_files.value)
        self.run_mode_combo.addItem(tr("Selected directories", self._language), RunMode.selected_directories.value)
        self.run_mode_combo.addItem(tr("Current directory", self._language), RunMode.current_directory.value)
        idx = self.run_mode_combo.findData(current)
        self.run_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.run_mode_combo.blockSignals(False)

    def _populate_submit_mode_combo(self):
        current = self.submit_mode_combo.currentData() if hasattr(self, "submit_mode_combo") else "all_sequential"
        self.submit_mode_combo.blockSignals(True)
        self.submit_mode_combo.clear()
        self.submit_mode_combo.addItem(tr("Run all sequentially", self._language), "all_sequential")
        self.submit_mode_combo.addItem(tr("Run first batch only", self._language), "first_batch")
        self.submit_mode_combo.addItem(tr("Create runs only", self._language), "create_only")
        idx = self.submit_mode_combo.findData(current)
        self.submit_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.submit_mode_combo.blockSignals(False)

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
            self._error_cb("Servers Error", str(exc))

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

        self._service = FileTransferService(
            factory,
            allowed_delete_roots=collect_remote_delete_roots(self.state.current_manifest_path),
        )
        self._connected_server_id = server_id
        self._connected_server = server
        self.connection_label.setText(connection_status_text(server_id, True, language=self._language))
        self._load_remembered_profile()
        self._refresh_remote()

    def _apply_default_local_folder(self):
        # Prefer last-used folder over the static default
        folder = self._gui_settings.last_local_folder or self._gui_settings.default_local_folder
        if folder and Path(folder).exists():
            self.state.current_project_root = Path(folder)

    def _save_last_local_folder(self, path: Path) -> None:
        """Persist the current local folder so it survives restarts."""
        from dataclasses import replace
        store = GuiSettingsStore()
        current = store.load()
        store.save(replace(current, last_local_folder=str(path)))

    def _apply_gui_settings(self):
        self._apply_default_local_folder()
        self._apply_gui_settings_no_folder()
        local_root = str(self.state.current_project_root or Path.cwd())
        self.local_path_btn.setText(local_root)
        self.local_path_btn.setToolTip(local_root)

    def _apply_gui_settings_no_folder(self):
        """Apply settings that don't touch the local folder or remote path."""
        self.command_edit.setCurrentText(self._gui_settings.command_template)
        self.max_parallel_spin.setValue(self._gui_settings.max_parallel)

    def _refresh_local(self):
        self._gui_settings = GuiSettingsStore().load()
        base = self.state.current_project_root or Path.cwd()
        hide_dot = self._gui_settings.hide_dotfiles
        rows = []
        parent = local_parent_row(base)
        if parent is not None:
            rows.append(parent)
        try:
            children = sorted(Path(base).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower(), p.name))
        except PermissionError:
            self._status_cb(f"无权限访问: {base}")
            children = []
        for child in children:
            if hide_dot and child.name.startswith("."):
                continue
            try:
                is_dir = child.is_dir()
                size = "" if is_dir else format_file_size(child.stat().st_size)
                mtime = format_modified_time(child.stat().st_mtime)
            except (PermissionError, OSError):
                continue
            rows.append(local_table_row(child.name, is_dir, size, str(child), mtime))
        _load_rows(self.local_table, rows)
        self._update_selection_summary()

    def _refresh_local_after_navigation(self):
        self._refresh_local()
        self.local_table.clearSelection()
        self.local_table.setCurrentCell(-1, -1)

    def _refresh_all(self):
        self._refresh_local()
        self._refresh_remote()

    def _refresh_remote(self):
        if self._service is None:
            self._auto_connect_selected_server()
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
        self.remote_worker = _BackgroundRunWorker(_run)
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
        self._status_cb(f"Remote listed: {remote_dir} ({len(rows)} entries)")

    def _on_remote_list_error(self, request_id: int, error: str):
        if request_id != self._remote_list_request_id:
            return
        if self._remote_list_fallbacks:
            fallback = self._remote_list_fallbacks.pop(0)
            self._status_cb(f"Remote path missing, trying: {fallback}")
            self._refresh_remote_path(fallback)
            return
        self.connection_label.setText(connection_status_text(self._connected_server_id, False, error.splitlines()[0], self._language))
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

    def _focused_file_pane(self) -> str:
        focused = self.focusWidget()
        if focused is self.local_table or self.local_table.isAncestorOf(focused):
            return "local"
        if focused is self.remote_table or self.remote_table.isAncestorOf(focused):
            return "remote"
        return ""

    def _delete_selected(self):
        scope = choose_delete_scope(
            len(self._selected_local_paths()),
            len(self._selected_remote_paths()),
            self._focused_file_pane(),
        )
        if scope == "local":
            self._delete_local()
            return
        if scope == "remote":
            self._delete_remote()
            return
        self._status_cb("Select a local or remote file/folder")

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
        try:
            for path in paths:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            self._refresh_local()
        except Exception as exc:
            self._error_cb("Delete Local Error", str(exc))

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

    def _connect_selection_signals(self):
        self.local_table.itemSelectionChanged.connect(self._update_selection_summary)
        self.remote_table.itemSelectionChanged.connect(self._update_selection_summary)

    def _local_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction(tr("Upload ->", self._language), self._upload_selected)
        menu.addAction(tr("Refresh", self._language), self._refresh_local)
        menu.addSeparator()
        menu.addAction(tr("New Folder", self._language), self._mkdir_local)
        menu.addAction(tr("New File", self._language), self._new_file_local)
        menu.addAction(tr("Delete", self._language), self._delete_local)
        menu.addSeparator()
        menu.addAction(tr("Generate GJF from XYZ…", self._language), self._local_generate_gjf)
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
        menu.addSeparator()
        menu.addAction(tr("Generate GJF from XYZ…", self._language), self._remote_generate_gjf)
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

    # ── Generate GJF ──────────────────────────────────────────────────────

    def _local_generate_gjf(self):
        row = self.local_table.currentRow()
        path_item = self.local_table.item(row, 4) if row >= 0 else None
        xyz_path = path_item.text() if path_item else ""
        from ..dialogs.input_builder_dialog import InputBuilderDialog
        dlg = InputBuilderDialog(self, xyz_path=xyz_path)
        dlg.exec()

    def _remote_generate_gjf(self):
        """Download selected remote .xyz to a temp file, open InputBuilderDialog."""
        row = self.remote_table.currentRow()
        path_item = self.remote_table.item(row, 5) if row >= 0 else None
        if path_item is None or self._service is None:
            return
        remote_path = path_item.text()
        if not remote_path.lower().endswith(".xyz"):
            self._status_cb("Select a .xyz file first")
            return
        import tempfile
        tmp = Path(tempfile.mktemp(suffix=".xyz"))
        try:
            self._service.download_path(remote_path, str(tmp))
        except Exception as exc:
            self._status_cb(f"Download failed: {exc}")
            return
        from ..dialogs.input_builder_dialog import InputBuilderDialog
        dlg = InputBuilderDialog(self, xyz_path=tmp)
        if dlg.exec() and dlg.generated_path():
            # Upload generated file back to remote dir
            gen = dlg.generated_path()
            remote_dest = f"{self.remote_path.text().rstrip('/')}/{gen.name}"
            try:
                self._service.upload_path(str(gen), remote_dest)
                self._refresh_remote()
                self._status_cb(f"Uploaded: {remote_dest}")
            except Exception as exc:
                self._status_cb(f"Upload failed: {exc}")
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass

    # ── Open in Viewer ────────────────────────────────────────────────────

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
        tmp = Path(tempfile.mktemp(suffix=suffix))
        try:
            self._service.download_path(remote_path, str(tmp))
        except Exception as exc:
            self._status_cb(f"Download failed: {exc}")
            return
        from ...core.viewer import open_in_viewer
        open_in_viewer(tmp, custom_path=exe)
        self._status_cb(f"Opened in viewer: {Path(remote_path).name}")

    def _create_only(self):
        """Create run record without submitting."""
        self._run_selected_chunks(submit=False)

    def _remote_target_for_local(self, local_path: Path) -> str:
        return remote_child_path(self.remote_path.text().strip() or "/", local_path.name)

    def _open_local_item(self, item):
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
        try:
            os.startfile(path)
        except Exception as exc:
            self._error_cb("Open File Error", str(exc))

    def _remote_up(self):
        self.remote_path.setText(remote_parent_path(self.remote_path.text()))
        self._refresh_remote()

    def _remote_home(self):
        self.remote_path.setText("/")
        self._refresh_remote()

    def _open_remote_item(self, item):
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

    def _open_remote_file_in_editor(self, remote_path: str):
        """Download remote file to a temp directory and open with the default OS editor."""
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        import tempfile
        suffix = Path(remote_path).suffix or ".tmp"
        name = Path(remote_path).name
        # Use a stable temp dir per session so re-opening the same file reuses the path
        tmp_dir = Path(tempfile.gettempdir()) / "jobdesk_remote_edit"
        tmp_dir.mkdir(exist_ok=True)
        tmp_file = tmp_dir / name

        def _download():
            from ...core.file_transfer import OverwritePolicy
            self._service.download_path(remote_path, str(tmp_file), OverwritePolicy.overwrite)
            return tmp_file

        def _on_done(path):
            try:
                os.startfile(str(path))
                self._status_cb(f"Opened: {name}")
            except Exception as exc:
                self._error_cb("Open Error", str(exc))

        worker = _BackgroundRunWorker(_download)
        worker.result.connect(_on_done)
        worker.error.connect(lambda e: self._status_cb(f"Download failed: {e}"))
        self._background_workers.append(worker)
        worker.start()
        self._status_cb(f"Downloading {name}…")

    def _upload_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        local_path = self._selected_local_path()
        if local_path is None:
            self._status_cb("Select a local file or folder")
            return
        try:
            records = self._service.upload_path(
                local_path,
                self._remote_target_for_local(local_path),
                OverwritePolicy.skip_same_size,
            )
            if not isinstance(records, list):
                records = [records]
            self._status_cb(format_queue_summary([r.status for r in records], self._language))
            self._refresh_remote()
        except Exception as exc:
            self._error_cb("Upload Error", str(exc))

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

        worker = _BackgroundRunWorker(lambda: None)  # placeholder, replaced below

        def _run():
            def _progress(done, total):
                worker.progress.emit(int(done), int(total))
            with self._service._sftp_factory() as sftp:
                rec = sftp.download_file(remote_path, target,
                                         overwrite=True, skip_if_same_size=False,
                                         progress_callback=_progress)
            return [rec]

        worker._target_fn = _run
        self._start_transfer_worker(worker, "Download", self._refresh_local)

    def _upload_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        local_path = self._selected_local_path()
        if local_path is None:
            self._status_cb("Select a local file or folder")
            return
        remote_target = self._remote_target_for_local(local_path)

        worker = _BackgroundRunWorker(lambda: None)

        def _run():
            def _progress(done, total):
                worker.progress.emit(int(done), int(total))
            with self._service._sftp_factory() as sftp:
                if local_path.is_dir():
                    records = sftp.upload_dir(local_path, remote_target,
                                              overwrite=False, skip_if_same_size=True)
                else:
                    records = [sftp.upload_file(local_path, remote_target,
                                                overwrite=False, skip_if_same_size=True,
                                                progress_callback=_progress)]
            return records

        worker._target_fn = _run
        self._start_transfer_worker(worker, "Upload", self._refresh_remote)

    def _start_transfer_worker(self, worker, label: str, on_done_refresh):
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setFormat(f"{label}: %p%")
        self.progress_bar.setVisible(True)

        def _on_progress(done, total):
            if total > 0:
                self.progress_bar.setValue(int(done * 100 / total))
                self.progress_bar.setFormat(f"{label}: {done // 1024}K / {total // 1024}K")
            else:
                self.progress_bar.setMaximum(0)  # indeterminate

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

        worker.progress.connect(_on_progress)
        worker.result.connect(_on_done)
        worker.error.connect(_on_error)
        self._background_workers.append(worker)
        worker.start()
        self._status_cb(f"{label} started…")

    def _upload_dropped_local_paths(self, paths: list[str]):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        # If paths are remote (start with /), treat as download
        if paths and paths[0].startswith("/"):
            self._download_dropped_remote_paths(paths)
            return
        service = self._service
        remote_dir = self.remote_path.text().strip() or "/"

        def _run():
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
                )
                records.extend(result if isinstance(result, list) else [result])
            return records

        from ..workers import BackgroundWorker
        w = BackgroundWorker(_run)
        w.result.connect(lambda recs: (
            self._status_cb(format_queue_summary([r.status for r in recs], self._language)) if recs else None,
            self._refresh_remote()
        ))
        w.error.connect(lambda e: self._error_cb("Drop Upload Error", str(e)))
        w.finished.connect(w.deleteLater)
        self._keep_worker(w)
        w.start()

    def _download_dropped_remote_paths(self, paths: list[str]):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        # If paths look like local files (Windows paths), treat as upload
        if paths and (paths[0].startswith("/") is False) and Path(paths[0]).exists():
            self._upload_dropped_local_paths(paths)
            return
        service = self._service
        local_base = self.state.current_project_root or Path.cwd()

        def _run():
            records = []
            for remote_path in paths:
                result = service.download_path(
                    remote_path,
                    Path(local_base) / Path(remote_path).name,
                    OverwritePolicy.skip_same_size,
                )
                records.extend(result if isinstance(result, list) else [result])
            return records

        from ..workers import BackgroundWorker
        w = BackgroundWorker(_run)
        w.result.connect(lambda recs: (
            self._status_cb(format_queue_summary([r.status for r in recs], self._language)) if recs else None,
            self._refresh_local()
        ))
        w.error.connect(lambda e: self._error_cb("Drop Download Error", str(e)))
        w.finished.connect(w.deleteLater)
        self._keep_worker(w)
        w.start()

    def _mkdir_local(self):
        name, ok = QInputDialog.getText(self, tr("New Folder", self._language), tr("Folder name:", self._language))
        if not ok or not name.strip():
            return
        name = name.strip()
        if "/" in name or "\\" in name or name == ".." or name.startswith(".."):
            self._error_cb("Invalid Name", "名称不能包含路径分隔符或 '..'")
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
        if "/" in name or "\\" in name or name == ".." or name.startswith(".."):
            self._error_cb("Invalid Name", "名称不能包含路径分隔符或 '..'")
            return
        base = self.state.current_project_root or Path.cwd()
        new_file = Path(base) / name
        try:
            new_file.touch(exist_ok=False)
            self._refresh_local()
            os.startfile(str(new_file))
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
        tmp = Path(tempfile.mktemp(suffix=Path(name).suffix or ".tmp"))
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
        name, ok = QInputDialog.getText(self, "New Remote Folder", "Folder name:")
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
        try:
            text = self._service.preview_remote_text(remote_path)
            QMessageBox.information(self, remote_path, text[:4000])
        except Exception as exc:
            self._error_cb("Preview Error", str(exc))

    def _rename_remote(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file or folder")
            return
        new_name, ok = QInputDialog.getText(self, "Rename Remote Path", "New name:", text=Path(remote_path).name)
        if not ok or not new_name.strip():
            return
        parent = remote_path.rsplit("/", 1)[0] or "/"
        new_path = f"{parent}/{new_name.strip()}" if parent != "/" else f"/{new_name.strip()}"
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
        message = "\n".join(remote_paths[:10])
        if len(remote_paths) > 10:
            message += f"\n... {len(remote_paths) - 10} more"
        if QMessageBox.question(
            self,
            "Delete Remote Path",
            f"Delete remote path(s)?\n{message}",
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        try:
            for remote_path in remote_paths:
                self._service.delete_remote(remote_path, recursive=True)
            self._refresh_remote()
        except Exception as exc:
            self._error_cb("Delete Error", str(exc))

    def _preview_run_commands(self):
        files, dirs = self._selected_remote_entries()
        try:
            rows = format_command_preview_rows(
                files,
                dirs,
                self.remote_path.text().strip() or "/",
                self.command_edit.currentText(),
                self.run_mode_combo.currentData(),
            )
            self.command_preview.setPlainText("\n".join(rows) if rows else "No commands to run")
            self.command_preview.setVisible(True)
        except Exception as exc:
            self._error_cb("Preview Commands Error", str(exc))

    def _run_selected(self):
        self._run_selected_chunks(submit=True)

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

    def _run_selected_chunks(self, submit: bool = True):
        # Detect whether selection is local or remote
        remote_files, remote_dirs = self._selected_remote_entries()
        local_files, local_dirs = self._selected_local_entries()
        use_local = (not remote_files and not remote_dirs) and (local_files or local_dirs)

        if use_local:
            if self._service is None or self._connected_server is None:
                self._status_cb(tr("Connect to a server first", self._language))
                return
            files = []  # will be populated after upload in bg worker
            dirs = []
        else:
            files = remote_files
            dirs = remote_dirs

        reason = run_button_reason(
            self._service is not None and self._connected_server is not None,
            len(local_files) + len(local_dirs) if use_local else
            (len(files) + len(dirs) if self.run_mode_combo.currentData() != RunMode.current_directory.value else 1),
            self.command_edit.currentText(),
        )
        if reason:
            self._status_cb(reason)
            return
        if submit and QMessageBox.question(
            self, "Confirm", tr("Submit tasks to remote server?", self._language),
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        local_base = self.state.current_project_root or Path.cwd()
        remote_dir = self.remote_path.text().strip() or "/"
        command_template = self.command_edit.currentText().strip()
        max_parallel = self.max_parallel_spin.value()
        run_mode = RunMode(self.run_mode_combo.currentData())
        server_id = self._connected_server_id or ""
        connected_server = self._connected_server
        file_service = self._service

        if use_local:
            # Upload + create_run + submit all in background
            local_paths_files = list(local_files)
            local_paths_dirs = list(local_dirs)

            def _run():
                from ...services.scheduler_helpers import scheduler_from_server, resources_from_server
                ssh = create_ssh_client(connected_server)
                ssh.connect()
                sftp = create_sftp_client(ssh)
                try:
                    # 1. Upload
                    self._status_cb("上传文件中...")
                    uploaded_files = []
                    uploaded_dirs = []
                    for lp in local_paths_files:
                        target = remote_child_path(remote_dir, Path(lp).name)
                        file_service.upload_path(Path(lp), target, OverwritePolicy.skip_same_size)
                        uploaded_files.append(target)
                    for ld in local_paths_dirs:
                        target = remote_child_path(remote_dir, Path(ld).name)
                        file_service.upload_path(Path(ld), target, OverwritePolicy.skip_same_size)
                        uploaded_dirs.append(target)
                    # 2. Create run
                    all_sources = [RunSource(path=p, is_dir=False) for p in uploaded_files] + [
                        RunSource(path=p, is_dir=True) for p in uploaded_dirs
                    ]
                    chunks = chunk_sources(all_sources, 0)
                    svc = RunService(local_base)
                    run_records = []
                    for chunk in chunks:
                        spec = RunSpec(
                            server_id=server_id,
                            remote_dir=remote_dir,
                            command_template=command_template,
                            max_parallel=max_parallel,
                            mode=run_mode,
                            sources=chunk,
                        )
                        run_records.append(svc.create_run(spec))
                    # 3. Submit
                    results = []
                    for record in run_records:
                        results.append(svc.submit_run(
                            record.run_id, ssh, sftp,
                            env_init_scripts=list(getattr(connected_server, "env_init_scripts", []) or []),
                            scheduler=scheduler_from_server(connected_server),
                            resources=resources_from_server(connected_server),
                        ))
                    return results
                finally:
                    sftp.close()
                    ssh.close()

            self._status_cb("提交中...")
            self.worker = _BackgroundRunWorker(_run)
            self.worker.result.connect(lambda results: self._on_runs_done(results))
            self.worker.error.connect(lambda error: self._error_cb("Run Error", error))
            self.worker.start()
            self._save_remembered_profile()
            self._save_command_history()
            return

        all_sources = [RunSource(path=p, is_dir=False) for p in files] + [
            RunSource(path=p, is_dir=True) for p in dirs
        ]
        if run_mode == RunMode.current_directory:
            all_sources = []
        chunks = chunk_sources(all_sources, 0)
        if run_mode == RunMode.current_directory:
            chunks = [[]]
        service = RunService(local_base)
        run_records = []
        for chunk in chunks:
            spec = RunSpec(
                server_id=server_id,
                remote_dir=remote_dir,
                command_template=command_template,
                max_parallel=max_parallel,
                mode=run_mode,
                sources=chunk,
            )
            run_records.append(service.create_run(spec))
        run_record = run_records[0]
        self.state.current_project_root = Path(local_base)
        self.state.current_batch_id = run_record.run_id
        self.state.current_manifest_path = run_record.manifest_path
        self._save_remembered_profile()
        self._save_command_history()

        if not submit:
            self._status_cb(f"Created {len(run_records)} run(s)")
            return

        def _run():
            results = []
            from ...services.scheduler_helpers import scheduler_from_server, resources_from_server
            for record in run_records:
                ssh = create_ssh_client(self._connected_server)
                ssh.connect()
                sftp = create_sftp_client(ssh)
                try:
                    results.append(RunService(local_base).submit_run(
                        record.run_id, ssh, sftp,
                        env_init_scripts=list(getattr(self._connected_server, "env_init_scripts", []) or []),
                        scheduler=scheduler_from_server(self._connected_server),
                        resources=resources_from_server(self._connected_server),
                    ))
                finally:
                    sftp.close()
                    ssh.close()
            return results

        self._log(f"Runs created: {', '.join(r.run_id for r in run_records)}")
        self._status_cb(f"Running {run_record.run_id}...")
        self.worker = _BackgroundRunWorker(_run)
        self.worker.result.connect(lambda results: self._on_runs_done(results))
        self.worker.error.connect(lambda error: self._error_cb("Run Error", error))
        self.worker.start()

    def _on_runs_done(self, results):
        for result in results:
            self._log(f"Run submitted: {result.batch_id}, tasks={result.submitted_task_count}, errors={len(result.errors)}")
            for error in result.errors:
                self._log(f"  {error}")
        self._status_cb(f"Submitted {len(results)} run(s)")

    def _save_remembered_profile(self):
        if not self._connected_server_id:
            return
        RunProfileStore().save_last(
            server_id=self._connected_server_id,
            remote_dir=self.remote_path.text().strip() or "/",
            command_template=self.command_edit.currentText().strip(),
            max_parallel=self.max_parallel_spin.value(),
            download_patterns=[],
        )

    def _save_command_history(self):
        cmd = self.command_edit.currentText().strip()
        if not cmd:
            return
        # Avoid duplicates; insert at top
        idx = self.command_edit.findText(cmd)
        if idx >= 0:
            self.command_edit.removeItem(idx)
        self.command_edit.insertItem(0, cmd)
        self.command_edit.setCurrentIndex(0)
        # Persist via RunProfileStore (limited to 20 entries)
        items = [self.command_edit.itemText(i) for i in range(min(self.command_edit.count(), 20))]
        RunProfileStore().save_command_history(items)

    def _load_command_history(self):
        history = RunProfileStore().load_command_history()
        self.command_edit.clear()
        for cmd in history:
            self.command_edit.addItem(cmd)

    def _progress_callback(self, transferred: int, total: int) -> None:
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(transferred)
            self.progress_bar.setVisible(True)
            pct = transferred * 100 // total
            self.progress_bar.setFormat(f"{pct}% ({transferred // 1024}KB / {total // 1024}KB)")

    def _reset_progress(self) -> None:
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)

    def _load_remembered_profile(self):
        if not self._connected_server_id:
            return
        profile = RunProfileStore().load_last(
            self._connected_server_id,
            self.remote_path.text().strip() or "/",
        )
        if profile is None:
            return
        self.command_edit.setCurrentText(profile.command_template)
        self.max_parallel_spin.setValue(profile.max_parallel)

    def _allow_width_shrink(self):
        for widget in (
            self.local_path_btn,
            self.connection_label,
            self.remote_path,
            self.command_edit,
        ):
            policy = widget.sizePolicy()
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Ignored, policy.verticalPolicy())
        for widget in (
            self.server_combo,
            self.run_mode_combo,
            self.max_parallel_spin,
            self.run_btn,
            self.create_only_btn,
        ):
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
            self.command_edit,
            self.preview_commands_btn,
            self.run_mode_combo,
            self.max_parallel_spin,
            self.run_btn,
            self.create_only_btn,
        )

    def shutdown(self):
        self._remember_current_remote_dir()
        store = GuiSettingsStore()
        current = store.load()
        new_server_id = self._connected_server_id or self.server_combo.currentData() or ""
        new_remote_dirs = {**dict(current.last_remote_dirs or {}), **self._server_remote_dirs}
        store.save(replace(current,
            last_server_id=new_server_id,
            last_remote_dirs=new_remote_dirs,
        ))
        workers = list(self._background_workers)
        worker = getattr(self, "worker", None)
        if worker is not None and worker not in workers:
            workers.append(worker)
        for worker in workers:
            if hasattr(worker, "stop_safely"):
                worker.stop_safely()
            elif hasattr(worker, "isRunning") and worker.isRunning():
                worker.quit()
                worker.wait(3000)

    def _keep_worker(self, worker):
        self._background_workers.append(worker)
        worker.finished.connect(lambda: self._background_workers.remove(worker) if worker in self._background_workers else None)

    def _restore_column_widths(self, table: QTableWidget, key: str):
        widths = (self._gui_settings.column_widths or {}).get(key) or _default_column_widths(key)
        for column, width in enumerate(_clamp_column_widths(key, widths)):
            if column < table.columnCount() and width > 0:
                table.setColumnWidth(column, width)

    def _save_column_widths(self, table: QTableWidget, key: str):
        current = GuiSettingsStore().load()
        widths = dict(current.column_widths or {})
        widths[key] = [table.columnWidth(column) for column in range(table.columnCount()) if not table.isColumnHidden(column)]
        GuiSettingsStore().save(replace(current, column_widths=widths))


class _ConnectedSFTP:
    def __init__(self, ssh, sftp):
        self._ssh = ssh
        self._sftp = sftp

    def __getattr__(self, name):
        return getattr(self._sftp, name)

    def close(self):
        self._sftp.close()
        self._ssh.close()


class _FileTable(QTableWidget):
    drop_files = Signal(list)
    key_delete = Signal()
    key_enter = Signal()

    def __init__(self, role: str):
        super().__init__()
        self.role = role
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.key_delete.emit()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.key_enter.emit()
        else:
            super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        if not rows and self.currentRow() >= 0:
            rows = [self.currentRow()]
        paths = []
        for row in rows:
            name_item = self.item(row, 0)
            path_item = self.item(row, 4 if self.role == "local" else 5)
            if not path_item or (name_item and name_item.text() == ".."):
                continue
            paths.append(path_item.text())
        if not paths:
            return
        mime = QMimeData()
        if self.role == "local":
            mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        else:
            mime.setData("application/x-jobdesk-remote-paths", "\n".join(paths).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, event):
        if self._accepts_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._accepts_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        if self.role == "remote" and mime.hasUrls():
            self.drop_files.emit([url.toLocalFile() for url in mime.urls() if url.isLocalFile()])
            event.acceptProposedAction()
            return
        if self.role == "local" and mime.hasFormat("application/x-jobdesk-remote-paths"):
            data = bytes(mime.data("application/x-jobdesk-remote-paths")).decode("utf-8")
            self.drop_files.emit([line for line in data.splitlines() if line])
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _accepts_mime(self, mime: QMimeData) -> bool:
        if self.role == "remote":
            return mime.hasUrls()
        return mime.hasFormat("application/x-jobdesk-remote-paths")


class _BackgroundRunWorker:
    def __new__(cls, target):
        from ..workers import BackgroundWorker
        return BackgroundWorker(target)


def _setup_table(table: QTableWidget, headers: list[str], hidden_columns: list[int] | None = None) -> None:
    from PySide6.QtCore import QSize
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.setIconSize(QSize(24, 24))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    for column in hidden_columns or []:
        table.setColumnHidden(column, True)


def _load_rows(table: QTableWidget, rows: list[list[str]]) -> None:
    from PySide6.QtWidgets import QStyle
    from PySide6.QtGui import QIcon
    style = table.style()
    folder_icon = style.standardIcon(QStyle.SP_DirIcon)
    file_icon = style.standardIcon(QStyle.SP_FileIcon)
    up_icon = style.standardIcon(QStyle.SP_ArrowUp)
    # kind column: local=3, remote=4
    kind_col = 4 if table.role == "remote" else 3
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        kind = row[kind_col] if kind_col < len(row) else ""
        is_parent = (str(row[0]) == "..")
        # Sort rank: ".." = 0, dir = 1, file = 2
        sort_rank = 0 if is_parent else (1 if kind == "dir" else 2)
        for c, value in enumerate(row):
            item = _SortableItem(str(value), sort_rank)
            if c == 0:
                if is_parent:
                    item.setIcon(up_icon)
                elif kind == "dir":
                    item.setIcon(folder_icon)
                else:
                    item.setIcon(file_icon)
            table.setItem(r, c, item)
    table.setSortingEnabled(True)


class _SortableItem(QTableWidgetItem):
    """Table item that sorts directories before files."""

    def __init__(self, text: str, sort_rank: int):
        super().__init__(text)
        self._sort_rank = sort_rank

    def __lt__(self, other):
        if isinstance(other, _SortableItem) and self._sort_rank != other._sort_rank:
            return self._sort_rank < other._sort_rank
        return self.text().lower() < other.text().lower()


def _default_column_widths(key: str) -> list[int]:
    if key == "files.remote":
        return [320, 95, 155, 82]
    return [360, 95, 155]


def _clamp_column_widths(key: str, widths: list[int]) -> list[int]:
    minimums = [90, 60, 110, 55]
    return [
        max(minimums[min(index, len(minimums) - 1)], int(width))
        for index, width in enumerate(widths)
    ]
