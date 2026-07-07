"""Tests for the Input Builder dialog's i18n (Phase 12).

Mirrors the wizard i18n tests: confirm every user-visible string flips
to Chinese in ``language="zh"`` mode, and that every ``tr()`` key has a
Chinese counterpart in :data:`ZH`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.dialogs.input_builder_dialog import InputBuilderDialog
from jobdesk_app.gui.i18n import ZH, tr


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def en_dialog(qtbot):
    dlg = InputBuilderDialog(language="en")
    qtbot.addWidget(dlg)
    return dlg


@pytest.fixture
def zh_dialog(qtbot):
    dlg = InputBuilderDialog(language="zh")
    qtbot.addWidget(dlg)
    return dlg


def _find_button_by_text(parent, text: str):
    from PySide6.QtWidgets import QPushButton

    for btn in parent.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    return None


# --- window title ----------------------------------------------------------


def test_en_window_title(en_dialog):
    assert en_dialog.windowTitle() == "Input File Builder"


def test_zh_window_title(zh_dialog):
    assert "\u8f93\u5165\u6587\u4ef6\u751f\u6210\u5668" in zh_dialog.windowTitle()


# --- form labels and buttons -----------------------------------------------


def test_zh_labels(zh_dialog):
    from PySide6.QtWidgets import QLabel

    labels = {lbl.text() for lbl in zh_dialog.findChildren(QLabel)}
    expected = {
        "XYZ \u6587\u4ef6:",   # XYZ file:
        "\u8f6f\u4ef6:",          # Software:
        "\u9884\u8bbe:",          # Preset:
        "\u65b9\u6cd5/\u57fa\u7ec4:",  # Method/Basis:
        "\u5173\u952e\u8bcd:",    # Keywords:
        "\u591a\u91cd\u5ea6:",    # Mult:
        "\u7535\u8377:",          # Charge:
        "\u5185\u5b58:",          # Mem:
        "\u8fdb\u7a0b\u6570:",    # nproc:
        "\u8f93\u51fa:",          # Output:
    }
    missing = expected - labels
    assert not missing, f"missing Chinese labels: {missing}"


def test_en_labels(en_dialog):
    from PySide6.QtWidgets import QLabel

    labels = {lbl.text() for lbl in en_dialog.findChildren(QLabel)}
    expected = {
        "XYZ file:",
        "Software:",
        "Preset:",
        "Method/Basis:",
        "Keywords:",
        "Mult:",
        "Charge:",
        "Mem:",
        "nproc:",
        "Output:",
    }
    missing = expected - labels
    assert not missing, f"missing English labels: {missing}"


def test_zh_buttons(zh_dialog):
    expected = {
        "\u6d4f\u89c8\u2026",   # Browse…
        "\u53e6\u5b58\u4e3a\u2026",  # Save as…
        "\u9884\u89c8",          # Preview
        "\u751f\u6210",          # Generate
        "\u5173\u95ed",          # Close
    }
    actual = {btn.text() for btn in zh_dialog.findChildren(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton)}
    missing = expected - actual
    assert not missing, f"missing Chinese buttons: {missing}"


def test_en_buttons(en_dialog):
    actual = {btn.text() for btn in en_dialog.findChildren(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton)}
    expected = {"Browse\u2026", "Save as\u2026", "Preview", "Generate", "Close"}
    missing = expected - actual
    assert not missing, f"missing English buttons: {missing}"


# --- placeholder text ------------------------------------------------------


def test_zh_placeholder(zh_dialog):
    assert zh_dialog.xyz_edit.placeholderText() == ".xyz \u6587\u4ef6\u8def\u5f84\u2026"
    assert zh_dialog.output_edit.placeholderText() == "\u7559\u7a7a\u5219\u53ea\u9884\u89c8"


def test_en_placeholder(en_dialog):
    assert en_dialog.xyz_edit.placeholderText() == "Path to .xyz file\u2026"
    assert en_dialog.output_edit.placeholderText() == "Leave blank to preview only"


# --- program radio buttons keep their technical names ------------------------


def test_software_radio_labels_are_kept_english(zh_dialog):
    """Gaussian / ORCA are technical names and stay English even in zh mode."""
    actual = {rb.text() for rb in zh_dialog.findChildren(__import__('PySide6.QtWidgets', fromlist=['QRadioButton']).QRadioButton)}
    assert any("Gaussian" in t for t in actual)
    assert any("ORCA" in t for t in actual)


# --- invariant: every tr() key has a Chinese counterpart -------------------


def _extract_tr_keys(path: str) -> set[str]:
    """Return the set of string literals passed as the first positional
    argument to ``tr(...)`` calls in ``path``.
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


def test_all_input_builder_tr_keys_have_zh_translations():
    keys = _extract_tr_keys("src/jobdesk_app/gui/dialogs/input_builder_dialog.py")
    assert keys, "no tr() keys found — has the import been wired?"
    missing = keys - set(ZH.keys())
    assert not missing, f"missing ZH translations: {missing}"