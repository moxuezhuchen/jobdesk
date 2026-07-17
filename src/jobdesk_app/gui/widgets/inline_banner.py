"""Reusable inline banner: dismissible warning/error surface for one page.

Phase 3.1 replaces ad-hoc QMessageBox / status bar / activity log writes
with a single in-page banner widget. Pages create one, show via
:meth:`show_warning` / :meth:`show_error`, and dismiss via the close
button or :meth:`dismiss`. The banner is intentionally NOT modal: it
sits at the top of the page so the user can keep the offending widget
visible while reading the message.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from jobdesk_app.gui.i18n import tr

_SEVERITY_COLORS = {
    "warning": ("#fff8e1", "#f0b400", "\u26a0"),  # soft yellow, amber, ⚠
    "error": ("#fdecea", "#d93025", "\u2716"),  # soft red, strong red, ✖
}


class InlineBanner(QFrame):
    """One-line dismissible banner for transient warnings/errors."""

    dismissed = Signal()  # emitted when the user clicks the X

    def __init__(
        self,
        *,
        language: str = "en",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self.setObjectName("InlineBanner")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setVisible(False)

        self._icon = QLabel(self)
        self._icon.setObjectName("InlineBannerIcon")
        self._icon.setFixedWidth(20)

        self._message = QLabel(self)
        self._message.setObjectName("InlineBannerMessage")
        self._message.setWordWrap(True)
        self._message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._dismiss = QPushButton("\u2715", self)  # ✕
        self._dismiss.setObjectName("InlineBannerDismiss")
        self._dismiss.setFixedSize(24, 24)
        self._dismiss.setFlat(True)
        self._dismiss.clicked.connect(self._on_dismiss)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._icon)
        layout.addWidget(self._message, 1)
        layout.addWidget(self._dismiss)

        self._current_severity = "warning"

    def apply_language(self, language: str) -> None:
        self._language = language
        # Tooltip on dismiss button
        self._dismiss.setToolTip(tr("Dismiss", language))

    def show_warning(self, message: str) -> None:
        self._render("warning", message)

    def show_error(self, message: str) -> None:
        self._render("error", message)

    def dismiss(self) -> None:
        if self.isVisible():
            self.setVisible(False)
            self.dismissed.emit()

    def _render(self, severity: str, message: str) -> None:
        self._current_severity = severity
        bg, fg, icon = _SEVERITY_COLORS[severity]
        self.setStyleSheet(
            f"#InlineBanner {{ background: {bg}; border: 1px solid {fg};"
            f" border-radius: 8px; }}"
            f"#InlineBanner QLabel {{ color: {fg}; }}"
            f"#InlineBanner QPushButton {{ color: {fg}; }}"
        )
        self._icon.setText(icon)
        self._message.setText(message)
        self.setVisible(True)

    def _on_dismiss(self) -> None:
        self.dismiss()


__all__ = ["InlineBanner"]
