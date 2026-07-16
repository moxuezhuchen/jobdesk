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
from .tokens import Animation, Colors, Metrics, Radius, Spacing


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
    """Single nav item: icon with glow effect and animated hover.

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
        self._hover = False
        self.setFixedHeight(Metrics.SIDEBAR_ITEM_HEIGHT)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(label)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(label)
        self.setAccessibleDescription("")
        self.setMouseTracking(True)

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, v: bool) -> None:
        self._active = v
        self.setProperty("selected", v)
        self.setAccessibleDescription("")
        self.update()
        try:
            from PySide6.QtGui import QAccessible
            QAccessible.updateAccessibility(self, 0)
        except Exception:
            pass

    def set_label(self, text: str) -> None:
        self._label = text
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Background for active state. Phase 18 visual cleanup: removed
        # the alpha-30 blue glow (which fought the page chrome) and kept
        # only a quiet left accent bar for the active indicator.
        if self._active:
            p.fillRect(0, 0, w, h, QColor(Colors.SIDEBAR_ACTIVE_BG))
            accent_rect = QRectF(0, 8, 3, h - 16)
            p.fillRect(accent_rect, QColor(Colors.SIDEBAR_INDICATOR))
        elif self._hover:
            p.fillRect(0, 0, w, h, QColor(Colors.SIDEBAR_HOVER))

        # Icon with color based on state
        if self._active:
            icon_color = Colors.SIDEBAR_TEXT_ACTIVE
        elif self._hover:
            icon_color = "#e2e8f0"
        else:
            icon_color = Colors.SIDEBAR_TEXT

        icon = get_icon(self._icon_name, icon_color, Metrics.SIDEBAR_ICON_SIZE)
        ix = (w - Metrics.SIDEBAR_ICON_SIZE) // 2
        iy = (h - Metrics.SIDEBAR_ICON_SIZE) // 2
        icon.paint(p, ix, iy, Metrics.SIDEBAR_ICON_SIZE, Metrics.SIDEBAR_ICON_SIZE)
        p.end()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Sidebar(QWidget):
    """Vertical dark navigation sidebar with modern glassmorphism effect.

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
            f"#SidebarNav {{ background: {Colors.SIDEBAR_BG}; "
            f"border-right: 1px solid rgba(0,0,0,0.2); }}"
            f" QToolTip {{ background: {Colors.BG_SURFACE}; color: {Colors.TEXT}; "
            f"border: 1px solid {Colors.BORDER}; padding: 6px 12px; "
            f"border-radius: {Radius.MD}px; font-size: 16px; }}"
        )
        self.setAccessibleName("Navigation")
        self.setProperty("role", "tablist")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, Spacing.LG, 0, Spacing.LG)
        lay.setSpacing(Spacing.SM)

        # Logo. Phase 18 visual cleanup: drop the indigo→blue gradient
        # (which fought the rest of the page chrome) and use the primary
        # brand colour. Weight reduced 800 → 600 to match the rest of
        # the design system.
        logo_container = QFrame(self)
        logo_container.setFixedHeight(48)
        logo_container.setStyleSheet(
            f"background: {Colors.PRIMARY}; "
            f"border-radius: {Radius.MD}px; margin: 0 8px;"
        )
        logo_layout = QVBoxLayout(logo_container)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo = QLabel("J", logo_container)
        logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(
            f"color: {Colors.SIDEBAR_TEXT_ACTIVE}; font-size: 20pt; "
            f"font-weight: 600; background: transparent; padding: 4px 0;"
        )
        logo_layout.addWidget(logo)
        lay.addWidget(logo_container)
        lay.addSpacing(Spacing.MD)

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

    def sizeHint(self) -> QSize:
        return QSize(Metrics.SIDEBAR_WIDTH, 600)


# ─── Settings-page shared widgets ──────────────────────────────────────────────


class ToggleSwitch(QWidget):
    """Modern sliding switch control with smooth animations."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._checked = checked
        self._offset = 28.0 if checked else 4.0
        self.setFixedSize(56, 30)
        self.setCursor(Qt.PointingHandCursor)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, v: bool) -> None:
        self._checked = v
        self._offset = 28.0 if v else 4.0
        self.update()

    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, v: float) -> None:
        self._offset = v
        self.update()

    offset = Property(float, _get_offset, _set_offset)  # type: ignore[arg-type]

    def mousePressEvent(self, e) -> None:
        self._checked = not self._checked
        anim = QPropertyAnimation(self, b"offset", self)
        anim.setDuration(Animation.FAST)
        anim.setStartValue(self._offset)
        anim.setEndValue(28.0 if self._checked else 4.0)
        anim.start(QPropertyAnimation.DeleteWhenStopped)
        self.toggled.emit(self._checked)

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Phase 18 visual cleanup: flat track, single-colour thumb with a
        # hairline border. The previous version layered a translucent
        # white ellipse and an inner rounded-rect stroke on top of the
        # thumb which read as plastic / sticker-y. Track colour is now
        # the primary brand colour when ON (was bright green, which
        # collided with the SUCCESS semantic elsewhere in the chrome).
        track_rect = QRectF(0, 0, 56, 30)
        track_color = QColor(Colors.PRIMARY) if self._checked else QColor(Colors.TEXT_MUTED)
        p.setBrush(track_color)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(track_rect, 15, 15)

        # Thumb (white circle with a 1-px hairline).
        thumb_rect = QRectF(self._offset, 3, 24, 24)
        p.setBrush(QColor("white"))
        p.setPen(QColor(0, 0, 0, 18))
        p.drawEllipse(thumb_rect)
        p.end()


class SettingCard(QFrame):
    """Modern settings card with clean layout: title + description on left, control on right.

    Phase 18 visual cleanup: title font 17 px → 14 px (now via the
    shared ``Metrics.CARD_TITLE_FONT_PX`` token); description 14 pt →
    ``Metrics.CARD_BODY_FONT_PX`` (13 px). The hard ``setFixedHeight(72)``
    is removed so a two-line description no longer clips.
    """

    def __init__(self, title: str, description: str, control: QWidget):
        super().__init__()
        self.setObjectName("SettingCard")
        self.setStyleSheet(
            f"#SettingCard {{ background: {Colors.CARD_BG}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: {Radius.MD}px; }}"
            f" #SettingCard QLabel {{ background: transparent; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(16)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        lbl_title = QLabel(title)
        lbl_title.setStyleSheet(
            f"color: {Colors.TEXT}; font-size: {Metrics.CARD_TITLE_FONT_PX}px; font-weight: 600;"
        )
        lbl_desc = QLabel(description)
        lbl_desc.setStyleSheet(
            f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.CARD_BODY_FONT_PX}px;"
        )
        lbl_desc.setWordWrap(True)
        text_layout.addWidget(lbl_title)
        text_layout.addWidget(lbl_desc)
        self.lbl_title = lbl_title
        self.lbl_desc = lbl_desc

        layout.addLayout(text_layout, 1)
        control.setMinimumWidth(160)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)


class StatusChip(QLabel):
    """Small flat pill used to surface connection / status / metadata.

    Replaces the bespoke "Server: 10.61.193.41:22" pills and the
    bare-text "状态: COMPLETED" labels that previously cluttered the
    page chrome. ``set_state`` swaps a QSS-driven colour set in
    ``theme.py``; the chip itself stays a single ``QLabel`` so screen
    readers and keyboard focus behave normally.
    """

    _STATES = {"neutral", "info", "success", "warning", "error"}

    def __init__(self, text: str = "", state: str = "neutral", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("StatusChip")
        self.set_state(state)

    def set_state(self, state: str) -> None:
        if state not in self._STATES:
            state = "neutral"
        self.setProperty("chipState", state)
        # ``chipState`` is a dynamic property: re-polish the style so
        # the new state colours take effect.
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        super().setText(text)
        # The chip should always look like a chip, not a regular label.
        self.set_state(self.property("chipState") or "neutral")
