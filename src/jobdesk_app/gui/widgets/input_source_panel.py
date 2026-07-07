"""Reusable :class:`InputSourcePanel` — tabbed picker for input files.

Phase 14B: extracted from the body of ``_XyzPage`` (ConfFlowWizard).
Same API surface (``set_paths`` / ``paths`` / ``add_files_requested``)
but framed as ``list[InputSource]`` rather than ``list[Path]`` so the
:mod:`SubmitPage` can carry both the file and the side ("local" /
"remote") it came from.

Layout:

    [ Local tab ]  [ Remote tab ]   (only show Remote tab if connected)
    +---------------------------------------------------+
    |  drag .xyz/.gjf/.inp here                         |
    |  [ + Add files ]  [ + Add dir ]                   |
    |  [ - Remove   ]  [ × Clear    ]                   |
    +---------------------------------------------------+
    |  Picked files:                                    |
    |   • water.xyz                                     |
    |   • methanol.xyz                                  |
    +---------------------------------------------------+
    |  [ recursive scan ] checkbox                      |
    +---------------------------------------------------+

The ``add_files_requested`` signal lets embedding pages drive the file
dialog (which can show different default dirs / filters depending on
whether the user is picking local or remote files — and the remote
case doesn't make sense anyway since the Files page is the source).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.submit_payload import InputSource
from ..button_feedback import ButtonRole, apply_button_role
from ..i18n import tr


_VALID_SUFFIXES = {".xyz", ".gjf", ".inp"}


def _kind_for(path: Path) -> str:
    """Return ``"xyz"``, ``"gjf"`` or ``"inp"`` for ``path``.

    Unknown suffixes default to ``"xyz"`` so the picker is forgiving
    for files the wizard's drag/drop layer let through.
    """
    suffix = path.suffix.lower()
    if suffix in _VALID_SUFFIXES:
        return suffix.lstrip(".")
    return "xyz"


class InputSourcePanel(QWidget):
    """Tabbed picker for input files (local / remote).

    Embedding pages listen to :pyattr:`sources_changed` to react to user
    edits, and :pyattr:`add_files_requested` to show a file dialog.  The
    panel itself does not own any file dialog state — it's intentionally
    test-friendly and easy to swap implementations for (e.g. a remote
    tree picker in a future release).
    """

    sources_changed = Signal(list)  # list[InputSource]
    add_files_requested = Signal(str, str)  # side ("local"|"remote"), default_dir

    def __init__(
        self,
        parent: QWidget | None = None,
        language: str = "en",
        remote_available: bool = False,
    ):
        super().__init__(parent)
        self._language = language
        self._remote_available = remote_available

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.tabs = QTabWidget()
        self.local_tab = self._build_tab("local")
        self.local_tab.btn_add_dir.clicked.connect(self._on_add_directory)
        self.local_tab.btn_add.clicked.connect(self._on_add_files_local)
        self.local_tab.btn_remove.clicked.connect(self._on_remove)
        self.local_tab.btn_clear.clicked.connect(self._on_clear)
        self.local_tab.recursive_cb.toggled.connect(self._on_recursive_toggled)
        self.tabs.addTab(self.local_tab, tr("Local", self._language))

        if remote_available:
            self.remote_tab = self._build_tab("remote")
            self.remote_tab.btn_add_dir.clicked.connect(self._on_add_directory)
            self.remote_tab.btn_add.clicked.connect(self._on_add_files_remote)
            self.remote_tab.btn_remove.clicked.connect(self._on_remove)
            self.remote_tab.btn_clear.clicked.connect(self._on_clear)
            self.remote_tab.recursive_cb.toggled.connect(self._on_recursive_toggled)
            self.tabs.addTab(self.remote_tab, tr("Remote", self._language))
        else:
            self.remote_tab = None

        layout.addWidget(self.tabs)

    # ── Public API ────────────────────────────────────────────────────────

    def apply_language(self, language: str) -> None:
        """Re-translate tab titles + button labels."""
        self._language = language
        self.tabs.setTabText(0, tr("Local", language))
        if self.remote_tab is not None and self.tabs.count() > 1:
            self.tabs.setTabText(1, tr("Remote", language))
        for tab in self._all_tabs():
            tab.btn_add.setText(tr("Add files…", language))
            tab.btn_add_dir.setText(tr("Add directory…", language))
            tab.btn_remove.setText(tr("Remove", language))
            tab.btn_clear.setText(tr("Clear", language))
            tab.recursive_cb.setText(tr("Include files in subdirectories", language))
            tab.refresh_count()

    def sources(self) -> list[InputSource]:
        """Return the currently picked list (preserves tab / order)."""
        result: list[InputSource] = []
        for tab in self._all_tabs():
            for source in tab._sources:
                result.append(source)
        return result

    def set_sources(self, sources: list[InputSource]) -> None:
        """Replace the current list — used by the cross-page wire."""
        # Drop everything first; then place each source on the right tab.
        self._reset_local()
        if self.remote_tab is not None:
            self._reset_remote()
        for source in sources:
            self._append_source(source)
        for tab in self._all_tabs():
            tab.refresh_count()
        self.sources_changed.emit(self.sources())

    def set_recursive(self, enabled: bool) -> None:
        for tab in self._all_tabs():
            tab.recursive_cb.setChecked(enabled)

    def is_recursive(self) -> bool:
        # All tabs share the same toggle — return the local one.
        return self.local_tab.recursive_cb.isChecked()

    # ── Internal helpers ──────────────────────────────────────────────────

    def _all_tabs(self):
        yield self.local_tab
        if self.remote_tab is not None:
            yield self.remote_tab

    def _build_tab(self, side: str) -> "_TabBody":
        return _TabBody(side=side, language=self._language)

    def _current_side(self) -> str:
        idx = self.tabs.currentIndex()
        if idx == 1 and self.remote_tab is not None:
            return "remote"
        return "local"

    def _current_tab(self) -> "_TabBody":
        side = self._current_side()
        if side == "remote" and self.remote_tab is not None:
            return self.remote_tab
        return self.local_tab

    def _reset_local(self) -> None:
        self.local_tab._sources.clear()
        self.local_tab.list_widget.clear()

    def _reset_remote(self) -> None:
        if self.remote_tab is None:
            return
        self.remote_tab._sources.clear()
        self.remote_tab.list_widget.clear()

    def _append_source(self, source: InputSource) -> None:
        if source.side == "remote" and self.remote_tab is not None:
            tab = self.remote_tab
        else:
            tab = self.local_tab
        if any(existing.path == source.path for existing in tab._sources):
            return
        tab._sources.append(source)
        tab.list_widget.addItem(QListWidgetItem(str(source.path)))

    def _on_add_files_local(self) -> None:
        self.add_files_requested.emit("local", "")

    def _on_add_files_remote(self) -> None:
        self.add_files_requested.emit("remote", "")

    def _on_add_directory(self) -> None:
        tab = self._current_tab()
        directory = QFileDialog.getExistingDirectory(self, tr("Select directory", self._language))
        if not directory:
            return
        added = tab.add_directory(Path(directory), recursive=tab.recursive_cb.isChecked())
        if added > 0:
            self.sources_changed.emit(self.sources())

    def _on_remove(self) -> None:
        tab = self._current_tab()
        tab.remove_selected()
        self.sources_changed.emit(self.sources())

    def _on_clear(self) -> None:
        tab = self._current_tab()
        tab.clear()
        self.sources_changed.emit(self.sources())

    def _on_recursive_toggled(self, _checked: bool) -> None:
        # The toggle is informational until the next Add-directory call.
        # No emit — the user hasn't changed the file list yet.
        return None

    # ── Convenience for embedding pages ───────────────────────────────────

    def add_local_paths(self, paths: list[Path]) -> int:
        """Add ``paths`` to the local tab; returns count of *new* items."""
        added = 0
        for raw in paths:
            p = Path(raw)
            if p.suffix.lower() not in _VALID_SUFFIXES:
                continue
            source = InputSource(path=p, side="local", kind=_kind_for(p))
            if any(existing.path == source.path for existing in self.local_tab._sources):
                continue
            self.local_tab._sources.append(source)
            self.local_tab.list_widget.addItem(QListWidgetItem(str(p)))
            added += 1
        self.local_tab.refresh_count(added)
        if added:
            self.sources_changed.emit(self.sources())
        return added

    def add_remote_paths(self, paths: list[str]) -> int:
        """Add ``paths`` to the remote tab; returns count of *new* items."""
        if self.remote_tab is None:
            return 0
        added = 0
        for raw in paths:
            p = Path(raw)
            if p.suffix.lower() not in _VALID_SUFFIXES:
                continue
            source = InputSource(path=p, side="remote", kind=_kind_for(p))
            if any(existing.path == source.path for existing in self.remote_tab._sources):
                continue
            self.remote_tab._sources.append(source)
            self.remote_tab.list_widget.addItem(QListWidgetItem(str(p)))
            added += 1
        self.remote_tab.refresh_count(added)
        if added:
            self.sources_changed.emit(self.sources())
        return added


class _TabBody(QWidget):
    """Body of a single tab in :class:`InputSourcePanel`."""

    def __init__(self, side: str, language: str):
        super().__init__()
        self._side = side
        self._language = language
        self._sources: list[InputSource] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.ExtendedSelection)
        self.list_widget.setAcceptDrops(True)
        self.list_widget.setDropIndicatorShown(True)
        self.list_widget.dragEnterEvent = self._dragEnterEvent  # type: ignore[assignment]
        self.list_widget.dragMoveEvent = self._dragMoveEvent  # type: ignore[assignment]
        self.list_widget.dropEvent = self._dropEvent  # type: ignore[assignment]
        layout.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        self.btn_add = apply_button_role(
            QPushButton(tr("Add files…", self._language)),
            ButtonRole.INSTANT_ACTION,
        )
        btn_row.addWidget(self.btn_add)
        self.btn_add_dir = apply_button_role(
            QPushButton(tr("Add directory…", self._language)),
            ButtonRole.INSTANT_ACTION,
        )
        btn_row.addWidget(self.btn_add_dir)
        self.btn_remove = apply_button_role(
            QPushButton(tr("Remove", self._language)),
            ButtonRole.INSTANT_ACTION,
        )
        btn_row.addWidget(self.btn_remove)
        self.btn_clear = apply_button_role(
            QPushButton(tr("Clear", self._language)),
            ButtonRole.INSTANT_ACTION,
        )
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.recursive_cb = QCheckBox(tr("Include files in subdirectories", self._language))
        layout.addWidget(self.recursive_cb)

        self.count_label = QLabel(tr("0 file(s) selected", self._language))
        self.count_label.setStyleSheet("color: #666;")
        layout.addWidget(self.count_label)

    def refresh_count(self, added: int = 0) -> None:
        n = len(self._sources)
        suffix = "s" if n != 1 else ""
        if added > 0:
            self.count_label.setText(
                tr(
                    "{n} file{suffix} selected (+{added} new)",
                    self._language,
                    n=n,
                    suffix=suffix,
                    added=added,
                )
            )
        else:
            self.count_label.setText(
                tr("{n} file{suffix} selected", self._language, n=n, suffix=suffix)
            )

    def add_directory(self, directory: Path, *, recursive: bool) -> int:
        if not directory.is_dir():
            return 0
        pattern = "**/*" if recursive else "*"
        added = 0
        for entry in sorted(directory.glob(pattern)):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in _VALID_SUFFIXES:
                continue
            source = InputSource(path=entry, side=self._side, kind=_kind_for(entry))
            if any(existing.path == source.path for existing in self._sources):
                continue
            self._sources.append(source)
            self.list_widget.addItem(QListWidgetItem(str(entry)))
            added += 1
        self.refresh_count(added)
        return added

    def remove_selected(self) -> None:
        rows = sorted(
            {self.list_widget.row(item) for item in self.list_widget.selectedItems()},
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self._sources):
                del self._sources[row]
            self.list_widget.takeItem(row)
        self.refresh_count(0)

    def clear(self) -> None:
        self._sources.clear()
        self.list_widget.clear()
        self.refresh_count(0)

    # ── Drag / drop ───────────────────────────────────────────────────────

    def _dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def _dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()
            return
        event.ignore()

    def _dropEvent(self, event):  # noqa: N802
        if not event.mimeData().hasUrls():
            event.ignore()
            return
        added = 0
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            p = Path(url.toLocalFile())
            if p.is_dir():
                added += self.add_directory(p, recursive=False)
            elif p.is_file() and p.suffix.lower() in _VALID_SUFFIXES:
                source = InputSource(path=p, side=self._side, kind=_kind_for(p))
                if any(existing.path == source.path for existing in self._sources):
                    continue
                self._sources.append(source)
                self.list_widget.addItem(QListWidgetItem(str(p)))
                added += 1
        if added > 0:
            event.acceptProposedAction()
            self.refresh_count(added)
        else:
            event.ignore()


__all__ = ["InputSourcePanel"]