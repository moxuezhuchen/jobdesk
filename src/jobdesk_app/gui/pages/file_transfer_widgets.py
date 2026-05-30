"""File-table widgets and table helpers for the Files page."""

from PySide6.QtCore import QMimeData, Qt, QUrl, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
)

from ..design.components import StyledTableWidget


class _ConnectedSFTP:
    def __init__(self, ssh, sftp):
        self._ssh = ssh
        self._sftp = sftp

    def __getattr__(self, name):
        return getattr(self._sftp, name)

    def close(self):
        self._sftp.close()
        self._ssh.close()


class _FileTable(StyledTableWidget):
    drop_files = Signal(list)
    copy_local_files = Signal(list)
    move_local_files = Signal(list, str)
    move_remote_files = Signal(list, str)
    key_delete = Signal()
    key_enter = Signal()

    def __init__(self, role: str):
        super().__init__()
        self.role = role
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            self.key_delete.emit()
        elif event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.key_enter.emit()
        else:
            super().keyPressEvent(event)

    def startDrag(self, supported_actions):
        rows = sorted({idx.row() for idx in self.selectedIndexes()})
        if not rows and self.currentRow() >= 0:
            rows = [self.currentRow()]
        paths = []
        for row in rows:
            name_item = self.item(row, 0)
            path_item = self.item(row, 4 if self.role == "local" else 5)
            if not path_item or (name_item and name_item.text() == ".."):
                continue
            paths.append(path_item.text())
        if not paths:
            return
        mime = QMimeData()
        if self.role == "local":
            mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        else:
            mime.setData("application/x-jobdesk-remote-paths", "\n".join(paths).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

    def dragEnterEvent(self, event):
        if self._accepts_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if self._accepts_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        mime = event.mimeData()
        local_paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()] if mime.hasUrls() else []
        if self.role == "local" and local_paths:
            target_dir = self._drop_directory_path(event)
            if target_dir:
                self.move_local_files.emit(local_paths, target_dir)
                event.acceptProposedAction()
                return
        if self.role == "remote" and mime.hasFormat("application/x-jobdesk-remote-paths"):
            data = bytes(mime.data("application/x-jobdesk-remote-paths")).decode("utf-8")
            remote_paths = [line for line in data.splitlines() if line]
            target_dir = self._drop_directory_path(event)
            if target_dir:
                self.move_remote_files.emit(remote_paths, target_dir)
                event.acceptProposedAction()
                return
            event.ignore()
            return
        if self.role == "remote" and local_paths:
            self.drop_files.emit(local_paths)
            event.acceptProposedAction()
            return
        if self.role == "local" and mime.hasFormat("application/x-jobdesk-remote-paths"):
            data = bytes(mime.data("application/x-jobdesk-remote-paths")).decode("utf-8")
            self.drop_files.emit([line for line in data.splitlines() if line])
            event.acceptProposedAction()
            return
        if self.role == "local" and local_paths:
            self.copy_local_files.emit(local_paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _drop_directory_path(self, event) -> str | None:
        try:
            item = self.itemAt(event.position().toPoint())
        except (AttributeError, TypeError):
            return None
        if item is None:
            return None
        row = item.row()
        name_item = self.item(row, 0)
        kind_item = self.item(row, 3 if self.role == "local" else 4)
        path_item = self.item(row, 4 if self.role == "local" else 5)
        if (
            name_item is None
            or name_item.text() == ".."
            or kind_item is None
            or kind_item.text() != "dir"
            or path_item is None
        ):
            return None
        return path_item.text()

    def _accepts_mime(self, mime: QMimeData) -> bool:
        has_local_paths = mime.hasUrls() and any(url.isLocalFile() for url in mime.urls())
        if self.role == "remote":
            return has_local_paths or mime.hasFormat("application/x-jobdesk-remote-paths")
        return mime.hasFormat("application/x-jobdesk-remote-paths") or has_local_paths


def _setup_table(table: QTableWidget, headers: list[str], hidden_columns: list[int] | None = None) -> None:
    from PySide6.QtCore import QSize
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.setIconSize(QSize(24, 24))
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    for column in hidden_columns or []:
        table.setColumnHidden(column, True)


def _load_rows(table: QTableWidget, rows: list[list[str]]) -> None:
    from PySide6.QtWidgets import QStyle
    style = table.style()
    folder_icon = style.standardIcon(QStyle.SP_DirIcon)
    file_icon = style.standardIcon(QStyle.SP_FileIcon)
    up_icon = style.standardIcon(QStyle.SP_ArrowUp)
    # kind column: local=3, remote=4
    kind_col = 4 if table.role == "remote" else 3
    table.setSortingEnabled(False)
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        kind = row[kind_col] if kind_col < len(row) else ""
        is_parent = (str(row[0]) == "..")
        # Sort rank: ".." = 0, dir = 1, file = 2
        sort_rank = 0 if is_parent else (1 if kind == "dir" else 2)
        for c, value in enumerate(row):
            item = _SortableItem(str(value), sort_rank)
            if c == 0:
                if is_parent:
                    item.setIcon(up_icon)
                elif kind == "dir":
                    item.setIcon(folder_icon)
                else:
                    item.setIcon(file_icon)
            table.setItem(r, c, item)
    table.setSortingEnabled(True)


class _SortableItem(QTableWidgetItem):
    """Table item that sorts directories before files."""

    def __init__(self, text: str, sort_rank: int):
        super().__init__(text)
        self._sort_rank = sort_rank

    def __lt__(self, other):
        if isinstance(other, _SortableItem) and self._sort_rank != other._sort_rank:
            return self._sort_rank < other._sort_rank
        return self.text().lower() < other.text().lower()


def _default_column_widths(key: str) -> list[int]:
    if key == "files.remote":
        return [320, 95, 155, 82]
    return [360, 95, 155]


def _clamp_column_widths(key: str, widths: list[int]) -> list[int]:
    minimums = [90, 60, 110, 55]
    return [
        max(minimums[min(index, len(minimums) - 1)], int(width))
        for index, width in enumerate(widths)
    ]
