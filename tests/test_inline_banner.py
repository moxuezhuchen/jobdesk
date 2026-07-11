"""Tests for :class:`InlineBanner` (Phase 3.1).

The banner is a small dismissible surface for non-modal warnings/errors. It
sits above the activity log so the user notices without scrolling.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication, QPushButton

from jobdesk_app.gui.widgets import InlineBanner


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_construct_invisible_by_default(qapp):
    banner = InlineBanner(language="en")
    assert not banner.isVisible()


def test_show_warning_makes_visible(qapp):
    banner = InlineBanner(language="en")
    banner.show_warning("oh no")
    assert banner.isVisible()
    assert banner._message.text() == "oh no"


def test_show_error_makes_visible(qapp):
    banner = InlineBanner(language="en")
    banner.show_error("ouch")
    assert banner.isVisible()


def test_dismiss_button_hides_banner(qapp):
    banner = InlineBanner(language="en")
    banner.show_warning("temp")
    assert banner.isVisible()
    dismiss_btn = banner.findChild(QPushButton, "InlineBannerDismiss")
    assert dismiss_btn is not None

    dismissed_count: list[int] = []

    def on_dismiss():
        dismissed_count.append(1)

    banner.dismissed.connect(on_dismiss)
    dismiss_btn.click()
    assert not banner.isVisible()
    assert len(dismissed_count) == 1


def test_dismiss_idempotent(qapp):
    banner = InlineBanner(language="en")
    banner.show_warning("temp")
    banner.dismiss()
    banner.dismiss()  # second call should not raise
    assert not banner.isVisible()


def test_apply_language_sets_tooltip(qapp):
    banner = InlineBanner(language="en")
    banner.apply_language("zh")
    tip = banner._dismiss.toolTip()
    assert tip != ""  # translated tool tip should be non-empty
