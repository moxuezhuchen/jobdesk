"""Tests for the ConfFlow wizard's i18n (Phase 12).

Verifies that the wizard's user-visible strings flip between English and
Chinese depending on the ``language`` constructor argument, and that the
``tr()`` helper returns Chinese for every key that the wizard uses.

Run-only-no-side-effects: ``required_permissions = ["all"]`` because the
underlying pytest plugins may need to spawn a Qt event loop.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.dialogs.confflow_wizard_dialog import (
    ConfFlowWizard,
    _CalcPage,
    _WorkflowPage,
    _XyzPage,
)
from jobdesk_app.gui.i18n import ZH, tr


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def en_wizard(qtbot):
    wiz = ConfFlowWizard(server_id="srv", remote_dir="/tmp/r", language="en")
    qtbot.addWidget(wiz)
    return wiz


@pytest.fixture
def zh_wizard(qtbot):
    wiz = ConfFlowWizard(server_id="srv", remote_dir="/tmp/r", language="zh")
    qtbot.addWidget(wiz)
    return wiz


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
    """Unknown keys return the original text in zh mode (no crash)."""
    unknown = "definitely_not_a_real_key"
    assert tr(unknown, "zh") == unknown


# --- wizard title and subtitles --------------------------------------------


def test_en_window_title(en_wizard):
    assert en_wizard.windowTitle() == "ConfFlow Workflow Wizard"


def test_zh_window_title(zh_wizard):
    assert "\u5de5\u4f5c\u6d41" in zh_wizard.windowTitle()


def test_en_xyz_page_title(en_wizard):
    assert en_wizard.xyz_page.title() == "Input XYZ files"


def test_zh_xyz_page_title(zh_wizard):
    assert zh_wizard.xyz_page.title() == "\u8f93\u5165 XYZ \u6587\u4ef6"


def test_en_calc_page_title(en_wizard):
    assert en_wizard.calc_page.title() == "Calculation settings"


def test_zh_calc_page_title(zh_wizard):
    assert zh_wizard.calc_page.title() == "\u8ba1\u7b97\u8bbe\u7f6e"


def test_en_workflow_page_title(en_wizard):
    assert en_wizard.workflow_page.title() == "Workflow settings & preview"


def test_zh_workflow_page_title(zh_wizard):
    assert zh_wizard.workflow_page.title() == "\u5de5\u4f5c\u6d41\u8bbe\u7f6e\u4e0e\u9884\u89c8"


def test_zh_subtitles_contain_chinese(zh_wizard):
    """Every page's subtitle switches to Chinese."""
    for page in (zh_wizard.xyz_page, zh_wizard.calc_page, zh_wizard.workflow_page):
        subtitle = page.subTitle()
        assert subtitle, "subtitle should not be empty"
        assert any('\u4e00' <= ch <= '\u9fff' for ch in subtitle), (
            f"expected Chinese characters in subtitle: {subtitle!r}"
        )


def test_en_subtitles_are_english(en_wizard):
    for page in (en_wizard.xyz_page, en_wizard.calc_page, en_wizard.workflow_page):
        subtitle = page.subTitle()
        assert subtitle
        assert subtitle.isascii(), f"expected ASCII subtitle: {subtitle!r}"


# --- form labels and buttons -----------------------------------------------


def _find_button_by_text(parent, text: str):
    """Return the first QPushButton whose text matches ``text``."""
    from PySide6.QtWidgets import QPushButton

    for btn in parent.findChildren(QPushButton):
        if btn.text() == text:
            return btn
    return None


def test_zh_xyz_buttons_are_translated(zh_wizard):
    """Add files / Add directory / Remove / Clear all switch to Chinese."""
    page = zh_wizard.xyz_page
    translated_texts = {
        "\u6dfb\u52a0\u6587\u4ef6\u2026",  # Add files…
        "\u6dfb\u52a0\u76ee\u5f55\u2026",  # Add directory…
        "\u79fb\u9664",  # Remove
        "\u6e05\u7a7a",  # Clear
    }
    actual = {_find_button_by_text(page, t) for t in translated_texts}
    assert all(actual), (
        f"missing Chinese buttons: "
        f"{translated_texts - {b.text() for b in page.findChildren(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton) if b}}"
    )


def test_en_xyz_buttons_are_english(en_wizard):
    page = en_wizard.xyz_page
    assert _find_button_by_text(page, "Add files…")
    assert _find_button_by_text(page, "Add directory…")
    assert _find_button_by_text(page, "Remove")
    assert _find_button_by_text(page, "Clear")


def test_zh_calc_form_labels(zh_wizard):
    """The form labels in _CalcPage are translated."""
    page = zh_wizard.calc_page
    expected_labels = {
        "\u7a0b\u5e8f:",  # Program:
        "\u9884\u8bbe:",  # Preset:
        "\u6700\u8fd1:",  # Recent:
        "\u65b9\u6cd5:",  # Method:
        "\u57fa\u7ec4:",  # Basis:
        "\u7535\u8377:",  # Charge:
        "\u81ea\u65cb\u591a\u91cd\u5ea6:",  # Multiplicity:
        "CPU \u6838\u6570:",  # CPU cores:
        "\u5185\u5b58:",  # Memory:
    }
    labels = {lbl.text() for lbl in page.findChildren(__import__('PySide6.QtWidgets', fromlist=['QLabel']).QLabel)}
    missing = expected_labels - labels
    assert not missing, f"missing Chinese labels: {missing}"


def test_zh_workflow_widget_labels(zh_wizard):
    """Steps GroupBox / Work dir name label / YAML preview GroupBox switch to Chinese."""
    page = zh_wizard.workflow_page
    groupbox_titles = {gb.title() for gb in page.findChildren(__import__('PySide6.QtWidgets', fromlist=['QGroupBox']).QGroupBox)}
    assert "\u6b65\u9aa4" in groupbox_titles  # Steps
    assert "YAML \u9884\u89c8" in groupbox_titles  # YAML preview

    labels = {lbl.text() for lbl in page.findChildren(__import__('PySide6.QtWidgets', fromlist=['QLabel']).QLabel)}
    assert "\u5de5\u4f5c\u76ee\u5f55\u540d:" in labels  # Work dir name:


# --- validation error messages ---------------------------------------------


def test_zh_calc_validation_messages(zh_wizard):
    """Empty method / basis / invalid spin produce Chinese error strings."""
    page: _CalcPage = zh_wizard.calc_page
    # Clear the fields so validation fails.
    page.method_edit.clear()
    page.basis_edit.clear()
    errors = page._compute_validation()
    assert errors["method"] == "\u65b9\u6cd5\u4e0d\u80fd\u4e3a\u7a7a\u3002"  # 方法不能为空。
    assert errors["basis"] == "\u57fa\u7ec4\u4e0d\u80fd\u4e3a\u7a7a\u3002"  # 基组不能为空。
    # Out-of-range charge — the spinbox clamps to its range, so to trigger
    # the charge error we have to bypass the spinbox and call _compute_validation
    # after manually setting an invalid value via the validator path. The
    # validation rule is "charge must be between -10 and 10"; the spinbox
    # range is exactly that, so the error only fires if a programmatic caller
    # sets a value out of range. Validate by patching the spin's value()
    # to return -99.
    from unittest.mock import patch

    with patch.object(page.charge_spin, "value", return_value=-99):
        errors = page._compute_validation()
    assert "charge" in errors, "expected charge error when value is out of range"
    assert errors["charge"] == "\u7535\u8377\u5fc5\u987b\u5728 -10 \u5230 10 \u4e4b\u95f4\u3002"


def test_en_calc_validation_messages(en_wizard):
    page: _CalcPage = en_wizard.calc_page
    page.method_edit.clear()
    page.basis_edit.clear()
    errors = page._compute_validation()
    assert errors["method"] == "Method is required."
    assert errors["basis"] == "Basis set is required."


def test_zh_workflow_validation_messages(zh_wizard):
    page: _WorkflowPage = zh_wizard.workflow_page
    page.work_dir_edit.clear()
    page._compute_validation()
    errors = page._compute_validation()
    assert errors["work_dir"] == "\u5de5\u4f5c\u76ee\u5f55\u540d\u4e0d\u80fd\u4e3a\u7a7a\u3002"  # 工作目录名不能为空。

    # Work dir with a slash.
    page.work_dir_edit.setText("has/slash")
    errors = page._compute_validation()
    assert "/" in errors["work_dir"]
    assert any('\u4e00' <= ch <= '\u9fff' for ch in errors["work_dir"])


def test_zh_duplicate_advanced_key_message(zh_wizard):
    page: _WorkflowPage = zh_wizard.workflow_page
    page.adv_edit.setPlainText("solvent=water\nsolvent=toluene")
    errors = page._compute_validation()
    assert "\u91cd\u590d" in errors["adv"]  # 重复


# --- ORCA hint and placeholder --------------------------------------------


def test_zh_orca_hint_switches(zh_wizard):
    """Selecting ORCA updates orca_hint to Chinese text."""
    page: _CalcPage = zh_wizard.calc_page
    page.program_combo.setCurrentText("orca")
    # The hint is updated synchronously via the currentTextChanged signal.
    assert any('\u4e00' <= ch <= '\u9fff' for ch in page.orca_hint.text()), (
        f"expected Chinese ORCA hint: {page.orca_hint.text()!r}"
    )


def test_en_orca_hint_stays_english(en_wizard):
    page: _CalcPage = en_wizard.calc_page
    page.program_combo.setCurrentText("orca")
    # The hint text contains a U+2014 em-dash, so it's not strictly ASCII,
    # but it must not contain any Chinese (CJK Unified Ideographs) characters.
    assert not any('\u4e00' <= ch <= '\u9fff' for ch in page.orca_hint.text()), (
        f"EN ORCA hint should not contain Chinese characters: {page.orca_hint.text()!r}"
    )
    assert "ORCA" in page.orca_hint.text()


# --- invariant: every wizard string has a Chinese translation --------------


def _extract_tr_keys(path: str) -> set[str]:
    """Return the set of string literals passed as the first positional
    argument to ``tr(...)`` calls in ``path``.

    Uses :mod:`ast` so it correctly handles multi-line invocations like

        tr(
            "long string",
            self._language,
        )

    that a line-by-line regex would miss.
    """
    import ast

    src = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Resolve the callee name (ignoring attribute access like self.tr).
        callee = node.func
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        else:
            continue
        if name != "tr":
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            keys.add(first.value)
    return keys


def test_all_wizard_tr_keys_have_zh_translations():
    """Every English string passed to ``tr()`` in the wizard dialog must
    have a Chinese counterpart in :data:`ZH`."""
    keys = _extract_tr_keys("src/jobdesk_app/gui/dialogs/confflow_wizard_dialog.py")
    assert keys, "no tr() keys found — has the import been wired?"
    missing = keys - set(ZH.keys())
    assert not missing, f"missing ZH translations: {missing}"


def test_tr_falls_back_to_input_when_zh_key_missing(monkeypatch):
    """If a tr() key is missing from ZH, tr() must return the original text."""
    sentinel = "no_such_key_12345"
    assert tr(sentinel, "zh") == sentinel
    assert tr(sentinel, "en") == sentinel