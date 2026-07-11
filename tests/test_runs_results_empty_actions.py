"""Review-fix tests for the Runs-page empty-state intent split.

Before this fix, the Runs-page empty-state had two buttons:
``go_to_submit`` and ``show_examples``. Both emitted the same
``go_to_submit_requested`` signal, so MainWindow could not tell the
two intents apart. ``show_examples`` ended up landing the user on the
Submit page but never opened the Examples drawer, so the button text
"Show example templates" was effectively a lie -- the user still had
to click the toolbar Examples button to pick a template.

This file tests the fix at the RunsResultsPage layer: the two action
ids now raise two distinct signals so the MainWindow wiring can
chain a ``editor.open_examples_menu()`` call after the page switch.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.pages.runs_results_page import RunsResultsPage


@pytest.fixture
def page(qtbot):
    """Build a bare ``RunsResultsPage`` with a MagicMock state object.

    The page's constructor takes (state, log_cb, status_cb, ...); we
    pass no-op callables and avoid any service_factory so the test
    doesn't touch the network or filesystem.
    """
    page = RunsResultsPage(
        state=MagicMock(),
        log_cb=lambda message: None,
        status_cb=lambda message: None,
        coordinator_factory=None,
    )
    qtbot.addWidget(page)
    return page


def test_go_to_submit_emits_plain_navigation_signal(page):
    """``go_to_submit`` routes to the plain ``go_to_submit_requested`` signal."""
    captured: list[None] = []
    page.go_to_submit_requested.connect(lambda: captured.append(None))

    page._on_empty_action("go_to_submit")

    assert len(captured) == 1


def test_show_examples_emits_with_examples_signal(page):
    """``show_examples`` routes to the intent-specific signal.

    Review-fix: previously this raised ``go_to_submit_requested``
    which MainWindow treated as a plain nav, so the button text was
    effectively wrong. The new signal carries the "and open the
    Examples drawer" intent so MainWindow can chain the call.
    """
    plain: list[None] = []
    with_examples: list[None] = []
    page.go_to_submit_requested.connect(lambda: plain.append(None))
    page.go_to_submit_with_examples_requested.connect(
        lambda: with_examples.append(None)
    )

    page._on_empty_action("show_examples")

    assert plain == []
    assert len(with_examples) == 1


def test_unknown_action_is_a_noop(page):
    """An unknown action id must not raise and must not fire any signal."""
    plain: list[None] = []
    with_examples: list[None] = []
    page.go_to_submit_requested.connect(lambda: plain.append(None))
    page.go_to_submit_with_examples_requested.connect(
        lambda: with_examples.append(None)
    )

    page._on_empty_action("nothing_matches_this")

    assert plain == []
    assert with_examples == []


def test_go_to_submit_with_examples_requested_is_armed():
    """Defensive helper: the new signal must exist on the page class.

    ``_on_empty_action`` and the MainWindow wiring both reference
    ``go_to_submit_with_examples_requested``; if a future refactor
    renames or removes the signal without updating those two
    callers, the wiring would silently fall back to the old
    plain-nav behaviour. This test catches the regression early by
    asserting the attribute exists.
    """
    assert hasattr(RunsResultsPage, "go_to_submit_with_examples_requested")
