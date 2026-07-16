"""Tests for the new Submit-page widgets' i18n (Phase 14D + Phase 10.6).

After Phase 10.6 the Submit page embeds ``InputSourcePanel`` plus the
``WorkflowGraphEditor`` (which is exercised by its own test files under
``tests/test_nodegraph/``). The Phase 14A ``CalculationWidget`` /
``WorkflowWidget`` / ``InputBuilderWidget`` were retired, so this file
focuses on:

* the ``tr()`` helper behaviour (EN / ZH / fallback / kwargs);
* ``InputSourcePanel`` user-visible labels switching to Chinese;
* the **invariant** that every ``tr()`` key in the still-live widget /
  page sources has a Chinese counterpart in :data:`ZH`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.i18n import ZH, tr
from jobdesk_app.gui.widgets.input_source_panel import InputSourcePanel

# --- fixtures --------------------------------------------------------------


@pytest.fixture
def en_panel(qtbot):
    panel = InputSourcePanel(language="en", remote_available=False)
    qtbot.addWidget(panel)
    return panel


@pytest.fixture
def zh_panel(qtbot):
    panel = InputSourcePanel(language="zh", remote_available=True)
    qtbot.addWidget(panel)
    return panel


# --- tr() helper -----------------------------------------------------------


def test_tr_helper_returns_zh_value():
    assert tr("Remove", "zh") == "\u79fb\u9664"


def test_tr_helper_returns_en_value():
    assert tr("Remove", "en") == "Remove"


def test_tr_helper_with_kwargs_in_zh():
    assert tr("Apply preset: {name}", "zh", name="b3lyp") == "\u5e94\u7528\u9884\u8bbe: b3lyp"


def test_tr_helper_with_kwargs_in_en():
    assert tr("Apply preset: {name}", "en", name="b3lyp") == "Apply preset: b3lyp"


def test_tr_helper_falls_back_to_en_text_for_unknown_key():
    unknown = "definitely_not_a_real_key"
    assert tr(unknown, "zh") == unknown


def test_tr_falls_back_to_input_when_zh_key_missing(monkeypatch):
    sentinel = "no_such_key_12345"
    assert tr(sentinel, "zh") == sentinel
    assert tr(sentinel, "en") == sentinel


# --- input source panel labels --------------------------------------------


def _find_button_by_text(parent, text: str):
    from PySide6.QtWidgets import QPushButton

    for btn in parent.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    return None


def test_zh_xyz_buttons_are_translated(zh_panel):
    """Add files / Add directory / Remove / Clear all switch to Chinese."""
    translated_texts = {
        "\u6dfb\u52a0\u6587\u4ef6\u2026",  # Add files…
        "\u6dfb\u52a0\u76ee\u5f55\u2026",  # Add directory…
        "\u79fb\u9664",                    # Remove
        "\u6e05\u7a7a",                    # Clear
    }
    actual = {
        btn.text()
        for btn in zh_panel.findChildren(
            __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton
        )
    }
    missing = translated_texts - actual
    assert not missing, f"missing Chinese buttons: {missing}"


def test_en_xyz_buttons_are_english(en_panel):
    from PySide6.QtWidgets import QPushButton

    actual = {btn.text() for btn in en_panel.findChildren(QPushButton)}
    assert "Add files\u2026" in actual
    assert "Add directory\u2026" in actual
    assert "Remove" in actual
    assert "Clear" in actual


def test_zh_input_source_panel_tabs(zh_panel):
    """Local / Remote tab labels switch to Chinese when remote_available=True."""
    assert zh_panel.tabs.tabText(0) == "\u672c\u5730"  # Local
    assert zh_panel.tabs.tabText(1) == "\u8fdc\u7a0b"  # Remote


def test_en_input_source_panel_tabs(en_panel):
    """Local tab always shows 'Local'; remote tab hidden when remote_available=False."""
    assert en_panel.tabs.tabText(0) == "Local"
    assert en_panel.tabs.count() == 1  # no Remote tab


# --- invariant: every tr() key has a Chinese counterpart -----------------


def _extract_tr_keys(path: str) -> set[str]:
    """Return the set of string literals passed as the first positional
    argument to ``tr(...)`` calls in ``path``.

    Uses :mod:`ast` so it correctly handles multi-line invocations like
    ``tr("long string", self._language)``.
    """
    src = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        else:
            continue
        if name != "tr" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.add(first.value)
    return keys


# Sources that still ship in Phase 10.6 — the legacy widgets are gone, but
# the live InputSourcePanel + Submit page are still here.
# Note: workflow_page.py is excluded as it's a deprecated backward-compat shim
# that just re-exports from workflow_page/__init__.py.
_LIVE_WIDGET_SOURCES = (
    "src/jobdesk_app/gui/widgets/input_source_panel.py",
    "src/jobdesk_app/gui/pages/workflow_page/__init__.py",
    "src/jobdesk_app/gui/pages/file_transfer_page.py",
    "src/jobdesk_app/gui/dialogs/submit_dialog.py",
)


@pytest.mark.parametrize("source", _LIVE_WIDGET_SOURCES)
def test_all_widget_tr_keys_have_zh_translations(source):
    """Every English string passed to ``tr()`` in the live widget sources
    must have a Chinese counterpart in :data:`ZH`."""
    keys = _extract_tr_keys(source)
    assert keys, f"no tr() keys found in {source} \u2014 has the import been wired?"
    missing = keys - set(ZH.keys())
    assert not missing, f"missing ZH translations in {source}: {missing}"
