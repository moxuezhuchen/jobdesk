"""Tests for the Calculation widget's validation (Phase 14A refactor).

Covers :class:`CalculationWidget` (the embedded version of the wizard's
calc page). Validates ``is_complete()``, ``validate()``, the inline
hint labels, and the ``_touched`` set that gates when hints appear.

Phase 14C.2: the standalone wizard has been retired; the same widget
body now lives at ``jobdesk_app.gui.widgets.calculation_widget``. These
tests drive the widget directly — no QWizard, no QWizardPage.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.widgets.calculation_widget import CalculationWidget


@pytest.fixture
def calc_widget(qtbot):
    widget = CalculationWidget(language="en")
    qtbot.addWidget(widget)
    return widget


def test_calc_page_complete_with_defaults(calc_widget):
    """Fresh widget uses sane defaults — is_complete() should be True."""
    assert calc_widget.is_complete() is True
    assert calc_widget._errors == {}
    fields = calc_widget.calc_fields()
    assert fields["method"] == "B3LYP"
    assert fields["basis"] == "6-31G(d)"


def test_calc_page_incomplete_when_method_empty(calc_widget):
    """Clearing the method box should block submission and surface an error."""
    calc_widget.method_edit.setText("")
    assert calc_widget.is_complete() is False
    assert "method" in calc_widget._errors


def test_calc_page_incomplete_when_method_whitespace_only(calc_widget):
    """Whitespace-only method is treated as empty."""
    calc_widget.method_edit.setText("   ")
    assert calc_widget.is_complete() is False
    assert "method" in calc_widget._errors


def test_calc_page_incomplete_when_basis_empty(calc_widget):
    """Clearing the basis box should block submission and surface an error."""
    calc_widget.basis_edit.setText("")
    assert calc_widget.is_complete() is False
    assert "basis" in calc_widget._errors


def test_calc_page_memory_below_floor_is_invalid(calc_widget):
    """Memory below 1024 MB (e.g. 512 MB) should fail validation."""
    calc_widget.mem_spin.setValue(512)
    assert calc_widget.is_complete() is False
    assert "mem" in calc_widget._errors
    assert calc_widget._errors["mem"]


def test_calc_page_nproc_stays_valid_at_range_floor(calc_widget):
    """nproc range is 1..256 so the spinbox floor matches the soft floor."""
    calc_widget.nproc_spin.setValue(1)
    assert calc_widget.is_complete() is True
    assert "nproc" not in calc_widget._errors
    calc_widget.nproc_spin.setValue(2)
    assert calc_widget.is_complete() is True


def test_calc_page_hint_label_starts_empty(calc_widget):
    """All six hint labels exist with empty text on a fresh widget."""
    for attr in (
        "method_hint",
        "basis_hint",
        "charge_hint",
        "mult_hint",
        "nproc_hint",
        "mem_hint",
    ):
        label = getattr(calc_widget, attr, None)
        assert label is not None, f"missing hint label: {attr}"
        assert label.text() == ""


def test_calc_page_hint_appears_on_invalid_method(calc_widget):
    """After the user finishes editing an invalid method, the hint appears."""
    calc_widget.method_edit.setText("")
    calc_widget.method_edit.editingFinished.emit()
    assert calc_widget.method_hint.text() != ""
    assert "Method" in calc_widget.method_hint.text() or "required" in calc_widget.method_hint.text()


def test_calc_page_hint_clears_when_field_fixed(calc_widget):
    """Setting a valid method back should clear the hint."""
    calc_widget.method_edit.setText("")
    calc_widget.method_edit.editingFinished.emit()
    assert calc_widget.method_hint.text() != ""
    calc_widget.method_edit.setText("MP2")
    calc_widget.method_edit.editingFinished.emit()
    assert calc_widget.method_hint.text() == ""
    assert calc_widget.is_complete() is True


def test_calc_page_touched_set_tracks_interactions(calc_widget):
    """editingFinished on method_edit adds 'method' to _touched."""
    assert "method" not in calc_widget._touched
    calc_widget.method_edit.editingFinished.emit()
    assert "method" in calc_widget._touched


def test_calc_page_charge_spin_touched_on_value_change(calc_widget):
    """Setting a charge value marks the field as touched; valid value keeps hint empty."""
    calc_widget.charge_spin.setValue(2)
    assert "charge" in calc_widget._touched
    # 2 is in the -10..10 range, so the hint should stay empty.
    assert calc_widget.charge_hint.text() == ""


def test_calc_page_complete_with_charge_out_of_range_via_helper(calc_widget, monkeypatch):
    """Inject an out-of-range charge via monkey-patch and assert is_complete() flips.

    We can't reach value 99 through the spinbox (range -10..10), so we
    override ``charge_spin.value`` for the duration of the test. This
    verifies that ``_compute_validation`` actually checks the constraint
    and that ``is_complete()`` reacts to it.
    """
    monkeypatch.setattr(calc_widget.charge_spin, "value", lambda: 99)
    assert calc_widget.is_complete() is False
    assert "charge" in calc_widget._errors
    monkeypatch.undo()
    calc_widget.charge_spin.setValue(0)
    assert calc_widget.is_complete() is True
