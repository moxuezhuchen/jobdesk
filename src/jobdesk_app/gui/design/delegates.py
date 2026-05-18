"""Table item delegates for rich cell rendering."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from .tokens import Colors, Radius, Spacing

_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "connected": (Colors.SUCCESS, Colors.SUCCESS_BG),
    "completed": (Colors.SUCCESS, Colors.SUCCESS_BG),
    "ok": (Colors.SUCCESS, Colors.SUCCESS_BG),
    "sftp-ok": (Colors.SUCCESS, Colors.SUCCESS_BG),
    "running": (Colors.INFO, Colors.INFO_BG),
    "uploading": (Colors.INFO, Colors.INFO_BG),
    "submitted": (Colors.INFO, Colors.INFO_BG),
    "pending": (Colors.TEXT_MUTED, Colors.BORDER_SUBTLE),
    "queued": (Colors.TEXT_MUTED, Colors.BORDER_SUBTLE),
    "error": (Colors.ERROR, Colors.ERROR_BG),
    "failed": (Colors.ERROR, Colors.ERROR_BG),
    "no-response": (Colors.WARNING, Colors.WARNING_BG),
}


def _resolve(text: str) -> tuple[str, str]:
    low = text.lower().strip()
    for key, colors in _STATUS_COLORS.items():
        if key in low:
            return colors
    return Colors.TEXT_SECONDARY, Colors.BORDER_SUBTLE


class StatusBadgeDelegate(QStyledItemDelegate):
    """Draws colored pill badge for status cells."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        text = index.data(Qt.DisplayRole) or ""
        if not text.strip():
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        fg_hex, bg_hex = _resolve(text)
        rect: QRect = option.rect.adjusted(Spacing.SM, Spacing.XS, -Spacing.SM, -Spacing.XS)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(bg_hex))
        painter.drawRoundedRect(rect, Radius.SM, Radius.SM)

        fg = QColor(fg_hex)
        dot_x = rect.left() + Spacing.SM + 3
        dot_y = rect.center().y()
        painter.setBrush(fg)
        painter.drawEllipse(dot_x - 3, dot_y - 3, 6, 6)

        painter.setPen(QPen(fg))
        text_rect = rect.adjusted(Spacing.SM + 10, 0, -Spacing.XS, 0)
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()

    def sizeHint(self, option, index):
        h = super().sizeHint(option, index)
        h.setHeight(max(h.height(), 26))
        return h
