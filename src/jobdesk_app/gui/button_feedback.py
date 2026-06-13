"""Small helpers for transient button feedback states."""

from __future__ import annotations

from enum import Enum
from typing import Iterable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton, QWidget


class ButtonRole(str, Enum):
    PRIMARY_ACTION = "primary_action"
    REFRESH_ACTION = "refresh_action"
    TRANSFER_ACTION = "transfer_action"
    DANGER_ACTION = "danger_action"
    SETTINGS_ACTION = "settings_action"
    TEST_ACTION = "test_action"
    INSTANT_ACTION = "instant_action"


class FeedbackState(str, Enum):
    IDLE = "idle"
    PENDING = "pending"
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"


def _refresh_style(widget: QWidget) -> None:
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)
    widget.update()


def apply_button_role(button: QPushButton, role: ButtonRole | str) -> QPushButton:
    button.setProperty("buttonRole", ButtonRole(role).value)
    _refresh_style(button)
    return button


class ButtonFeedback:
    def __init__(
        self,
        button: QPushButton,
        role: ButtonRole | str = ButtonRole.PRIMARY_ACTION,
        *,
        group: Iterable[QWidget] | None = None,
        success_ms: int = 1200,
        error_ms: int | None = 2000,
    ) -> None:
        self.button = button
        self.role = ButtonRole(role)
        self.group = list(group or [])
        self.success_ms = success_ms
        self.error_ms = error_ms
        self._idle_text = button.text()
        self._idle_tooltip = button.toolTip()
        self._state = FeedbackState.IDLE
        self._saved_enabled: dict[QWidget, bool] = {}
        self._restore_timer = QTimer(button)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self.restore)

        apply_button_role(self.button, self.role)
        self._set_feedback_state(FeedbackState.IDLE)

    def set_idle_text(self, text: str) -> None:
        self._idle_text = text
        if self._state == FeedbackState.IDLE:
            self.button.setText(text)

    def pending(self, text: str) -> None:
        self._show_blocked_state(FeedbackState.PENDING, text)

    def success(self, text: str, *, timeout_ms: int | None = None) -> None:
        self._show_blocked_state(FeedbackState.SUCCESS, text)
        self._restore_timer.start(self.success_ms if timeout_ms is None else timeout_ms)

    def error(self, text: str, *, timeout_ms: int | None = None) -> None:
        self._show_blocked_state(FeedbackState.ERROR, text)
        restore_after = self.error_ms if timeout_ms is None else timeout_ms
        if restore_after is not None:
            self._restore_timer.start(restore_after)

    def blocked(self, reason: str) -> None:
        self._restore_timer.stop()
        if not self._saved_enabled:
            for widget in self._controlled_widgets():
                self._saved_enabled[widget] = widget.isEnabled()
        self.button.setToolTip(reason)
        for widget in self._controlled_widgets():
            widget.setEnabled(False)
            _refresh_style(widget)
        self._set_feedback_state(FeedbackState.BLOCKED)

    def restore(self) -> None:
        self._restore_timer.stop()
        self.button.setText(self._idle_text)
        self.button.setToolTip(self._idle_tooltip)
        for widget, was_enabled in self._saved_enabled.items():
            widget.setEnabled(was_enabled)
            _refresh_style(widget)
        self._saved_enabled.clear()
        self._set_feedback_state(FeedbackState.IDLE)

    def _show_blocked_state(self, state: FeedbackState, text: str) -> None:
        self._restore_timer.stop()
        if not self._saved_enabled:
            for widget in self._controlled_widgets():
                self._saved_enabled[widget] = widget.isEnabled()
        self.button.setText(text)
        for widget in self._controlled_widgets():
            widget.setEnabled(False)
            _refresh_style(widget)
        self._set_feedback_state(state)

    def _controlled_widgets(self) -> list[QWidget]:
        widgets: list[QWidget] = [self.button]
        for widget in self.group:
            if widget not in widgets:
                widgets.append(widget)
        return widgets

    def _set_feedback_state(self, state: FeedbackState) -> None:
        self._state = state
        self.button.setProperty("feedbackState", state.value)
        _refresh_style(self.button)
