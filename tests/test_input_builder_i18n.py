"""Tests for the InputBuilder widget's i18n (Phase 14D).

Phase 14C.2 retired the ``InputBuilderDialog`` QDialog shell. The body
now lives in :class:`InputBuilderWidget`. These tests mirror the legacy
``test_input_builder_i18n`` but drive the embedded widget directly.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.i18n import ZH
from jobdesk_app.gui.widgets.input_builder_widget import InputBuilderWidget


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def en_widget(qtbot):
    widget = InputBuilderWidget(language="en")
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def zh_widget(qtbot):
    widget = InputBuilderWidget(language="zh")
    qtbot.addWidget(widget)
    return widget


# --- form labels ----------------------------------------------------------


def test_zh_labels(zh_widget):
    from PySide6.QtWidgets import QLabel

    labels = {lbl.text() for lbl in zh_widget.findChildren(QLabel)}
    expected = {
        "XYZ \u6587\u4ef6:",
        "\u8f6f\u4ef6:",
        "\u9884\u8bbe:",
        "\u65b9\u6cd5/\u57fa\u7ec4:",
        "\u5173\u952e\u8bcd:",
        "\u591a\u91cd\u5ea6:",
        "\u7535\u8377:",
        "\u5185\u5b58:",
        "\u8fdb\u7a0b\u6570:",
        "\u8f93\u51fa:",
    }
    missing = expected - labels
    assert not missing, f"missing Chinese labels: {missing}"


def test_en_labels(en_widget):
    from PySide6.QtWidgets import QLabel

    labels = {lbl.text() for lbl in en_widget.findChildren(QLabel)}
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


def test_zh_buttons(zh_widget):
    from PySide6.QtWidgets import QPushButton

    expected = {
        "\u6d4f\u89c8\u2026",
        "\u53e6\u5b58\u4e3a\u2026",
        "\u9884\u89c8",
        "\u751f\u6210",
        "\u5173\u95ed",
    }
    actual = {btn.text() for btn in zh_widget.findChildren(QPushButton)}
    missing = expected - actual
    assert not missing, f"missing Chinese buttons: {missing}"


def test_en_buttons(en_widget):
    from PySide6.QtWidgets import QPushButton

    actual = {btn.text() for btn in en_widget.findChildren(QPushButton)}
    expected = {"Browse\u2026", "Save as\u2026", "Preview", "Generate", "Close"}
    missing = expected - actual
    assert not missing, f"missing English buttons: {missing}"


def test_zh_placeholder(zh_widget):
    assert zh_widget.xyz_edit.placeholderText() == ".xyz \u6587\u4ef6\u8def\u5f84\u2026"
    assert zh_widget.output_edit.placeholderText() == "\u7559\u7a7a\u5219\u53ea\u9884\u89c8"


def test_en_placeholder(en_widget):
    assert en_widget.xyz_edit.placeholderText() == "Path to .xyz file\u2026"
    assert en_widget.output_edit.placeholderText() == "Leave blank to preview only"


def test_software_radio_labels_kept_english(zh_widget):
    """Gaussian / ORCA are technical names and stay English even in zh mode."""
    from PySide6.QtWidgets import QRadioButton

    actual = {rb.text() for rb in zh_widget.findChildren(QRadioButton)}
    assert any("Gaussian" in t for t in actual)
    assert any("ORCA" in t for t in actual)


# --- invariant: every tr() key has a Chinese counterpart -----------------


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
    keys = _extract_tr_keys("src/jobdesk_app/gui/widgets/input_builder_widget.py")
    assert keys, "no tr() keys found — has the import been wired?"
    missing = keys - set(ZH.keys())
    assert not missing, f"missing ZH translations: {missing}"
