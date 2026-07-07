"""Tests for the ConfFlow wizard's workflow-page validation (Phase 9D-1).

Covers ``_WorkflowPage.isComplete()``, ``_compute_validation()``, the inline
hint labels (``work_dir_hint`` / ``steps_hint`` / ``adv_hint``), and the
``_touched`` set that gates when hints appear. Mirrors the structure of
``tests/test_confflow_wizard_calc_validation.py`` for Phase 9C.

The tests bypass wizard navigation (``wizard.next()``) on purpose: the
``_XyzPage`` is incomplete when no files are loaded, so stepping the wizard
forward never reaches ``_WorkflowPage``. They drive ``_WorkflowPage`` directly
via ``wizard.workflow_page`` instead.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.dialogs.confflow_wizard_dialog import ConfFlowWizard


@pytest.fixture
def wizard(qtbot):
    wiz = ConfFlowWizard(server_id="srv", remote_dir="/tmp/r")
    qtbot.addWidget(wiz)
    return wiz


@pytest.fixture
def workflow_page(wizard, qtbot):
    # Direct page access — we never advance the wizard, so _WorkflowPage
    # remains at its defaults (5 steps checked, work_dir placeholder).
    qtbot.wait_exposed(wizard)
    return wizard.workflow_page


def test_workflow_page_complete_with_defaults(workflow_page):
    """Fresh wizard has all 5 steps checked + default work_dir name."""
    assert workflow_page.isComplete() is True
    assert workflow_page._errors == {}
    # Sanity-check defaults.
    assert workflow_page.work_dir_edit.text() == "{basename}_confflow_work"
    assert all(cb.isChecked() for cb in workflow_page._step_checks.values())


def test_workflow_page_incomplete_when_work_dir_empty(workflow_page):
    """Clearing the work_dir box should block Next and surface an error."""
    workflow_page.work_dir_edit.setText("")
    assert workflow_page.isComplete() is False
    assert "work_dir" in workflow_page._errors
    assert "required" in workflow_page._errors["work_dir"].lower()


def test_workflow_page_incomplete_when_work_dir_whitespace(workflow_page):
    """Whitespace-only work_dir is treated as empty."""
    workflow_page.work_dir_edit.setText("   ")
    assert workflow_page.isComplete() is False
    assert "work_dir" in workflow_page._errors


def test_workflow_page_incomplete_when_work_dir_has_slash(workflow_page):
    """Work_dir containing '/' (or '\\') is rejected — it's a *name*, not a path."""
    workflow_page.work_dir_edit.setText("foo/bar")
    assert workflow_page.isComplete() is False
    assert "work_dir" in workflow_page._errors
    assert "/" in workflow_page._errors["work_dir"] or "\\" in workflow_page._errors["work_dir"]
    # Backslash variant too.
    workflow_page.work_dir_edit.setText("foo\\bar")
    assert workflow_page.isComplete() is False
    assert "work_dir" in workflow_page._errors


def test_workflow_page_incomplete_when_no_steps_selected(workflow_page):
    """Unchecking every step must surface a 'pick at least one' error."""
    for cb in workflow_page._step_checks.values():
        cb.setChecked(False)
    assert workflow_page.isComplete() is False
    assert "steps" in workflow_page._errors
    assert "step" in workflow_page._errors["steps"].lower()


def test_workflow_page_incomplete_when_duplicate_adv_keys(workflow_page):
    """Duplicate keys in the advanced textarea surface a specific error."""
    workflow_page.adv_edit.setPlainText("solvent=water\nsolvent=acetonitrile")
    assert workflow_page.isComplete() is False
    assert "adv" in workflow_page._errors
    assert "solvent" in workflow_page._errors["adv"]


def test_workflow_page_accepts_unique_adv_keys(workflow_page):
    """Two distinct keys should pass validation (sanity check)."""
    workflow_page.adv_edit.setPlainText("solvent=water\ncharge=0")
    assert workflow_page.isComplete() is True
    assert "adv" not in workflow_page._errors


def test_workflow_page_hint_labels_exist_and_start_empty(workflow_page):
    """All three hint labels exist with empty text on a fresh wizard."""
    for attr in ("work_dir_hint", "steps_hint", "adv_hint"):
        label = getattr(workflow_page, attr, None)
        assert label is not None, f"missing hint label: {attr}"
        assert label.text() == ""
        # Style should be wired so the hint is visible to the user.
        assert label.styleSheet() != ""
        assert label.wordWrap() is True


def test_workflow_page_hint_appears_on_invalid_work_dir(workflow_page):
    """After the user finishes editing an invalid work_dir, the hint appears."""
    workflow_page.work_dir_edit.setText("")
    workflow_page.work_dir_edit.editingFinished.emit()
    assert workflow_page.work_dir_hint.text() != ""
    assert "required" in workflow_page.work_dir_hint.text().lower()


def test_workflow_page_hint_clears_when_fixed(workflow_page):
    """Setting a valid work_dir back should clear the hint."""
    workflow_page.work_dir_edit.setText("")
    workflow_page.work_dir_edit.editingFinished.emit()
    assert workflow_page.work_dir_hint.text() != ""
    workflow_page.work_dir_edit.setText("valid_work_dir")
    workflow_page.work_dir_edit.editingFinished.emit()
    assert workflow_page.work_dir_hint.text() == ""
    assert workflow_page.isComplete() is True


def test_workflow_page_touched_tracks_work_dir(workflow_page):
    """editingFinished on work_dir_edit adds 'work_dir' to _touched."""
    assert "work_dir" not in workflow_page._touched
    workflow_page.work_dir_edit.editingFinished.emit()
    assert "work_dir" in workflow_page._touched


def test_workflow_page_step_toggle_marks_touched(workflow_page):
    """Any step setChecked(False) should mark 'steps' as touched."""
    assert "steps" not in workflow_page._touched
    # Pick one step and uncheck it.
    any_step = next(iter(workflow_page._step_checks.values()))
    any_step.setChecked(False)
    assert "steps" in workflow_page._touched


def test_workflow_page_adv_edit_marks_touched_on_first_keystroke(workflow_page):
    """The first textChanged on adv_edit marks 'adv' as touched (no editingFinished)."""
    assert "adv" not in workflow_page._touched
    workflow_page.adv_edit.setPlainText("solvent=water")
    assert "adv" in workflow_page._touched


def test_workflow_page_adv_hint_appears_on_duplicate_keys(workflow_page):
    """Touched adv field + duplicate keys should surface a hint message."""
    workflow_page.adv_edit.setPlainText("solvent=water\nsolvent=acetonitrile")
    assert workflow_page.adv_hint.text() != ""
    assert "Duplicate" in workflow_page.adv_hint.text() or "duplicate" in workflow_page.adv_hint.text()


def test_workflow_page_steps_hint_appears_when_all_unchecked(workflow_page):
    """After touching steps (via toggle) and unchecking all, hint should appear."""
    for cb in workflow_page._step_checks.values():
        cb.setChecked(False)
    assert workflow_page.steps_hint.text() != ""