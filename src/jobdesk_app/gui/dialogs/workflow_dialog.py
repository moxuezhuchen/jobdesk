"""Workflow launch dialog — select built-in workflow and initial input file."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QPushButton, QLabel, QLineEdit, QComboBox,
    QDialogButtonBox,
)

from ...services.workflow_service import BUILTIN_WORKFLOWS


class WorkflowDialog(QDialog):
    """Select a workflow and the initial run to start from."""

    def __init__(self, parent=None, workspace: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("Start Workflow")
        self.setMinimumWidth(420)
        self._workspace = workspace

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.workflow_combo = QComboBox()
        for name in BUILTIN_WORKFLOWS:
            self.workflow_combo.addItem(name)
        form.addRow("Workflow:", self.workflow_combo)

        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("server_id from servers.yaml")
        form.addRow("Server:", self.server_edit)

        self.remote_dir_edit = QLineEdit()
        self.remote_dir_edit.setPlaceholderText("/path/to/remote/workdir")
        form.addRow("Remote dir:", self.remote_dir_edit)

        self.input_file_edit = QLineEdit()
        self.input_file_edit.setPlaceholderText("e.g. /remote/mol.gjf")
        form.addRow("Input file:", self.input_file_edit)

        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #d97706;")
        layout.addWidget(self.status_label)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_accept(self):
        if not self.server_edit.text().strip():
            self.status_label.setText("Server ID is required")
            return
        if not self.remote_dir_edit.text().strip():
            self.status_label.setText("Remote dir is required")
            return
        if not self.input_file_edit.text().strip():
            self.status_label.setText("Input file is required")
            return
        self.accept()

    def workflow_name(self) -> str:
        return self.workflow_combo.currentText()

    def server_id(self) -> str:
        return self.server_edit.text().strip()

    def remote_dir(self) -> str:
        return self.remote_dir_edit.text().strip()

    def input_file(self) -> str:
        return self.input_file_edit.text().strip()
