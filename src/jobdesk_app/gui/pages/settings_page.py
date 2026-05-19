from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QCheckBox,
    QSpinBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
)
from PySide6.QtCore import Signal

from ...config.servers import get_default_servers_path, load_servers
from ...services.gui_settings import GuiSettings, GuiSettingsStore
from ...services.run_profiles import RunProfileStore
from ..i18n import tr


def build_settings_rows(workspace_dir: str | Path) -> list[tuple[str, str]]:
    workspace = Path(workspace_dir).resolve()
    return [
        ("workspace", str(workspace)),
        ("runs", str(workspace / ".jobdesk" / "runs")),
        ("results", str(workspace / "results")),
        ("servers_config", str(get_default_servers_path())),
        ("run_profiles", str(RunProfileStore().path)),
        ("gui_settings", str(GuiSettingsStore().path)),
    ]


def settings_status_summary(server_id: str, remote_dir: str, auto_connect: bool, language: str = "en") -> str:
    if not auto_connect:
        return tr("Auto connect disabled", language)
    if language == "zh":
        server = server_id or tr("(first server)", language)
        return f"自动连接到 {server}，远程目录 {remote_dir or '/'}"
    return f"Auto connect to {server_id or '(first server)'} at {remote_dir or '/'}"


class SettingsPage(QWidget):
    language_changed = Signal(str)

    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._store = GuiSettingsStore()
        self._language = self._store.load().language

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.general_box = QGroupBox()
        form = QFormLayout(self.general_box)

        local_row = QHBoxLayout()
        self.local_folder_edit = QLineEdit()
        self.browse_btn = QPushButton()
        self.browse_btn.clicked.connect(self._browse_local_folder)
        local_row.addWidget(self.local_folder_edit, 1)
        local_row.addWidget(self.browse_btn)

        self.remote_dir_edit = QLineEdit()
        self.server_combo = QComboBox()
        self.auto_connect_check = QCheckBox()
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setRange(1, 9999)
        self.language_combo = QComboBox()

        self.local_folder_label = QLabel()
        self.server_label = QLabel()
        self.remote_dir_label = QLabel()
        self.connection_label = QLabel()
        self.max_parallel_label = QLabel()
        self.language_label = QLabel()

        form.addRow(self.local_folder_label, local_row)
        form.addRow(self.server_label, self.server_combo)
        form.addRow(self.remote_dir_label, self.remote_dir_edit)
        form.addRow(self.connection_label, self.auto_connect_check)
        form.addRow(self.max_parallel_label, self.max_parallel_spin)
        form.addRow(self.language_label, self.language_combo)
        layout.addWidget(self.general_box)

        self.paths_box = QGroupBox()
        paths_layout = QVBoxLayout(self.paths_box)
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        paths_layout.addWidget(self.table)
        layout.addWidget(self.paths_box, 1)

        btns = QHBoxLayout()
        self.save_btn = QPushButton()
        self.save_btn.clicked.connect(self._save_settings)
        btns.addWidget(self.save_btn)
        self.reload_btn = QPushButton()
        self.reload_btn.clicked.connect(self.refresh)
        btns.addWidget(self.reload_btn)
        self.clear_profile_btn = QPushButton()
        self.clear_profile_btn.clicked.connect(self._clear_run_profiles)
        btns.addWidget(self.clear_profile_btn)
        btns.addStretch()
        layout.addLayout(btns)

        self.apply_language(self._language)

    def on_activated(self):
        self.refresh()

    def apply_language(self, language: str):
        self._language = language
        self.general_box.setTitle(tr("Defaults", language))
        self.paths_box.setTitle(tr("Paths", language))
        self.browse_btn.setText(tr("Browse", language))
        self.local_folder_label.setText(tr("Default local folder:", language))
        self.server_label.setText(tr("Default server:", language))
        self.remote_dir_label.setText(tr("Default remote directory:", language))
        self.connection_label.setText(tr("Connection:", language))
        self.auto_connect_check.setText(tr("Auto connect selected server", language))
        self.max_parallel_label.setText(tr("Max parallel", language))
        self.language_label.setText(tr("Language:", language))
        self.save_btn.setText(tr("Save Settings", language))
        self.reload_btn.setText(tr("Reload Settings", language))
        self.clear_profile_btn.setText(tr("Clear Run Profiles", language))
        self.table.setHorizontalHeaderLabels([tr("setting", language), tr("value", language)])
        self._populate_language_combo()

    def refresh(self):
        self._language = self._store.load().language
        self.apply_language(self._language)
        self._load_servers()
        self._load_settings()
        self._load_paths()

    def _load_servers(self):
        current = self.server_combo.currentData()
        self.server_combo.clear()
        self.server_combo.addItem(tr("(first server)", self._language), "")
        try:
            for sid in sorted(load_servers().servers):
                self.server_combo.addItem(sid, sid)
        except Exception as exc:
            self._log(f"Settings server list unavailable: {exc}")
        if current is not None:
            idx = self.server_combo.findData(current)
            if idx >= 0:
                self.server_combo.setCurrentIndex(idx)

    def _load_settings(self):
        settings = self._store.load()
        self.local_folder_edit.setText(settings.default_local_folder)
        self.remote_dir_edit.setText(settings.default_remote_dir)
        idx = self.server_combo.findData(settings.default_server_id)
        if idx >= 0:
            self.server_combo.setCurrentIndex(idx)
        self.auto_connect_check.setChecked(settings.auto_connect)
        self.max_parallel_spin.setValue(settings.max_parallel)
        idx = self.language_combo.findData(settings.language)
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        self._status_cb(settings_status_summary(
            settings.default_server_id,
            settings.default_remote_dir,
            settings.auto_connect,
            self._language,
        ))

    def _load_paths(self):
        workspace = self.state.current_project_root or Path.cwd()
        rows = build_settings_rows(workspace)
        self.table.setRowCount(len(rows))
        for row, (name, value) in enumerate(rows):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem(value))

    def _settings_from_controls(self) -> GuiSettings:
        existing = self._store.load()
        return GuiSettings(
            default_local_folder=self.local_folder_edit.text().strip(),
            default_remote_dir=self.remote_dir_edit.text().strip() or "/tmp",
            default_server_id=self.server_combo.currentData() or "",
            auto_connect=self.auto_connect_check.isChecked(),
            overwrite_policy=existing.overwrite_policy,
            command_template=existing.command_template,
            max_parallel=self.max_parallel_spin.value(),
            batch_size=existing.batch_size,
            language=self.language_combo.currentData() or "en",
            column_widths=existing.column_widths or {},
        )

    def _save_settings(self):
        settings = self._settings_from_controls()
        path = self._store.save(settings)
        self._language = settings.language
        self.apply_language(self._language)
        self._load_servers()
        self._log(f"GUI settings saved: {path}")
        self._status_cb(settings_status_summary(
            settings.default_server_id,
            settings.default_remote_dir,
            settings.auto_connect,
            self._language,
        ))
        self._load_paths()
        self.language_changed.emit(self._language)

    def _browse_local_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Select Default Local Folder", self.local_folder_edit.text())
        if path:
            self.local_folder_edit.setText(path)

    def _clear_run_profiles(self):
        path = RunProfileStore().path
        path.unlink(missing_ok=True)
        self._log(f"Run profiles cleared: {path}")
        self.refresh()

    def _populate_language_combo(self):
        current = self.language_combo.currentData() if self.language_combo.count() else self._language
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem(tr("English", self._language), "en")
        self.language_combo.addItem(tr("Chinese", self._language), "zh")
        idx = self.language_combo.findData(current)
        self.language_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.language_combo.blockSignals(False)
