"""Tests for the new Submit-page widgets' i18n (Phase 14D).

Phase 14C.2 retired the QWizard + InputBuilderDialog. The widget bodies
were extracted into ``CalculationWidget``, ``WorkflowWidget``,
``InputBuilderWidget``, and ``InputSourcePanel``. This test file
exercises the user-visible labels on those widgets and asserts they
flip to Chinese in ``language="zh"`` mode.

It also walks every ``tr()`` call in the new widget sources and asserts
each key has a Chinese counterpart in :data:`ZH` — the same invariant
the old wizard i18n test enforced.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.i18n import ZH, tr
from jobdesk_app.gui.widgets.calculation_widget import CalculationWidget
from jobdesk_app.gui.widgets.input_builder_widget import InputBuilderWidget
from jobdesk_app.gui.widgets.input_source_panel import InputSourcePanel
from jobdesk_app.gui.widgets.workflow_widget import WorkflowWidget


# --- fixtures --------------------------------------------------------------


@pytest.fixture
def en_calc(qtbot):
    widget = CalculationWidget(language="en")
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def zh_calc(qtbot):
    widget = CalculationWidget(language="zh")
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def en_workflow(qtbot):
    widget = WorkflowWidget(language="en")
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def zh_workflow(qtbot):
    widget = WorkflowWidget(language="zh")
    qtbot.addWidget(widget)
    return widget


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


@pytest.fixture
def en_input_builder(qtbot):
    widget = InputBuilderWidget(language="en")
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def zh_input_builder(qtbot):
    widget = InputBuilderWidget(language="zh")
    qtbot.addWidget(widget)
    return widget


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


# --- calculation widget labels --------------------------------------------


def test_zh_calc_form_labels(zh_calc):
    """The form labels on CalculationWidget are translated."""
    from PySide6.QtWidgets import QLabel

    expected_labels = {
        "\u7a0b\u5e8f:",   # Program:
        "\u9884\u8bbe:",   # Preset:
        "\u65b9\u6cd5:",   # Method:
        "\u57fa\u7ec4:",   # Basis:
        "\u7535\u8377:",   # Charge:
        "\u81ea\u65cb\u591a\u91cd\u5ea6:",  # Multiplicity:
        "CPU \u6838\u6570:",  # CPU cores:
        "\u5185\u5b58:",   # Memory:
    }
    labels = {lbl.text() for lbl in zh_calc.findChildren(QLabel)}
    missing = expected_labels - labels
    assert not missing, f"missing Chinese labels: {missing}"


def test_zh_calc_validation_messages(zh_calc):
    """Empty method / basis / invalid spin produce Chinese error strings."""
    zh_calc.method_edit.clear()
    zh_calc.basis_edit.clear()
    errors = zh_calc.validate()
    assert errors["method"] == "\u65b9\u6cd5\u4e0d\u80fd\u4e3a\u7a7a\u3002"
    assert errors["basis"] == "\u57fa\u7ec4\u4e0d\u80fd\u4e3a\u7a7a\u3002"

    from unittest.mock import patch

    with patch.object(zh_calc.charge_spin, "value", return_value=-99):
        errors = zh_calc.validate()
    assert "charge" in errors
    assert errors["charge"] == "\u7535\u8377\u5fc5\u987b\u5728 -10 \u5230 10 \u4e4b\u95f4\u3002"


def test_en_calc_validation_messages(en_calc):
    en_calc.method_edit.clear()
    en_calc.basis_edit.clear()
    errors = en_calc.validate()
    assert errors["method"] == "Method is required."
    assert errors["basis"] == "Basis set is required."


def test_zh_orca_hint_switches(zh_calc):
    """Selecting ORCA updates orca_hint to Chinese text."""
    zh_calc.program_combo.setCurrentText("orca")
    assert any('\u4e00' <= ch <= '\u9fff' for ch in zh_calc.orca_hint.text()), (
        f"expected Chinese ORCA hint: {zh_calc.orca_hint.text()!r}"
    )


def test_en_orca_hint_stays_english(en_calc):
    en_calc.program_combo.setCurrentText("orca")
    assert not any('\u4e00' <= ch <= '\u9fff' for ch in en_calc.orca_hint.text()), (
        f"EN ORCA hint should not contain Chinese characters: {en_calc.orca_hint.text()!r}"
    )
    assert "ORCA" in en_calc.orca_hint.text()


# --- workflow widget labels -----------------------------------------------


def test_zh_workflow_widget_labels(zh_workflow):
    """Steps GroupBox / Work dir name label / YAML preview GroupBox switch to Chinese."""
    from PySide6.QtWidgets import QGroupBox, QLabel

    groupbox_titles = {gb.title() for gb in zh_workflow.findChildren(QGroupBox)}
    assert "\u6b65\u9aa4" in groupbox_titles  # Steps
    assert "YAML \u9884\u89c8" in groupbox_titles  # YAML preview

    labels = {lbl.text() for lbl in zh_workflow.findChildren(QLabel)}
    assert "\u5de5\u4f5c\u76ee\u5f55\u540d:" in labels  # Work dir name:


def test_zh_workflow_validation_messages(zh_workflow):
    zh_workflow.work_dir_edit.clear()
    errors = zh_workflow.validate()
    assert errors["work_dir"] == "\u5de5\u4f5c\u76ee\u5f55\u540d\u4e0d\u80fd\u4e3a\u7a7a\u3002"

    zh_workflow.work_dir_edit.setText("has/slash")
    errors = zh_workflow.validate()
    assert "/" in errors["work_dir"]
    assert any('\u4e00' <= ch <= '\u9fff' for ch in errors["work_dir"])


def test_zh_duplicate_advanced_key_message(zh_workflow):
    zh_workflow.adv_edit.setPlainText("solvent=water\nsolvent=toluene")
    errors = zh_workflow.validate()
    assert "\u91cd\u590d" in errors["adv"]


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
    actual = {btn.text() for btn in zh_panel.findChildren(__import__(
        "PySide6.QtWidgets", fromlist=["QPushButton"]
    ).QPushButton)}
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


# --- input builder widget labels ------------------------------------------


def test_zh_input_builder_labels(zh_input_builder):
    from PySide6.QtWidgets import QLabel

    labels = {lbl.text() for lbl in zh_input_builder.findChildren(QLabel)}
    expected = {
        "XYZ \u6587\u4ef6:",          # XYZ file:
        "\u8f6f\u4ef6:",              # Software:
        "\u9884\u8bbe:",              # Preset:
        "\u65b9\u6cd5/\u57fa\u7ec4:",  # Method/Basis:
        "\u5173\u952e\u8bcd:",         # Keywords:
        "\u591a\u91cd\u5ea6:",         # Mult:
        "\u7535\u8377:",               # Charge:
        "\u5185\u5b58:",               # Mem:
        "\u8fdb\u7a0b\u6570:",         # nproc:
        "\u8f93\u51fa:",               # Output:
    }
    missing = expected - labels
    assert not missing, f"missing Chinese labels: {missing}"


def test_en_input_builder_labels(en_input_builder):
    from PySide6.QtWidgets import QLabel

    labels = {lbl.text() for lbl in en_input_builder.findChildren(QLabel)}
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


def test_zh_input_builder_buttons(zh_input_builder):
    expected = {
        "\u6d4f\u89c8\u2026",   # Browse…
        "\u53e6\u5b58\u4e3a\u2026",  # Save as…
        "\u9884\u89c8",          # Preview
        "\u751f\u6210",          # Generate
        "\u5173\u95ed",          # Close
    }
    actual = {btn.text() for btn in zh_input_builder.findChildren(
        __import__("PySide6.QtWidgets", fromlist=["QPushButton"]).QPushButton
    )}
    missing = expected - actual
    assert not missing, f"missing Chinese buttons: {missing}"


def test_en_input_builder_buttons(en_input_builder):
    from PySide6.QtWidgets import QPushButton

    actual = {btn.text() for btn in en_input_builder.findChildren(QPushButton)}
    expected = {"Browse\u2026", "Save as\u2026", "Preview", "Generate", "Close"}
    missing = expected - actual
    assert not missing, f"missing English buttons: {missing}"


def test_zh_input_builder_placeholder(zh_input_builder):
    assert zh_input_builder.xyz_edit.placeholderText() == ".xyz \u6587\u4ef6\u8def\u5f84\u2026"
    assert zh_input_builder.output_edit.placeholderText() == "\u7559\u7a7a\u5219\u53ea\u9884\u89c8"


def test_en_input_builder_placeholder(en_input_builder):
    assert en_input_builder.xyz_edit.placeholderText() == "Path to .xyz file\u2026"
    assert en_input_builder.output_edit.placeholderText() == "Leave blank to preview only"


def test_input_builder_software_radio_labels_kept_english(zh_input_builder):
    """Gaussian / ORCA are technical names and stay English even in zh mode."""
    from PySide6.QtWidgets import QRadioButton

    actual = {rb.text() for rb in zh_input_builder.findChildren(QRadioButton)}
    assert any("Gaussian" in t for t in actual)
    assert any("ORCA" in t for t in actual)


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


_NEW_WIDGET_SOURCES = (
    "src/jobdesk_app/gui/widgets/calculation_widget.py",
    "src/jobdesk_app/gui/widgets/workflow_widget.py",
    "src/jobdesk_app/gui/widgets/input_builder_widget.py",
    "src/jobdesk_app/gui/widgets/input_source_panel.py",
    "src/jobdesk_app/gui/pages/submit_page.py",
    "src/jobdesk_app/gui/pages/file_transfer_page.py",
)


@pytest.mark.parametrize("source", _NEW_WIDGET_SOURCES)
def test_all_widget_tr_keys_have_zh_translations(source):
    """Every English string passed to ``tr()`` in the new widgets must
    have a Chinese counterpart in :data:`ZH`."""
    keys = _extract_tr_keys(source)
    assert keys, f"no tr() keys found in {source} — has the import been wired?"
    missing = keys - set(ZH.keys())
    assert not missing, f"missing ZH translations in {source}: {missing}"
