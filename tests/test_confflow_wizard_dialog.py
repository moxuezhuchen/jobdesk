"""End-to-end pytest-qt tests for the Submit-page widgets (Phase 14D).

Phase 14C.2 retired the QWizard. The widget bodies are now embedded in
``CalculationWidget`` + ``WorkflowWidget`` + ``InputBuilderWidget`` and
live inside the new ``SubmitPage``. These tests exercise the user
interactions and the YAML preview / preset pipeline that the old
wizard tests covered.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.core import workflow_spec
from jobdesk_app.gui.widgets.calculation_widget import CalculationWidget
from jobdesk_app.gui.widgets.workflow_widget import WorkflowWidget


@pytest.fixture
def calc_widget(qtbot):
    widget = CalculationWidget(language="en")
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def workflow_widget(qtbot, calc_widget):
    widget = WorkflowWidget(language="en", calc_widget=calc_widget)
    qtbot.addWidget(widget)
    return widget


def test_calc_widget_starts_with_default_program_gaussian(calc_widget):
    assert calc_widget.program_combo.currentText() == "gaussian"


def test_calc_widget_orca_hint_appears_when_orca_selected(calc_widget):
    calc_widget.program_combo.setCurrentText("orca")
    hint = calc_widget.orca_hint.text()
    assert "ORCA" in hint
    assert "single-point" in hint or "opt" in hint


def test_calc_widget_preset_combo_populated_for_gaussian(calc_widget):
    preset_names = [
        calc_widget.preset_combo.itemData(i)
        for i in range(calc_widget.preset_combo.count())
    ]
    assert None in preset_names  # "(manual)"
    assert "b3lyp_631gd_opt_freq" in preset_names
    # ORCA presets should not be visible while Gaussian is selected.
    assert "b3lyp_def2tzvp_opt_freq" not in preset_names


def test_calc_widget_preset_combo_repopulated_when_program_changes(calc_widget):
    calc_widget.program_combo.setCurrentText("orca")
    preset_names = [
        calc_widget.preset_combo.itemData(i)
        for i in range(calc_widget.preset_combo.count())
    ]
    assert "b3lyp_def2tzvp_opt_freq" in preset_names
    assert "b3lyp_631gd_opt_freq" not in preset_names


def test_calc_widget_picking_orca_preset_fills_method_basis(calc_widget):
    calc_widget.program_combo.setCurrentText("orca")
    target = None
    for i in range(calc_widget.preset_combo.count()):
        if calc_widget.preset_combo.itemData(i) == "b3lyp_def2tzvp_opt_freq":
            target = i
            break
    assert target is not None
    calc_widget.preset_combo.setCurrentIndex(target)
    assert calc_widget.method_edit.text() == "B3LYP D3BJ"
    assert "def2-TZVP" in calc_widget.basis_edit.text()


def test_calc_widget_picking_gaussian_preset_fills_method_basis(calc_widget):
    target = None
    for i in range(calc_widget.preset_combo.count()):
        if calc_widget.preset_combo.itemData(i) == "b3lyp_631gd_opt_freq":
            target = i
            break
    assert target is not None
    calc_widget.preset_combo.setCurrentIndex(target)
    assert calc_widget.method_edit.text() == "B3LYP"
    assert calc_widget.basis_edit.text() == "6-31G(d)"
    assert calc_widget.nproc_spin.value() == 8
    assert calc_widget.mem_spin.value() == 16 * 1024


def test_workflow_widget_builds_spec_with_assembled_keyword(calc_widget, workflow_widget):
    """End-to-end: pick ORCA + manual fields, click Refresh, see assembled keyword."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    calc_widget.program_combo.setCurrentText("orca")
    calc_widget.method_edit.setText("b3lyp")
    calc_widget.basis_edit.setText("def2-svp")
    calc = calc_widget.calc_fields()
    spec = workflow_widget.build_spec(calc)
    text = spec.to_yaml()
    assert "keyword: b3lyp def2-svp" in text
    # No double '!' from the ORCA template + the user's input.
    assert "!!" not in text


def test_workflow_widget_dry_run_status_label(calc_widget, workflow_widget):
    """Refresh preview updates the YAML preview and status label."""
    calc_widget.program_combo.setCurrentText("gaussian")
    workflow_widget._on_refresh_clicked()
    status = workflow_widget.status_label.text()
    assert status


def test_workflow_widget_render_preview_text(calc_widget, workflow_widget):
    """Refresh preview writes the YAML to the preview text edit."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    calc_widget.program_combo.setCurrentText("gaussian")
    workflow_widget._on_refresh_clicked()
    preview_text = workflow_widget.preview.toPlainText()
    assert "work_dir:" in preview_text
    assert "calc:" in preview_text
