"""End-to-end pytest-qt tests for the ConfFlow wizard.

These tests drive the actual ``ConfFlowWizard`` QWizard and assert that
form interactions produce the expected YAML — without invoking confflow
on a real server. They run on Windows even when confflow is not
installed (the wizard is GUI-only; only ``from_form`` -> ``to_yaml``
needs confflow, and we import it lazily inside a method that the test
never calls).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")
from PySide6.QtWidgets import QWizard

from jobdesk_app.core import workflow_spec
from jobdesk_app.gui.dialogs.confflow_wizard_dialog import ConfFlowWizard


@pytest.fixture
def xyz_file(tmp_path: Path) -> Path:
    """Write a tiny but valid XYZ file the wizard can ingest."""
    xyz = tmp_path / "methane.xyz"
    xyz.write_text(
        "5\n"
        "methane\n"
        "C   0.000000   0.000000   0.000000\n"
        "H   0.629118   0.629118   0.629118\n"
        "H  -0.629118  -0.629118   0.629118\n"
        "H  -0.629118   0.629118  -0.629118\n"
        "H   0.629118  -0.629118  -0.629118\n",
        encoding="utf-8",
    )
    return xyz


@pytest.fixture
def wizard(qtbot, xyz_file):
    wiz = ConfFlowWizard(server_id="test-server", remote_dir="/tmp/jobdesk-test")
    qtbot.addWidget(wiz)
    # Pre-populate XYZ page so isComplete() returns True and we can advance.
    wiz.xyz_page._xyz_paths = [xyz_file]
    wiz.xyz_page.list.addItem(str(xyz_file))
    return wiz


def test_wizard_starts_with_default_program_gaussian(qtbot, wizard):
    assert wizard.calc_page.program_combo.currentText() == "gaussian"


def test_wizard_orca_hint_appears_when_orca_selected(qtbot, wizard):
    wizard.calc_page.program_combo.setCurrentText("orca")
    hint = wizard.calc_page.orca_hint.text()
    assert "ORCA" in hint
    assert "single-point" in hint or "opt" in hint


def test_wizard_orca_unchecks_sp_step(qtbot, wizard):
    """Phase 7 UX: switching to ORCA auto-unchecks the SP workflow step."""
    sp_cb = wizard.workflow_page._step_checks["sp"]
    assert sp_cb.isChecked()  # default: checked
    wizard.calc_page.program_combo.setCurrentText("orca")
    assert not sp_cb.isChecked()


def test_wizard_preset_combo_populated_for_gaussian(qtbot, wizard):
    preset_names = [
        wizard.calc_page.preset_combo.itemData(i)
        for i in range(wizard.calc_page.preset_combo.count())
    ]
    assert None in preset_names  # "(manual)"
    assert "b3lyp_631gd_opt_freq" in preset_names
    # ORCA presets should not be visible while Gaussian is selected.
    assert "b3lyp_def2tzvp_opt_freq" not in preset_names


def test_wizard_preset_combo_repopulated_when_program_changes(qtbot, wizard):
    wizard.calc_page.program_combo.setCurrentText("orca")
    preset_names = [
        wizard.calc_page.preset_combo.itemData(i)
        for i in range(wizard.calc_page.preset_combo.count())
    ]
    assert "b3lyp_def2tzvp_opt_freq" in preset_names
    assert "b3lyp_631gd_opt_freq" not in preset_names


def test_wizard_picking_orca_preset_fills_method_basis(qtbot, wizard):
    wizard.calc_page.program_combo.setCurrentText("orca")
    # Find the index of "b3lyp_def2tzvp_opt_freq"
    target = None
    for i in range(wizard.calc_page.preset_combo.count()):
        if wizard.calc_page.preset_combo.itemData(i) == "b3lyp_def2tzvp_opt_freq":
            target = i
            break
    assert target is not None
    wizard.calc_page.preset_combo.setCurrentIndex(target)
    assert wizard.calc_page.method_edit.text() == "B3LYP D3BJ"
    assert "def2-TZVP" in wizard.calc_page.basis_edit.text()


def test_wizard_picking_gaussian_preset_fills_method_basis(qtbot, wizard):
    target = None
    for i in range(wizard.calc_page.preset_combo.count()):
        if wizard.calc_page.preset_combo.itemData(i) == "b3lyp_631gd_opt_freq":
            target = i
            break
    assert target is not None
    wizard.calc_page.preset_combo.setCurrentIndex(target)
    assert wizard.calc_page.method_edit.text() == "B3LYP"
    assert wizard.calc_page.basis_edit.text() == "6-31G(d)"
    assert wizard.calc_page.nproc_spin.value() == 8
    assert wizard.calc_page.mem_spin.value() == 16 * 1024


def test_wizard_workflow_page_builds_spec_with_assembled_keyword(qtbot, wizard, monkeypatch):
    """End-to-end: pick ORCA + manual fields, click Refresh, see assembled keyword."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    wizard.calc_page.program_combo.setCurrentText("orca")
    wizard.calc_page.method_edit.setText("b3lyp")
    wizard.calc_page.basis_edit.setText("def2-svp")
    calc = wizard.calc_page.calc_fields()
    spec = wizard.workflow_page.build_spec(calc)
    text = spec.to_yaml()
    assert "keyword: b3lyp def2-svp" in text
    # No double '!' from the ORCA template + the user's input.
    assert "!!" not in text


def test_wizard_orca_user_pastes_bang_keyword(qtbot, wizard, monkeypatch):
    """If the user pastes '!' into the method field, sanitize removes it."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    wizard.calc_page.program_combo.setCurrentText("orca")
    wizard.calc_page.method_edit.setText("! b3lyp")
    wizard.calc_page.basis_edit.setText("def2-svp")
    calc = wizard.calc_page.calc_fields()
    spec = wizard.workflow_page.build_spec(calc)
    text = spec.to_yaml()
    assert "!!" not in text
    assert "b3lyp def2-svp" in text


def test_wizard_advance_pages(qtbot, wizard):
    """The wizard can be advanced through all three pages."""
    # QWizard page IDs are populated only after restart()/show() is called.
    wizard.restart()
    assert wizard.currentId() == 0
    wizard.next()
    assert wizard.currentId() == 1
    wizard.next()
    assert wizard.currentId() == 2


def test_wizard_dry_run_status_label(qtbot, wizard):
    """Refresh preview updates the YAML preview and status label."""
    wizard.calc_page.program_combo.setCurrentText("gaussian")
    wizard.workflow_page._on_refresh_clicked()
    status = wizard.workflow_page.status_label.text()
    assert status  # Either "✓ YAML valid" or an error message, but non-empty.


def test_wizard_render_preview_text(qtbot, wizard):
    """Refresh preview writes the YAML to the preview text edit."""
    if not workflow_spec._CONFFLOW_AVAILABLE:
        pytest.skip("confflow package not installed in test env")
    wizard.calc_page.program_combo.setCurrentText("gaussian")
    wizard.workflow_page._on_refresh_clicked()
    preview_text = wizard.workflow_page.preview.toPlainText()
    assert "work_dir:" in preview_text
    assert "calc:" in preview_text