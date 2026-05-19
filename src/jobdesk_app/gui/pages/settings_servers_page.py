"""设置页 — Windows Terminal 风格卡片布局（白色主题）。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QSpinBox, QComboBox, QFileDialog, QFrame, QScrollArea, QCheckBox,
)
from PySide6.QtCore import Signal, Qt, QPropertyAnimation, Property, QRectF
from PySide6.QtGui import QPainter, QColor

from ...config.servers import load_servers
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
            " padding: 6px 16px; border-radius: 4px; }"
            " #SettingCard QLineEdit, #SettingCard QSpinBox, #SettingCard QComboBox {"
            " background: #cbd5e1; border: 1px solid #94a3b8; border-radius: 4px;"
            " padding: 4px 8px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

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
            " padding: 6px 16px; border-radius: 4px; }"
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
            "QTableWidget { background: transparent; border: none; alternate-background-color: transparent; }"
            " QTableWidget::item { background: transparent; }"
        )
        self.server_table.horizontalHeader().setStyleSheet(
            "QHeaderView { background: transparent; }"
            " QHeaderView::section { background: transparent; border: none;"
            " border-bottom: 1px solid #94a3b8; border-right: 1px solid #94a3b8; }"
        )
        srv_inner.addWidget(self.server_table)

        srv_btns = QHBoxLayout()
        self.reload_srv_btn = QPushButton("刷新")
        self.reload_srv_btn.clicked.connect(self._load_servers)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        srv_btns.addWidget(self.reload_srv_btn)
        srv_btns.addWidget(self.test_btn)
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
            "background: #3b82f6; color: white; padding: 6px 16px; border-radius: 4px;"
        )
        self.save_btn.clicked.connect(self._save_settings)
        self.discard_btn = QPushButton("放弃更改")
        self.discard_btn.setStyleSheet(
            "background: #cbd5e1; border: 1px solid #94a3b8; padding: 6px 16px; border-radius: 4px;"
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
        row = self.server_table.currentRow()
        if row < 0:
            self._status_cb("请先选择服务器")
            return
        sid = self.server_table.item(row, 0).text()
        self.server_table.setItem(row, 4, QTableWidgetItem("测试中..."))
        try:
            cfg = load_servers()
            srv = cfg.servers[sid]
        except Exception:
            return

        def _run():
            ssh = create_ssh_client(srv)
            try:
                ssh.connect()
                return "connected" if ssh.test_connection() else "no-response"
            finally:
                ssh.close()

        self._worker = BackgroundWorker(_run)
        self._worker.result.connect(lambda s: self.server_table.setItem(row, 4, QTableWidgetItem(s)))
        self._worker.error.connect(lambda e: self.server_table.setItem(row, 4, QTableWidgetItem(f"错误: {e}")))
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

    def _save_settings(self):
        from dataclasses import replace
        existing = self._store.load()
        new_settings = replace(
            existing,
            default_local_folder=self.local_folder_edit.text().strip(),
            max_parallel=self.max_parallel_spin.value(),
            language=self.language_combo.currentData() or "zh",
            hide_dotfiles=self.hide_dotfiles_cb.isChecked(),
        )
        self._store.save(new_settings)
        self._status_cb("设置已保存")
        if new_settings.language != existing.language:
            self.language_changed.emit(new_settings.language)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "选择本地目录", self.local_folder_edit.text())
        if path:
            self.local_folder_edit.setText(path)

    def shutdown(self):
        w = getattr(self, "_worker", None)
        if w and hasattr(w, "stop_safely"):
            w.stop_safely()
