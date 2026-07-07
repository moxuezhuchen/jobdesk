"""Tests for the ConfFlow wizard's calculation-page validation (Phase 9C).

Covers ``_CalcPage.isComplete()``, ``_compute_validation()``, the inline
hint labels, and the ``_touched`` set that gates when hints appear.

The tests bypass wizard navigation (``wizard.next()``) on purpose: the
``_XyzPage`` is incomplete when no files are loaded, so stepping the wizard
forward never reaches ``_CalcPage``. They drive ``_CalcPage`` directly via
``wizard.calc_page`` instead.
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
def calc_page(wizard, qtbot):
    # Direct page access — we never advance the wizard, so _CalcPage remains
    # at its defaults (B3LYP / 6-31G(d) / charge 0 / mult 1 / nproc 8 / 4096 MB).
    qtbot.wait_exposed(wizard)
    return wizard.calc_page


def test_calc_page_complete_with_defaults(calc_page):
    """Fresh wizard uses sane defaults — isComplete() should be True."""
    assert calc_page.isComplete() is True
    assert calc_page._errors == {}
    fields = calc_page.calc_fields()
    assert fields["method"] == "B3LYP"
    assert fields["basis"] == "6-31G(d)"


def test_calc_page_incomplete_when_method_empty(calc_page):
    """Clearing the method box should block Next and surface an error."""
    calc_page.method_edit.setText("")
    assert calc_page.isComplete() is False
    assert "method" in calc_page._errors


def test_calc_page_incomplete_when_method_whitespace_only(calc_page):
    """Whitespace-only method is treated as empty."""
    calc_page.method_edit.setText("   ")
    assert calc_page.isComplete() is False
    assert "method" in calc_page._errors


def test_calc_page_incomplete_when_basis_empty(calc_page):
    """Clearing the basis box should block Next and surface an error."""
    calc_page.basis_edit.setText("")
    assert calc_page.isComplete() is False
    assert "basis" in calc_page._errors


def test_calc_page_memory_below_floor_is_invalid(calc_page):
    """Memory below 1024 MB (e.g. 512 MB) should fail validation."""
    calc_page.mem_spin.setValue(512)
    assert calc_page.isComplete() is False
    assert "mem" in calc_page._errors
    # The QSpinBox range clamps values >= 256; 1024 is the wizard's soft floor.
    assert calc_page._errors["mem"]


def test_calc_page_nproc_stays_valid_at_range_floor(calc_page):
    """nproc range is 1..256 so we can't go below 1 via the spinbox.

    The wizard's soft floor matches the QSpinBox lower bound, so isComplete()
    stays True at value=1. Verify the helper reports no error there.
    """
    calc_page.nproc_spin.setValue(1)
    assert calc_page.isComplete() is True
    assert "nproc" not in calc_page._errors
    calc_page.nproc_spin.setValue(2)
    assert calc_page.isComplete() is True


def test_calc_page_hint_label_starts_empty(calc_page):
    """All six hint labels exist with empty text on a fresh wizard."""
    for attr in (
        "method_hint",
        "basis_hint",
        "charge_hint",
        "mult_hint",
        "nproc_hint",
        "mem_hint",
    ):
        label = getattr(calc_page, attr, None)
        assert label is not None, f"missing hint label: {attr}"
        assert label.text() == ""


def test_calc_page_hint_appears_on_invalid_method(calc_page):
    """After the user finishes editing an invalid method, the hint appears."""
    calc_page.method_edit.setText("")
    calc_page.method_edit.editingFinished.emit()
    assert calc_page.method_hint.text() != ""
    # Helpful message — make sure it mentions the field.
    assert "Method" in calc_page.method_hint.text() or "required" in calc_page.method_hint.text()


def test_calc_page_hint_clears_when_field_fixed(calc_page):
    """Setting a valid method back should clear the hint."""
    calc_page.method_edit.setText("")
    calc_page.method_edit.editingFinished.emit()
    assert calc_page.method_hint.text() != ""
    calc_page.method_edit.setText("MP2")
    calc_page.method_edit.editingFinished.emit()
    assert calc_page.method_hint.text() == ""
    assert calc_page.isComplete() is True


def test_calc_page_touched_set_tracks_interactions(calc_page):
    """editingFinished on method_edit adds 'method' to _touched."""
    assert "method" not in calc_page._touched
    calc_page.method_edit.editingFinished.emit()
    assert "method" in calc_page._touched


def test_calc_page_charge_spin_touched_on_value_change(calc_page):
    """Setting a charge value marks the field as touched; valid value keeps hint empty."""
    calc_page.charge_spin.setValue(2)
    assert "charge" in calc_page._touched
    # 2 is in the -10..10 range, so the hint should stay empty.
    assert calc_page.charge_hint.text() == ""


def test_calc_page_complete_with_charge_out_of_range_via_helper(calc_page, monkeypatch):
    """Inject an out-of-range charge via monkey-patch and assert isComplete() flips.

    We can't reach value 99 through the spinbox (range -10..10), so we
    override ``charge_spin.value`` for the duration of the test. This
    verifies that ``_compute_validation`` actually checks the constraint and
    that ``isComplete()`` reacts to it.
    """
    monkeypatch.setattr(calc_page.charge_spin, "value", lambda: 99)
    assert calc_page.isComplete() is False
    assert "charge" in calc_page._errors
    # Restore so the spinbox behaves normally again.
    monkeypatch.undo()
    calc_page.charge_spin.setValue(0)
    assert calc_page.isComplete() is True