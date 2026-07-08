"""Tests for :class:`InputSourcePanel` (Phase 14B refactor).

The existing ``test_confflow_wizard_xyz_batch`` /
``test_confflow_wizard_xyz_drop`` files cover the per-tab
``_TabBody.add_directory`` / drop event behaviour. This file
focuses on the *panel-level* API the Submit page actually uses:

* :meth:`add_local_paths` / :meth:`add_remote_paths`
* :meth:`sources` / :meth:`set_sources` (the cross-page wire endpoint)
* :meth:`set_recursive` / :meth:`is_recursive`
* The Remote tab's visibility based on ``remote_available``
* The ``sources_changed`` signal lifecycle
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.core.submit_payload import InputSource
from jobdesk_app.gui.widgets.input_source_panel import InputSourcePanel


def _write_xyz(path: Path, atoms: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(["H  0.0  0.0  0.0"] * atoms)
    path.write_text(f"{atoms}\nmock\n{body}\n", encoding="utf-8")


# --- add_local_paths / add_remote_paths -----------------------------------


def test_add_local_paths_filters_to_valid_suffixes(panel_factory, tmp_path):
    panel = panel_factory(remote_available=False)
    good_xyz = tmp_path / "a.xyz"
    good_gjf = tmp_path / "b.gjf"
    bad_txt = tmp_path / "c.txt"
    _write_xyz(good_xyz)
    good_gjf.write_text("%chk=test\n", encoding="utf-8")
    bad_txt.write_text("notes", encoding="utf-8")
    added = panel.add_local_paths([good_xyz, good_gjf, bad_txt])
    assert added == 2
    names = {s.path.name for s in panel.sources()}
    assert names == {"a.xyz", "b.gjf"}


def test_add_local_paths_dedupes(panel_factory, tmp_path):
    panel = panel_factory(remote_available=False)
    xyz = tmp_path / "dup.xyz"
    _write_xyz(xyz)
    first = panel.add_local_paths([xyz])
    second = panel.add_local_paths([xyz])
    assert first == 1
    assert second == 0
    assert len(panel.sources()) == 1


def test_add_remote_paths_noop_without_remote_tab(panel_factory, tmp_path):
    """When remote_available=False, add_remote_paths is a no-op."""
    panel = panel_factory(remote_available=False)
    remote_xyz = tmp_path / "remote.xyz"
    _write_xyz(remote_xyz)
    added = panel.add_remote_paths([str(remote_xyz)])
    assert added == 0
    assert panel.remote_tab is None


def test_add_remote_paths_routes_to_remote_tab(panel_factory, tmp_path):
    panel = panel_factory(remote_available=True)
    remote_xyz = tmp_path / "remote.xyz"
    _write_xyz(remote_xyz)
    added = panel.add_remote_paths([str(remote_xyz)])
    assert added == 1
    sources = panel.sources()
    assert len(sources) == 1
    assert sources[0].side == "remote"
    assert sources[0].path == remote_xyz


# --- set_sources / sources ------------------------------------------------


def test_set_sources_replaces_existing(panel_factory):
    panel = panel_factory(remote_available=True)
    panel.set_sources([
        InputSource(path=Path("a.xyz"), side="local", kind="xyz"),
        InputSource(path=Path("/remote/b.xyz"), side="remote", kind="xyz"),
    ])
    names = {s.path.name for s in panel.sources()}
    assert names == {"a.xyz", "b.xyz"}
    panel.set_sources([InputSource(path=Path("c.xyz"))])
    names = {s.path.name for s in panel.sources()}
    assert names == {"c.xyz"}


def test_sources_preserves_insertion_order(panel_factory, tmp_path):
    panel = panel_factory(remote_available=False)
    paths = []
    for i in range(3):
        p = tmp_path / f"mol_{i}.xyz"
        _write_xyz(p)
        paths.append(p)
    panel.add_local_paths(paths)
    out = [s.path for s in panel.sources()]
    assert out == paths


# --- recursive ------------------------------------------------------------


def test_set_recursive_toggles_checkbox(panel_factory):
    panel = panel_factory(remote_available=True)
    assert panel.is_recursive() is False
    panel.set_recursive(True)
    assert panel.is_recursive() is True
    panel.set_recursive(False)
    assert panel.is_recursive() is False


# --- Remote tab visibility ------------------------------------------------


def test_remote_tab_hidden_when_remote_unavailable(panel_factory):
    panel = panel_factory(remote_available=False)
    assert panel.remote_tab is None
    assert panel.tabs.count() == 1  # only Local


def test_remote_tab_visible_when_remote_available(panel_factory):
    panel = panel_factory(remote_available=True)
    assert panel.remote_tab is not None
    assert panel.tabs.count() == 2  # Local + Remote


def test_remote_tab_inherits_local_recursive_state(panel_factory):
    """The remote tab shares the same recursive checkbox state."""
    panel = panel_factory(remote_available=True)
    panel.set_recursive(True)
    assert panel.remote_tab is not None
    assert panel.remote_tab.recursive_cb.isChecked() is True


# --- sources_changed signal -----------------------------------------------


def test_sources_changed_emits_on_add(panel_factory, tmp_path, qtbot):
    panel = panel_factory(remote_available=False)
    xyz = tmp_path / "a.xyz"
    _write_xyz(xyz)
    with qtbot.waitSignal(panel.sources_changed, timeout=500):
        panel.add_local_paths([xyz])


def test_sources_changed_emits_on_clear(panel_factory, tmp_path, qtbot):
    panel = panel_factory(remote_available=False)
    xyz = tmp_path / "a.xyz"
    _write_xyz(xyz)
    panel.add_local_paths([xyz])
    # Trigger the panel-level signal (button click → _on_clear → emit).
    with qtbot.waitSignal(panel.sources_changed, timeout=500):
        panel._on_clear()


def test_sources_changed_emits_on_remove(panel_factory, tmp_path, qtbot):
    panel = panel_factory(remote_available=False)
    xyz = tmp_path / "a.xyz"
    _write_xyz(xyz)
    panel.add_local_paths([xyz])
    panel.local_tab.list_widget.setCurrentRow(0)
    with qtbot.waitSignal(panel.sources_changed, timeout=500):
        panel._on_remove()


# --- language switch ------------------------------------------------------


def test_apply_language_re_translates_tabs(panel_factory):
    panel = panel_factory(remote_available=True)
    panel.apply_language("zh")
    assert panel.tabs.tabText(0) == "\u672c\u5730"  # Local
    assert panel.tabs.tabText(1) == "\u8fdc\u7a0b"  # Remote
    panel.apply_language("en")
    assert panel.tabs.tabText(0) == "Local"
    assert panel.tabs.tabText(1) == "Remote"


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def panel_factory(qtbot):
    """Returns a factory that builds a panel with the given remote_available."""
    def _make(remote_available: bool) -> InputSourcePanel:
        panel = InputSourcePanel(language="en", remote_available=remote_available)
        qtbot.addWidget(panel)
        return panel

    return _make
