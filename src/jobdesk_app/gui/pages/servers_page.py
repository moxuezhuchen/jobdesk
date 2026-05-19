"""Servers 页面 — 显示 servers.yaml 服务器列表 + Test Connection。"""

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QLabel, QHeaderView, QTableWidgetItem,
)
from PySide6.QtCore import Qt

from ..workers import BackgroundWorker
from ..session import create_ssh_client
from ...config.servers import load_servers, get_default_servers_path


class ServersPage(QWidget):
    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setHorizontalHeaderLabels([
            "服务器ID", "主机", "端口", "用户名", "认证方式", "状态"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._load_servers)
        btn_row.addWidget(refresh_btn)

        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.test_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._load_servers()

    def _load_servers(self):
        try:
            cfg = load_servers()
        except Exception as e:
            self.table.setRowCount(1)
            self.table.setColumnCount(1)
            self.table.setHorizontalHeaderLabels(["错误"])
            self.table.setItem(0, 0, QTableWidgetItem(str(e)))
            return

        servers = cfg.servers
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "server_id", "host", "port", "username", "auth_method", "status"
        ])
        self.table.setRowCount(len(servers))
        for r, (sid, srv) in enumerate(sorted(servers.items())):
            self.table.setItem(r, 0, QTableWidgetItem(sid))
            self.table.setItem(r, 1, QTableWidgetItem(srv.host))
            self.table.setItem(r, 2, QTableWidgetItem(str(srv.port)))
            self.table.setItem(r, 3, QTableWidgetItem(srv.username))
            self.table.setItem(r, 4, QTableWidgetItem(srv.auth_method.value))
            self.table.setItem(r, 5, QTableWidgetItem(""))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _test_connection(self):
        row = self.table.currentRow()
        if row < 0:
            self._status_cb("Please select a server first")
            return
        sid = self.table.item(row, 0).text()
        self.table.setItem(row, 5, QTableWidgetItem("测试中..."))

        try:
            cfg = load_servers()
            srv = cfg.servers[sid]
        except Exception as e:
            self._log(f"Load servers error: {e}")
            return

        def _run():
            ssh = create_ssh_client(srv)  # pass ServerConfig directly
            try:
                ssh.connect()
                alive = ssh.test_connection()
                return ("connected" if alive else "no-response", None)
            finally:
                ssh.close()

        self.worker = BackgroundWorker(_run)
        self.worker.result.connect(lambda r: self._on_test_result(row, r[0]))
        self.worker.error.connect(lambda e: self._on_test_result(row, f"Error: {e}"))
        self._log(f"Testing connection to {sid}...")
        self.worker.start()

    def _on_test_result(self, row: int, status: str):
        self.table.setItem(row, 5, QTableWidgetItem(status))
        self._log(f"Connection test: {status}")
        self._status_cb(status)
