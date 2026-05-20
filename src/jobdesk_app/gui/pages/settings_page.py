from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox,
    QSpinBox, QComboBox, QGroupBox,
)
from PySide6.QtCore import Signal

from ...config.servers import get_default_servers_path
from ...services.gui_settings import GuiSettings, GuiSettingsStore
from ...services.run_profiles import RunProfileStore
from ..i18n import tr


def build_settings_rows(workspace_dir: str | Path) -> list[tuple[str, str]]:
    import os
    workspace = Path(workspace_dir).resolve()
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return [
        ("workspace", str(workspace)),
        ("runs", str(Path(appdata) / "JobDesk" / "runs")),
        ("results", str(workspace / "results")),
        ("servers_config", str(get_default_servers_path())),
        ("run_profiles", str(RunProfileStore().path)),
        ("gui_settings", str(GuiSettingsStore().path)),
    ]


def settings_status_summary(server_id: str, remote_dir: str, auto_connect: bool, language: str = "en") -> str:
    return ""


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
        layout.setContentsMargins(24, 20, 24, 14)
        layout.setSpacing(16)

        # ── General settings ──────────────────────────────────────────────
        self.general_box = QGroupBox()
        form = QVBoxLayout(self.general_box)
        form.setSpacing(14)
        form.setContentsMargins(16, 16, 16, 16)

        # Row: Language
        row1 = QHBoxLayout()
        self.language_label = QLabel()
        self.language_label.setFixedWidth(130)
        self.language_combo = QComboBox()
        self.language_combo.setFixedWidth(140)
        row1.addWidget(self.language_label)
        row1.addWidget(self.language_combo)
        row1.addStretch()
        form.addLayout(row1)

        # Row: Max parallel
        row2 = QHBoxLayout()
        self.max_parallel_label = QLabel()
        self.max_parallel_label.setFixedWidth(130)
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setRange(1, 9999)
        self.max_parallel_spin.setFixedWidth(80)
        row2.addWidget(self.max_parallel_label)
        row2.addWidget(self.max_parallel_spin)
        row2.addStretch()
        form.addLayout(row2)

        # Row: Hide dotfiles
        self.hide_dotfiles_check = QCheckBox()
        form.addWidget(self.hide_dotfiles_check)

        layout.addWidget(self.general_box)

        # ── Paths ─────────────────────────────────────────────────────────
        self.paths_box = QGroupBox()
        paths_layout = QVBoxLayout(self.paths_box)
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        paths_layout.addWidget(self.table)
        layout.addWidget(self.paths_box, 1)

        # ── Buttons ───────────────────────────────────────────────────────
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
        self.general_box.setTitle(tr("Settings", language))
        self.paths_box.setTitle(tr("Paths", language))
        self.language_label.setText(tr("Language:", language))
        self.max_parallel_label.setText(tr("Max parallel:", language))
        self.hide_dotfiles_check.setText(tr("Hide dotfiles (.xx)", language))
        self.save_btn.setText(tr("Save Settings", language))
        self.reload_btn.setText(tr("Reload Settings", language))
        self.clear_profile_btn.setText(tr("Clear Run Profiles", language))
        self.table.setHorizontalHeaderLabels([tr("setting", language), tr("value", language)])
        self._populate_language_combo()

    def refresh(self):
        self._language = self._store.load().language
        self.apply_language(self._language)
        self._load_settings()
        self._load_paths()

    def _load_settings(self):
        settings = self._store.load()
        self.max_parallel_spin.setValue(settings.max_parallel)
        self.hide_dotfiles_check.setChecked(settings.hide_dotfiles)
        idx = self.language_combo.findData(settings.language)
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)

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
            default_local_folder=existing.default_local_folder,
            last_local_folder=existing.last_local_folder,
            last_server_id=existing.last_server_id,
            last_remote_dirs=existing.last_remote_dirs,
            default_remote_dir=existing.default_remote_dir,
            default_server_id=existing.default_server_id,
            auto_connect=True,
            overwrite_policy=existing.overwrite_policy,
            command_template=existing.command_template,
            max_parallel=self.max_parallel_spin.value(),
            batch_size=existing.batch_size,
            language=self.language_combo.currentData() or "en",
            column_widths=existing.column_widths or {},
            hide_dotfiles=self.hide_dotfiles_check.isChecked(),
            auto_refresh_enabled=existing.auto_refresh_enabled,
            auto_refresh_interval=existing.auto_refresh_interval,
            auto_download_enabled=existing.auto_download_enabled,
            notify_enabled=existing.notify_enabled,
            download_patterns=existing.download_patterns,
        )

    def _save_settings(self):
        settings = self._settings_from_controls()
        path = self._store.save(settings)
        self._language = settings.language
        self.apply_language(self._language)
        self._log(f"Settings saved: {path}")
        self._status_cb(tr("Settings saved", self._language))
        self._load_paths()
        self.language_changed.emit(self._language)

    def _clear_run_profiles(self):
        path = RunProfileStore().path
        path.unlink(missing_ok=True)
        self._log(f"Run profiles cleared: {path}")
        self.refresh()

    def _populate_language_combo(self):
        current = self.language_combo.currentData() if self.language_combo.count() else self._language
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("\u4e2d\u6587", "zh")
        idx = self.language_combo.findData(current)
        self.language_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.language_combo.blockSignals(False)
