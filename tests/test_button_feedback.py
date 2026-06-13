import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QPushButton, QWidget

from jobdesk_app.gui.button_feedback import (
    ButtonFeedback,
    ButtonRole,
    FeedbackState,
    apply_button_role,
)


def test_button_roles_match_required_style_contract():
    assert [role.value for role in ButtonRole] == [
        "primary_action",
        "refresh_action",
        "transfer_action",
        "danger_action",
        "settings_action",
        "test_action",
        "instant_action",
    ]


def test_apply_button_role_sets_property_and_returns_button(qtbot):
    button = QPushButton("Refresh")
    qtbot.addWidget(button)

    returned = apply_button_role(button, ButtonRole.REFRESH_ACTION)

    assert returned is button
    assert button.property("buttonRole") == "refresh_action"


def test_pending_sets_primary_feedback_and_restore_resets_button(qtbot):
    button = QPushButton("Submit")
    helper = ButtonFeedback(button, ButtonRole.PRIMARY_ACTION)
    qtbot.addWidget(button)

    helper.pending("Submitting...")

    assert button.text() == "Submitting..."
    assert not button.isEnabled()
    assert button.property("feedbackState") == "pending"
    assert button.property("buttonRole") == "primary_action"

    helper.restore()

    assert button.text() == "Submit"
    assert button.isEnabled()
    assert button.property("feedbackState") == "idle"


def test_success_sets_feedback_then_restores_after_timer(qtbot):
    button = QPushButton("Save")
    grouped = QWidget()
    helper = ButtonFeedback(
        button,
        ButtonRole.PRIMARY_ACTION,
        group=[grouped],
        success_ms=20,
    )
    qtbot.addWidget(button)
    qtbot.addWidget(grouped)

    helper.success("Saved")

    assert button.text() == "Saved"
    assert not button.isEnabled()
    assert not grouped.isEnabled()
    assert button.property("feedbackState") == "success"

    qtbot.waitUntil(
        lambda: button.text() == "Save" and button.isEnabled() and grouped.isEnabled(),
        timeout=1000,
    )
    assert button.property("feedbackState") == "idle"


def test_danger_error_keeps_danger_role_and_blocks_until_restore(qtbot):
    button = QPushButton("Delete")
    grouped = QWidget()
    helper = ButtonFeedback(
        button,
        ButtonRole.DANGER_ACTION,
        group=[grouped],
        error_ms=20,
    )
    qtbot.addWidget(button)
    qtbot.addWidget(grouped)

    helper.error("Delete failed")

    assert button.text() == "Delete failed"
    assert not button.isEnabled()
    assert not grouped.isEnabled()
    assert button.property("feedbackState") == "error"
    assert button.property("buttonRole") == "danger_action"

    helper.restore()

    assert button.text() == "Delete"
    assert button.isEnabled()
    assert grouped.isEnabled()
    assert button.property("feedbackState") == FeedbackState.IDLE.value
    assert button.property("buttonRole") == ButtonRole.DANGER_ACTION.value


def test_error_uses_constructor_default_restore_timer(qtbot):
    button = QPushButton("Delete")
    helper = ButtonFeedback(button, ButtonRole.DANGER_ACTION, error_ms=20)
    qtbot.addWidget(button)

    helper.error("Delete failed")

    assert button.text() == "Delete failed"
    assert button.property("feedbackState") == "error"

    qtbot.waitUntil(
        lambda: button.text() == "Delete" and button.property("feedbackState") == "idle",
        timeout=1000,
    )


def test_blocked_sets_tooltip_and_restores_idle_state(qtbot):
    button = QPushButton("Run")
    helper = ButtonFeedback(button, ButtonRole.TRANSFER_ACTION)
    qtbot.addWidget(button)

    helper.blocked("Connect to a server first")

    assert button.text() == "Run"
    assert not button.isEnabled()
    assert button.toolTip() == "Connect to a server first"
    assert button.property("feedbackState") == "blocked"
    assert button.property("buttonRole") == "transfer_action"

    helper.restore()

    assert button.text() == "Run"
    assert button.isEnabled()
    assert button.toolTip() == ""
    assert button.property("feedbackState") == FeedbackState.IDLE.value
