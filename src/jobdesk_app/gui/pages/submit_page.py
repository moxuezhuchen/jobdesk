"""SubmitPage — first-class unified submit UI.

Phase 14B: replaces three legacy entry points on ``FileTransferPage``
(``_run_selected``, ``_run_confflow``, ``_open_confflow_wizard``) plus
two modal dialogs (``InputBuilderDialog``, ``ConfFlowWizard``) with a
single embedded widget.

Layout (top to bottom):

    ┌─────────────────────────────────────────────────┐
    │ InputSourcePanel  (local / remote tabs)         │
    ├─────────────────────────────────────────────────┤
    │ Mode tabs: [ Build input file | Build workflow ]│
    │   ├ Build input file: InputBuilderWidget        │
    │   └ Build workflow:    WorkflowWidget + calc    │
    ├─────────────────────────────────────────────────┤
    │ [Submit] [Create tasks only] [Refresh preview]  │
    │   server: <pill>   max parallel: <spin>         │
    ├─────────────────────────────────────────────────┤
    │ Live preview pane (gjf | inp | workflow.yaml)   │
    ├─────────────────────────────────────────────────┤
    │ Activity log (last 50 status messages)           │
    └─────────────────────────────────────────────────┘

Public signals:

* :pyattr:`submit_requested(SubmitPayload)` — fires on Submit click.
* :pyattr:`create_only_requested(SubmitPayload)` — fires on Create-only.
* :pyattr:`use_as_input_received(list)` — fires when cross-page push
  comes in (Files page right-click menu); the page itself is the
  consumer, so this signal is mostly informational (the main window
  doesn't need to react, it just navigates).
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.submit_payload import InputSource, SubmitKind, SubmitPayload, WorkflowFields
from ...services.submit_use_case import PreparedBatch, SubmitUseCase
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr
from ..widgets.calculation_widget import CalculationWidget, CalculationFields
from ..widgets.input_builder_widget import InputBuilderWidget
from ..widgets.input_source_panel import InputSourcePanel
from ..widgets.workflow_widget import WorkflowWidget

_ACTIVITY_LIMIT = 50


class SubmitPage(QWidget):
    """Embedded unified-submit widget."""

    submit_requested = Signal(object)  # SubmitPayload
    create_only_requested = Signal(object)  # SubmitPayload
    use_as_input_received = Signal(list)  # list[InputSource]

    def __init__(
        self,
        state: Any,
        parent: QWidget | None = None,
        *,
        language: str = "en",
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        remote_available: bool = False,
        server_label: str = "",
    ):
        super().__init__(parent)
        self.state = state
        self._language = language
        self._on_status = on_status or (lambda msg: None)
        self._on_error = on_error or (lambda title, msg: None)
        self._remote_available = remote_available
        self._server_label = server_label
        self._use_case = SubmitUseCase()
        self._last_batch: PreparedBatch | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # ── 1. Input source panel ──────────────────────────────────────────
        self.input_panel = InputSourcePanel(
            language=language, remote_available=remote_available
        )
        self.input_panel.sources_changed.connect(self._on_sources_changed)
        self.input_panel.add_files_requested.connect(self._on_add_files_requested)
        layout.addWidget(self.input_panel)

        # ── 2. Mode tabs ───────────────────────────────────────────────────
        self.mode_tabs = QTabWidget()
        self._build_input_tab = InputBuilderWidget(language=language)
        self._calc_widget = CalculationWidget(language=language)
        self._workflow_widget = WorkflowWidget(
            language=language, calc_widget=self._calc_widget
        )
        self.mode_tabs.addTab(self._build_input_tab, tr("Build input file", language))
        self.mode_tabs.addTab(self._workflow_widget, tr("Build workflow", language))
        self.mode_tabs.currentChanged.connect(self._on_mode_tab_changed)
        layout.addWidget(self.mode_tabs, 1)

        # ── 3. Run options row ─────────────────────────────────────────────
        options_row = QHBoxLayout()
        options_row.setSpacing(8)

        self.server_pill = QLabel(self._server_pill_text())
        self.server_pill.setStyleSheet("padding: 4px 10px; border-radius: 10px;")
        options_row.addWidget(self.server_pill)

        self.max_parallel_label = QLabel(tr("Max parallel:", language))
        options_row.addWidget(self.max_parallel_label)
        self.max_parallel_spin = QSpinBox()
        self.max_parallel_spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.max_parallel_spin.setRange(1, 9999)
        self.max_parallel_spin.setValue(1)
        options_row.addWidget(self.max_parallel_spin)

        options_row.addStretch()

        self.refresh_btn = apply_button_role(
            QPushButton(tr("Refresh preview", language)),
            ButtonRole.INSTANT_ACTION,
        )
        self.refresh_btn.clicked.connect(self._on_refresh_preview_clicked)
        options_row.addWidget(self.refresh_btn)

        self.create_only_btn = QPushButton(tr("Create tasks only", language))
        self.create_only_btn.clicked.connect(self._on_create_only_clicked)
        options_row.addWidget(self.create_only_btn)

        self.submit_btn = QPushButton(tr("Submit", language))
        self.submit_btn.setObjectName("PrimaryBtn")
        apply_button_role(self.submit_btn, ButtonRole.PRIMARY_ACTION)
        self.submit_btn.clicked.connect(self._on_submit_clicked)
        options_row.addWidget(self.submit_btn)

        layout.addLayout(options_row)

        # ── 4. Preview pane ────────────────────────────────────────────────
        preview_box = QGroupBox(tr("Live preview", language))
        pv_layout = QVBoxLayout(preview_box)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        font = QFont("Courier New")
        font.setStyleHint(QFont.Monospace)
        self.preview.setFont(font)
        self.preview.setMinimumHeight(160)
        pv_layout.addWidget(self.preview)
        layout.addWidget(preview_box)

        # ── 5. Activity log ────────────────────────────────────────────────
        log_box = QGroupBox(tr("Activity log", language))
        log_layout = QVBoxLayout(log_box)
        self.activity_list = QListWidget()
        self.activity_list.setMaximumHeight(120)
        log_layout.addWidget(self.activity_list)
        layout.addWidget(log_box)

    # ── Public API ────────────────────────────────────────────────────────

    def apply_language(self, language: str) -> None:
        """Re-translate every static label."""
        self._language = language
        self.input_panel.apply_language(language)
        self.mode_tabs.setTabText(0, tr("Build input file", language))
        self.mode_tabs.setTabText(1, tr("Build workflow", language))
        self._build_input_tab.apply_language(language)
        self._calc_widget.apply_language(language)
        self._workflow_widget.apply_language(language)
        self.max_parallel_label.setText(tr("Max parallel:", language))
        self.refresh_btn.setText(tr("Refresh preview", language))
        self.create_only_btn.setText(tr("Create tasks only", language))
        self.submit_btn.setText(tr("Submit", language))

    def set_server_status(self, connected: bool, server_label: str = "") -> None:
        """Update the server pill text and active state."""
        self._server_label = server_label
        self._remote_available = connected
        self.server_pill.setText(self._server_pill_text())
        # Refresh the input panel tabs so the Remote tab visibility
        # toggles correctly when the user connects / disconnects.
        was_remote_available = self.input_panel.remote_tab is not None
        if was_remote_available != connected:
            self.input_panel._remote_available = connected
            # Easiest path: rebuild the tab area by toggling visibility.
            idx = self.input_panel.tabs.indexOf(self.input_panel.remote_tab) if self.input_panel.remote_tab else -1
            if connected and idx < 0 and self.input_panel.remote_tab is None:
                self.input_panel.remote_tab = self.input_panel._build_tab("remote")
                self.input_panel.remote_tab.btn_add.clicked.connect(self.input_panel._on_add_files_remote)
                self.input_panel.remote_tab.btn_remove.clicked.connect(self.input_panel._on_remove)
                self.input_panel.remote_tab.btn_clear.clicked.connect(self.input_panel._on_clear)
                self.input_panel.remote_tab.recursive_cb.toggled.connect(self.input_panel._on_recursive_toggled)
                self.input_panel.tabs.addTab(self.input_panel.remote_tab, tr("Remote", language))
            elif not connected and self.input_panel.remote_tab is not None:
                idx = self.input_panel.tabs.indexOf(self.input_panel.remote_tab)
                if idx >= 0:
                    self.input_panel.tabs.removeTab(idx)
                self.input_panel.remote_tab.deleteLater()
                self.input_panel.remote_tab = None

    def on_submission_result(self, payload: object) -> None:
        """Called by the main window after the worker completes."""
        # ``payload`` is a RunOperationOutcome — extract what we need for
        # the activity log.  We don't introspect it deeply here; the
        # Runs page is the authority on the resulting batch.
        batch_id = ""
        errors: list[str] = []
        try:
            records = list(getattr(payload, "records", []) or [])
            if records:
                batch_id = str(getattr(records[0], "run_id", ""))
            errors = list(getattr(payload, "errors", []) or [])
        except Exception:
            pass
        if errors:
            self._log(f"Submit failed: {'; '.join(errors)}")
            return
        if batch_id:
            self._log(f"Submitted: {batch_id}")
        else:
            self._log("Submitted.")

    def push_sources(self, sources: list[InputSource]) -> None:
        """Wire endpoint for the cross-page right-click menu."""
        self.input_panel.set_sources(list(sources))
        self.use_as_input_received.emit(list(sources))
        self._log(f"Pushed {len(sources)} source(s) from Files page.")

    def set_max_parallel(self, value: int) -> None:
        self.max_parallel_spin.setValue(int(value))

    def set_server_id(self, server_id: str) -> None:
        self._server_label = server_id
        self.server_pill.setText(self._server_pill_text())

    # ── Internal helpers ──────────────────────────────────────────────────

    def _server_pill_text(self) -> str:
        if not self._server_label:
            return tr("No server", self._language)
        return f"{tr('Server', self._language)}: {self._server_label}"

    def _log(self, message: str) -> None:
        self.activity_list.addItem(QListWidgetItem(message))
        items: deque = deque(maxlen=_ACTIVITY_LIMIT)
        for idx in range(self.activity_list.count()):
            items.append(self.activity_list.item(idx).text())
        # Trim to last N entries.
        if self.activity_list.count() > _ACTIVITY_LIMIT:
            for _ in range(self.activity_list.count() - _ACTIVITY_LIMIT):
                self.activity_list.takeItem(0)
        self._on_status(message)

    def _on_sources_changed(self, _sources: list[InputSource]) -> None:
        # The mode-tab availability could flip based on input kind
        # (e.g. switching between single / confflow).  We don't flip
        # yet — the user picks the mode explicitly.
        return None

    def _on_add_files_requested(self, side: str, _default_dir: str) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            tr("Select input files", self._language),
            "",
            "Input files (*.xyz *.gjf *.inp);;XYZ files (*.xyz);;GJF files (*.gjf);;INP files (*.inp);;All files (*)",
        )
        if side == "remote":
            self.input_panel.add_remote_paths(files)
        else:
            self.input_panel.add_local_paths(files)

    def _on_mode_tab_changed(self, _index: int) -> None:
        # Refresh the preview whenever the user switches tabs so they
        # always see the relevant content for the active mode.
        self._refresh_preview()

    def _on_refresh_preview_clicked(self) -> None:
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        idx = self.mode_tabs.currentIndex()
        if idx == 0:
            self._refresh_input_preview()
        else:
            self._refresh_workflow_preview()

    def _refresh_input_preview(self) -> None:
        try:
            content = self._build_input_tab.build_content()
        except Exception as exc:
            self.preview.setPlainText(f"Error: {exc}")
            return
        self.preview.setPlainText(content)

    def _refresh_workflow_preview(self) -> None:
        calc = self._calc_widget.calc_fields()
        try:
            spec = self._workflow_widget.build_spec(calc)
        except Exception as exc:
            self.preview.setPlainText(f"Build failed: {exc}")
            return
        self._workflow_widget.render_yaml_preview(spec)

    def _validate_active(self) -> dict[str, str]:
        idx = self.mode_tabs.currentIndex()
        if idx == 0:
            return self._validate_input_mode()
        return self._validate_workflow_mode()

    def _validate_input_mode(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        if not self.input_panel.sources():
            errors["inputs"] = tr("Add at least one input file.", self._language)
        if not self._build_input_tab.xyz_edit.text().strip():
            errors["xyz"] = tr("XYZ path is required.", self._language)
        return errors

    def _validate_workflow_mode(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        if not self.input_panel.sources():
            errors["inputs"] = tr("Add at least one input file.", self._language)
        errors.update(self._calc_widget.validate())
        errors.update(self._workflow_widget.validate())
        return errors

    def _build_payload(self, kind: SubmitKind) -> SubmitPayload | None:
        sources = self.input_panel.sources()
        if not sources:
            self._log(tr("No inputs selected.", self._language))
            return None
        # All sources must share a directory for the workflow YAML
        # landing pad; we pick the first one's parent.
        first = sources[0].path
        output_dir = first.parent if first.is_absolute() else Path(".")
        calc = self._calc_widget.fields()
        idx = self.mode_tabs.currentIndex()
        if idx == 0:
            # Build-input mode: we render via InputBuilderWidget. The
            # use case still uses the calc widget's fields to keep the
            # command template consistent; workflow is None.
            workflow: WorkflowFields | None = None
            program = self._build_input_tab.program
        else:
            workflow = WorkflowFields(
                work_dir_name=self._workflow_widget.work_dir_name(),
                steps=self._workflow_widget.steps(),
                advanced_options=self._workflow_widget.advanced_options(),
            )
            program = calc.program
        return SubmitPayload(
            kind=kind,
            inputs=sources,
            program=program,
            calc=calc,
            workflow=workflow,
            output_dir=output_dir,
            output_paths=[],
            server_id=self._server_label or "",
            remote_dir="/",
            max_parallel=self.max_parallel_spin.value(),
        )

    def _on_submit_clicked(self) -> None:
        self._refresh_preview()
        errors = self._validate_active()
        if errors:
            for field, msg in errors.items():
                self._log(f"Validation [{field}]: {msg}")
        idx = self.mode_tabs.currentIndex()
        kind: SubmitKind = "confflow" if idx == 1 else "single"
        payload = self._build_payload(kind)
        if payload is None:
            return
        self.submit_requested.emit(payload)

    def _on_create_only_clicked(self) -> None:
        self._refresh_preview()
        errors = self._validate_active()
        if errors:
            for field, msg in errors.items():
                self._log(f"Validation [{field}]: {msg}")
        idx = self.mode_tabs.currentIndex()
        kind: SubmitKind = "confflow" if idx == 1 else "single"
        payload = self._build_payload(kind)
        if payload is None:
            return
        self.create_only_requested.emit(payload)


__all__ = ["SubmitPage"]