from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from PySide6.QtCore import Qt, Signal
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
from ..design.components import SettingCard, StyledTableWidget, ToggleSwitch
from ..design.tokens import Colors, Metrics, Radius
from ..i18n import tr
from ..session import ssh_session
from ..theme import help_text, section_title_label
from ..widgets import EmptyStateHint
from ..worker_utils import WorkerContext, start_context_worker
from .settings_servers_helpers import (
    build_external_tools_fields,
    build_scheduler_fields,
    build_ssh_access_fields,
    external_tools_dict,
    scheduler_dict,
    ssh_access_dict,
    validate_server_id_change,
)

SERVER_TEST_TIMEOUT_SECONDS = 20.0


def _test_server_connections(
    servers_list,
    *,
    language: str,
    emit_log,
    tester=None,
    timeout_seconds: float = SERVER_TEST_TIMEOUT_SECONDS,
    poll_seconds: float = 0.1,
) -> None:
    def _default_tester(_sid, srv):
        with ssh_session(srv) as ssh:
            ok = ssh.test_connection()
        return "connected" if ok else "no-response"

    test_one = tester or _default_tester
    pool = ThreadPoolExecutor(max_workers=max(1, len(servers_list)))
    futures = {}
    try:
        for sid, srv in servers_list:
            futures[pool.submit(test_one, sid, srv)] = sid

        pending = set(futures)
        deadline = time.monotonic() + timeout_seconds
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = wait(
                pending,
                timeout=min(poll_seconds, remaining),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                sid = futures[future]
                try:
                    status = future.result()
                except Exception as exc:
                    status = f"{tr('Error:', language)} {exc}"
                emit_log(f"{sid}\t{status}")

        for future in pending:
            sid = futures[future]
            future.cancel()
            emit_log(f"{sid}\t{tr('Error:', language)} timed out after {timeout_seconds:g}s")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


class SettingsServersPage(QWidget):
    language_changed = Signal(str)

    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._shutting_down = False
        self._connection_test_running = False
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
        # Phase 18 visual cleanup: standardise the page padding with
        # the other three pages (24 px horizontal, 20 px vertical, 12
        # px section spacing). The previous (24, 20, 24, 20) left the
        # cards' titles butting up against the sidebar.
        layout.setContentsMargins(
            Metrics.PAGE_PADDING,
            Metrics.PAGE_PADDING - 4,
            Metrics.PAGE_PADDING,
            Metrics.PAGE_PADDING - 4,
        )
        layout.setSpacing(12)

        # Page title (using the shared ``page_title_label`` helper from
        # ``theme.py`` so it picks up the new PAGE_TITLE_FONT_PX token).
        from ..theme import page_title_label

        self._page_title = page_title_label(tr("Settings", self._language))
        layout.addWidget(self._page_title)
        layout.addSpacing(8)

        # -- Phase 2.1: empty-state hint for "no servers" --
        # Visible whenever the server table is empty. Lives above the
        # server profiles card so a first-time user is greeted by it
        # before scrolling through the other settings.
        self._empty_hint = EmptyStateHint(
            title_key="Add a server to get started",
            body_key=(
                "JobDesk uses SSH to talk to your Linux compute server. "
                "You need host, port, username, and an auth method."
            ),
            action_texts=(
                ("add_server", "+ Add server"),
                ("copy_sample", "Copy sample YAML"),
            ),
            language=self._language,
            parent=self,
        )
        self._empty_hint.action_requested.connect(self._on_empty_action)
        self._empty_hint.setVisible(False)
        layout.addWidget(self._empty_hint)

        # ─── 本地目录 ───
        folder_ctrl = QWidget()
        fc_layout = QHBoxLayout(folder_ctrl)
        fc_layout.setContentsMargins(0, 0, 0, 0)
        self.local_folder_edit = QLineEdit()
        self.browse_btn = QPushButton(tr("Browse", self._language))
        self.browse_btn.clicked.connect(self._browse)
        fc_layout.addWidget(self.local_folder_edit, 1)
        fc_layout.addWidget(self.browse_btn)
        self._card_local = SettingCard(
            tr("Local Directory", self._language),
            tr("Default save path for downloaded results", self._language),
            folder_ctrl,
        )
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
        self._card_parallel = SettingCard(
            tr("Max Parallel", self._language),
            tr("Maximum concurrent remote tasks", self._language),
            self.max_parallel_spin,
        )
        layout.addWidget(self._card_parallel)

        # ─── 语言 ───
        self.language_combo = QComboBox()
        self.language_combo.addItem(tr("Chinese", self._language), "zh")
        self.language_combo.addItem("English", "en")
        self._card_language = SettingCard(
            tr("Language", self._language),
            tr("UI language, takes effect immediately", self._language),
            self.language_combo,
        )
        layout.addWidget(self._card_language)

        # ─── 隐藏.文件 ───
        self.hide_dotfiles_cb = ToggleSwitch()
        toggle_ctrl = QWidget()
        toggle_ctrl.setStyleSheet("background: transparent;")
        toggle_layout = QHBoxLayout(toggle_ctrl)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(20)
        self._toggle_label = QLabel(
            tr("On", self._language) if self.hide_dotfiles_cb.isChecked() else tr("Off", self._language)
        )
        toggle_layout.addStretch()
        toggle_layout.addWidget(self._toggle_label)
        toggle_layout.addWidget(self.hide_dotfiles_cb)
        self.hide_dotfiles_cb.toggled.connect(
            lambda v: self._toggle_label.setText(tr("On", self._language) if v else tr("Off", self._language))
        )
        self._card_dotfiles = SettingCard(
            tr("Hide Dotfiles", self._language),
            tr("Hide files starting with . in remote listing", self._language),
            toggle_ctrl,
        )
        layout.addWidget(self._card_dotfiles)

        # ─── 服务器配置 ───
        layout.addSpacing(12)
        # Phase 18 visual cleanup: sub-section header now uses the
        # shared ``section_title_label`` helper (15 px / 600) instead
        # of a 24 px PageTitle-style label that competed with the
        # page-level "Settings" title for visual weight.
        self._srv_title = section_title_label(tr("Server Profiles", self._language))
        layout.addWidget(self._srv_title)
        layout.addSpacing(4)

        srv_card = QFrame()
        srv_card.setObjectName("SettingCard")
        srv_card.setStyleSheet(
            f"#SettingCard {{ background: {Colors.CARD_BG}; border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }}"
        )
        # Phase 18 visual cleanup: the inner QFrame previously used
        # (16, 12, 16, 12) margins which double-padded the card and
        # gave the server table a visible "frame inside a frame"
        # effect. Using zero margins lets the card border sit flush
        # against the table — the table's own gridlines provide the
        # separation.
        srv_inner = QVBoxLayout(srv_card)
        srv_inner.setContentsMargins(0, 0, 0, 0)
        srv_inner.setSpacing(8)

        self.server_table = StyledTableWidget()
        self.server_table.setColumnCount(5)
        self.server_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.server_table.verticalHeader().setVisible(False)
        self.server_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.server_table.setHorizontalHeaderLabels(
            [
                "ID",
                tr("Host", self._language),
                tr("Port", self._language),
                tr("User", self._language),
                tr("Status", self._language),
            ]
        )
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
        dl_header.setSpacing(8)
        # Phase 18 visual cleanup: sub-section header uses the shared
        # ``section_title_label`` helper so its visual weight matches
        # the rest of the page.
        self._dl_title = section_title_label(tr("Software Profiles", self._language))
        dl_header.addWidget(self._dl_title)
        self._dl_desc = help_text(tr("{name}=filename, {basename}=name without extension", self._language))
        self._dl_desc.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.HELP_TEXT_FONT_PX}px;")
        dl_header.addWidget(self._dl_desc)
        dl_header.addStretch()
        layout.addLayout(dl_header)
        layout.addSpacing(4)

        self.profile_table = StyledTableWidget()
        self.profile_table.setColumnCount(4)
        self.profile_table.setHorizontalHeaderLabels(
            [
                tr("Name", self._language),
                tr("Input Ext", self._language),
                tr("Command", self._language),
                tr("Output Ext", self._language),
            ]
        )
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
            f"#ProfileCard {{ background: {Colors.CARD_BG}; border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }}"
        )
        profile_card_inner = QVBoxLayout(profile_card)
        # Phase 18 visual cleanup: see the server-card note above.
        profile_card_inner.setContentsMargins(0, 0, 0, 0)
        profile_card_inner.setSpacing(8)
        profile_card_inner.addWidget(self.profile_table)
        profile_card_inner.addLayout(profile_btns)
        self._confflow_note = help_text(
            tr(
                "ConfFlow downloads are managed from declared task outputs; "
                "shown patterns describe the default artifacts.",
                self._language,
            )
        )
        self._confflow_note.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.HELP_TEXT_FONT_PX}px;")
        profile_card_inner.addWidget(self._confflow_note)
        layout.addWidget(profile_card)

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ─── 底部按钮栏（固定） ───
        # Phase 18 visual cleanup: the bottom bar previously used a
        # hand-picked ``#9aaec4`` border colour which did not match
        # the card borders above (which use ``Colors.BORDER``). The
        # desync read as a hard seam between the cards and the footer;
        # using the same border token makes the seam disappear.
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet(f"border-top: 1px solid {Colors.BORDER};")
        bar_layout = QHBoxLayout(bottom_bar)
        bar_layout.setContentsMargins(24, 10, 24, 10)
        bar_layout.setSpacing(8)
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
            tr(
                "ConfFlow downloads are managed from declared task outputs; "
                "shown patterns describe the default artifacts.",
                language,
            )
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
        self.profile_table.setHorizontalHeaderLabels(
            [tr("Name", language), tr("Input Ext", language), tr("Command", language), tr("Output Ext", language)]
        )
        self.server_table.setHorizontalHeaderLabels(
            ["ID", tr("Host", language), tr("Port", language), tr("User", language), tr("Status", language)]
        )
        # Phase 2.1: retranslate the empty-state hint alongside the rest.
        self._empty_hint.apply_language(language)

    def _load_servers(self):
        try:
            cfg = load_servers()
            servers = cfg.servers
        except FileNotFoundError:
            # ``servers.yaml`` is opt-in: a brand-new install has not
            # created one yet. Treat that as the "no servers" empty state
            # so the user sees the Phase 2.1 onboarding card instead of a
            # raw FileNotFoundError string in the table.
            servers = {}
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
        # Phase 2.1: toggle the empty-state hint based on the freshly
        # loaded server count. The hint lives in the layout above the
        # server profiles card so users see it before the table.
        self._empty_hint.setVisible(not servers)

    def _on_empty_action(self, action_id: str) -> None:
        """Route the Settings-page empty-state buttons.

        "add_server" delegates to the existing "Add" button slot —
        which is the same code-path the toolbar uses to launch the
        server dialog — so no logic is duplicated. "copy_sample"
        drops a small, copy-paste-ready YAML snippet onto the system
        clipboard so the user can paste it directly into servers.yaml.
        """
        if action_id == "add_server":
            self._add_server()
            return
        if action_id == "copy_sample":
            sample = (
                "servers:\n"
                "  my_linux_box:\n"
                "    host: my-linux.example.edu\n"
                "    port: 22\n"
                "    username: myuser\n"
                "    auth_method: key\n"
                "    key_path: ~/.ssh/id_ed25519\n"
                "    env_init_scripts:\n"
                "      - /opt/g16/bsd/g16.profile\n"
            )
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(sample)
            self._status_cb(tr("Sample YAML copied to clipboard", self._language))
            return

    def _test_connection(self):
        if self._connection_test_running:
            return
        try:
            cfg = load_servers()
        except Exception:
            return
        if not cfg.servers:
            return
        self._connection_test_running = True
        self._test_feedback.pending(tr("Testing...", self._language))
        for row in range(self.server_table.rowCount()):
            self.server_table.setItem(row, 4, QTableWidgetItem(tr("Testing...", self._language)))

        servers_list = sorted(cfg.servers.items())
        failed = False

        def _run(ctx: WorkerContext):
            _test_server_connections(
                servers_list,
                language=self._language,
                emit_log=ctx.emit_log,
            )
            return {}

        def _on_log(msg):
            sid, status = msg.split("\t", 1)
            for row in range(self.server_table.rowCount()):
                if self.server_table.item(row, 0).text() == sid:
                    self.server_table.setItem(row, 4, QTableWidgetItem(status))
                    break

        def _on_error(e):
            nonlocal failed
            self._connection_test_running = False
            failed = True
            self._test_feedback.error(tr("Test failed", self._language))
            self._status_cb(f"{tr('Test failed:', self._language)} {e}")

        def _on_finished():
            self._connection_test_running = False
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
        path = QFileDialog.getExistingDirectory(
            self, tr("Select local directory", self._language), self.local_folder_edit.text()
        )
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

        if (
            QMessageBox.question(
                self, tr("Delete Server", self._language), tr("Delete {sid}?", self._language, sid=sid)
            )
            != QMessageBox.Yes
        ):
            return
        path = get_default_servers_path()
        # ``servers.yaml`` is opt-in: a fresh install simply doesn't
        # have one. Treat "not present" as an idempotent no-op rather
        # than crashing the dialog (which is what the bare ``read_text``
        # does, via ``FileNotFoundError``).
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        else:
            data = {}
        servers = data.get("servers", {})
        servers.pop(sid, None)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        self._load_servers()

    def _edit_server(self):
        import yaml
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout

        row = self.server_table.currentRow()
        if row < 0:
            self._status_cb(tr("Select a server first", self._language))
            return
        sid = self.server_table.item(row, 0).text()
        path = get_default_servers_path()
        # Same fall-back as ``_delete_server``: an empty / missing
        # ``servers.yaml`` should still let the user edit metadata on
        # an existing in-memory row.
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
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
        else:
            auth_combo.setCurrentText("key")
        auth_combo.setToolTip(
            tr(
                "Key-based SSH authentication. Password auth is not supported.",
                self._language,
            )
        )
        key_edit = QLineEdit(srv.get("key_path", ""))
        # Phase 3.2: tooltips for the most-confusing fields. The dialog
        # labels themselves stay short; the hover-time tooltip explains
        # what to type and falls back to placeholder text first.
        host_edit.setToolTip(
            tr(
                "The hostname or IP address of the remote server. Examples: login.cluster.example.org or 10.0.0.42.",
                self._language,
            )
        )
        user_edit.setToolTip(
            tr(
                "Your SSH username on the remote server (the one you would type at the Password: prompt).",
                self._language,
            )
        )
        key_edit.setToolTip(
            tr(
                "Absolute path to your SSH private key. Use ~ for your home "
                "folder — e.g. ~/.ssh/id_ed25519. On Windows, the dialog "
                "viewer shows known keys under %USERPROFILE%\\.ssh\\.",
                self._language,
            )
        )
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(key_edit, 1)
        key_browse = QPushButton(" ... ")
        key_browse.clicked.connect(
            lambda: key_edit.setText(
                QFileDialog.getOpenFileName(
                    dlg,
                    tr("Select SSH Key", self._language),
                    key_edit.text() or str(__import__("pathlib").Path.home() / ".ssh"),
                )[0]
                or key_edit.text()
            )
        )
        key_layout.addWidget(key_browse)
        tofu_toggle = ToggleSwitch(bool(srv.get("trust_on_first_use", False)))

        form.addRow("ID:", id_edit)
        form.addRow(tr("Host:", self._language), host_edit)
        form.addRow(tr("Port:", self._language), port_edit)
        form.addRow(tr("Username:", self._language), user_edit)
        form.addRow(tr("Auth:", self._language), auth_combo)
        form.addRow(tr("Key Path:", self._language), key_row)
        form.addRow("Trust unknown host key on first connection:", tofu_toggle)
        sched_widgets = build_scheduler_fields(form, dlg, srv.get("scheduler", {}) or {}, self._language)
        external_widgets = build_external_tools_fields(form, srv.get("external_tools", {}) or {}, self._language)
        ssh_access_widgets = build_ssh_access_fields(form, srv.get("ssh_access", {}) or {}, self._language)

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
        existing.update(
            {
                "host": host_edit.text().strip(),
                "port": port_edit.value(),
                "username": user_edit.text().strip(),
                "auth_method": auth_combo.currentText(),
                "trust_on_first_use": tofu_toggle.isChecked(),
            }
        )
        existing["scheduler"] = scheduler_dict(sched_widgets, srv.get("scheduler", {}) or {})
        existing["external_tools"] = external_tools_dict(
            external_widgets,
            srv.get("external_tools", {}) or {},
        )
        existing["ssh_access"] = ssh_access_dict(
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
        id_edit.setPlaceholderText(tr("e.g. myserver", self._language))
        host_edit = QLineEdit()
        host_edit.setPlaceholderText(tr("e.g. 192.168.1.100", self._language))
        port_edit = QSpinBox()
        port_edit.setRange(1, 65535)
        port_edit.setValue(22)
        user_edit = QLineEdit()
        user_edit.setPlaceholderText(tr("e.g. root", self._language))
        # Phase 3.2: tooltips parallel to the edit dialog
        host_edit.setToolTip(host_edit.placeholderText())
        user_edit.setToolTip(user_edit.placeholderText())
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("~/.ssh/id_ed25519")
        key_edit.setToolTip(
            tr(
                "Absolute path to your SSH private key. Use ~ for your home "
                "folder — e.g. ~/.ssh/id_ed25519. On Windows, the dialog "
                "viewer shows known keys under %USERPROFILE%\\.ssh\\.",
                self._language,
            )
        )
        # -- Auth method combo --
        # JobDesk only supports key-based SSH auth today (ServerConfig
        # rejects password auth); the combo is built explicitly so the
        # default selection on a fresh dialog is "key" rather than blank.
        auth_combo = QComboBox()
        auth_combo.addItems(["key"])
        auth_combo.setCurrentText("key")
        auth_combo.setToolTip(
            tr(
                "Key-based SSH authentication. Password auth is not supported.",
                self._language,
            )
        )
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(key_edit, 1)
        key_browse = QPushButton(" ... ")
        key_browse.clicked.connect(
            lambda: key_edit.setText(
                QFileDialog.getOpenFileName(
                    dlg,
                    tr("Select SSH Key", self._language),
                    key_edit.text() or str(__import__("pathlib").Path.home() / ".ssh"),
                )[0]
                or key_edit.text()
            )
        )
        key_layout.addWidget(key_browse)
        tofu_toggle = ToggleSwitch(False)

        form.addRow("ID:", id_edit)
        form.addRow(tr("Host:", self._language), host_edit)
        form.addRow(tr("Port:", self._language), port_edit)
        form.addRow(tr("Username:", self._language), user_edit)
        form.addRow(tr("Auth:", self._language), auth_combo)
        form.addRow(tr("Key Path:", self._language), key_row)
        form.addRow("Trust unknown host key on first connection:", tofu_toggle)
        sched_widgets = build_scheduler_fields(form, dlg, {}, self._language)
        external_widgets = build_external_tools_fields(form, {}, self._language)
        ssh_access_widgets = build_ssh_access_fields(form, {}, self._language)

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
            "scheduler": scheduler_dict(sched_widgets),
            "external_tools": external_tools_dict(external_widgets),
            "ssh_access": ssh_access_dict(ssh_access_widgets),
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
