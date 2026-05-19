"""设置页 — 服务器管理 + 应用设置，统一在一个滚动页面内。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QSpinBox, QComboBox, QFileDialog, QGroupBox, QFormLayout,
)
from PySide6.QtCore import Signal

from ...config.servers import load_servers
from ...services.gui_settings import GuiSettings, GuiSettingsStore
from ..i18n import tr
from ..workers import BackgroundWorker
from ..session import create_ssh_client


class SettingsServersPage(QWidget):
    language_changed = Signal(str)

    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._store = GuiSettingsStore()
        self._language = self._store.load().language

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        # ─── Settings section ───
        settings_box = QGroupBox("设置")
        form = QFormLayout(settings_box)
        form.setSpacing(8)

        folder_row = QHBoxLayout()
        self.local_folder_edit = QLineEdit()
        self.browse_btn = QPushButton("浏览")
        self.browse_btn.clicked.connect(self._browse)
        folder_row.addWidget(self.local_folder_edit, 1)
        folder_row.addWidget(self.browse_btn)
        form.addRow("本地目录:", folder_row)

        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setRange(1, 9999)
        self.max_parallel_spin.setMaximumWidth(100)
        form.addRow("最大并发:", self.max_parallel_spin)

        self.language_combo = QComboBox()
        self.language_combo.addItem("中文", "zh")
        self.language_combo.addItem("English", "en")
        self.language_combo.setMaximumWidth(160)
        form.addRow("语言:", self.language_combo)

        layout.addWidget(settings_box)

        # ─── Servers section ───
        servers_box = QGroupBox("服务器")
        servers_layout = QVBoxLayout(servers_box)
        servers_layout.setSpacing(6)

        self.server_table = QTableWidget()
        self.server_table.setColumnCount(5)
        self.server_table.setMaximumHeight(180)
        self.server_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.server_table.verticalHeader().setVisible(False)
        self.server_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.server_table.setHorizontalHeaderLabels(["ID", "主机", "端口", "用户", "状态"])
        self.server_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        servers_layout.addWidget(self.server_table)

        srv_btns = QHBoxLayout()
        self.reload_srv_btn = QPushButton("刷新")
        self.reload_srv_btn.clicked.connect(self._load_servers)
        srv_btns.addWidget(self.reload_srv_btn)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        srv_btns.addWidget(self.test_btn)
        srv_btns.addStretch()
        servers_layout.addLayout(srv_btns)

        layout.addWidget(servers_box)

        # ─── Save button ───
        save_row = QHBoxLayout()
        self.save_btn = QPushButton("保存设置")
        self.save_btn.clicked.connect(self._save_settings)
        save_row.addWidget(self.save_btn)
        save_row.addStretch()
        layout.addLayout(save_row)

        layout.addStretch()

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

    def _save_settings(self):
        from dataclasses import replace
        existing = self._store.load()
        new_settings = replace(
            existing,
            default_local_folder=self.local_folder_edit.text().strip(),
            max_parallel=self.max_parallel_spin.value(),
            language=self.language_combo.currentData() or "zh",
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
