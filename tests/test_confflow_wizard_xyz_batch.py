"""Tests for the ConfFlow wizard's XYZ batch import (Phase 9B).

These tests focus on the new ``add_directory`` API and the recursive
checkbox. They avoid QFileDialog (which would block on user input)
by calling ``add_directory`` directly with a Path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.dialogs.confflow_wizard_dialog import (
    ConfFlowWizard,
    WizardResult,
)


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
def wizard(qtbot):
    wiz = ConfFlowWizard(server_id="srv", remote_dir="/tmp/r")
    qtbot.addWidget(wiz)
    return wiz


def test_add_directory_top_level_only(wizard, xyz_dir):
    """Default scan picks up only direct children of the directory."""
    added = wizard.xyz_page.add_directory(xyz_dir, recursive=False)
    assert added == 3
    paths = wizard.xyz_page.xyz_paths()
    names = {p.name for p in paths}
    assert names == {"a.xyz", "b.xyz", "c.xyz"}
    # Order is sorted so the user gets a predictable list.
    assert paths == sorted(paths)


def test_add_directory_recursive(wizard, xyz_dir):
    """With recursive=True, nested .xyz files are picked up too."""
    added = wizard.xyz_page.add_directory(xyz_dir, recursive=True)
    assert added == 5
    names = {p.name for p in wizard.xyz_page.xyz_paths()}
    assert names == {"a.xyz", "b.xyz", "c.xyz", "d.xyz", "e.xyz"}


def test_add_directory_skips_non_xyz_files(wizard, xyz_dir):
    """Non-.xyz files are silently ignored."""
    # Add a few extras so we know they're not picked up.
    _write_xyz(xyz_dir / "extra.txt", atoms=1)
    _write_xyz(xyz_dir / "no_extension", atoms=1)
    added = wizard.xyz_page.add_directory(xyz_dir, recursive=False)
    assert added == 3
    names = {p.name for p in wizard.xyz_page.xyz_paths()}
    assert "extra.txt" not in names
    assert "no_extension" not in names


def test_add_directory_deduplicates(wizard, xyz_dir):
    """Calling add_directory twice on the same dir does not double-add."""
    added1 = wizard.xyz_page.add_directory(xyz_dir, recursive=True)
    added2 = wizard.xyz_page.add_directory(xyz_dir, recursive=True)
    assert added1 == 5
    assert added2 == 0  # all already present
    assert len(wizard.xyz_page.xyz_paths()) == 5


def test_add_directory_missing_dir_returns_zero(wizard, tmp_path):
    """A non-existent directory is a no-op (no error)."""
    missing = tmp_path / "does_not_exist"
    added = wizard.xyz_page.add_directory(missing, recursive=True)
    assert added == 0
    assert wizard.xyz_page.xyz_paths() == []


def test_add_directory_empty_dir_returns_zero(wizard, tmp_path):
    """An empty directory adds no files."""
    empty = tmp_path / "empty"
    empty.mkdir()
    added = wizard.xyz_page.add_directory(empty, recursive=True)
    assert added == 0
    assert wizard.xyz_page.xyz_paths() == []


def test_add_directory_mixed_recursion(wizard, xyz_dir):
    """Recursive=True walks both top-level and nested subdirs."""
    added = wizard.xyz_page.add_directory(xyz_dir, recursive=True)
    paths = {p.name for p in wizard.xyz_page.xyz_paths()}
    assert "a.xyz" in paths                # top-level
    assert "d.xyz" in paths                # nested/
    assert "e.xyz" in paths                # nested/deeper/


def test_xyz_page_has_recursive_checkbox(wizard):
    """The recursive checkbox exists and defaults to unchecked."""
    cb = wizard.xyz_page.recursive_checkbox
    assert cb is not None
    assert cb.isChecked() is False


def test_xyz_page_has_clear_button(wizard, xyz_dir):
    """The Clear button empties the list."""
    wizard.xyz_page.add_directory(xyz_dir, recursive=False)
    assert len(wizard.xyz_page.xyz_paths()) == 3
    wizard.xyz_page._clear()
    assert wizard.xyz_page.xyz_paths() == []
    assert wizard.xyz_page.list.count() == 0


def test_xyz_page_count_label_updates(wizard, xyz_dir):
    """The status label reflects the current selection size."""
    assert "0" in wizard.xyz_page.count_label.text()
    wizard.xyz_page.add_directory(xyz_dir, recursive=False)
    assert "3" in wizard.xyz_page.count_label.text()


def test_xyz_page_isComplete_requires_files(wizard, xyz_dir):
    """isComplete() returns True only when at least one file is selected."""
    assert wizard.xyz_page.isComplete() is False
    wizard.xyz_page.add_directory(xyz_dir, recursive=False)
    assert wizard.xyz_page.isComplete() is True


def test_xyz_page_combines_files_and_directories(wizard, xyz_dir, tmp_path):
    """Files added via _add and add_directory both show up."""
    single = tmp_path / "single.xyz"
    _write_xyz(single, atoms=2)
    # Inject directly (skip QFileDialog).
    wizard.xyz_page._try_add_path(single)
    wizard.xyz_page.add_directory(xyz_dir, recursive=False)
    paths = wizard.xyz_page.xyz_paths()
    assert single in paths
    assert xyz_dir / "a.xyz" in paths
    assert len(paths) == 4  # 1 single + 3 from dir


def test_recursive_checkbox_checkbox_state(wizard):
    """Toggling the recursive checkbox updates its state correctly."""
    cb = wizard.xyz_page.recursive_checkbox
    cb.setChecked(True)
    assert cb.isChecked() is True
    cb.setChecked(False)
    assert cb.isChecked() is False