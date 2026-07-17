"""Tests for the InputSourcePanel's drag-and-drop (Phase 14B refactor).

Mirrors the pattern at ``tests/test_gui_behavior.py`` — we construct a
QMimeData + MagicMock event and call ``dropEvent()`` directly. This
avoids needing a real ``QDragEnterEvent`` (which requires a native
window) while still exercising the panel's URL-routing logic.

Phase 14C.2 behavioural note: the new ``_TabBody._dropEvent`` always
uses ``recursive=False`` for directory drops. The recursive checkbox
is only honoured by the explicit "Add directory…" button flow.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.gui.widgets.input_source_panel import InputSourcePanel


@pytest.fixture
def panel(qtbot):
    widget = InputSourcePanel(language="en", remote_available=False)
    qtbot.addWidget(widget)
    return widget


def _write_xyz(path: Path, atoms: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(["H  0.0  0.0  0.0"] * atoms)
    path.write_text(f"{atoms}\nmock\n{body}\n", encoding="utf-8")


def _make_drop_event(urls):
    """Build a MagicMock drop event carrying ``urls`` as a QMimeData."""
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


def test_local_list_accepts_drops(panel):
    """The QListWidget itself is the drop target — ensure it's enabled."""
    assert panel.local_tab.list_widget.acceptDrops() is True


def test_drop_event_accepts_local_xyz_file(panel, tmp_path):
    xyz = tmp_path / "molecule.xyz"
    _write_xyz(xyz, atoms=3)

    event = _make_drop_event([xyz])
    panel.local_tab._dropEvent(event)

    assert [s.path.resolve() for s in panel.sources()] == [xyz.resolve()]
    event.acceptProposedAction.assert_called_once_with()


def test_drop_event_skips_non_xyz_files(panel, tmp_path):
    """Only valid suffix files from a mixed drop should make it into the list."""
    xyz = tmp_path / "ok.xyz"
    txt = tmp_path / "readme.txt"
    _write_xyz(xyz, atoms=2)
    txt.write_text("notes", encoding="utf-8")

    event = _make_drop_event([xyz, txt])
    panel.local_tab._dropEvent(event)

    assert [s.path.resolve() for s in panel.sources()] == [xyz.resolve()]
    event.acceptProposedAction.assert_called_once_with()


def test_drop_event_routes_directory_to_add_directory(panel, tmp_path):
    """Dropping a directory must funnel through add_directory (non-recursive)."""
    root = tmp_path / "batch"
    _write_xyz(root / "a.xyz", atoms=3)
    _write_xyz(root / "b.xyz", atoms=4)
    _write_xyz(root / "c.xyz", atoms=5)
    _write_xyz(root / "nested" / "ignored.xyz", atoms=2)  # must NOT be added

    assert panel.local_tab.recursive_cb.isChecked() is False
    event = _make_drop_event([root])
    panel.local_tab._dropEvent(event)

    names = {s.path.name for s in panel.sources()}
    assert names == {"a.xyz", "b.xyz", "c.xyz"}
    assert len(panel.sources()) == 3
    event.acceptProposedAction.assert_called_once_with()


def test_drop_event_deduplicates_existing_paths(panel, tmp_path):
    """Re-dropping a path already added via add_directory must NOT duplicate."""
    xyz = tmp_path / "dup.xyz"
    _write_xyz(xyz, atoms=3)

    panel.local_tab.add_directory(tmp_path, recursive=False)
    assert len(panel.sources()) == 1

    event = _make_drop_event([xyz])
    panel.local_tab._dropEvent(event)

    assert len(panel.sources()) == 1
    assert panel.sources()[0].path.resolve() == xyz.resolve()


def test_drop_event_ignores_non_local_urls(panel):
    """Remote https URLs must be ignored (no adds, event.ignore() called)."""
    from PySide6.QtCore import QUrl

    remote = QUrl("https://example.com/file.xyz")
    event = _make_drop_event([remote])
    panel.local_tab._dropEvent(event)

    assert panel.sources() == []
    event.ignore.assert_called_once_with()
    event.acceptProposedAction.assert_not_called()


def test_drop_event_ignore_when_nothing_xyz_in_mime(panel, tmp_path):
    """A drop containing a directory with no valid suffix files must call ignore()."""
    root = tmp_path / "empty_batch"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    (root / "b.txt").write_text("world", encoding="utf-8")

    event = _make_drop_event([root])
    panel.local_tab._dropEvent(event)

    assert panel.sources() == []
    event.ignore.assert_called_once_with()
    event.acceptProposedAction.assert_not_called()


def test_drag_enter_accepts_local_url(panel, tmp_path):
    xyz = tmp_path / "molecule.xyz"
    _write_xyz(xyz, atoms=2)

    event = _make_drop_event([xyz])
    panel.local_tab._dragEnterEvent(event)

    event.acceptProposedAction.assert_called_once_with()
    event.ignore.assert_not_called()


def test_drag_enter_rejects_remote_url(panel):
    from PySide6.QtCore import QUrl

    remote = QUrl("https://example.com/file.xyz")
    event = _make_drop_event([remote])
    panel.local_tab._dragEnterEvent(event)

    event.ignore.assert_called_once_with()
    event.acceptProposedAction.assert_not_called()


def test_drop_event_accepts_local_gjf_file(panel, tmp_path):
    """Phase 14B: the panel now accepts .gjf files in addition to .xyz."""
    gjf = tmp_path / "input.gjf"
    gjf.write_text("%chk=test\n\n", encoding="utf-8")

    event = _make_drop_event([gjf])
    panel.local_tab._dropEvent(event)

    sources = panel.sources()
    assert len(sources) == 1
    assert sources[0].path.resolve() == gjf.resolve()
    assert sources[0].kind == "gjf"
    event.acceptProposedAction.assert_called_once_with()
