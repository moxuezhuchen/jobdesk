"""Library group headers (Phase IMP-04).

Tests cover the four behaviors the IMP-04 deliverable lists:

1. Default state: every group expanded, all 10 buttons visible.
2. Collapsing Calcs hides the Calcs group buttons but leaves Inputs
   and Sentinels visible.
3. Searching the collapsed group's name while collapsed does NOT
   un-hide the buttons (the search filter respects the
   ``_hidden_by_topology`` semantics the collapse promotes).
4. Persisted collapsed state round-trips through ``GuiSettingsStore``:
   collapsing a group, reloading the panel, sees it still collapsed.
"""
from __future__ import annotations

import pytest

from jobdesk_app.gui.nodegraph.library import (
    GROUP_CALCS,
    GROUP_INPUTS,
    GROUP_SENTINELS,
    GROUPS,
    NodeLibraryPanel,
    PALETTE_ORDER,
)
from jobdesk_app.gui.nodegraph.model import NodeKind
from jobdesk_app.services.gui_settings import GuiSettings, GuiSettingsStore


@pytest.fixture
def store(tmp_path):
    s = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    s.save(GuiSettings(show_onboarding=False, collapsed_library_groups=()))
    return s


@pytest.fixture
def panel(qtbot, store):
    widget = NodeLibraryPanel(language="en", settings_store=store)
    widget.resize(260, 480)
    qtbot.addWidget(widget)
    widget.show()
    qtbot.waitUntil(lambda: widget.isVisible(), timeout=500)
    return widget


def _all_kinds() -> tuple[NodeKind, ...]:
    return PALETTE_ORDER


# ── default state ────────────────────────────────────────────────────


def test_default_state_all_buttons_visible(panel):
    """Out of the box, every group is expanded and every button shows."""
    visible = {k for k in _all_kinds() if panel.is_kind_shown(k)}
    assert visible == set(_all_kinds())


def test_default_state_no_group_collapsed(panel):
    assert panel.collapsed_groups() == ()


def test_three_group_headers_exist(panel):
    """There are exactly three group headers, one per GROUPS entry."""
    assert len(panel._group_headers) == len(GROUPS)
    assert set(panel._group_headers) == {GROUP_INPUTS, GROUP_CALCS, GROUP_SENTINELS}


# ── collapsing a group ───────────────────────────────────────────────


def test_collapse_calcs_hides_only_calcs_members(panel):
    panel.set_group_collapsed(GROUP_CALCS, True)

    visible = {k for k in _all_kinds() if panel.is_kind_shown(k)}
    assert visible == {NodeKind.XYZ_FILE, NodeKind.OUTPUT}, (
        f"only Inputs + Sentinels should remain: got {visible}"
    )


def test_collapse_calcs_persists_via_set_group_collapsed(panel, store):
    panel.set_group_collapsed(GROUP_CALCS, True)
    # disk: reloading the store sees "calcs" in the persisted list.
    reloaded = store.load()
    assert GROUP_CALCS in reloaded.collapsed_library_groups
    assert tuple(reloaded.collapsed_library_groups) == (GROUP_CALCS,)


def test_search_while_calcs_collapsed_does_not_unhide_calcs(panel):
    """The filter respects collapsed groups; typing 'geometry' must
    NOT bring the OPT row back even though it matches the search.

    We pick a query (``"geometry"``) that matches the OPT kind but
    not XYZ_FILE, so the assertion on XYZ_FILE checks the search
    filter itself isn't accidentally hiding it through a sibling
    path; the Calcs members should still be hidden by the
    collapse-aware path.
    """
    panel.set_group_collapsed(GROUP_CALCS, True)

    panel._search_box.setText("geometry")

    visible = {k for k in _all_kinds() if panel.is_kind_shown(k)}
    # Calcs members all still hidden — even OPT, which would match
    # "geometry" if the group wasn't collapsed.
    assert NodeKind.OPT not in visible
    assert NodeKind.SINGLE_POINT not in visible
    assert NodeKind.FREQUENCY not in visible
    assert NodeKind.TS not in visible
    assert NodeKind.PRE_OPT not in visible
    assert NodeKind.CONF_GEN not in visible
    assert NodeKind.REFINE not in visible
    assert NodeKind.ADVANCED not in visible
    # XYZ_FILE doesn't match "geometry" → also hidden by the
    # search filter; OUTPUT / OUTPUT also doesn't match.
    assert NodeKind.XYZ_FILE not in visible
    assert NodeKind.OUTPUT not in visible


def test_expand_calcs_restores_calcs_members(panel):
    panel.set_group_collapsed(GROUP_CALCS, True)
    panel.set_group_collapsed(GROUP_CALCS, False)
    visible = {k for k in _all_kinds() if panel.is_kind_shown(k)}
    assert visible == set(_all_kinds())


def test_collapse_inputs_hides_only_inputs_members(panel):
    panel.set_group_collapsed(GROUP_INPUTS, True)
    visible = {k for k in _all_kinds() if panel.is_kind_shown(k)}
    assert NodeKind.XYZ_FILE not in visible
    # Sentinels + Calcs still visible.
    assert NodeKind.OUTPUT in visible
    assert NodeKind.OPT in visible


def test_collapse_sentinels_hides_output_even_when_graph_empty(panel):
    """Even with no graph at all, collapsing Sentinels must hide OUTPUT."""
    panel.set_group_collapsed(GROUP_SENTINELS, True)
    visible = {k for k in _all_kinds() if panel.is_kind_shown(k)}
    assert NodeKind.OUTPUT not in visible
    # Inputs and Calcs remain visible.
    assert NodeKind.XYZ_FILE in visible
    assert NodeKind.OPT in visible


def test_collapsing_all_three_groups_leaves_nothing_visible(panel):
    for gid in (GROUP_INPUTS, GROUP_CALCS, GROUP_SENTINELS):
        panel.set_group_collapsed(gid, True)
    visible = {k for k in _all_kinds() if panel.is_kind_shown(k)}
    assert visible == set()


# ── persistence across panel restarts ───────────────────────────────


def test_collapsed_state_round_trips_through_settings_store(qtbot, tmp_path):
    settings_path = tmp_path / "gui_settings.yaml"
    store = GuiSettingsStore(settings_path)
    store.save(GuiSettings(show_onboarding=False))

    def _make_panel_with_store() -> NodeLibraryPanel:
        p = NodeLibraryPanel(language="en", settings_store=store)
        p.resize(260, 480)
        qtbot.addWidget(p)
        p.show()
        qtbot.waitUntil(lambda: p.isVisible(), timeout=500)
        return p

    # First session: collapse Calcs, then close the panel.
    panel_a = _make_panel_with_store()
    panel_a.set_group_collapsed(GROUP_CALCS, True)
    panel_a.deleteLater()

    # Second session: a fresh panel loads from the same store and
    # starts with Calcs already collapsed.
    panel_b = _make_panel_with_store()

    assert panel_b.is_group_collapsed(GROUP_CALCS)
    visible_b = {k for k in _all_kinds() if panel_b.is_kind_shown(k)}
    assert NodeKind.OPT not in visible_b
    assert NodeKind.XYZ_FILE in visible_b
    assert NodeKind.OUTPUT in visible_b


def test_constructor_with_empty_settings_has_no_collapsed_groups(qtbot, tmp_path):
    store = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    store.save(GuiSettings(show_onboarding=False))
    panel = NodeLibraryPanel(language="en", settings_store=store)
    qtbot.addWidget(panel)
    assert panel.collapsed_groups() == ()


def test_constructor_without_store_has_no_persistence(qtbot):
    """Without a settings store, collapse state stays in memory only."""
    panel = NodeLibraryPanel(language="en")
    qtbot.addWidget(panel)
    panel.set_group_collapsed(GROUP_CALCS, True)
    assert panel.is_group_collapsed(GROUP_CALCS)
    # And no setter error is raised: in-memory toggle still works.
    panel.set_group_collapsed(GROUP_CALCS, False)
    assert not panel.is_group_collapsed(GROUP_CALCS)


# ── group-toggle driven via header click ─────────────────────────────


def test_group_header_click_toggles_collapsed_state(panel):
    header = panel._group_headers[GROUP_CALCS]
    # Click the header (currently checked == expanded).
    header.setChecked(False)  # collapse
    assert panel.is_group_collapsed(GROUP_CALCS)
    header.setChecked(True)  # re-expand
    assert not panel.is_group_collapsed(GROUP_CALCS)
