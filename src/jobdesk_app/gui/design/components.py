"""Reusable design system components."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from .icons import get_icon
from .tokens import Colors, Metrics, Radius, Shadow, Spacing


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
            p.fillRect(0, 8, 3, h - 16, QColor(Colors.SIDEBAR_INDICATOR))

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
        self.setStyleSheet(f"background:{Colors.SIDEBAR_BG};")

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
