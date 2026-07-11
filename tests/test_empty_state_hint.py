"""Tests for :class:`EmptyStateHint` (Phase 2.1 GUI onboarding fix).

Mirrors the surface tested for the node-graph :class:`OnboardingCard`
(``tests/test_nodegraph/test_onboarding_card.py``) — same qtbot
fixtures, same findChild-by-objectName pattern — so the two empty-
state surfaces have a consistent test layout even though they live in
different packages.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QPushButton

from jobdesk_app.gui.widgets.empty_state_hint import EmptyStateHint


@pytest.fixture
def title_body():
    """Canonical English title / body used by most tests."""
    return ("Welcome to JobDesk", "Add a server from Settings to begin.")


def test_constructs_with_title_and_body(qtbot, title_body):
    title, body = title_body
    hint = EmptyStateHint(title_key=title, body_key=body)
    qtbot.addWidget(hint)

    assert hint._title_label.text() == title
    assert hint._body_label.text() == body
    # No buttons were passed in, so no action row should have been built.
    assert hint._action_buttons == {}


def test_action_button_click_emits_signal(qtbot):
    """Each action button emits its own action_id on click."""
    captured: list[str] = []

    hint = EmptyStateHint(
        title_key="No runs yet",
        body_key="Click Go to Submit.",
        action_texts=(
            ("go_to_submit", "Go to Submit"),
            ("show_examples", "Show example templates"),
        ),
    )
    qtbot.addWidget(hint)
    hint.action_requested.connect(captured.append)

    go_btn = next(
        btn for btn in hint.findChildren(QPushButton)
        if btn.objectName() == "EmptyStateAction_go_to_submit"
    )
    examples_btn = next(
        btn for btn in hint.findChildren(QPushButton)
        if btn.objectName() == "EmptyStateAction_show_examples"
    )

    go_btn.click()
    examples_btn.click()

    assert captured == ["go_to_submit", "show_examples"]


def test_apply_language_retranslates(qtbot):
    hint = EmptyStateHint(
        title_key="No server connected",
        body_key="Add a Linux SSH server from the Settings tab to browse and transfer files.",
        action_texts=(("open_settings", "Open Settings"),),
    )
    qtbot.addWidget(hint)

    # Default English content.
    assert hint._title_label.text() == "No server connected"
    assert "Settings tab" in hint._body_label.text()

    hint.apply_language("zh")

    # Title should now contain CJK characters (the Chinese value begins
    # with \u672a which is "未"). We assert against a specific substring
    # so a future punctuation tweak doesn't break this test.
    assert "\u672a" in hint._title_label.text()
    assert "Settings" not in hint._title_label.text()
    # The "Open Settings" button label is "打开设置" in zh.
    open_btn = next(
        btn for btn in hint.findChildren(QPushButton)
        if btn.objectName() == "EmptyStateAction_open_settings"
    )
    assert "\u6253\u5f00" in open_btn.text()  # "打开"


def test_no_actions_means_no_button_row(qtbot):
    hint = EmptyStateHint(
        title_key="Browse a remote directory",
        body_key="Pick a folder, then drop files here.",
        action_texts=(),
    )
    qtbot.addWidget(hint)

    # Title and body still rendered.
    assert hint._title_label.text() == "Browse a remote directory"
    assert hint._body_label.text() == "Pick a folder, then drop files here."
    # No button row means no action buttons exist at all.
    action_buttons = [
        btn for btn in hint.findChildren(QPushButton)
        if btn.objectName().startswith("EmptyStateAction_")
    ]
    assert action_buttons == []
