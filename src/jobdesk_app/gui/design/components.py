"""Reusable design system components."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QEvent, Qt, Signal, QSize
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHeaderView, QLabel, QPushButton,
    QTableWidget, QVBoxLayout, QWidget,
)

from .icons import get_icon
from .tokens import Colors, Metrics, Radius, Shadow, Spacing
from ...services.gui_settings import GuiSettingsStore


class Card(QFrame):
    """White rounded container with subtle shadow."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet(
            f"QFrame#Card {{ background: {Colors.BG_SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.LG}px; }}"
        )
        effect = QGraphicsDropShadowEffect(self)
        ox, oy, blur, alpha = Shadow.SM
        effect.setOffset(ox, oy)
        effect.setBlurRadius(blur)
        effect.setColor(QColor(0, 0, 0, alpha))
        self.setGraphicsEffect(effect)


class StatusBadge(QLabel):
    """Colored pill with dot indicator."""

    _VARIANTS = {
        "success": (Colors.SUCCESS, Colors.SUCCESS_BG),
        "warning": (Colors.WARNING, Colors.WARNING_BG),
        "error": (Colors.ERROR, Colors.ERROR_BG),
        "info": (Colors.INFO, Colors.INFO_BG),
        "muted": (Colors.TEXT_MUTED, Colors.BORDER_SUBTLE),
    }

    def __init__(self, text: str = "", variant: str = "muted", parent: QWidget | None = None):
        super().__init__(parent)
        self.set_status(text, variant)

    def set_status(self, text: str, variant: str = "muted") -> None:
        fg, bg = self._VARIANTS.get(variant, self._VARIANTS["muted"])
        self.setText(f" ● {text}")
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:{Radius.SM}px; "
            f"padding:2px 8px 2px 4px; font-weight:500;"
        )


class PrimaryButton(QPushButton):
    """Filled accent button for main actions."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("PrimaryBtn")
        self.setCursor(Qt.PointingHandCursor)


class _GridHeaderView(QHeaderView):
    """Header that draws table grid lines from section geometry."""

    def __init__(self, orientation: Qt.Orientation, parent: QWidget | None = None):
        super().__init__(orientation, parent)
        self._grid_color = QColor("#94a3b8")

    def set_grid_color(self, color: str | QColor) -> None:
        self._grid_color = QColor(color)
        self.viewport().update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.orientation() != Qt.Horizontal:
            return

        first_x, last_x, left_edges = self._visible_section_edges()
        if first_x is None or last_x is None:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(self._grid_color, 1))
        y_top = 0
        y_bottom = self.height() - 1
        for x in left_edges:
            painter.drawLine(x, y_top, x, y_bottom)
        painter.drawLine(last_x, y_top, last_x, y_bottom)
        painter.drawLine(first_x, y_top, last_x, y_top)
        painter.drawLine(first_x, y_bottom, last_x, y_bottom)
        painter.end()

    def _visible_section_edges(self) -> tuple[int | None, int | None, list[int]]:
        left_edges: list[int] = []
        first_x: int | None = None
        last_x: int | None = None
        for logical in range(self.count()):
            if self.isSectionHidden(logical):
                continue
            section_left = self.sectionViewportPosition(logical)
            w = self.sectionSize(logical)
            if section_left + w <= 0 or section_left >= self.viewport().width():
                continue
            x = max(0, section_left)
            right = min(self.viewport().width() - 1, section_left + w - 1)
            left_edges.append(x)
            first_x = x if first_x is None else min(first_x, x)
            last_x = right if last_x is None else max(last_x, right)
        return first_x, last_x, left_edges


class StyledTableWidget(QTableWidget):
    """Transparent table with crisp, aligned grid lines."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._grid_color = QColor("#94a3b8")
        self._column_width_key: str | None = None
        self._restoring_column_widths = False
        self.setHorizontalHeader(_GridHeaderView(Qt.Horizontal, self))
        self.setShowGrid(False)
        self.setStyleSheet(
            "QTableWidget { background: transparent; border: none; border-radius: 0; }"
            " QTableWidget::item { background: transparent; }"
            " QTableCornerButton::section { background: transparent; border: none; }"
            " QHeaderView { background: transparent; border: none; }"
            " QHeaderView::section { background: transparent; border: none; font-weight: normal; }"
        )

    def bind_column_widths(self, key: str, default_widths: list[int] | None = None) -> None:
        self._column_width_key = key
        self.restore_column_widths(key, default_widths)
        self.horizontalHeader().sectionResized.connect(self._save_bound_column_widths)

    def restore_column_widths(self, key: str, default_widths: list[int] | None = None) -> None:
        settings = GuiSettingsStore().load()
        widths = (settings.column_widths or {}).get(key) or default_widths or []
        if not widths:
            return

        self._restoring_column_widths = True
        try:
            for column, width in enumerate(widths):
                if column < self.columnCount() and width > 0:
                    self.horizontalHeader().resizeSection(column, width)
        finally:
            self._restoring_column_widths = False

    def save_column_widths(self, key: str) -> None:
        if self._restoring_column_widths:
            return
        settings_store = GuiSettingsStore()
        settings = settings_store.load()
        widths = dict(settings.column_widths or {})
        widths[key] = [
            self.horizontalHeader().sectionSize(column)
            for column in range(self.columnCount())
        ]
        settings_store.save(replace(settings, column_widths=widths))

    def _save_bound_column_widths(self) -> None:
        if self._column_width_key:
            self.save_column_widths(self._column_width_key)

    def set_grid_color(self, color: str | QColor) -> None:
        self._grid_color = QColor(color)
        header = self.horizontalHeader()
        if isinstance(header, _GridHeaderView):
            header.set_grid_color(self._grid_color)
        self.viewport().update()

    def viewportEvent(self, event) -> bool:  # noqa: N802
        handled = super().viewportEvent(event)
        if event.type() == QEvent.Paint:
            self._paint_viewport_grid()
        return handled

    def _paint_viewport_grid(self) -> None:
        first_x, last_x, left_edges = self._visible_column_edges()
        if first_x is None or last_x is None:
            return

        last_y = self._last_visible_row_bottom()
        if last_y is None:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setPen(QPen(self._grid_color, 1))
        for x in left_edges:
            painter.drawLine(x, 0, x, last_y)
        painter.drawLine(last_x, 0, last_x, last_y)

        for row in range(self.rowCount()):
            if self.isRowHidden(row):
                continue
            y = self.rowViewportPosition(row)
            h = self.rowHeight(row)
            if y >= self.viewport().height() or y + h <= 0:
                continue
            bottom = min(self.viewport().height() - 1, y + h - 1)
            painter.drawLine(first_x, bottom, last_x, bottom)
        painter.end()

    def _visible_column_edges(self) -> tuple[int | None, int | None, list[int]]:
        header = self.horizontalHeader()
        left_edges: list[int] = []
        first_x: int | None = None
        last_x: int | None = None
        for logical in range(self.columnCount()):
            if self.isColumnHidden(logical):
                continue
            section_left = header.sectionViewportPosition(logical)
            w = header.sectionSize(logical)
            if section_left + w <= 0 or section_left >= self.viewport().width():
                continue
            x = max(0, section_left)
            right = min(self.viewport().width() - 1, section_left + w - 1)
            left_edges.append(x)
            first_x = x if first_x is None else min(first_x, x)
            last_x = right if last_x is None else max(last_x, right)
        return first_x, last_x, left_edges

    def _last_visible_row_bottom(self) -> int | None:
        last_y: int | None = None
        for row in range(self.rowCount()):
            if self.isRowHidden(row):
                continue
            y = self.rowViewportPosition(row)
            h = self.rowHeight(row)
            if y >= self.viewport().height() or y + h <= 0:
                continue
            bottom = min(self.viewport().height() - 1, y + h - 1)
            last_y = bottom if last_y is None else max(last_y, bottom)
        return last_y


# ─── Sidebar ─────────────────────────────────────────────────────────────────


class _SidebarItem(QWidget):
    """Single nav item: icon-only with tooltip."""

    clicked = Signal()

    def __init__(self, icon_name: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._label = label
        self._active = False
        self.setFixedHeight(Metrics.SIDEBAR_ITEM_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, v: bool) -> None:
        self._active = v
        self.update()

    def set_label(self, text: str) -> None:
        self._label = text
        self.setToolTip(text)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        if self._active:
            p.fillRect(0, 0, w, h, QColor(Colors.SIDEBAR_HOVER))
            p.fillRect(0, 0, 3, h, QColor(Colors.SIDEBAR_INDICATOR))

        color = Colors.SIDEBAR_TEXT_ACTIVE if self._active else Colors.SIDEBAR_TEXT
        icon = get_icon(self._icon_name, color, Metrics.SIDEBAR_ICON_SIZE)
        ix = (w - Metrics.SIDEBAR_ICON_SIZE) // 2
        iy = (h - Metrics.SIDEBAR_ICON_SIZE) // 2
        icon.paint(p, ix, iy, Metrics.SIDEBAR_ICON_SIZE, Metrics.SIDEBAR_ICON_SIZE)
        p.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.clicked.emit()


class Sidebar(QWidget):
    """Vertical dark navigation sidebar."""

    current_changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(Metrics.SIDEBAR_WIDTH)
        self.setObjectName("SidebarNav")
        self.setStyleSheet(
            f"#SidebarNav {{ background:{Colors.SIDEBAR_BG}; }}"
            f" QToolTip {{ background:{Colors.BG_SURFACE}; color:{Colors.TEXT}; border:1px solid {Colors.BORDER}; padding:4px 8px; }}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, Spacing.LG, 0, Spacing.LG)
        lay.setSpacing(Spacing.XS)

        # Logo
        logo = QLabel("J")
        logo.setAlignment(Qt.AlignCenter)
        logo.setFixedHeight(36)
        logo.setStyleSheet(
            f"color:{Colors.SIDEBAR_TEXT_ACTIVE}; font-size:15pt; "
            f"font-weight:700; background:transparent;"
        )
        lay.addWidget(logo)
        lay.addSpacing(Spacing.SM)

        self._items: list[_SidebarItem] = []
        self._current = -1
        for icon_name, label in items:
            item = _SidebarItem(icon_name, label, self)
            idx = len(self._items)
            item.clicked.connect(lambda i=idx: self.set_current(i))
            self._items.append(item)
            lay.addWidget(item)

        lay.addStretch()

    def set_current(self, index: int) -> None:
        if index == self._current:
            return
        self._current = index
        for i, item in enumerate(self._items):
            item.active = (i == index)
        self.current_changed.emit(index)

    def set_item_label(self, index: int, text: str) -> None:
        if 0 <= index < len(self._items):
            self._items[index].set_label(text)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(Metrics.SIDEBAR_WIDTH, 600)
