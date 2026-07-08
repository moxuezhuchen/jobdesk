"""Tests for the Calculation widget's recent-presets strip (Phase 14A refactor).

Phase 9D-4 introduced the strip in-memory only. Phase 9E-1 promotes it
to a YAML-on-disk MRU backed by :class:`PresetFavouriteStore`. Each test
injects a fresh tmp-path store so it does not pollute the real app data
directory.

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
from jobdesk_app.services.recent_presets import PresetFavouriteStore


@pytest.fixture
def calc_widget(qtbot, tmp_path):
    store = PresetFavouriteStore(tmp_path / "recent_presets.yaml")
    widget = CalculationWidget(language="en", preset_store=store)
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


def test_recent_presets_shared_between_widget_instances(qtbot, tmp_path):
    """Two widgets backed by the same PresetFavouriteStore share state."""
    store = PresetFavouriteStore(tmp_path / "recent_presets.yaml")
    wiz_a = CalculationWidget(language="en", preset_store=store)
    qtbot.addWidget(wiz_a)
    _pick_preset(wiz_a, "b3lyp_631gd_opt_freq")
    # Build wiz_b AFTER the pick so its constructor hydrates from disk.
    wiz_b = CalculationWidget(language="en", preset_store=store)
    qtbot.addWidget(wiz_b)
    assert list(wiz_b.recent_presets.keys()) == ["b3lyp_631gd_opt_freq"]


def test_late_widget_hydrates_existing_mru_on_construction(qtbot, tmp_path):
    """A widget built against an already-populated store picks it up."""
    store = PresetFavouriteStore(tmp_path / "recent_presets.yaml")
    # Manually seed (simulates a previous run of the wizard).
    store.save(["preset_a", "preset_b"])
    widget = CalculationWidget(language="en", preset_store=store)
    qtbot.addWidget(widget)
    assert list(widget.recent_presets.keys()) == ["preset_a", "preset_b"]


def test_recent_presets_isolated_with_independent_stores(qtbot, tmp_path):
    """Two widgets with **different** stores do not share MRU state."""
    store_a = PresetFavouriteStore(tmp_path / "a.yaml")
    store_b = PresetFavouriteStore(tmp_path / "b.yaml")
    wiz_a = CalculationWidget(language="en", preset_store=store_a)
    wiz_b = CalculationWidget(language="en", preset_store=store_b)
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


# -- Phase 9E-1: persistence tests ---------------------------------------


def test_widget_picks_persist_to_disk(calc_widget):
    """Each preset selection writes the updated MRU back to the store."""
    _pick_preset(calc_widget, "b3lyp_631gd_opt_freq")
    saved = calc_widget._preset_store.load()
    assert saved == ["b3lyp_631gd_opt_freq"]


def test_widget_survives_corrupt_disk_store(qtbot, tmp_path):
    """A malformed YAML file should not crash widget construction."""
    bad = tmp_path / "recent_presets.yaml"
    bad.write_text(": this is : not : valid :", encoding="utf-8")
    store = PresetFavouriteStore(bad)
    widget = CalculationWidget(language="en", preset_store=store)
    qtbot.addWidget(widget)
    assert widget.recent_presets == OrderedDict()


def test_widget_dedupes_and_caps_on_hydration(qtbot, tmp_path):
    """A store holding duplicates or more than ``_MAX_RECENT_PRESETS`` is sanitised."""
    store = PresetFavouriteStore(tmp_path / "recent_presets.yaml")
    store.save(["p1", "p2", "p3", "p4", "p5", "p6", "p7", "p1"])
    widget = CalculationWidget(language="en", preset_store=store)
    qtbot.addWidget(widget)
    keys = list(widget.recent_presets.keys())
    assert len(keys) <= _MAX_RECENT_PRESETS
    assert len(set(keys)) == len(keys)


def test_widget_drops_non_string_entries_from_disk(qtbot, tmp_path):
    """Non-string entries in the YAML file are silently filtered out."""
    import yaml

    bad = tmp_path / "recent_presets.yaml"
    raw = {"recent_presets": ["good_preset", 42, None, "another_good"]}
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    store = PresetFavouriteStore(bad)
    widget = CalculationWidget(language="en", preset_store=store)
    qtbot.addWidget(widget)
    keys = list(widget.recent_presets.keys())
    assert all(isinstance(k, str) for k in keys)
    assert "good_preset" in keys
    assert "another_good" in keys


def test_store_round_trip_preserves_mru_order(tmp_path):
    """Saving and reloading an MRU keeps original order (most-recent-first)."""
    store = PresetFavouriteStore(tmp_path / "recent_presets.yaml")
    original = ["preset_a", "preset_b", "preset_c"]
    store.save(original)
    assert store.load() == original


def test_store_clear_removes_disk_file(tmp_path):
    """``clear()`` unlinks the YAML so a fresh widget sees no MRU."""
    store = PresetFavouriteStore(tmp_path / "recent_presets.yaml")
    store.save(["preset_a"])
    assert store.path.exists()
    store.clear()
    assert not store.path.exists()
    assert store.load() == []


def test_default_store_path_uses_app_data_dir(monkeypatch, tmp_path):
    """When no path is given, the store lands in the app data dir."""
    from jobdesk_app.services import recent_presets as rp

    monkeypatch.setattr(rp, "get_app_data_dir", lambda: tmp_path / "JobDesk")
    store = PresetFavouriteStore()
    store.save(["preset_a"])
    assert (tmp_path / "JobDesk" / "recent_presets.yaml").exists()
