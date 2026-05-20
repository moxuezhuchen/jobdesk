"""设置页 — Windows Terminal 风格卡片布局（白色主题）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QSpinBox, QComboBox, QFileDialog, QFrame, QScrollArea, QCheckBox,
)
from PySide6.QtCore import Signal, Qt, QPropertyAnimation, Property, QRectF
from PySide6.QtGui import QPainter, QColor

from ...config.servers import load_servers, get_default_servers_path
from ...services.gui_settings import GuiSettings, GuiSettingsStore
from ..i18n import tr
from ..workers import BackgroundWorker
from ..session import create_ssh_client


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
        track_color = QColor("#3b82f6") if self._checked else QColor("#94a3b8")
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
            "#SettingCard { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; }"
            " #SettingCard QLabel { background: transparent; }"
            " #SettingCard QPushButton { background: #cbd5e1; border: 1px solid #94a3b8;"
            " padding: 0 16px; border-radius: 4px; min-height: 44px; max-height: 44px; }"
            " #SettingCard QPushButton:pressed { background: #93c5fd; border-color: #3b82f6; }"
            " #SettingCard QLineEdit, #SettingCard QSpinBox, #SettingCard QComboBox {"
            " background: #cbd5e1; border: 1px solid #94a3b8; border-radius: 4px;"
            " padding: 0 8px; min-height: 44px; max-height: 44px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        self.setFixedHeight(60)

        lbl_title = QLabel(title)
        lbl_desc = QLabel(description)
        lbl_desc.setStyleSheet("color: #64748b; font-size: 15pt;")

        layout.addWidget(lbl_title)
        layout.addSpacing(16)
        layout.addWidget(lbl_desc)
        layout.addStretch()
        control.setMinimumWidth(180)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)


class SettingsServersPage(QWidget):
    language_changed = Signal(str)

    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._store = GuiSettingsStore()
        self._language = self._store.load().language

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
        title = QLabel("设置")
        title.setStyleSheet("font-size: 20pt; color: #0f172a; font-weight: 600;")
        layout.addWidget(title)
        layout.addSpacing(8)

        # ─── 本地目录 ───
        folder_ctrl = QWidget()
        fc_layout = QHBoxLayout(folder_ctrl)
        fc_layout.setContentsMargins(0, 0, 0, 0)
        self.local_folder_edit = QLineEdit()
        self.browse_btn = QPushButton("浏览")
        self.browse_btn.clicked.connect(self._browse)
        fc_layout.addWidget(self.local_folder_edit, 1)
        fc_layout.addWidget(self.browse_btn)
        layout.addWidget(SettingCard("本地目录", "下载结果文件的默认保存位置", folder_ctrl))

        # ─── 最大并发 ───
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setRange(1, 9999)
        layout.addWidget(SettingCard("最大并发", "同时运行的远程任务数上限", self.max_parallel_spin))

        # ─── 语言 ───
        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh")
        self.language_combo.addItem("English", "en")
        layout.addWidget(SettingCard("语言", "界面显示语言，切换后立即生效", self.language_combo))

        # ─── 隐藏.文件 ───
        self.hide_dotfiles_cb = ToggleSwitch()
        toggle_ctrl = QWidget()
        toggle_ctrl.setStyleSheet("background: transparent;")
        toggle_layout = QHBoxLayout(toggle_ctrl)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(20)
        self._toggle_label = QLabel("开" if self.hide_dotfiles_cb.isChecked() else "关")
        toggle_layout.addStretch()
        toggle_layout.addWidget(self._toggle_label)
        toggle_layout.addWidget(self.hide_dotfiles_cb)
        self.hide_dotfiles_cb.toggled.connect(
            lambda v: self._toggle_label.setText("开" if v else "关")
        )
        layout.addWidget(SettingCard("隐藏点文件", "远程文件列表中不显示以 . 开头的文件", toggle_ctrl))

        # ─── 软件配置 ───
        layout.addSpacing(12)
        dl_title = QLabel("软件配置")
        dl_title.setStyleSheet("font-size: 20pt; color: #0f172a; font-weight: 600;")
        layout.addWidget(dl_title)
        layout.addSpacing(4)

        def _make_profile_row(label, ext_ph, cmd_ph, dl_ph):
            row = QFrame()
            row.setObjectName("SettingCard")
            row.setStyleSheet(
                "#SettingCard { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; }"
                " #SettingCard QLabel { background: transparent; }"
                " #SettingCard QLineEdit { background: #cbd5e1; border: 1px solid #94a3b8;"
                " border-radius: 4px; padding: 0 8px; min-height: 36px; max-height: 36px; }"
            )
            row.setFixedHeight(52)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(16, 0, 16, 0)
            rl.setSpacing(8)
            lbl = QLabel(label)
            lbl.setMinimumWidth(70)
            rl.addWidget(lbl)
            ext = QLineEdit()
            ext.setPlaceholderText(ext_ph)
            rl.addWidget(ext, 1)
            cmd = QLineEdit()
            cmd.setPlaceholderText(cmd_ph)
            rl.addWidget(cmd, 2)
            dl = QLineEdit()
            dl.setPlaceholderText(dl_ph)
            rl.addWidget(dl, 1)
            return row, ext, cmd, dl

        row_g, self.gaussian_extensions, self.gaussian_command, self.gaussian_patterns = \
            _make_profile_row("Gaussian", ".gjf,.com", "g16 {name}", "*.log,*.chk")
        layout.addWidget(row_g)

        row_o, self.orca_extensions, self.orca_command, self.orca_patterns = \
            _make_profile_row("ORCA", ".inp", "orca {name} > {basename}.out", "*.out,*.gbw")
        layout.addWidget(row_o)

        # ─── 服务器 ───
        layout.addSpacing(12)
        srv_title = QLabel("服务器")
        srv_title.setStyleSheet("font-size: 20pt; color: #0f172a; font-weight: 600;")
        layout.addWidget(srv_title)
        layout.addSpacing(4)

        srv_card = QFrame()
        srv_card.setObjectName("SettingCard")
        srv_card.setStyleSheet(
            "#SettingCard { background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 6px; }"
            " #SettingCard QLabel { background: transparent; }"
            " #SettingCard QTableWidget { background: transparent; border: none;"
            "   alternate-background-color: transparent; }"
            " #SettingCard QTableWidget::item { background: transparent; }"
            " #SettingCard QHeaderView::section { background: transparent; }"
            " #SettingCard QTableCornerButton::section { background: transparent; }"
            " #SettingCard QPushButton { background: #cbd5e1; border: 1px solid #94a3b8;"
            " padding: 0 16px; border-radius: 4px; min-height: 44px; max-height: 44px; }"
            " #SettingCard QPushButton:pressed { background: #93c5fd; border-color: #3b82f6; }"
        )
        srv_inner = QVBoxLayout(srv_card)
        srv_inner.setContentsMargins(16, 12, 16, 12)
        srv_inner.setSpacing(8)

        self.server_table = QTableWidget()
        self.server_table.setColumnCount(5)
        self.server_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.server_table.verticalHeader().setVisible(False)
        self.server_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.server_table.setHorizontalHeaderLabels(["ID", "主机", "端口", "用户", "状态"])
        self.server_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.server_table.setMaximumHeight(200)
        self.server_table.setStyleSheet(
            "QTableWidget { background: transparent; border: none;"
            " alternate-background-color: transparent; gridline-color: #94a3b8; }"
            " QTableWidget::item { background: transparent; }"
        )
        self.server_table.horizontalHeader().setStyleSheet(
            "QHeaderView { background: transparent; }"
            " QHeaderView::section { background: transparent; border: none;"
            " border-bottom: 1px solid #94a3b8; border-right: 1px solid #94a3b8; }"
        )
        srv_inner.addWidget(self.server_table)

        srv_btns = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        self.edit_yaml_btn = QPushButton("添加服务器")
        self.edit_yaml_btn.clicked.connect(self._add_server)
        self.delete_srv_btn = QPushButton("删除")
        self.delete_srv_btn.clicked.connect(self._delete_server)
        srv_btns.addWidget(self.test_btn)
        srv_btns.addWidget(self.edit_yaml_btn)
        srv_btns.addWidget(self.delete_srv_btn)
        srv_btns.addStretch()
        srv_inner.addLayout(srv_btns)

        layout.addWidget(srv_card)
        layout.addStretch()

        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        # ─── 底部按钮栏（固定） ───
        bottom_bar = QFrame()
        bottom_bar.setStyleSheet("border-top: 1px solid #e2e8f0;")
        bar_layout = QHBoxLayout(bottom_bar)
        bar_layout.setContentsMargins(24, 10, 24, 10)
        bar_layout.addStretch()
        self.save_btn = QPushButton("保存设置")
        self.save_btn.setStyleSheet(
            "QPushButton { background: #3b82f6; color: white; padding: 0 16px; border-radius: 4px;"
            " min-height: 44px; max-height: 44px; }"
            " QPushButton:pressed { background: #1d4ed8; }"
        )
        self.save_btn.clicked.connect(self._save_settings)
        self.discard_btn = QPushButton("放弃更改")
        self.discard_btn.setStyleSheet(
            "QPushButton { background: #cbd5e1; border: 1px solid #94a3b8; padding: 0 16px; border-radius: 4px;"
            " min-height: 44px; max-height: 44px; }"
            " QPushButton:pressed { background: #93c5fd; border-color: #3b82f6; }"
        )
        self.discard_btn.clicked.connect(self._load_settings)
        bar_layout.addWidget(self.save_btn)
        bar_layout.addWidget(self.discard_btn)
        root.addWidget(bottom_bar)

        self._load_servers()
        self._load_settings()

    def on_activated(self):
        self._language = self._store.load().language
        self._load_servers()
        self._load_settings()

    def apply_language(self, language: str):
        self._language = language

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

    def _test_connection(self):
        try:
            cfg = load_servers()
        except Exception:
            return
        if not cfg.servers:
            return
        for row in range(self.server_table.rowCount()):
            self.server_table.setItem(row, 4, QTableWidgetItem("测试中..."))

        servers_list = sorted(cfg.servers.items())

        def _run():
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _test_one(sid, srv):
                try:
                    ssh = create_ssh_client(srv)
                    ssh.connect()
                    ok = ssh.test_connection()
                    ssh.close()
                    return sid, "connected" if ok else "no-response"
                except Exception as e:
                    return sid, f"错误: {e}"

            with ThreadPoolExecutor(max_workers=len(servers_list)) as pool:
                futures = {pool.submit(_test_one, sid, srv): sid for sid, srv in servers_list}
                for f in as_completed(futures):
                    sid, status = f.result()
                    self._worker.log.emit(f"{sid}\t{status}")
            return {}

        self._worker = BackgroundWorker(_run)

        def _on_log(msg):
            sid, status = msg.split("\t", 1)
            for row in range(self.server_table.rowCount()):
                if self.server_table.item(row, 0).text() == sid:
                    self.server_table.setItem(row, 4, QTableWidgetItem(status))
                    break

        self._worker.log.connect(_on_log)
        self._worker.error.connect(lambda e: self._status_cb(f"测试失败: {e}"))
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.start()

    def _load_settings(self):
        s = self._store.load()
        self.local_folder_edit.setText(s.default_local_folder)
        self.max_parallel_spin.setValue(s.max_parallel)
        idx = self.language_combo.findData(s.language)
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        self.hide_dotfiles_cb.setChecked(s.hide_dotfiles)
        self._toggle_label.setText("开" if s.hide_dotfiles else "关")
        patterns = s.software_profiles or {}
        g = patterns.get("Gaussian", {})
        self.gaussian_extensions.setText(g.get("input_extensions", ".gjf,.com"))
        self.gaussian_command.setText(g.get("command_template", "g16 {name}"))
        self.gaussian_patterns.setText(g.get("download_patterns", "*.log,*.chk"))
        o = patterns.get("ORCA", {})
        self.orca_extensions.setText(o.get("input_extensions", ".inp"))
        self.orca_command.setText(o.get("command_template", "orca {name}"))
        self.orca_patterns.setText(o.get("download_patterns", "*.out,*.gbw"))

    def _save_settings(self):
        from dataclasses import replace
        existing = self._store.load()
        new_settings = replace(
            existing,
            default_local_folder=self.local_folder_edit.text().strip(),
            max_parallel=self.max_parallel_spin.value(),
            language=self.language_combo.currentData() or "zh",
            hide_dotfiles=self.hide_dotfiles_cb.isChecked(),
            software_profiles={
                "Gaussian": {
                    "input_extensions": self.gaussian_extensions.text().strip() or ".gjf,.com",
                    "command_template": self.gaussian_command.text().strip() or "g16 {name}",
                    "download_patterns": self.gaussian_patterns.text().strip() or "*.log,*.chk",
                },
                "ORCA": {
                    "input_extensions": self.orca_extensions.text().strip() or ".inp",
                    "command_template": self.orca_command.text().strip() or "orca {name}",
                    "download_patterns": self.orca_patterns.text().strip() or "*.out,*.gbw",
                },
            },
        )
        self._store.save(new_settings)
        self._status_cb("设置已保存")
        if new_settings.language != existing.language:
            self.language_changed.emit(new_settings.language)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择本地目录", self.local_folder_edit.text())
        if path:
            self.local_folder_edit.setText(path)

    def _delete_server(self):
        import yaml
        row = self.server_table.currentRow()
        if row < 0:
            self._status_cb("请先选择服务器")
            return
        sid = self.server_table.item(row, 0).text()
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, "删除服务器", f"确定删除 {sid}？") != QMessageBox.Yes:
            return
        path = get_default_servers_path()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        servers = data.get("servers", {})
        servers.pop(sid, None)
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self._load_servers()

    def _add_server(self):
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox
        import yaml

        dlg = QDialog(self)
        dlg.setWindowTitle("添加服务器")
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
        auth_combo.addItems(["key", "password"])
        key_edit = QLineEdit()
        key_edit.setPlaceholderText("~/.ssh/id_ed25519")

        form.addRow("ID:", id_edit)
        form.addRow("主机:", host_edit)
        form.addRow("端口:", port_edit)
        form.addRow("用户:", user_edit)
        form.addRow("认证方式:", auth_combo)
        form.addRow("密钥路径:", key_edit)

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
        servers[sid] = {
            "host": host,
            "port": port_edit.value(),
            "username": user,
            "auth_method": auth_combo.currentText(),
        }
        if key_edit.text().strip():
            servers[sid]["key_path"] = key_edit.text().strip()
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        self._load_servers()

    def shutdown(self):
        w = getattr(self, "_worker", None)
        if w and w.isRunning():
            w.wait(3000)
