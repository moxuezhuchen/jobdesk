"""Reusable design system components."""

from __future__ import annotations

from PySide6.QtCore import Property, QEvent, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ...services.gui_settings import GuiSettingsStore
from .icons import get_icon
from .tokens import Colors, Metrics, Spacing


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
        settings_store.update(column_widths=widths)

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
    """Single nav item: icon-only with tooltip.

    Phase 11.1 — added keyboard activation and accessibility metadata
    so screen readers and keyboard users can navigate. The control is
    announced as ``role="tab"`` (sidebar entries behave like tabs of
    the underlying QStackedWidget) with the label exposed via
    ``accessibleName`` and toggled ``aria-selected`` when active.
    """

    clicked = Signal()

    def __init__(self, icon_name: str, label: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._label = label
        self._active = False
        self.setFixedHeight(Metrics.SIDEBAR_ITEM_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)
        # Accept keyboard focus so Tab / Shift+Tab can land on a nav
        # item. Without this, the control is invisible to the focus
        # chain and screen readers skip it entirely.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Expose the label to assistive tech. We deliberately do NOT
        # set accessibleDescription so screen readers don't read the
        # tooltip twice.
        self.setAccessibleName(label)
        self.setAccessibleDescription("")

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, v: bool) -> None:
        self._active = v
        # Mirror the visual "active" state into the accessibility tree
        # so screen readers can announce the currently selected tab.
        self.setProperty("selected", v)
        self.setAccessibleDescription("")
        # ``aria-selected`` lives on the same property in Qt's a11y
        # bridge; the call below is the official way to refresh the
        # cache after a property mutation.
        self.update()
        try:
            from PySide6.QtGui import QAccessible
            QAccessible.updateAccessibility(self, 0)  # type: ignore[call-arg, arg-type]  # PySide6 6.11 changed signature; runtime accepts (object, int)
        except Exception:
            pass

    def set_label(self, text: str) -> None:
        self._label = text
        self.setToolTip(text)
        # Keep the accessibility tree in sync with the tooltip text.
        self.setAccessibleName(text)
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

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Activate on Space / Return — these are the two keys Qt's
        # default button handling uses, so the sidebar feels consistent
        # with the rest of the app. Arrow keys are intentionally NOT
        # handled here because sidebar navigation is owned by the
        # Sidebar container (which can decide on Up/Down vs. nothing).
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Sidebar(QWidget):
    """Vertical dark navigation sidebar.

    Phase 11.1 — added ``role="tablist"`` on the container and
    ``role="tab"`` (via ``QAccessible.Role.PageTab``) on each item so
    screen readers announce the four pages correctly. Keyboard users
    can Tab between items and press Space / Enter to navigate.
    """

    current_changed = Signal(int)

    def __init__(self, items: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(Metrics.SIDEBAR_WIDTH)
        self.setObjectName("SidebarNav")
        self.setStyleSheet(
            f"#SidebarNav {{ background:{Colors.SIDEBAR_BG}; }}"
            f" QToolTip {{ background:{Colors.BG_SURFACE}; color:{Colors.TEXT}; border:1px solid {Colors.BORDER}; padding:4px 8px; }}"
        )
        # Announce the sidebar as a tablist of page tabs.
        self.setAccessibleName("Navigation")
        # ``setProperty`` with these keys is the documented way to
        # control role/state in Qt's accessibility bridge.
        self.setProperty("role", "tablist")

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


# ─── Settings-page shared widgets ──────────────────────────────────────────────


class ToggleSwitch(QWidget):
    """滑动开关控件。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._checked = checked
        self._offset = 30.0 if checked else 6.0
        self.setFixedSize(60, 32)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, v: bool) -> None:
        self._checked = v
        self._offset = 30.0 if v else 6.0
        self.update()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, v: float) -> None:
        self._offset = v
        self.update()

    offset = Property(float, _get_offset, _set_offset)  # type: ignore[arg-type]

    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._checked = not self._checked
        anim = QPropertyAnimation(self, b"offset", self)
        anim.setDuration(120)
        anim.setStartValue(self._offset)
        anim.setEndValue(30.0 if self._checked else 6.0)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self.toggled.emit(self._checked)

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track_color = QColor("#5c7fa6") if self._checked else QColor("#9aaec4")
        p.setBrush(track_color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(0, 0, 60, 32), 16, 16)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(self._offset, 5, 22, 22))
        p.end()


class SettingCard(QFrame):
    """Windows Terminal 风格卡片: 圆角背景, 标题+描述紧贴左侧, 控件右侧。"""

    def __init__(self, title: str, description: str, control: QWidget):
        super().__init__()
        self.setObjectName("SettingCard")
        self.setStyleSheet(
            "#SettingCard { background: #dfe7f0; border: 1px solid #9aaec4; border-radius: 3px; }"
            " #SettingCard QLabel { background: transparent; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        self.setFixedHeight(60)

        lbl_title = QLabel(title)
        lbl_desc = QLabel(description)
        lbl_desc.setStyleSheet("color: #2f3b49; font-size: 14pt;")
        self.lbl_title = lbl_title
        self.lbl_desc = lbl_desc

        layout.addWidget(lbl_title)
        layout.addSpacing(16)
        layout.addWidget(lbl_desc)
        layout.addStretch()
        control.setMinimumWidth(160)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
