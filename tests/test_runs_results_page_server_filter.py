"""Tests for the server-filter helpers in :mod:`runs_results_page`.

These helpers are pure functions, so they can be exercised without spinning
up a Qt event loop. The Qt-coupled bits (signal wiring, checkbox lifecycle)
are covered indirectly by the existing ``test_gui_imports`` import-only test.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.pages.runs_results_page import (
    coerce_visible_servers,
    filter_runs_by_servers,
    toggle_all_selection,
)


def _record(server_id: str, run_id: str) -> SimpleNamespace:
    return SimpleNamespace(server_id=server_id, run_id=run_id)


# ---- filter_runs_by_servers ------------------------------------------------


def test_filter_excludes_unchecked_server() -> None:
    runs = [_record("a", "a-1"), _record("b", "b-1"), _record("a", "a-2")]
    result = filter_runs_by_servers(runs, {"a"})
    assert [r.run_id for r in result] == ["a-1", "a-2"]


def test_filter_includes_checked_server() -> None:
    runs = [_record("a", "a-1"), _record("b", "b-1"), _record("c", "c-1")]
    result = filter_runs_by_servers(runs, {"a", "c"})
    assert [r.run_id for r in result] == ["a-1", "c-1"]


def test_filter_empty_selection_returns_all() -> None:
    """``None`` or empty set means "no filter, show everything"."""
    runs = [_record("a", "a-1"), _record("b", "b-1")]
    assert [r.run_id for r in filter_runs_by_servers(runs, None)] == ["a-1", "b-1"]
    assert [r.run_id for r in filter_runs_by_servers(runs, set())] == ["a-1", "b-1"]


def test_filter_does_not_mutate_input_list() -> None:
    runs = [_record("a", "a-1"), _record("b", "b-1")]
    snapshot = list(runs)
    filter_runs_by_servers(runs, {"a"})
    assert runs == snapshot


# ---- coerce_visible_servers -----------------------------------------------


def test_coerce_empty_persisted_returns_known() -> None:
    assert coerce_visible_servers(None, ["a", "b"]) == {"a", "b"}
    assert coerce_visible_servers([], ["a", "b"]) == {"a", "b"}


def test_coerce_drops_unknown_persisted_ids() -> None:
    """A stale entry from a removed server must not appear in the selection."""
    result = coerce_visible_servers(["a", "ghost-server"], ["a", "b"])
    assert result == {"a"}


def test_coerce_drops_empty_strings() -> None:
    assert coerce_visible_servers(["a", "", "b"], ["a", "b"]) == {"a", "b"}


def test_coerce_returns_empty_when_nothing_matches() -> None:
    """If persisted IDs are all unknown, the result is empty (caller handles)."""
    result = coerce_visible_servers(["ghost"], ["a", "b"])
    assert result == set()


# ---- toggle_all_selection --------------------------------------------------


def test_toggle_all_select_returns_every_known() -> None:
    result = toggle_all_selection(set(), ["a", "b", "c"], select=True)
    assert result == {"a", "b", "c"}


def test_toggle_all_deselect_returns_empty() -> None:
    result = toggle_all_selection({"a", "b"}, ["a", "b"], select=False)
    assert result == set()


def test_toggle_all_with_no_servers_selects_empty_set() -> None:
    """Selecting from an empty universe yields an empty set, not a crash."""
    assert toggle_all_selection(set(), [], select=True) == set()
    assert toggle_all_selection(set(), [], select=False) == set()


# ---- integration: full pipeline -------------------------------------------


def test_filter_pipeline_round_trip() -> None:
    """Simulate load → coerce → filter on a small scenario."""
    known = ["gpu-1", "gpu-2", "cpu-1"]
    # User previously selected only the GPU servers
    persisted = ["gpu-1", "gpu-2"]
    visible = coerce_visible_servers(persisted, known)
    runs = [
        _record("gpu-1", "g1"),
        _record("cpu-1", "c1"),
        _record("gpu-2", "g2"),
        _record("deleted-server", "stale"),
    ]
    # Step: filter the table using the visible set
    shown = filter_runs_by_servers(runs, visible)
    assert [r.run_id for r in shown] == ["g1", "g2"]

    # Step: user clicks "All" — every known server becomes visible
    visible = toggle_all_selection(visible, known, select=True)
    shown = filter_runs_by_servers(runs, visible)
    # Note: 'deleted-server' still filtered out because not in visible set.
    assert sorted(r.run_id for r in shown) == ["c1", "g1", "g2"]

    # Step: user clicks "None" — visible set becomes empty
    visible = toggle_all_selection(visible, known, select=False)
    assert visible == set()
    # ``filter_runs_by_servers`` treats an empty selection as "show all",
    # so the page treats "None" as a no-op filter rather than wiping the
    # table. (The persistence layer still records the empty choice so the
    # next session starts in a known state.)
    shown = filter_runs_by_servers(runs, visible)
    assert len(shown) == len(runs)