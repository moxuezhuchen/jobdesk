"""Tests for the Calculation widget's recent-presets strip (Phase 14A refactor).

The favourites strip is in-memory MRU only — no persistence. It records
each preset the user picks via the combo, caps at 5 most-recent entries,
and renders one ``QToolButton`` per preset in MRU order.

Phase 14C.2: the strip moved with the rest of the calc widget body from
``_CalcPage`` (QWizardPage) to ``CalculationWidget`` (QWidget).
"""
from __future__ import annotations

from collections import OrderedDict

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.widgets.calculation_widget import (
    _MAX_RECENT_PRESETS,
    CalculationWidget,
)


@pytest.fixture
def calc_widget(qtbot):
    widget = CalculationWidget(language="en")
    qtbot.addWidget(widget)
    return widget


def _pick_preset(calc_widget: CalculationWidget, preset_name: str) -> None:
    """Drive the preset combo programmatically (matches user selection)."""
    idx = calc_widget.preset_combo.findData(preset_name)
    assert idx >= 0, f"preset {preset_name!r} not found"
    calc_widget.preset_combo.setCurrentIndex(idx)


def test_recent_strip_starts_hidden(calc_widget):
    """A fresh widget shows no recent strip until a preset is picked."""
    assert calc_widget.recent_presets == OrderedDict()
    assert calc_widget.recent_strip_wrap.isVisibleTo(calc_widget) is False


def test_record_recent_preset_adds_to_mru(calc_widget):
    """Picking a preset records it at the front of the MRU list."""
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    assert "b3lyp_631gd_opt_freq" in calc_widget.recent_presets
    assert list(calc_widget.recent_presets.keys())[0] == "b3lyp_631gd_opt_freq"


def test_recent_strip_appears_after_first_pick(calc_widget, qtbot):
    """After picking a preset the strip becomes visible."""
    calc_widget.show()
    qtbot.waitExposed(calc_widget)
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    assert calc_widget.recent_strip_wrap.isVisibleTo(calc_widget) is True


def test_recent_strip_contains_one_button_per_recent_preset(calc_widget, qtbot):
    """The strip renders one QToolButton per preset in the MRU."""
    calc_widget.show()
    qtbot.waitExposed(calc_widget)
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    _pick_preset(calc_widget, "m062x_def2tzvp_opt_freq")
    # Strip should have: [label, btn1, btn2, stretch]
    assert calc_widget.recent_strip.count() == 4


def test_recent_presets_deduplicate(calc_widget):
    """Picking the same preset twice keeps it once, but moves it to the front."""
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    _pick_preset(calc_widget, "m062x_def2tzvp_opt_freq")
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    keys = list(calc_widget.recent_presets.keys())
    assert keys.count("b3lyp_631gd_opt_freq") == 1
    assert keys[0] == "b3lyp_631gd_opt_freq"
    assert keys[1] == "m062x_def2tzvp_opt_freq"


def test_recent_presets_capped_at_max(calc_widget):
    """Picking more than _MAX_RECENT_PRESETS trims the oldest entry."""
    presets = [
        "b3lyp_631gd_opt_freq",
        "m062x_def2tzvp_opt_freq",
        "b3lyp_d3_def2tzvp_opt",
        "wb97xd_def2tzvp_sp",
        "ccsd_t_ccpvtz_sp",
        "b3lyp_631gd_opt_freq",
    ]
    for name in presets:
        _pick_preset(calc_widget, name)
    assert len(calc_widget.recent_presets) <= _MAX_RECENT_PRESETS


def test_recent_strip_updates_when_preset_re_picked(calc_widget, qtbot):
    """Re-picking a preset moves its button to the front of the strip."""
    calc_widget.show()
    qtbot.waitExposed(calc_widget)
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    _pick_preset(calc_widget, "m062x_def2tzvp_opt_freq")
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    btn1 = calc_widget.recent_strip.itemAt(1).widget()
    btn2 = calc_widget.recent_strip.itemAt(2).widget()
    assert btn1.text() == "b3lyp_631gd_opt_freq"
    assert btn2.text() == "m062x_def2tzvp_opt_freq"


def test_recent_button_click_applies_preset(calc_widget, qtbot):
    """Clicking a recent button re-fills method/basis/nproc/memory."""
    calc_widget.show()
    qtbot.waitExposed(calc_widget)
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    calc_widget.method_edit.setText("manual-override")
    btn = calc_widget.recent_strip.itemAt(1).widget()
    btn.click()
    assert calc_widget.method_edit.text() == "B3LYP"


def test_recent_presets_isolated_between_widget_instances(qtbot):
    """Two widgets share no recent-presets state (in-memory per-instance)."""
    wiz_a = CalculationWidget(language="en")
    wiz_b = CalculationWidget(language="en")
    qtbot.addWidget(wiz_a)
    qtbot.addWidget(wiz_b)
    _pick_preset(wiz_a, "b3lyp_631gd_opt_freq")
    assert wiz_b.recent_presets == OrderedDict()


def test_apply_recent_preset_unknown_id_is_noop(calc_widget):
    """Trying to apply a name that isn't in the combo is a silent no-op."""
    calc_widget.method_edit.setText("untouched")
    calc_widget._apply_recent_preset("definitely_not_a_real_preset")
    assert calc_widget.method_edit.text() == "untouched"
    assert "definitely_not_a_real_preset" not in calc_widget.recent_presets
