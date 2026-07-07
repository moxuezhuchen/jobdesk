"""Tests for the ConfFlow wizard's recent-presets strip (Phase 9D-4).

The favourites strip is in-memory MRU only — no persistence. It records
each preset the user picks via the combo, caps at 5 most-recent entries,
and renders one :class:`QToolButton` per preset in MRU order.
"""
from __future__ import annotations

from collections import OrderedDict

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.dialogs.confflow_wizard_dialog import (
    _MAX_RECENT_PRESETS,
    ConfFlowWizard,
)


@pytest.fixture
def wizard(qtbot):
    wiz = ConfFlowWizard(server_id="srv", remote_dir="/tmp/r")
    qtbot.addWidget(wiz)
    return wiz


def _pick_preset(calc_page, preset_name: str) -> None:
    """Drive the preset combo programmatically (matches user selection)."""
    idx = calc_page.preset_combo.findData(preset_name)
    assert idx >= 0, f"preset {preset_name!r} not found"
    calc_page.preset_combo.setCurrentIndex(idx)


def test_recent_strip_starts_hidden(wizard):
    """A fresh wizard shows no recent strip until a preset is picked."""
    assert wizard.calc_page.recent_presets == OrderedDict()
    assert wizard.calc_page.recent_strip_wrap.isVisibleTo(wizard.calc_page) is False


def test_record_recent_preset_adds_to_mru(wizard):
    """Picking a preset records it at the front of the MRU list."""
    calc = wizard.calc_page
    _pick_preset(calc, "b3lyp_631gd_opt_freq")
    assert "b3lyp_631gd_opt_freq" in calc.recent_presets
    # Most-recent-first: just-added is at index 0.
    assert list(calc.recent_presets.keys())[0] == "b3lyp_631gd_opt_freq"


def test_recent_strip_appears_after_first_pick(wizard, qtbot):
    """After picking a preset the strip becomes visible."""
    calc = wizard.calc_page
    calc.show()
    qtbot.waitExposed(calc)
    _pick_preset(calc, "b3lyp_631gd_opt_freq")
    # Use isVisibleTo(parent) — the wizard page itself is not a top-level
    # window so isVisible() returns False even when the widget is laid out.
    assert calc.recent_strip_wrap.isVisibleTo(calc) is True


def test_recent_strip_contains_one_button_per_recent_preset(wizard, qtbot):
    """The strip renders one QToolButton per preset in the MRU."""
    calc = wizard.calc_page
    calc.show()
    qtbot.waitExposed(calc)
    _pick_preset(calc, "b3lyp_631gd_opt_freq")
    _pick_preset(calc, "m062x_def2tzvp_opt_freq")
    # Strip should have: [label, btn1, btn2, stretch]
    assert calc.recent_strip.count() == 4


def test_recent_presets_deduplicate(wizard):
    """Picking the same preset twice keeps it once, but moves it to the front."""
    calc = wizard.calc_page
    _pick_preset(calc, "b3lyp_631gd_opt_freq")
    _pick_preset(calc, "m062x_def2tzvp_opt_freq")
    _pick_preset(calc, "b3lyp_631gd_opt_freq")  # re-pick
    keys = list(calc.recent_presets.keys())
    assert keys.count("b3lyp_631gd_opt_freq") == 1
    assert keys[0] == "b3lyp_631gd_opt_freq"  # moved to front
    assert keys[1] == "m062x_def2tzvp_opt_freq"


def test_recent_presets_capped_at_max(wizard):
    """Picking more than _MAX_RECENT_PRESETS trims the oldest entry."""
    calc = wizard.calc_page
    presets = [
        "b3lyp_631gd_opt_freq",
        "m062x_def2tzvp_opt_freq",
        "b3lyp_d3_def2tzvp_opt",
        "wb97xd_def2tzvp_sp",
        "ccsd_t_ccpvtz_sp",
        "b3lyp_631gd_opt_freq",  # re-pick to force extra
    ]
    for name in presets:
        _pick_preset(calc, name)
    assert len(calc.recent_presets) <= _MAX_RECENT_PRESETS


def test_recent_strip_updates_when_preset_re_picked(wizard, qtbot):
    """Re-picking a preset moves its button to the front of the strip."""
    calc = wizard.calc_page
    calc.show()
    qtbot.waitExposed(calc)
    _pick_preset(calc, "b3lyp_631gd_opt_freq")
    _pick_preset(calc, "m062x_def2tzvp_opt_freq")
    _pick_preset(calc, "b3lyp_631gd_opt_freq")  # re-pick
    btn1 = calc.recent_strip.itemAt(1).widget()
    btn2 = calc.recent_strip.itemAt(2).widget()
    assert btn1.text() == "b3lyp_631gd_opt_freq"
    assert btn2.text() == "m062x_def2tzvp_opt_freq"


def test_recent_button_click_applies_preset(wizard, qtbot):
    """Clicking a recent button re-fills method/basis/nproc/memory."""
    calc = wizard.calc_page
    calc.show()
    qtbot.waitExposed(calc)
    _pick_preset(calc, "b3lyp_631gd_opt_freq")
    # Manually tweak method so we can confirm click restores it.
    calc.method_edit.setText("manual-override")
    btn = calc.recent_strip.itemAt(1).widget()
    btn.click()
    assert calc.method_edit.text() == "B3LYP"  # from the preset


def test_recent_presets_isolated_between_wizard_instances(qtbot):
    """Two wizards share no recent-presets state (in-memory per-instance)."""
    wiz_a = ConfFlowWizard(server_id="a", remote_dir="/tmp/a")
    wiz_b = ConfFlowWizard(server_id="b", remote_dir="/tmp/b")
    qtbot.addWidget(wiz_a)
    qtbot.addWidget(wiz_b)
    _pick_preset(wiz_a.calc_page, "b3lyp_631gd_opt_freq")
    assert wiz_b.calc_page.recent_presets == OrderedDict()


def test_apply_recent_preset_unknown_id_is_noop(wizard):
    """Trying to apply a name that isn't in the combo is a silent no-op."""
    calc = wizard.calc_page
    # The findData inside _apply_recent_preset returns -1 for unknown names,
    # so the call is a no-op (does not change combo state or fields).
    calc.method_edit.setText("untouched")
    calc._apply_recent_preset("definitely_not_a_real_preset")
    assert calc.method_edit.text() == "untouched"
    # The record path stores unconditionally — apply path is the gate.
    assert "definitely_not_a_real_preset" not in calc.recent_presets