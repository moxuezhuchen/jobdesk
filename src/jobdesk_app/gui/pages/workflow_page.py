"""Sidebar Workflow page: list, save, and dispatch workflow presets.

Replaces the Phase-2 ``SubmitPage`` with a read-mostly view of
named presets plus a ``[Use this preset for submit]`` button that
navigates to Files with a pre-selected preset.
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...services.method_presets import MethodPreset, MethodPresetStore
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr


class WorkflowPage(QWidget):
    """Sidebar page (index 1) for browsing and saving workflow presets."""

    preset_chosen_for_submit = Signal(str, str)  # (name, source)
    preset_saved = Signal(str, str)              # (name, source)
    tour_requested = Signal()                    # propagated from optional embedded editor

    def __init__(
        self,
        state: Any,
        *,
        language: str = "en",
        preset_store: MethodPresetStore,
        settings_store: Any = None,
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str, str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._state = state
        self._store = preset_store
        self._settings_store = settings_store
        self._on_status = on_status or (lambda msg: None)
        self._on_error = on_error or (lambda title, msg: None)
        self._current_preset: MethodPreset | None = None
        self._remote_dir = "/"
        self._current_server_label = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        # Title
        self.preset_label = QLabel(tr("Workflow presets", language))
        font = self.preset_label.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        self.preset_label.setFont(font)
        layout.addWidget(self.preset_label)

        # Selector row
        selector_row = QHBoxLayout()
        selector_row.setSpacing(8)
        self.preset_combo = QComboBox()
        self.preset_combo.currentIndexChanged.connect(self._on_preset_combo_changed)
        selector_row.addWidget(self.preset_combo, 1)
        self.btn_new = QPushButton(tr("New workflow", language))
        self.btn_new.clicked.connect(self._on_new_workflow)
        selector_row.addWidget(self.btn_new)
        layout.addLayout(selector_row)

        # Step list (read-only)
        self.step_list = QListWidget()
        self.step_list.setMinimumHeight(180)
        layout.addWidget(self.step_list, 1)

        # Action row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_save_user = apply_button_role(
            QPushButton(tr("Save as user preset", language)),
            ButtonRole.INSTANT_ACTION,
        )
        self.btn_save_user.clicked.connect(self._on_save_user_clicked)
        action_row.addWidget(self.btn_save_user)
        action_row.addStretch()
        layout.addLayout(action_row)

        # Server pill row
        server_row = QHBoxLayout()
        server_row.setSpacing(8)
        self.server_pill = QLabel(tr("No server", language))
        self.server_pill.setStyleSheet("padding: 4px 10px; border-radius: 10px;")
        server_row.addWidget(self.server_pill)
        server_row.addStretch()
        layout.addLayout(server_row)

        # Dispatch
        self.btn_dispatch = QPushButton(tr("Use this preset for submit", language))
        self.btn_dispatch.setObjectName("WorkflowDispatchBtn")
        apply_button_role(self.btn_dispatch, ButtonRole.PRIMARY_ACTION)
        self.btn_dispatch.clicked.connect(self._on_use_for_submit)
        layout.addWidget(self.btn_dispatch)

        self._refresh_preset_combo()
        self._refresh_step_list()

    # Public API

    def apply_language(self, language: str) -> None:
        self._language = language
        self.preset_label.setText(tr("Workflow presets", language))
        self.btn_new.setText(tr("New workflow", language))
        self.btn_save_user.setText(tr("Save as user preset", language))
        self.btn_dispatch.setText(tr("Use this preset for submit", language))
        if not self._current_server_label:
            self.server_pill.setText(tr("No server", language))
        self._refresh_preset_combo()
        self._refresh_step_list()

    def set_server_status(self, connected: bool, server_label: str = "") -> None:
        self._current_server_label = server_label
        if server_label:
            self.server_pill.setText(
                tr("Server pill: {label}", self._language, label=server_label)
            )
        else:
            self.server_pill.setText(tr("No server", self._language))

    def set_remote_dir(self, remote_dir: str) -> None:
        self._remote_dir = remote_dir

    # Internal helpers

    def _refresh_preset_combo(self) -> None:
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._store.list_presets():
            label = f"{preset.name}  ({tr(preset.source.capitalize(), self._language)})"
            self.preset_combo.addItem(label, (preset.name, preset.source))
        self.preset_combo.blockSignals(False)
        if self.preset_combo.count() > 0:
            self.preset_combo.setCurrentIndex(0)
            self._on_preset_combo_changed(0)

    def _refresh_step_list(self) -> None:
        self.step_list.clear()
        if self._current_preset is None:
            return
        form = self._current_preset.spec.to_form()
        steps = form.get("steps", [])
        if not steps:
            self.step_list.addItem(QListWidgetItem("\u2014"))
            return
        for i, step in enumerate(steps, start=1):
            self.step_list.addItem(QListWidgetItem(f"{i}. {step}"))

    def _on_preset_combo_changed(self, _index: int) -> None:
        data = self.preset_combo.currentData()
        if not data:
            return
        name, source = data
        for p in self._store.list_presets():
            if p.name == name and p.source == source:
                self._current_preset = p
                break
        self._refresh_step_list()

    def _on_new_workflow(self) -> None:
        # Clear current selection — the user is expected to use the
        # SubmitDialog's [Edit workflow] button to author from scratch.
        self._current_preset = None
        self._refresh_step_list()

    def _on_save_user_clicked(self) -> None:
        if self._current_preset is None:
            self._on_error(
                tr("Save as user preset", self._language),
                tr("Add a step first.", self._language),
            )
            return
        name, ok = QInputDialog.getText(
            self,
            tr("Save as user preset", self._language),
            tr("Preset name:", self._language),
        )
        name = (name or "").strip()
        if not ok or not name:
            return
        self._save_as_user(name)

    def _save_as_user(self, name: str) -> None:
        if self._current_preset is None:
            self._on_error(
                tr("Save as user preset", self._language),
                tr("Add a step first.", self._language),
            )
            return
        try:
            path = self._store.save_user(name, self._current_preset.spec)
            self.preset_saved.emit(name, "user")
            self._on_status(f"Saved {path}")
            self._refresh_preset_combo()
        except Exception as exc:
            self._on_error("Save preset", str(exc))

    def _on_use_for_submit(self) -> None:
        if self._current_preset is None:
            self._on_error(
                tr("Use this preset for submit", self._language),
                tr("Pick a preset first.", self._language),
            )
            return
        self.preset_chosen_for_submit.emit(
            self._current_preset.name, self._current_preset.source
        )


__all__ = ["WorkflowPage"]
