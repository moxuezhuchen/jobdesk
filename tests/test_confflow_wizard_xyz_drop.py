"""Tests for the ConfFlow wizard's XYZ drag-and-drop (Phase 9D-2).

Mirrors the pattern at tests/test_gui_behavior.py:2003 — we construct a
QMimeData + MagicMock event and call dropEvent() directly. This avoids
needing a real QDragEnterEvent (which requires a native window) while
still exercising the page's URL-routing logic.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.dialogs.confflow_wizard_dialog import ConfFlowWizard


@pytest.fixture
def wizard(qtbot):
    wiz = ConfFlowWizard(server_id="srv", remote_dir="/tmp/r")
    qtbot.addWidget(wiz)
    return wiz


def _write_xyz(path: Path, atoms: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(["H  0.0  0.0  0.0"] * atoms)
    path.write_text(f"{atoms}\nmock\n{body}\n", encoding="utf-8")


def _make_drop_event(urls):
    """Build a MagicMock drop event carrying ``urls`` as a QMimeData.

    ``urls`` may be a list of Path/str (interpreted as local files) or
    pre-built QUrl instances (so tests can include remote schemes).
    """
    from PySide6.QtCore import QMimeData, QUrl

    mime = QMimeData()
    qurls = []
    for item in urls:
        if isinstance(item, QUrl):
            qurls.append(item)
        else:
            qurls.append(QUrl.fromLocalFile(str(item)))
    mime.setUrls(qurls)
    event = MagicMock()
    event.mimeData.return_value = mime
    return event


def test_xyz_list_accepts_drops(wizard):
    """The QListWidget itself is the drop target — ensure it's enabled."""
    assert wizard.xyz_page.list.acceptDrops() is True


def test_drop_event_accepts_local_xyz_file(wizard, tmp_path):
    xyz = tmp_path / "molecule.xyz"
    _write_xyz(xyz, atoms=3)

    event = _make_drop_event([xyz])
    wizard.xyz_page.dropEvent(event)

    assert [p.resolve() for p in wizard.xyz_page.xyz_paths()] == [xyz.resolve()]
    event.acceptProposedAction.assert_called_once_with()


def test_drop_event_skips_non_xyz_files(wizard, tmp_path):
    """Only .xyz files from a mixed drop should make it into the list."""
    xyz = tmp_path / "ok.xyz"
    txt = tmp_path / "readme.txt"
    _write_xyz(xyz, atoms=2)
    txt.write_text("notes", encoding="utf-8")

    event = _make_drop_event([xyz, txt])
    wizard.xyz_page.dropEvent(event)

    assert [p.resolve() for p in wizard.xyz_page.xyz_paths()] == [xyz.resolve()]
    event.acceptProposedAction.assert_called_once_with()


def test_drop_event_routes_directory_to_add_directory(wizard, tmp_path):
    """Dropping a directory must funnel through add_directory (non-recursive)."""
    root = tmp_path / "batch"
    _write_xyz(root / "a.xyz", atoms=3)
    _write_xyz(root / "b.xyz", atoms=4)
    _write_xyz(root / "c.xyz", atoms=5)
    _write_xyz(root / "nested" / "ignored.xyz", atoms=2)  # must NOT be added

    assert wizard.xyz_page.recursive_checkbox.isChecked() is False
    event = _make_drop_event([root])
    wizard.xyz_page.dropEvent(event)

    names = {p.name for p in wizard.xyz_page.xyz_paths()}
    assert names == {"a.xyz", "b.xyz", "c.xyz"}
    assert len(wizard.xyz_page.xyz_paths()) == 3
    event.acceptProposedAction.assert_called_once_with()


def test_drop_event_directory_honors_recursive_checkbox(wizard, tmp_path):
    """When the recursive checkbox is on, nested .xyz files are picked up too."""
    root = tmp_path / "batch"
    _write_xyz(root / "a.xyz", atoms=3)
    _write_xyz(root / "b.xyz", atoms=4)
    _write_xyz(root / "nested" / "d.xyz", atoms=2)
    _write_xyz(root / "nested" / "deeper" / "e.xyz", atoms=6)
    _write_xyz(root / "ignored.txt")

    wizard.xyz_page.recursive_checkbox.setChecked(True)

    event = _make_drop_event([root])
    wizard.xyz_page.dropEvent(event)

    names = {p.name for p in wizard.xyz_page.xyz_paths()}
    assert names == {"a.xyz", "b.xyz", "d.xyz", "e.xyz"}
    assert len(wizard.xyz_page.xyz_paths()) == 4
    event.acceptProposedAction.assert_called_once_with()


def test_drop_event_deduplicates_existing_paths(wizard, tmp_path):
    """Re-dropping a path already added via add_directory must NOT duplicate."""
    xyz = tmp_path / "dup.xyz"
    _write_xyz(xyz, atoms=3)

    # Pre-add via the public API so dedup table is populated.
    wizard.xyz_page.add_directory(tmp_path, recursive=False)
    assert len(wizard.xyz_page.xyz_paths()) == 1

    # Drop the same file again. No new file is added, so the page ignores
    # the proposed action — but the existing list must not change.
    event = _make_drop_event([xyz])
    wizard.xyz_page.dropEvent(event)

    assert len(wizard.xyz_page.xyz_paths()) == 1
    assert wizard.xyz_page.xyz_paths()[0].resolve() == xyz.resolve()


def test_drop_event_ignores_non_local_urls(wizard):
    """Remote https URLs must be ignored (no adds, event.ignore() called)."""
    from PySide6.QtCore import QUrl

    remote = QUrl("https://example.com/file.xyz")

    event = _make_drop_event([remote])
    wizard.xyz_page.dropEvent(event)

    assert wizard.xyz_page.xyz_paths() == []
    event.ignore.assert_called_once_with()
    event.acceptProposedAction.assert_not_called()


def test_drop_event_ignore_when_nothing_xyz_in_mime(wizard, tmp_path):
    """A drop containing a directory with no .xyz files must call ignore()."""
    root = tmp_path / "empty_batch"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "b.txt").write_text("world", encoding="utf-8")

    event = _make_drop_event([root])
    wizard.xyz_page.dropEvent(event)

    assert wizard.xyz_page.xyz_paths() == []
    event.ignore.assert_called_once_with()
    event.acceptProposedAction.assert_not_called()


def test_drag_enter_accepts_local_url(wizard, tmp_path):
    xyz = tmp_path / "molecule.xyz"
    _write_xyz(xyz, atoms=2)

    event = _make_drop_event([xyz])
    wizard.xyz_page.dragEnterEvent(event)

    event.acceptProposedAction.assert_called_once_with()
    event.ignore.assert_not_called()


def test_drag_enter_rejects_remote_url(wizard):
    from PySide6.QtCore import QUrl

    remote = QUrl("https://example.com/file.xyz")
    event = _make_drop_event([remote])
    wizard.xyz_page.dragEnterEvent(event)

    event.ignore.assert_called_once_with()
    event.acceptProposedAction.assert_not_called()