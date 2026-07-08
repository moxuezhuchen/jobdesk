"""Tests for the Workflow widget's validation (Phase 14A refactor).

Covers :class:`WorkflowWidget` (the embedded version of the wizard's
workflow page). Validates ``is_complete()``, ``validate()``, the inline
hint labels (``work_dir_hint`` / ``steps_hint`` / ``adv_hint``), and the
``_touched`` set that gates when hints appear.

Phase 14C.2: the standalone wizard has been retired; the same widget
body now lives at ``jobdesk_app.gui.widgets.workflow_widget``. These
tests drive the widget directly — no QWizard, no QWizardPage.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.widgets.workflow_widget import WorkflowWidget


@pytest.fixture
def workflow_widget(qtbot):
    widget = WorkflowWidget(language="en")
    qtbot.addWidget(widget)
    return widget


def test_workflow_page_complete_with_defaults(workflow_widget):
    """Fresh widget has all 5 steps checked + default work_dir name."""
    assert workflow_widget.is_complete() is True
    assert workflow_widget._errors == {}
    assert workflow_widget.work_dir_edit.text() == "{basename}_confflow_work"
    assert all(cb.isChecked() for cb in workflow_widget._step_checks.values())


def test_workflow_page_incomplete_when_work_dir_empty(workflow_widget):
    """Clearing the work_dir box should block submission and surface an error."""
    workflow_widget.work_dir_edit.setText("")
    assert workflow_widget.is_complete() is False
    assert "work_dir" in workflow_widget._errors
    assert "required" in workflow_widget._errors["work_dir"].lower()


def test_workflow_page_incomplete_when_work_dir_whitespace(workflow_widget):
    """Whitespace-only work_dir is treated as empty."""
    workflow_widget.work_dir_edit.setText("   ")
    assert workflow_widget.is_complete() is False
    assert "work_dir" in workflow_widget._errors


def test_workflow_page_incomplete_when_work_dir_has_slash(workflow_widget):
    """Work_dir containing '/' (or '\\') is rejected — it's a *name*, not a path."""
    workflow_widget.work_dir_edit.setText("foo/bar")
    assert workflow_widget.is_complete() is False
    assert "work_dir" in workflow_widget._errors
    assert "/" in workflow_widget._errors["work_dir"] or "\\" in workflow_widget._errors["work_dir"]
    workflow_widget.work_dir_edit.setText("foo\\bar")
    assert workflow_widget.is_complete() is False
    assert "work_dir" in workflow_widget._errors


def test_workflow_page_incomplete_when_no_steps_selected(workflow_widget):
    """Unchecking every step must surface a 'pick at least one' error."""
    for cb in workflow_widget._step_checks.values():
        cb.setChecked(False)
    assert workflow_widget.is_complete() is False
    assert "steps" in workflow_widget._errors
    assert "step" in workflow_widget._errors["steps"].lower()


def test_workflow_page_incomplete_when_duplicate_adv_keys(workflow_widget):
    """Duplicate keys in the advanced textarea surface a specific error."""
    workflow_widget.adv_edit.setPlainText("solvent=water\nsolvent=acetonitrile")
    assert workflow_widget.is_complete() is False
    assert "adv" in workflow_widget._errors
    assert "solvent" in workflow_widget._errors["adv"]


def test_workflow_page_accepts_unique_adv_keys(workflow_widget):
    """Two distinct keys should pass validation (sanity check)."""
    workflow_widget.adv_edit.setPlainText("solvent=water\ncharge=0")
    assert workflow_widget.is_complete() is True
    assert "adv" not in workflow_widget._errors


def test_workflow_page_hint_labels_exist_and_start_empty(workflow_widget):
    """All three hint labels exist with empty text on a fresh widget."""
    for attr in ("work_dir_hint", "steps_hint", "adv_hint"):
        label = getattr(workflow_widget, attr, None)
        assert label is not None, f"missing hint label: {attr}"
        assert label.text() == ""
        assert label.styleSheet() != ""
        assert label.wordWrap() is True


def test_workflow_page_hint_appears_on_invalid_work_dir(workflow_widget):
    """After the user finishes editing an invalid work_dir, the hint appears."""
    workflow_widget.work_dir_edit.setText("")
    workflow_widget.work_dir_edit.editingFinished.emit()
    assert workflow_widget.work_dir_hint.text() != ""
    assert "required" in workflow_widget.work_dir_hint.text().lower()


def test_workflow_page_hint_clears_when_fixed(workflow_widget):
    """Setting a valid work_dir back should clear the hint."""
    workflow_widget.work_dir_edit.setText("")
    workflow_widget.work_dir_edit.editingFinished.emit()
    assert workflow_widget.work_dir_hint.text() != ""
    workflow_widget.work_dir_edit.setText("valid_work_dir")
    workflow_widget.work_dir_edit.editingFinished.emit()
    assert workflow_widget.work_dir_hint.text() == ""
    assert workflow_widget.is_complete() is True


def test_workflow_page_touched_tracks_work_dir(workflow_widget):
    """editingFinished on work_dir_edit adds 'work_dir' to _touched."""
    assert "work_dir" not in workflow_widget._touched
    workflow_widget.work_dir_edit.editingFinished.emit()
    assert "work_dir" in workflow_widget._touched


def test_workflow_page_step_toggle_marks_touched(workflow_widget):
    """Any step setChecked(False) should mark 'steps' as touched."""
    assert "steps" not in workflow_widget._touched
    any_step = next(iter(workflow_widget._step_checks.values()))
    any_step.setChecked(False)
    assert "steps" in workflow_widget._touched


def test_workflow_page_adv_edit_marks_touched_on_first_keystroke(workflow_widget):
    """The first textChanged on adv_edit marks 'adv' as touched (no editingFinished)."""
    assert "adv" not in workflow_widget._touched
    workflow_widget.adv_edit.setPlainText("solvent=water")
    assert "adv" in workflow_widget._touched


def test_workflow_page_adv_hint_appears_on_duplicate_keys(workflow_widget):
    """Touched adv field + duplicate keys should surface a hint message."""
    workflow_widget.adv_edit.setPlainText("solvent=water\nsolvent=acetonitrile")
    assert workflow_widget.adv_hint.text() != ""
    assert "Duplicate" in workflow_widget.adv_hint.text() or "duplicate" in workflow_widget.adv_hint.text()


def test_workflow_page_steps_hint_appears_when_all_unchecked(workflow_widget):
    """After touching steps (via toggle) and unchecking all, hint should appear."""
    for cb in workflow_widget._step_checks.values():
        cb.setChecked(False)
    assert workflow_widget.steps_hint.text() != ""
