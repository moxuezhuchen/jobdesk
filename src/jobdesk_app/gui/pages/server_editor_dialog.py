from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..design.components import ToggleSwitch
from ..design.tokens import Colors
from ..i18n import tr
from ..worker_utils import WorkerContext, start_context_worker
from .settings_servers_helpers import (
    build_external_tools_fields,
    build_scheduler_fields,
    build_ssh_access_fields,
    external_tools_dict,
    scheduler_dict,
    ssh_access_dict,
    validate_executable_reference,
    validate_server_id_change,
)


class _FormAdapter:
    """Small addRow-compatible adapter used inside collapsible sections."""

    def __init__(self, layout: QVBoxLayout):
        self.layout = layout
        self._labels: list[tuple[QLabel, str, QWidget | None]] = []

    def addRow(self, label, field=None):  # noqa: N802 - Qt-compatible API
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 2, 0, 2)
        row_layout.setSpacing(10)
        if field is None:
            row_layout.addWidget(label)
            return None
        else:
            label_widget = label if isinstance(label, QWidget) else QLabel(str(label))
            label_widget.setMinimumWidth(150)
            if isinstance(label_widget, QLabel):
                self._labels.append((label_widget, label_widget.text(), field if isinstance(field, QWidget) else None))
                if isinstance(field, QWidget):
                    label_widget.setBuddy(field)
                    field.setAccessibleName(label_widget.text())
                    field.setAccessibleDescription(label_widget.text())
            row_layout.addWidget(label_widget)
            row_layout.addWidget(field, 1)
        self.layout.addWidget(row)
        return label_widget

    def apply_language(self, language: str) -> None:
        for label, source_text, field in self._labels:
            translated = tr(source_text, language)
            label.setText(translated)
            if field is not None:
                field.setAccessibleName(translated)
                field.setAccessibleDescription(translated)


class _Section(QWidget):
    def __init__(self, key: str, title: str, *, expanded: bool):
        super().__init__()
        self.setObjectName(f"ServerEditorSection_{key}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = QToolButton()
        self.header.setObjectName(f"ServerEditorSectionHeader_{key}")
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        layout.addWidget(self.header)
        self.body = QWidget()
        self.body.setObjectName(f"ServerEditorSectionBody_{key}")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(16, 2, 0, 8)
        self.body_layout.setSpacing(2)
        layout.addWidget(self.body)
        self.header.toggled.connect(self._set_expanded)
        self._set_expanded(expanded)

    def _set_expanded(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.header.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    def is_expanded(self) -> bool:
        return self.header.isChecked()


class ServerEditorDialog(QDialog):
    """Shared add/edit server editor. No configuration is written by this dialog."""

    def __init__(
        self,
        *,
        language: str,
        existing_ids: set[str],
        old_id: str | None = None,
        server_id: str = "",
        server: dict | None = None,
        connection_tester=None,
        parent=None,
    ):
        super().__init__(parent)
        self.language = language
        self.existing_ids = set(existing_ids)
        self.old_id = old_id
        self._server = dict(server or {})
        self._connection_tester = connection_tester
        self._connection_test_running = False
        self._background_workers: list[object] = []
        self._original_trust = bool(self._server.get("trust_on_first_use", False))
        self._sections: dict[str, _Section] = {}
        self.setObjectName("ServerEditorDialog")
        self.setWindowTitle(
            f"{tr('Edit Server:', language)} {server_id}" if old_id else tr("Add", language)
        )
        self.setMinimumSize(560, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        scroll = QScrollArea()
        scroll.setObjectName("ServerEditorScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 16, 20, 16)
        content_layout.setSpacing(10)

        basic = self._add_section(content_layout, "basic", tr("Basic", language), expanded=True)
        basic_form = _FormAdapter(basic.body_layout)
        self._basic_form = basic_form
        self.id_edit = QLineEdit(server_id)
        self.id_edit.setObjectName("ServerEditorId")
        self.id_edit.setPlaceholderText(tr("e.g. myserver", language))
        self.host_edit = QLineEdit(str(self._server.get("host", "")))
        self.host_edit.setObjectName("ServerEditorHost")
        self.host_edit.setPlaceholderText(tr("e.g. 192.168.1.100", language))
        self.port_edit = QSpinBox()
        self.port_edit.setObjectName("ServerEditorPort")
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(int(self._server.get("port", 22)))
        self.user_edit = QLineEdit(str(self._server.get("username", "")))
        self.user_edit.setObjectName("ServerEditorUsername")
        self.user_edit.setPlaceholderText(tr("e.g. root", language))
        from PySide6.QtWidgets import QComboBox

        self.auth_combo = QComboBox()
        self.auth_combo.setObjectName("ServerEditorAuth")
        self.auth_combo.addItems(["key"])
        self.auth_combo.setToolTip(
            tr("Key-based SSH authentication. Password auth is not supported.", language)
        )
        self.key_edit = QLineEdit(str(self._server.get("key_path", "") or ""))
        self.key_edit.setObjectName("ServerEditorKeyPath")
        self.key_edit.setPlaceholderText("~/.ssh/id_ed25519")
        self.key_edit.setToolTip(
            tr(
                "Absolute path to your SSH private key. Use ~ for your home folder — "
                "e.g. ~/.ssh/id_ed25519. On Windows, the dialog viewer shows known keys "
                "under %USERPROFILE%\\.ssh\\.",
                language,
            )
        )
        key_row = QWidget()
        key_layout = QHBoxLayout(key_row)
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.key_edit, 1)
        key_browse = QPushButton(" ... ")
        key_browse.setObjectName("ServerEditorKeyBrowse")
        key_browse.clicked.connect(self._browse_key)
        key_layout.addWidget(key_browse)
        self.id_label = basic_form.addRow("ID:", self.id_edit)
        self.host_label = basic_form.addRow("Host:", self.host_edit)
        self.port_label = basic_form.addRow("Port:", self.port_edit)
        self.username_label = basic_form.addRow("Username:", self.user_edit)
        self.auth_label = basic_form.addRow("Auth:", self.auth_combo)
        self.key_path_label = basic_form.addRow("Key Path:", key_row)
        self.key_path_label.setBuddy(self.key_edit)
        self.key_browse_btn = key_browse
        self.key_browse_btn.setAccessibleName(tr("Browse SSH key", language))
        self.key_browse_btn.setAccessibleDescription(tr("Choose an SSH private key file", language))
        self.key_edit.setAccessibleName(tr("Key Path:", language))
        self.key_edit.setAccessibleDescription(tr("Key Path:", language))

        trust_row = QWidget()
        trust_layout = QVBoxLayout(trust_row)
        trust_layout.setContentsMargins(0, 0, 0, 0)
        self.trust_toggle = ToggleSwitch(self._original_trust)
        self.trust_toggle.setObjectName("ServerEditorTrustUnknownHost")
        self.trust_toggle.setFocusPolicy(Qt.StrongFocus)
        self.trust_toggle.setAccessibleName(tr("Trust unknown host", language))
        self.trust_toggle.setAccessibleDescription(
            tr(
                "Trust and store an unknown SSH host key on first connection after verifying its fingerprint.",
                language,
            )
        )
        self.trust_toggle.installEventFilter(self)
        trust_layout.addWidget(self.trust_toggle, 0, Qt.AlignLeft)
        self.trust_explanation = QLabel(
            tr(
                "Off by default. Enabling this trusts and stores an unknown SSH host key "
                "on first connection; verify the host fingerprint through a trusted channel.",
                language,
            )
        )
        self.trust_explanation.setObjectName("ServerEditorTrustExplanation")
        self.trust_explanation.setWordWrap(True)
        self.trust_explanation.setStyleSheet(f"color: {Colors.WARNING};")
        trust_layout.addWidget(self.trust_explanation)
        self.trust_label = basic_form.addRow("Trust unknown host:", trust_row)
        self.trust_label.setBuddy(self.trust_toggle)

        scheduler = self._add_section(
            content_layout,
            "scheduler",
            tr("Scheduler and resources", language),
            expanded=False,
        )
        scheduler_form = _FormAdapter(scheduler.body_layout)
        self._scheduler_form = scheduler_form
        self.scheduler_widgets = build_scheduler_fields(
            scheduler_form,
            self,
            self._server.get("scheduler", {}) or {},
            language,
        )
        self.max_cores_edit = QSpinBox()
        self.max_cores_edit.setRange(0, 1000000)
        self.max_cores_edit.setSpecialValueText(tr("Unlimited", language))
        self.max_cores_edit.setValue(int(self._server.get("max_cores") or 0))
        scheduler_form.addRow(tr("Max cores:", language), self.max_cores_edit)

        terminal = self._add_section(
            content_layout,
            "terminal",
            tr("Terminal integration", language),
            expanded=False,
        )
        terminal_form = _FormAdapter(terminal.body_layout)
        self._terminal_form = terminal_form
        self.external_widgets = build_external_tools_fields(
            terminal_form,
            self._server.get("external_tools", {}) or {},
            language,
        )
        self.external_widgets["terminal_path"].setObjectName("ServerEditorTerminalPath")
        self.external_widgets["terminal_path"].setToolTip(
            tr(
                "Enter an executable path or a command available on PATH, such as wt or putty.exe.",
                language,
            )
        )

        ssh = self._add_section(
            content_layout,
            "ssh",
            tr("SSH and proxy", language),
            expanded=False,
        )
        ssh_form = _FormAdapter(ssh.body_layout)
        self._ssh_form = ssh_form
        self.ssh_access_widgets = build_ssh_access_fields(
            ssh_form,
            self._server.get("ssh_access", {}) or {},
            language,
        )
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        footer = QFrame()
        footer.setObjectName("ServerEditorFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 10, 20, 10)
        self.connection_result_label = QLabel(tr("Not tested", language))
        self.connection_result_label.setObjectName("ServerEditorConnectionResult")
        footer_layout.addWidget(self.connection_result_label, 1)
        self.test_connection_btn = QPushButton(tr("Test Connection", language))
        self.test_connection_btn.setObjectName("ServerEditorTestBtn")
        self.test_connection_btn.clicked.connect(self._test_connection)
        footer_layout.addWidget(self.test_connection_btn)
        cancel = QPushButton(tr("Cancel", language))
        cancel.setObjectName("ServerEditorCancelBtn")
        cancel.clicked.connect(self.reject)
        footer_layout.addWidget(cancel)
        self.cancel_button = cancel
        self.save_button = QPushButton(tr("Save", language))
        self.save_button.setObjectName("ServerEditorSaveBtn")
        self.save_button.clicked.connect(self._attempt_accept)
        footer_layout.addWidget(self.save_button)
        root.addWidget(footer)

        self.validation_label = QLabel()
        self.validation_label.setObjectName("ServerEditorValidation")
        self.validation_label.setStyleSheet(f"color: {Colors.ERROR};")
        self.validation_label.setWordWrap(True)
        basic.body_layout.insertWidget(0, self.validation_label)
        for edit in (self.id_edit, self.host_edit, self.user_edit):
            edit.textChanged.connect(self.validate_inline)
        self.external_widgets["terminal_path"].textChanged.connect(self.validate_inline)
        self.apply_language(language)

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API
        if watched is self.trust_toggle and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                self.trust_toggle.setChecked(not self.trust_toggle.isChecked())
                self.trust_toggle.toggled.emit(self.trust_toggle.isChecked())
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _add_section(self, layout, key: str, title: str, *, expanded: bool) -> _Section:
        section = _Section(key, title, expanded=expanded)
        self._sections[key] = section
        layout.addWidget(section)
        return section

    def section_is_expanded(self, key: str) -> bool:
        return self._sections[key].is_expanded()

    def apply_language(self, language: str) -> None:
        """Retranslate this live dialog without discarding in-progress edits."""
        self.language = language
        self.setWindowTitle(
            f"{tr('Edit Server:', language)} {self.id_edit.text()}" if self.old_id else tr("Add", language)
        )
        for form in (self._basic_form, self._scheduler_form, self._terminal_form, self._ssh_form):
            form.apply_language(language)
        self._sections["basic"].header.setText(tr("Basic", language))
        self._sections["scheduler"].header.setText(tr("Scheduler and resources", language))
        self._sections["terminal"].header.setText(tr("Terminal integration", language))
        self._sections["ssh"].header.setText(tr("SSH and proxy", language))
        self.id_edit.setPlaceholderText(tr("e.g. myserver", language))
        self.host_edit.setPlaceholderText(tr("e.g. 192.168.1.100", language))
        self.user_edit.setPlaceholderText(tr("e.g. root", language))
        self.auth_combo.setToolTip(tr("Key-based SSH authentication. Password auth is not supported.", language))
        self.key_browse_btn.setAccessibleName(tr("Browse SSH key", language))
        self.key_browse_btn.setAccessibleDescription(tr("Choose an SSH private key file", language))
        self.key_edit.setAccessibleName(tr("Key Path:", language))
        self.key_edit.setAccessibleDescription(tr("Key Path:", language))
        self.trust_toggle.setAccessibleName(tr("Trust unknown host", language))
        self.trust_toggle.setAccessibleDescription(
            tr("Trust and store an unknown SSH host key on first connection after verifying its fingerprint.", language)
        )
        self.trust_explanation.setText(
            tr(
                "Off by default. Enabling this trusts and stores an unknown SSH host key "
                "on first connection; verify the host fingerprint through a trusted channel.",
                language,
            )
        )
        self.max_cores_edit.setSpecialValueText(tr("Unlimited", language))
        self.test_connection_btn.setText(tr("Test Connection", language))
        self.cancel_button.setText(tr("Cancel", language))
        self.save_button.setText(tr("Save", language))
        if not self._connection_test_running:
            self.connection_result_label.setText(tr("Not tested", language))
        self.validate_inline()

    def _browse_key(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            tr("Select SSH Key", self.language),
            self.key_edit.text() or str(Path.home() / ".ssh"),
        )
        if selected:
            self.key_edit.setText(selected)

    @staticmethod
    def _set_validation_state(widget, error: str | None) -> None:
        widget.setProperty("validationState", "error" if error else "valid")
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def validate_inline(self, *_args) -> bool:
        errors: list[str] = []
        id_error = validate_server_id_change(
            self.existing_ids,
            old_id=self.old_id,
            new_id=self.id_edit.text(),
        )
        self._set_validation_state(self.id_edit, id_error)
        if id_error:
            errors.append(id_error)
        for widget, message in (
            (self.host_edit, tr("Host is required", self.language)),
            (self.user_edit, tr("Username is required", self.language)),
        ):
            error = None if widget.text().strip() else message
            self._set_validation_state(widget, error)
            if error:
                errors.append(error)
        terminal_path = self.external_widgets["terminal_path"]
        terminal_error = validate_executable_reference(terminal_path.text())
        if terminal_error:
            terminal_error = tr(terminal_error, self.language)
        self._set_validation_state(terminal_path, terminal_error)
        if terminal_error:
            errors.append(terminal_error)
        self.validation_label.setText("\n".join(errors))
        self.save_button.setEnabled(not errors)
        self.test_connection_btn.setEnabled(not errors)
        return not errors

    def result_config(self) -> tuple[str, dict]:
        result = dict(self._server)
        result.update(
            {
                "host": self.host_edit.text().strip(),
                "port": self.port_edit.value(),
                "username": self.user_edit.text().strip(),
                "auth_method": self.auth_combo.currentText(),
                "trust_on_first_use": self.trust_toggle.isChecked(),
                "scheduler": scheduler_dict(
                    self.scheduler_widgets,
                    self._server.get("scheduler", {}) or {},
                ),
                "external_tools": external_tools_dict(
                    self.external_widgets,
                    self._server.get("external_tools", {}) or {},
                ),
                "ssh_access": ssh_access_dict(
                    self.ssh_access_widgets,
                    self._server.get("ssh_access", {}) or {},
                ),
            }
        )
        if self.key_edit.text().strip():
            result["key_path"] = self.key_edit.text().strip()
        else:
            result.pop("key_path", None)
        if self.max_cores_edit.value() > 0:
            result["max_cores"] = self.max_cores_edit.value()
        else:
            result.pop("max_cores", None)
        return self.id_edit.text().strip(), result

    def _attempt_accept(self) -> None:
        if not self.validate_inline():
            return
        if not self._original_trust and self.trust_toggle.isChecked():
            answer = QMessageBox.question(
                self,
                tr("Trust unknown host?", self.language),
                tr(
                    "Only enable this after verifying the server identity through a trusted channel. Continue?",
                    self.language,
                ),
            )
            if answer != QMessageBox.Yes:
                return
        self.accept()

    def _test_connection(self) -> None:
        if self._connection_test_running or not self.validate_inline():
            return
        _, config = self.result_config()
        self._connection_test_running = True
        self.test_connection_btn.setEnabled(False)
        self.connection_result_label.setText(tr("Testing...", self.language))

        def _run(_ctx: WorkerContext):
            result = self._connection_tester(config) if self._connection_tester else tr("Ready to test", self.language)
            return str(result or tr("Connected", self.language))

        def _on_result(status):
            tested_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            self.connection_result_label.setText(f"{status} · {tested_at}")

        def _on_error(error):
            self.connection_result_label.setText(f"{tr('Error:', self.language)} {error}")

        def _on_finished():
            self._connection_test_running = False
            self.test_connection_btn.setEnabled(self.validate_inline())

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=_on_result,
            on_error=_on_error,
            on_finished=_on_finished,
        )
