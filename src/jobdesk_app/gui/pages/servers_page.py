"""Servers page — server list + connection test + scheduler config editor."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QLabel, QHeaderView, QTableWidgetItem,
    QGroupBox, QFormLayout, QLineEdit, QComboBox, QSpinBox,
    QSplitter,
)
from PySide6.QtCore import Qt

from ..workers import BackgroundWorker
from ..session import create_ssh_client
from ...config.servers import load_servers, get_default_servers_path
from ..i18n import tr


class ServersPage(QWidget):
    def __init__(self, state, log_cb, status_cb):
        super().__init__()
        self.state = state
        self._log = log_cb
        self._status_cb = status_cb
        self._language = "en"
        self._servers_cfg = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Horizontal)

        # ── Left: server table ────────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.currentRowChanged.connect(self._on_row_changed)
        left_layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self._load_servers)
        btn_row.addWidget(self.refresh_btn)
        self.test_btn = QPushButton()
        self.test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.test_btn)
        self.open_yaml_btn = QPushButton()
        self.open_yaml_btn.clicked.connect(self._open_yaml)
        btn_row.addWidget(self.open_yaml_btn)
        btn_row.addStretch()
        left_layout.addLayout(btn_row)
        splitter.addWidget(left)

        # ── Right: scheduler config panel ─────────────────────────────────
        right = QGroupBox()
        right_layout = QFormLayout(right)
        right_layout.setLabelAlignment(Qt.AlignRight)

        self.sched_type_combo = QComboBox()
        self.sched_type_combo.addItems(["nohup", "slurm", "pbs"])
        right_layout.addRow(tr("Scheduler:", self._language), self.sched_type_combo)

        self.partition_edit = QLineEdit()
        self.partition_edit.setPlaceholderText("e.g. compute (Slurm) or batch (PBS)")
        right_layout.addRow(tr("Partition/Queue:", self._language), self.partition_edit)

        self.nproc_spin = QSpinBox()
        self.nproc_spin.setRange(1, 512)
        self.nproc_spin.setValue(8)
        right_layout.addRow(tr("Default nproc:", self._language), self.nproc_spin)

        self.mem_edit = QLineEdit()
        self.mem_edit.setPlaceholderText("e.g. 16GB")
        right_layout.addRow(tr("Default memory:", self._language), self.mem_edit)

        self.walltime_edit = QLineEdit()
        self.walltime_edit.setPlaceholderText("e.g. 24:00:00")
        right_layout.addRow(tr("Walltime:", self._language), self.walltime_edit)

        self.save_sched_btn = QPushButton()
        self.save_sched_btn.clicked.connect(self._save_scheduler_config)
        right_layout.addRow("", self.save_sched_btn)

        self.sched_note = QLabel()
        self.sched_note.setStyleSheet("color: #6b7280; font-size: 11px;")
        self.sched_note.setWordWrap(True)
        right_layout.addRow("", self.sched_note)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        self.apply_language(self._language)
        self._load_servers()

    # ── public API ────────────────────────────────────────────────────────

    def on_activated(self):
        self._load_servers()

    def apply_language(self, language: str):
        self._language = language
        self.refresh_btn.setText(tr("Refresh", language))
        self.test_btn.setText(tr("Test Connection", language))
        self.open_yaml_btn.setText(tr("Open servers.yaml", language))
        self.save_sched_btn.setText(tr("Save Scheduler Config", language))
        self.table.setHorizontalHeaderLabels([
            tr("server_id", language), tr("host", language), tr("port", language),
            tr("username", language), tr("auth_method", language),
            tr("scheduler", language), tr("status", language),
        ])

    # ── internal ──────────────────────────────────────────────────────────

    def _load_servers(self):
        try:
            self._servers_cfg = load_servers()
        except Exception as e:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem(str(e)))
            return

        servers = self._servers_cfg.servers
        self.table.setRowCount(len(servers))
        for r, (sid, srv) in enumerate(sorted(servers.items())):
            sched = ""
            if getattr(srv, "scheduler", None):
                sched = srv.scheduler.type if hasattr(srv.scheduler, "type") else str(srv.scheduler)
            self.table.setItem(r, 0, QTableWidgetItem(sid))
            self.table.setItem(r, 1, QTableWidgetItem(srv.host))
            self.table.setItem(r, 2, QTableWidgetItem(str(srv.port)))
            self.table.setItem(r, 3, QTableWidgetItem(srv.username))
            self.table.setItem(r, 4, QTableWidgetItem(srv.auth_method.value))
            self.table.setItem(r, 5, QTableWidgetItem(sched))
            self.table.setItem(r, 6, QTableWidgetItem(""))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

    def _on_row_changed(self, row: int):
        """Populate scheduler panel from selected server's config."""
        if row < 0 or self._servers_cfg is None:
            return
        sid_item = self.table.item(row, 0)
        if sid_item is None:
            return
        sid = sid_item.text()
        srv = self._servers_cfg.servers.get(sid)
        if srv is None:
            return
        sched = getattr(srv, "scheduler", None)
        if sched:
            idx = self.sched_type_combo.findText(getattr(sched, "type", "nohup"))
            self.sched_type_combo.setCurrentIndex(max(0, idx))
            self.partition_edit.setText(getattr(sched, "partition", "") or "")
        else:
            self.sched_type_combo.setCurrentIndex(0)
            self.partition_edit.clear()

        res = getattr(srv, "default_resources", None)
        if res:
            self.nproc_spin.setValue(getattr(res, "nproc", 8) or 8)
            self.mem_edit.setText(getattr(res, "mem", "") or "")
            self.walltime_edit.setText(getattr(res, "walltime", "") or "")
        else:
            self.nproc_spin.setValue(8)
            self.mem_edit.clear()
            self.walltime_edit.clear()

    def _save_scheduler_config(self):
        """Write scheduler config back to servers.yaml via ruamel.yaml or plain text patch."""
        row = self.table.currentRow()
        if row < 0:
            self._status_cb(tr("Select a server first", self._language))
            return
        sid_item = self.table.item(row, 0)
        if sid_item is None:
            return
        sid = sid_item.text()

        sched_type = self.sched_type_combo.currentText()
        partition = self.partition_edit.text().strip()
        nproc = self.nproc_spin.value()
        mem = self.mem_edit.text().strip()
        walltime = self.walltime_edit.text().strip()

        yaml_path = get_default_servers_path()
        try:
            _patch_server_scheduler(yaml_path, sid, sched_type, partition, nproc, mem, walltime)
            self._load_servers()
            self._status_cb(f"Saved scheduler config for {sid}")
            self.sched_note.setText(tr("Saved. Restart or reconnect to apply.", self._language))
        except Exception as exc:
            self._status_cb(f"Save failed: {exc}")
            self._log(f"Save scheduler config error: {exc}")

    def _test_connection(self):
        row = self.table.currentRow()
        if row < 0:
            self._status_cb(tr("Select a server first", self._language))
            return
        sid = self.table.item(row, 0).text()
        self.table.setItem(row, 6, QTableWidgetItem("testing…"))
        try:
            srv = self._servers_cfg.servers[sid]
        except Exception:
            return

        def _run():
            ssh = create_ssh_client(srv)
            try:
                ssh.connect()
                alive = ssh.test_connection()
                return "connected" if alive else "no-response"
            finally:
                ssh.close()

        self.worker = BackgroundWorker(_run)
        self.worker.result.connect(lambda r: self._on_test_result(row, r))
        self.worker.error.connect(lambda e: self._on_test_result(row, f"Error: {e}"))
        self.worker.start()

    def _on_test_result(self, row: int, status: str):
        self.table.setItem(row, 6, QTableWidgetItem(status))
        self._status_cb(status)

    def _open_yaml(self):
        import os
        path = get_default_servers_path()
        try:
            os.startfile(str(path))
        except Exception:
            self._status_cb(str(path))


# ── YAML patch helper ─────────────────────────────────────────────────────────

def _patch_server_scheduler(
    yaml_path: Path,
    server_id: str,
    sched_type: str,
    partition: str,
    nproc: int,
    mem: str,
    walltime: str,
) -> None:
    """Patch servers.yaml to add/update scheduler and default_resources for a server.

    Uses ruamel.yaml if available (preserves comments), falls back to PyYAML.
    """
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.load(f)
        _apply_sched_patch(data, server_id, sched_type, partition, nproc, mem, walltime)
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f)
    except ImportError:
        import yaml as pyyaml
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = pyyaml.safe_load(f) or {}
        _apply_sched_patch(data, server_id, sched_type, partition, nproc, mem, walltime)
        with open(yaml_path, "w", encoding="utf-8") as f:
            pyyaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def _apply_sched_patch(data: dict, server_id: str, sched_type: str,
                       partition: str, nproc: int, mem: str, walltime: str) -> None:
    servers = data.get("servers", {})
    if server_id not in servers:
        return
    srv = servers[server_id]
    if sched_type == "nohup":
        srv.pop("scheduler", None)
    else:
        sched: dict = {"type": sched_type}
        if partition:
            sched["partition"] = partition
        srv["scheduler"] = sched
    res: dict = {}
    if nproc:
        res["nproc"] = nproc
    if mem:
        res["mem"] = mem
    if walltime:
        res["walltime"] = walltime
    if res:
        srv["default_resources"] = res
    else:
        srv.pop("default_resources", None)
