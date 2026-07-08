"""Tests for the InputSourcePanel's batch import (Phase 14B refactor).

The legacy wizard's ``_XyzPage.add_directory`` / ``_try_add_path`` API
moved into :class:`InputSourcePanel`'s per-tab ``_TabBody`` and the
panel-level ``add_local_paths`` convenience method.

Behavioural notes vs. the old wizard:

* The new panel accepts ``.xyz``, ``.gjf`` and ``.inp`` files (not
  just ``.xyz``).
* The new panel deduplicates by full path (matches the old behaviour).
* ``isComplete()`` is gone — call :meth:`InputSourcePanel.sources`
  instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.widgets.input_source_panel import InputSourcePanel


def _write_xyz(path: Path, atoms: int = 3) -> None:
    """Write a minimal valid XYZ file at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(["H  0.0  0.0  0.0"] * atoms)
    path.write_text(f"{atoms}\nmock\n{body}\n", encoding="utf-8")


@pytest.fixture
def xyz_dir(tmp_path: Path) -> Path:
    """Directory with 3 flat .xyz files and 2 nested ones."""
    root = tmp_path / "batch"
    _write_xyz(root / "a.xyz", atoms=3)
    _write_xyz(root / "b.xyz", atoms=4)
    _write_xyz(root / "c.xyz", atoms=5)
    _write_xyz(root / "nested" / "d.xyz", atoms=2)
    _write_xyz(root / "nested" / "deeper" / "e.xyz", atoms=6)
    _write_xyz(root / "ignored.txt")  # not .xyz — must be skipped
    return root


@pytest.fixture
def panel(qtbot):
    widget = InputSourcePanel(language="en", remote_available=False)
    qtbot.addWidget(widget)
    return widget


def test_add_directory_top_level_only(panel, xyz_dir):
    """Default scan picks up only direct children of the directory."""
    added = panel.local_tab.add_directory(xyz_dir, recursive=False)
    assert added == 3
    sources = panel.sources()
    names = {s.path.name for s in sources}
    assert names == {"a.xyz", "b.xyz", "c.xyz"}
    paths = [s.path for s in sources]
    assert paths == sorted(paths)


def test_add_directory_recursive(panel, xyz_dir):
    """With recursive=True, nested .xyz files are picked up too."""
    added = panel.local_tab.add_directory(xyz_dir, recursive=True)
    assert added == 5
    names = {s.path.name for s in panel.sources()}
    assert names == {"a.xyz", "b.xyz", "c.xyz", "d.xyz", "e.xyz"}


def test_add_directory_skips_non_xyz_files(panel, xyz_dir):
    """Non-.xyz files are silently ignored."""
    _write_xyz(xyz_dir / "extra.txt", atoms=1)
    _write_xyz(xyz_dir / "no_extension", atoms=1)
    added = panel.local_tab.add_directory(xyz_dir, recursive=False)
    assert added == 3
    names = {s.path.name for s in panel.sources()}
    assert "extra.txt" not in names
    assert "no_extension" not in names


def test_add_directory_deduplicates(panel, xyz_dir):
    """Calling add_directory twice on the same dir does not double-add."""
    added1 = panel.local_tab.add_directory(xyz_dir, recursive=True)
    added2 = panel.local_tab.add_directory(xyz_dir, recursive=True)
    assert added1 == 5
    assert added2 == 0
    assert len(panel.sources()) == 5


def test_add_directory_missing_dir_returns_zero(panel, tmp_path):
    """A non-existent directory is a no-op (no error)."""
    missing = tmp_path / "does_not_exist"
    added = panel.local_tab.add_directory(missing, recursive=True)
    assert added == 0
    assert panel.sources() == []


def test_add_directory_empty_dir_returns_zero(panel, tmp_path):
    """An empty directory adds no files."""
    empty = tmp_path / "empty"
    empty.mkdir()
    added = panel.local_tab.add_directory(empty, recursive=True)
    assert added == 0
    assert panel.sources() == []


def test_add_directory_mixed_recursion(panel, xyz_dir):
    """Recursive=True walks both top-level and nested subdirs."""
    panel.local_tab.add_directory(xyz_dir, recursive=True)
    names = {s.path.name for s in panel.sources()}
    assert "a.xyz" in names
    assert "d.xyz" in names
    assert "e.xyz" in names


def test_panel_has_recursive_checkbox(panel):
    """The recursive checkbox exists and defaults to unchecked."""
    cb = panel.local_tab.recursive_cb
    assert cb is not None
    assert cb.isChecked() is False


def test_panel_has_clear_button(panel, xyz_dir):
    """The Clear button empties the list."""
    panel.local_tab.add_directory(xyz_dir, recursive=False)
    assert len(panel.sources()) == 3
    panel.local_tab.clear()
    assert panel.sources() == []
    assert panel.local_tab.list_widget.count() == 0


def test_panel_count_label_updates(panel, xyz_dir):
    """The status label reflects the current selection size."""
    assert "0" in panel.local_tab.count_label.text()
    panel.local_tab.add_directory(xyz_dir, recursive=False)
    assert "3" in panel.local_tab.count_label.text()


def test_panel_combines_files_and_directories(panel, xyz_dir, tmp_path):
    """Files added via add_local_paths and add_directory both show up."""
    single = tmp_path / "single.xyz"
    _write_xyz(single, atoms=2)
    panel.add_local_paths([single])
    panel.local_tab.add_directory(xyz_dir, recursive=False)
    sources = panel.sources()
    assert single in {s.path for s in sources}
    assert xyz_dir / "a.xyz" in {s.path for s in sources}
    assert len(sources) == 4  # 1 single + 3 from dir


def test_recursive_checkbox_state(panel):
    """Toggling the recursive checkbox updates its state correctly."""
    cb = panel.local_tab.recursive_cb
    cb.setChecked(True)
    assert cb.isChecked() is True
    cb.setChecked(False)
    assert cb.isChecked() is False


def test_sources_have_correct_kind(panel, tmp_path):
    """The InputSource's kind is inferred from the file extension."""
    xyz = tmp_path / "a.xyz"
    gjf = tmp_path / "b.gjf"
    inp = tmp_path / "c.inp"
    _write_xyz(xyz, atoms=1)
    gjf.write_text("%chk=test\n\n", encoding="utf-8")
    inp.write_text("! opt\n", encoding="utf-8")
    panel.add_local_paths([xyz, gjf, inp])
    sources = panel.sources()
    kinds = {s.path.name: s.kind for s in sources}
    assert kinds == {"a.xyz": "xyz", "b.gjf": "gjf", "c.inp": "inp"}
    assert all(s.side == "local" for s in sources)
