"""设置页 — Windows Terminal 风格卡片布局（白色主题）。"""

from __future__ import annotations

from PySide6.QtCore import Property, QPropertyAnimation, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...config.servers import get_default_servers_path, load_servers
from ...core.atomic_write import atomic_write_text
from ...services.gui_settings import GuiSettingsStore
from ..button_feedback import ButtonFeedback, ButtonRole
from ..design.components import StyledTableWidget
from ..i18n import tr
from ..session import ssh_session
from ..worker_utils import WorkerContext, start_context_worker
from .settings_servers_helpers import validate_server_id_change


class ToggleSwitch(QWidget):
    """滑动开关控件。"""
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._offset = 6.0 if not checked else 30.0
        self.setFixedSize(60, 32)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        self._checked = v
        self._offset = 30.0 if v else 6.0
        self.update()

    def _get_offset(self):
        return self._offset

    def _set_offset(self, v):
        self._offset = v
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    def mousePressEvent(self, e):
        self._checked = not self._checked
        anim = QPropertyAnimation(self, b"offset", self)
        anim.setDuration(120)
        anim.setStartValue(self._offset)
        anim.setEndValue(30.0 if self._checked else 6.0)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self.toggled.emit(self._checked)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track_color = QColor("#5c7fa6") if self._checked else QColor("#9aaec4")
        p.setBrush(track_color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 60, 32), 16, 16)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(self._offset, 5, 22, 22))
        p.end()


class SettingCard(QFrame):
    """Windows Terminal 风格卡片：圆角背景，标题+描述紧贴左侧，控件右侧。"""

    def __init__(self, title: str, description: str, control: QWidget):
        super().__init__()
        self.setObjectName("SettingCard")
        self.setStyleSheet(
            "#SettingCard { background: #dfe7f0; border: 1px solid #9aaec4; border-radius: 3px; }"
            " #SettingCard QLabel { background: transparent; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        self.setFixedHeight(60)

        lbl_title = QLabel(title)
        lbl_desc = QLabel(description)
        lbl_desc.setStyleSheet("color: #2f3b49; font-size: 14pt;")
        self.lbl_title = lbl_title
        self.lbl_desc = lbl_desc

        layout.addWidget(lbl_title)
        layout.addSpacing(16)
        layout.addWidget(lbl_desc)
        layout.addStretch()
        control.setMinimumWidth(160)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)


class SettingsServersPage(QWidget):
    language_changed = Signal(str)

    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._shutting_down = False
        self._store = GuiSettingsStore()
        self._language = self._store.load().language
        self._background_workers = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        # Page title
        self._page_title = QLabel(tr("Settings", self._language))
        self._page_title.setStyleSheet("font-size: 13pt; color: #111827; font-weight: 600;")
        layout.addWidget(self._page_title)
        layout.addSpacing(8)

        # ─── 本地目录 ───
        folder_ctrl = QWidget()
        fc_layout = QHBoxLayout(folder_ctrl)
        fc_layout.setContentsMargins(0, 0, 0, 0)
        self.local_folder_edit = QLineEdit()
        self.browse_btn = QPushButton(tr("Browse", self._language))
        self.browse_btn.clicked.connect(self._browse)
        fc_layout.addWidget(self.local_folder_edit, 1)
        fc_layout.addWidget(self.browse_btn)
        self._card_local = SettingCard(tr("Local Directory", self._language), tr("Default save path for downloaded results", self._language), folder_ctrl)
        layout.addWidget(self._card_local)

        editor_ctrl = QWidget()
        editor_layout = QHBoxLayout(editor_ctrl)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.text_editor_edit = QLineEdit()
        self.text_editor_browse_btn = QPushButton(tr("Browse", self._language))
        self.text_editor_browse_btn.clicked.connect(self._browse_text_editor)
        editor_layout.addWidget(self.text_editor_edit, 1)
        editor_layout.addWidget(self.text_editor_browse_btn)
        self._card_text_editor = SettingCard(
            tr("Text Editor", self._language),
            tr("Editor used to open files in Files page", self._language),
            editor_ctrl,
        )
        layout.addWidget(self._card_text_editor)

        # ─── 最大并发 ───
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setRange(1, 9999)
        self._card_parallel = SettingCard(tr("Max Parallel", self._language), tr("Maximum concurrent remote tasks", self._language), self.max_parallel_spin)
        layout.addWidget(self._card_parallel)

        # ─── 语言 ───
        self.language_combo = QComboBox()
        self.language_combo.addItem(tr("Chinese", self._language), "zh")
        self.language_combo.addItem("English", "en")
        self._card_language = SettingCard(tr("Language", self._language), tr("UI language, takes effect immediately", self._language), self.language_combo)
        layout.addWidget(self._card_language)

        # ─── 隐藏.文件 ───
        self.hide_dotfiles_cb = ToggleSwitch()
        toggle_ctrl = QWidget()
        toggle_ctrl.setStyleSheet("background: transparent;")
        toggle_layout = QHBoxLayout(toggle_ctrl)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(20)
        self._toggle_label = QLabel(tr("On", self._language) if self.hide_dotfiles_cb.isChecked() else tr("Off", self._language))
        toggle_layout.addStretch()
        toggle_layout.addWidget(self._toggle_label)
        toggle_layout.addWidget(self.hide_dotfiles_cb)
        self.hide_dotfiles_cb.toggled.connect(
            lambda v: self._toggle_label.setText(tr("On", self._language) if v else tr("Off", self._language))
        )
        self._card_dotfiles = SettingCard(tr("Hide Dotfiles", self._language), tr("Hide files starting with . in remote listing", self._language), toggle_ctrl)
        layout.addWidget(self._card_dotfiles)

        # ─── 服务器配置 ───
        layout.addSpacing(12)
        self._srv_title = QLabel(tr("Server Profiles", self._language))
        self._srv_title.setStyleSheet("font-size: 13pt; color: #111827; font-weight: 600;")
        layout.addWidget(self._srv_title)
        layout.addSpacing(4)

        srv_card = QFrame()
        srv_card.setObjectName("SettingCard")
        srv_card.setStyleSheet(
            "#SettingCard { background: #dfe7f0; border: 1px solid #9aaec4; border-radius: 3px; }"
        )
        srv_inner = QVBoxLayout(srv_card)
        srv_inner.setContentsMargins(16, 12, 16, 12)
        srv_inner.setSpacing(8)

        self.server_table = StyledTableWidget()
        self.server_table.setColumnCount(5)
        self.server_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.server_table.verticalHeader().setVisible(False)
        self.server_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.server_table.setHorizontalHeaderLabels(["ID", tr("Host", self._language), tr("Port", self._language), tr("User", self._language), tr("Status", self._language)])
        self.server_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.server_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.server_table.bind_column_widths("settings.servers", [120, 260, 80, 180, 120])
        srv_inner.addWidget(self.server_table)

        srv_btns = QHBoxLayout()
        self.test_btn = QPushButton(tr("Test Connection", self._language))
        self.test_btn.clicked.connect(self._test_connection)
        self.edit_yaml_btn = QPushButton(tr("Add", self._language))
        self.edit_yaml_btn.clicked.connect(self._add_server)
        self.edit_srv_btn = QPushButton(tr("Edit", self._language))
        self.edit_srv_btn.clicked.connect(self._edit_server)
        self.delete_srv_btn = QPushButton(tr("Delete", self._language))
        self.delete_srv_btn.clicked.connect(self._delete_server)
        srv_btns.addWidget(self.test_btn)
        srv_btns.addWidget(self.edit_yaml_btn)
        srv_btns.addWidget(self.edit_srv_btn)
        srv_btns.addWidget(self.delete_srv_btn)
        srv_btns.addStretch()
        srv_inner.addLayout(srv_btns)

        layout.addWidget(srv_card)

        # ─── 软件配置 ───
        layout.addSpacing(12)
        dl_header = QHBoxLayout()
        self._dl_title = QLabel(tr("Software Profiles", self._language))
        self._dl_title.setStyleSheet("font-size: 13pt; color: #111827; font-weight: 600;")
        dl_header.addWidget(self._dl_title)
        self._dl_desc = QLabel(tr("{name}=filename, {basename}=name without extension", self._language))
        self._dl_desc.setStyleSheet("color: #2f3b49; font-size: 14pt;")
        dl_header.addWidget(self._dl_desc)
        dl_header.addStretch()
        layout.addLayout(dl_header)
        layout.addSpacing(4)

        self.profile_table = StyledTableWidget()
        self.profile_table.setColumnCount(4)
        self.profile_table.setHorizontalHeaderLabels([tr("Name", self._language), tr("Input Ext", self._language), tr("Command", self._language), tr("Output Ext", self._language)])
        self.profile_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.profile_table.horizontalHeader().setStretchLastSection(False)
        self.profile_table.horizontalHeader().resizeSection(0, 120)
        self.profile_table.horizontalHeader().resizeSection(1, 140)
        self.profile_table.horizontalHeader().resizeSection(2, 300)
        self.profile_table.horizontalHeader().resizeSection(3, 100)
        self.profile_table.verticalHeader().setVisible(False)
        self.profile_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.profile_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.profile_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.profile_table.bind_column_widths("software_profiles", [120, 140, 300, 100])
        profile_btns = QHBoxLayout()
        self._add_profile_btn = QPushButton(tr("Add", self._language))
        self._add_profile_btn.clicked.connect(self._add_profile_row)
        self._del_profile_btn = QPushButton(tr("Delete", self._language))
        self._del_profile_btn.clicked.connect(self._del_profile_row)
        profile_btns.addWidget(self._add_profile_btn)
        profile_btns.addWidget(self._del_profile_btn)
        profile_btns.addStretch()

        profile_card = QFrame()
        profile_card.setObjectName("ProfileCard")
        profile_card.setStyleSheet(
            "#ProfileCard { background: #dfe7f0; border: 1px solid #9aaec4; border-radius: 3px; }"
        )
        profile_card_inner = QVBoxLayout(profile_card)
        profile_card_inner.setContentsMargins(16, 12, 16, 12)
        profile_card_inner.setSpacing(8)
        profile_card_inner.addWidget(self.profile_table)
        profile_card_inner.addLayout(profile_btns)
        self._confflow_note = QLabel(
            tr("ConfFlow downloads are managed from declared task outputs; "
               "shown patterns describe the default artifacts.", self._language)
        )
        self._confflow_note.setStyleSheet("color: #2f3b49; font-size: 14pt; padding: 4px 0;")
        self._confflow_note.setWordWrap(True)
        profile_card_inner.addWidget(self._confflow_note)
        layout.addWidget(profile_card)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ─── 底部按钮栏（固定） ───
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet("border-top: 1px solid #9aaec4;")
        bar_layout = QHBoxLayout(bottom_bar)
        bar_layout.setContentsMargins(24, 10, 24, 10)
        bar_layout.addStretch()
        self.save_btn = QPushButton(tr("Save Settings", self._language))
        self.save_btn.clicked.connect(self._save_settings)
        self.discard_btn = QPushButton(tr("Discard", self._language))
        self.discard_btn.clicked.connect(lambda: self._load_settings(show_discard_feedback=True))
        bar_layout.addWidget(self.save_btn)
        bar_layout.addWidget(self.discard_btn)
        root.addWidget(bottom_bar)

        self._browse_feedback = ButtonFeedback(self.browse_btn, ButtonRole.INSTANT_ACTION)
        self._text_editor_browse_feedback = ButtonFeedback(
            self.text_editor_browse_btn,
            ButtonRole.INSTANT_ACTION,
        )
        self._test_feedback = ButtonFeedback(self.test_btn, ButtonRole.TEST_ACTION)
        self._add_server_feedback = ButtonFeedback(self.edit_yaml_btn, ButtonRole.PRIMARY_ACTION)
        self._edit_server_feedback = ButtonFeedback(self.edit_srv_btn, ButtonRole.PRIMARY_ACTION)
        self._delete_server_feedback = ButtonFeedback(self.delete_srv_btn, ButtonRole.DANGER_ACTION)
        self._add_profile_feedback = ButtonFeedback(self._add_profile_btn, ButtonRole.PRIMARY_ACTION)
        self._delete_profile_feedback = ButtonFeedback(self._del_profile_btn, ButtonRole.DANGER_ACTION)
        self._save_feedback = ButtonFeedback(self.save_btn, ButtonRole.SETTINGS_ACTION)
        self._discard_feedback = ButtonFeedback(self.discard_btn, ButtonRole.SETTINGS_ACTION)

        self._load_servers()
        self._load_settings()

    def on_activated(self):
        self._language = self._store.load().language
        self._load_servers()
        self._load_settings()

    def apply_language(self, language: str):
        self._language = language
        # Page and section titles
        self._page_title.setText(tr("Settings", language))
        self._dl_title.setText(tr("Software Profiles", language))
        self._dl_desc.setText(tr("{name}=filename, {basename}=name without extension", language))
        self._confflow_note.setText(
            tr("ConfFlow downloads are managed from declared task outputs; "
               "shown patterns describe the default artifacts.", language)
        )
        self._srv_title.setText(tr("Server Profiles", language))
        # Setting cards
        self._card_local.lbl_title.setText(tr("Local Directory", language))
        self._card_local.lbl_desc.setText(tr("Default save path for downloaded results", language))
        self._card_text_editor.lbl_title.setText(tr("Text Editor", language))
        self._card_text_editor.lbl_desc.setText(tr("Editor used to open files in Files page", language))
        self._card_parallel.lbl_title.setText(tr("Max Parallel", language))
        self._card_parallel.lbl_desc.setText(tr("Maximum concurrent remote tasks", language))
        self._card_language.lbl_title.setText(tr("Language", language))
        self._card_language.lbl_desc.setText(tr("UI language, takes effect immediately", language))
        self._card_dotfiles.lbl_title.setText(tr("Hide Dotfiles", language))
        self._card_dotfiles.lbl_desc.setText(tr("Hide files starting with . in remote listing", language))
        # Buttons
        self.browse_btn.setText(tr("Browse", language))
        self.text_editor_browse_btn.setText(tr("Browse", language))
        self._add_profile_btn.setText(tr("Add", language))
        self._del_profile_btn.setText(tr("Delete", language))
        self.test_btn.setText(tr("Test Connection", language))
        self.edit_yaml_btn.setText(tr("Add", language))
        self.edit_srv_btn.setText(tr("Edit", language))
        self.delete_srv_btn.setText(tr("Delete", language))
        self.save_btn.setText(tr("Save Settings", language))
        self.discard_btn.setText(tr("Discard", language))
        self._browse_feedback.set_idle_text(tr("Browse", language))
        self._text_editor_browse_feedback.set_idle_text(tr("Browse", language))
        self._add_profile_feedback.set_idle_text(tr("Add", language))
        self._delete_profile_feedback.set_idle_text(tr("Delete", language))
        self._test_feedback.set_idle_text(tr("Test Connection", language))
        self._add_server_feedback.set_idle_text(tr("Add", language))
        self._edit_server_feedback.set_idle_text(tr("Edit", language))
        self._delete_server_feedback.set_idle_text(tr("Delete", language))
        self._save_feedback.set_idle_text(tr("Save Settings", language))
        self._discard_feedback.set_idle_text(tr("Discard", language))
        self._toggle_label.setText(tr("On", language) if self.hide_dotfiles_cb.isChecked() else tr("Off", language))
        # Table headers
        self.profile_table.setHorizontalHeaderLabels([
            tr("Name", language), tr("Input Ext", language), tr("Command", language), tr("Output Ext", language)
        ])
        self.server_table.setHorizontalHeaderLabels([
            "ID", tr("Host", language), tr("Port", language), tr("User", language), tr("Status", language)
        ])

    def _load_servers(self):
        try:
            cfg = load_servers()
            servers = cfg.servers
        except Exception as e:
            self.server_table.setRowCount(1)
            self.server_table.setItem(0, 0, QTableWidgetItem(str(e)))
            return
        self.server_table.setRowCount(len(servers))
        for r, (sid, srv) in enumerate(sorted(servers.items())):
            self.server_table.setItem(r, 0, QTableWidgetItem(sid))
            self.server_table.setItem(r, 1, QTableWidgetItem(srv.host))
            self.server_table.setItem(r, 2, QTableWidgetItem(str(srv.port)))
            self.server_table.setItem(r, 3, QTableWidgetItem(srv.username))
            self.server_table.setItem(r, 4, QTableWidgetItem(""))
        self._fit_table_height(self.server_table)

    def _test_connection(self):
        try:
            cfg = load_servers()
        except Exception:
            return
        if not cfg.servers:
            return
        self._test_feedback.pending(tr("Testing...", self._language))
        for row in range(self.server_table.rowCount()):
            self.server_table.setItem(row, 4, QTableWidgetItem(tr("Testing...", self._language)))

        servers_list = sorted(cfg.servers.items())
        failed = False

        def _run(ctx: WorkerContext):
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _test_one(sid, srv):
                try:
                    with ssh_session(srv) as ssh:
                        ok = ssh.test_connection()
                    return sid, "connected" if ok else "no-response"
                except Exception as e:
                    return sid, f"{tr('Error:', self._language)} {e}"

            with ThreadPoolExecutor(max_workers=len(servers_list)) as pool:
                futures = {pool.submit(_test_one, sid, srv): sid for sid, srv in servers_list}
                for f in as_completed(futures):
                    sid, status = f.result()
                    ctx.emit_log(f"{sid}\t{status}")
            return {}

        def _on_log(msg):
            sid, status = msg.split("\t", 1)
            for row in range(self.server_table.rowCount()):
                if self.server_table.item(row, 0).text() == sid:
                    self.server_table.setItem(row, 4, QTableWidgetItem(status))
                    break

        def _on_error(e):
            nonlocal failed
            failed = True
            self._test_feedback.error(tr("Test failed", self._language))
            self._status_cb(f"{tr('Test failed:', self._language)} {e}")

        def _on_finished():
            if failed:
                return
            self._test_feedback.success(tr("Tested", self._language))

        self._worker = start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_log=_on_log,
            on_error=_on_error,
            on_finished=_on_finished,
        )

    @staticmethod
    def _fit_table_height(table):
        """Set table fixed height to show all rows without internal scrolling."""
        h = table.horizontalHeader().height() + 2
        for i in range(table.rowCount()):
            h += table.rowHeight(i)
        table.setFixedHeight(h)

    def _load_settings(self, *, show_discard_feedback: bool = False):
        s = self._store.load()
        self.local_folder_edit.setText(s.default_local_folder)
        self.text_editor_edit.setText(s.text_editor_path)
        self.max_parallel_spin.setValue(s.max_parallel)
        idx = self.language_combo.findData(s.language)
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        self.hide_dotfiles_cb.setChecked(s.hide_dotfiles)
        self._toggle_label.setText(tr("On", self._language) if s.hide_dotfiles else tr("Off", self._language))
        # Load software profiles into table
        profiles = s.software_profiles or {}
        self.profile_table.setRowCount(len(profiles))
        for row, (name, p) in enumerate(profiles.items()):
            self.profile_table.setItem(row, 0, QTableWidgetItem(name))
            self.profile_table.setItem(row, 1, QTableWidgetItem(p.get("input_extensions", "")))
            self.profile_table.setItem(row, 2, QTableWidgetItem(p.get("command_template", "")))
            self.profile_table.setItem(row, 3, QTableWidgetItem(p.get("download_patterns", "")))
        self._fit_table_height(self.profile_table)
        if show_discard_feedback and hasattr(self, "_discard_feedback"):
            self._discard_feedback.success(tr("Discarded", self._language))

    def _save_settings(self):
        from dataclasses import replace
        self._save_feedback.pending(tr("Saving...", self._language))
        try:
            existing = self._store.load()
            # Read profiles from table
            profiles = {}
            for row in range(self.profile_table.rowCount()):
                name = (self.profile_table.item(row, 0) or QTableWidgetItem("")).text().strip()
                if not name:
                    continue
                profiles[name] = {
                    "input_extensions": (self.profile_table.item(row, 1) or QTableWidgetItem("")).text().strip(),
                    "command_template": (self.profile_table.item(row, 2) or QTableWidgetItem("")).text().strip(),
                    "download_patterns": (self.profile_table.item(row, 3) or QTableWidgetItem("")).text().strip(),
                }
            new_settings = replace(
                existing,
                default_local_folder=self.local_folder_edit.text().strip(),
                text_editor_path=self.text_editor_edit.text().strip() or "notepad.exe",
                max_parallel=self.max_parallel_spin.value(),
                language=self.language_combo.currentData() or "zh",
                hide_dotfiles=self.hide_dotfiles_cb.isChecked(),
                software_profiles=profiles,
            )
            self._store.save(new_settings)
        except Exception:
            self._save_feedback.error(tr("Save failed", self._language))
            raise
        self._save_feedback.success(tr("Saved", self._language))
        self._status_cb(tr("Settings saved", self._language))
        if new_settings.language != existing.language:
            self.language_changed.emit(new_settings.language)

    def _add_profile_row(self):
        row = self.profile_table.rowCount()
        self.profile_table.insertRow(row)
        self._fit_table_height(self.profile_table)

    def _del_profile_row(self):
        row = self.profile_table.currentRow()
        if row >= 0:
            self.profile_table.removeRow(row)
            self._fit_table_height(self.profile_table)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, tr("Select local directory", self._language), self.local_folder_edit.text())
        if path:
            self.local_folder_edit.setText(path)

    def _browse_text_editor(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("Select text editor", self._language),
            self.text_editor_edit.text(),
            "Applications (*.exe);;All files (*)",
        )
        if path:
            self.text_editor_edit.setText(path)

    def _delete_server(self):
        import yaml
        row = self.server_table.currentRow()
        if row < 0:
            self._status_cb(tr("Select a server first", self._language))
            return
        sid = self.server_table.item(row, 0).text()
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, tr("Delete Server", self._language), tr("Delete {sid}?", self._language, sid=sid)) != QMessageBox.Yes:
            return
        path = get_default_servers_path()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        servers = data.get("servers", {})
        servers.pop(sid, None)
        atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        self._load_servers()

    def _add_scheduler_fields(self, form, dlg, sched: dict) -> dict:
        """Add scheduler resource widgets to a server dialog form; return widget dict."""
        from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox
        type_combo = QComboBox()
        type_combo.addItems(["nohup", "slurm", "pbs"])
        ti = type_combo.findText(str(sched.get("type", "nohup")))
        if ti >= 0:
            type_combo.setCurrentIndex(ti)
        cpus = QSpinBox()
        cpus.setRange(1, 4096)
        cpus.setValue(int(sched.get("default_cpus", 1)))
        mem = QSpinBox()
        mem.setRange(128, 4194304)
        mem.setValue(int(sched.get("default_memory_mb", 2048)))
        wall = QSpinBox()
        wall.setRange(1, 1051200)
        wall.setValue(int(sched.get("default_walltime_minutes", 1440)))
        partition = QLineEdit(str(sched.get("default_partition", "")))
        account = QLineEdit(str(sched.get("default_account", "")))
        widgets = {"type": type_combo, "cpus": cpus, "mem": mem, "wall": wall,
                   "partition": partition, "account": account}

        def _toggle(*_):
            hpc = type_combo.currentText() != "nohup"
            for w in (partition, account, wall):
                w.setEnabled(hpc)
        type_combo.currentTextChanged.connect(_toggle)
        _toggle()

        form.addRow(tr("Scheduler:", self._language), type_combo)
        form.addRow("CPUs:", cpus)
        form.addRow(tr("Memory(MB):", self._language), mem)
        form.addRow(tr("Walltime:", self._language), wall)
        form.addRow(tr("Partition/Queue:", self._language), partition)
        form.addRow(tr("Account:", self._language), account)
        return widgets

    @staticmethod
    def _scheduler_dict(widgets: dict, existing: dict | None = None) -> dict:
        """Read scheduler widgets into a config dict, preserving hidden keys (gpus, extra_directives)."""
        result = dict(existing or {})
        result.update({
            "type": widgets["type"].currentText(),
            "default_cpus": widgets["cpus"].value(),
            "default_memory_mb": widgets["mem"].value(),
            "default_walltime_minutes": widgets["wall"].value(),
            "default_partition": widgets["partition"].text().strip(),
            "default_account": widgets["account"].text().strip(),
        })
        return result

    def _add_external_tools_fields(self, form, tools: dict) -> dict:
        provider = QComboBox()
        provider.addItems(["windows_terminal", "putty"])
        current = str(tools.get("terminal_provider", "windows_terminal"))
        idx = provider.findText(current)
        if idx >= 0:
            provider.setCurrentIndex(idx)
        ssh_alias = QLineEdit(str(tools.get("ssh_alias", "")))
        ssh_alias.setPlaceholderText("OpenSSH alias")
        putty_session = QLineEdit(str(tools.get("putty_session", "")))
        putty_session.setPlaceholderText("PuTTY saved session")
        terminal_path = QLineEdit(str(tools.get("terminal_path", "")))
        terminal_path.setPlaceholderText("Path to terminal executable")
        form.addRow(tr("Terminal:", self._language), provider)
        form.addRow(tr("Terminal Path:", self._language), terminal_path)
        form.addRow(tr("SSH Alias:", self._language), ssh_alias)
        form.addRow(tr("PuTTY Session:", self._language), putty_session)
        return {
            "terminal_provider": provider,
            "ssh_alias": ssh_alias,
            "putty_session": putty_session,
            "terminal_path": terminal_path,
        }

    @staticmethod
    def _external_tools_dict(widgets: dict, existing: dict | None = None) -> dict:
        result = dict(existing or {})
        result.update({
            "terminal_provider": widgets["terminal_provider"].currentText(),
            "ssh_alias": widgets["ssh_alias"].text().strip(),
            "putty_session": widgets["putty_session"].text().strip(),
            "terminal_path": widgets["terminal_path"].text().strip(),
        })
        return result

    def _add_ssh_access_fields(self, form, access: dict) -> dict:
        config_alias = QLineEdit(str(access.get("config_alias", "")))
        config_alias.setPlaceholderText("OpenSSH config alias")
        proxy_command = QLineEdit(str(access.get("proxy_command", "")))
        proxy_command.setPlaceholderText("ssh -W %h:%p gateway")
        proxy_jump = QLineEdit(str(access.get("proxy_jump", "")))
        proxy_jump.setPlaceholderText("gateway")
        form.addRow(tr("SSH Config Alias:", self._language), config_alias)
        form.addRow(tr("ProxyCommand:", self._language), proxy_command)
        form.addRow(tr("ProxyJump:", self._language), proxy_jump)
        return {
            "config_alias": config_alias,
            "proxy_command": proxy_command,
            "proxy_jump": proxy_jump,
        }

    @staticmethod
    def _ssh_access_dict(widgets: dict, existing: dict | None = None) -> dict:
        result = dict(existing or {})
        result.update({
            "config_alias": widgets["config_alias"].text().strip(),
            "proxy_command": widgets["proxy_command"].text().strip(),
            "proxy_jump": widgets["proxy_jump"].text().strip(),
        })
        return result

    def _edit_server(self):
        import yaml
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout

        row = self.server_table.currentRow()
        if row < 0:
            self._status_cb(tr("Select a server first", self._language))
            return
        sid = self.server_table.item(row, 0).text()
        path = get_default_servers_path()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        srv = data.get("servers", {}).get(sid, {})

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{tr('Edit Server:', self._language)} {sid}")
        dlg.setMinimumWidth(400)
        form = QFormLayout(dlg)

        id_edit = QLineEdit(sid)
        host_edit = QLineEdit(srv.get("host", ""))
        port_edit = QSpinBox()
        port_edit.setRange(1, 65535)
        port_edit.setValue(srv.get("port", 22))
        user_edit = QLineEdit(srv.get("username", ""))
        auth_combo = QComboBox()
        auth_combo.addItems(["key"])
        idx = auth_combo.findText(srv.get("auth_method", "key"))
        if idx >= 0:
            auth_combo.setCurrentIndex(idx)
        key_edit = QLineEdit(srv.get("key_path", ""))
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(key_edit, 1)
        key_browse = QPushButton(" ... ")
        key_browse.clicked.connect(lambda: key_edit.setText(
            QFileDialog.getOpenFileName(dlg, tr("Select SSH Key", self._language),
                                        key_edit.text() or str(__import__('pathlib').Path.home() / ".ssh"))[0] or key_edit.text()))
        key_layout.addWidget(key_browse)
        tofu_toggle = ToggleSwitch(bool(srv.get("trust_on_first_use", False)))

        form.addRow("ID:", id_edit)
        form.addRow(tr("Host:", self._language), host_edit)
        form.addRow(tr("Port:", self._language), port_edit)
        form.addRow(tr("Username:", self._language), user_edit)
        form.addRow(tr("Auth:", self._language), auth_combo)
        form.addRow(tr("Key Path:", self._language), key_row)
        form.addRow("Trust unknown host key on first connection:", tofu_toggle)
        sched_widgets = self._add_scheduler_fields(form, dlg, srv.get("scheduler", {}) or {})
        external_widgets = self._add_external_tools_fields(form, srv.get("external_tools", {}) or {})
        ssh_access_widgets = self._add_ssh_access_fields(form, srv.get("ssh_access", {}) or {})

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.Accepted:
            return
        new_sid = id_edit.text().strip()
        server_id_error = validate_server_id_change(set(data.get("servers", {})), old_id=sid, new_id=new_sid)
        if server_id_error:
            self._status_cb(server_id_error)
            QMessageBox.warning(self, tr("Edit Server:", self._language), server_id_error)
            return
        if new_sid != sid:
            data["servers"].pop(sid, None)
        # Preserve existing keys not shown in dialog (e.g. env_init_scripts)
        existing = srv.copy()
        existing.update({
            "host": host_edit.text().strip(),
            "port": port_edit.value(),
            "username": user_edit.text().strip(),
            "auth_method": auth_combo.currentText(),
            "trust_on_first_use": tofu_toggle.isChecked(),
        })
        existing["scheduler"] = self._scheduler_dict(sched_widgets, srv.get("scheduler", {}) or {})
        existing["external_tools"] = self._external_tools_dict(
            external_widgets,
            srv.get("external_tools", {}) or {},
        )
        existing["ssh_access"] = self._ssh_access_dict(
            ssh_access_widgets,
            srv.get("ssh_access", {}) or {},
        )
        if key_edit.text().strip():
            existing["key_path"] = key_edit.text().strip()
        elif "key_path" in existing and not key_edit.text().strip():
            existing.pop("key_path", None)
        data["servers"][new_sid] = existing
        atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        self._load_servers()

    def _add_server(self):
        import yaml
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Add", self._language))
        dlg.setMinimumWidth(400)
        form = QFormLayout(dlg)

        id_edit = QLineEdit()
        id_edit.setPlaceholderText("如: myserver")
        host_edit = QLineEdit()
        host_edit.setPlaceholderText("如: 192.168.1.100")
        port_edit = QSpinBox()
        port_edit.setRange(1, 65535)
        port_edit.setValue(22)
        user_edit = QLineEdit()
        user_edit.setPlaceholderText("如: root")
        auth_combo = QComboBox()
        auth_combo.addItems(["key"])
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("~/.ssh/id_ed25519")
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(key_edit, 1)
        key_browse = QPushButton(" ... ")
        key_browse.clicked.connect(lambda: key_edit.setText(
            QFileDialog.getOpenFileName(dlg, tr("Select SSH Key", self._language),
                                        key_edit.text() or str(__import__('pathlib').Path.home() / ".ssh"))[0] or key_edit.text()))
        key_layout.addWidget(key_browse)
        tofu_toggle = ToggleSwitch(False)

        form.addRow("ID:", id_edit)
        form.addRow(tr("Host:", self._language), host_edit)
        form.addRow(tr("Port:", self._language), port_edit)
        form.addRow(tr("Username:", self._language), user_edit)
        form.addRow(tr("Auth:", self._language), auth_combo)
        form.addRow(tr("Key Path:", self._language), key_row)
        form.addRow("Trust unknown host key on first connection:", tofu_toggle)
        sched_widgets = self._add_scheduler_fields(form, dlg, {})
        external_widgets = self._add_external_tools_fields(form, {})
        ssh_access_widgets = self._add_ssh_access_fields(form, {})

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        form.addRow(btns)

        if dlg.exec() != QDialog.Accepted:
            return
        sid = id_edit.text().strip()
        host = host_edit.text().strip()
        user = user_edit.text().strip()
        if not sid or not host or not user:
            return

        path = get_default_servers_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        servers = data.setdefault("servers", {})
        server_id_error = validate_server_id_change(set(servers), old_id=None, new_id=sid)
        if server_id_error:
            self._status_cb(server_id_error)
            QMessageBox.warning(self, tr("Add", self._language), server_id_error)
            return
        servers[sid] = {
            "host": host,
            "port": port_edit.value(),
            "username": user,
            "auth_method": auth_combo.currentText(),
            "trust_on_first_use": tofu_toggle.isChecked(),
            "scheduler": self._scheduler_dict(sched_widgets),
            "external_tools": self._external_tools_dict(external_widgets),
            "ssh_access": self._ssh_access_dict(ssh_access_widgets),
        }
        if key_edit.text().strip():
            servers[sid]["key_path"] = key_edit.text().strip()
        atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        self._load_servers()

    def shutdown(self):
        self._shutting_down = True
        for worker in list(getattr(self, "_background_workers", [])):
            if hasattr(worker, "stop_safely"):
                worker.stop_safely(3000)
        w = getattr(self, "_worker", None)
        if w and hasattr(w, "stop_safely"):
            w.stop_safely(3000)
        elif w and w.isRunning():
            w.quit()
            w.wait(3000)
