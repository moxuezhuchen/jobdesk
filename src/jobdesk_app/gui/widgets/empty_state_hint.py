"""Reusable empty-state hint card shown atop a page when there is nothing to do.

Phase 2.1 of the GUI onboarding fix. Used by the Files / Runs / Settings
pages whenever the page has nothing meaningful to display (no servers,
no runs, no directory listing, etc.). The page keeps ownership of when
the card is visible — :class:`EmptyStateHint` itself just renders the
copy and emits ``action_requested`` when the user clicks one of its
buttons.

The visual style deliberately mirrors the existing node-graph
:class:`OnboardingCard` (white background, soft #d7dde5 border,
14px rounded corners) so the two empty-state surfaces feel like the
same family without needing to share a stylesheet file.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from jobdesk_app.gui.i18n import tr


class EmptyStateHint(QFrame):
    """Empty-state card: title + body + up to 2 action buttons.

    ``action_requested`` carries the ``action_id`` of the clicked button
    (``"open_settings"``, ``"go_to_submit"``, ``"add_server"``, ...)
    so embedding pages can route the request however they want. Buttons
    emit a fresh id per click; the page decides if the same action
    produces a navigation, a status message, or a service call.
    """

    action_requested = Signal(str)  # action_id

    def __init__(
        self,
        *,
        title_key: str,
        body_key: str,
        action_texts: tuple[tuple[str, str], ...] = (),
        language: str = "en",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._title_key = title_key
        self._body_key = body_key
        self._action_texts = action_texts

        self.setObjectName("EmptyStateHint")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # match OnboardingCard visual: white bg, soft border, rounded corners
        self.setStyleSheet(
            "#EmptyStateHint {"
            " background: #ffffff; border: 1px solid #d7dde5; border-radius: 14px;"
            "}"
            "#EmptyStateHint QLabel { color: #253041; }"
            "#EmptyStateHint QPushButton { padding: 6px 10px; }"
        )

        title_font = self.font()
        title_font.setBold(True)
        title_font.setPointSize(max(title_font.pointSize() + 1, 11))

        self._title_label = QLabel(self)
        self._title_label.setWordWrap(True)
        self._title_label.setFont(title_font)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._body_label = QLabel(self)
        self._body_label.setWordWrap(True)
        self._body_label.setStyleSheet("color: #4b5563;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)
        layout.addWidget(self._title_label)
        layout.addWidget(self._body_label)

        self._action_buttons: dict[str, QPushButton] = {}
        if action_texts:
            actions_row = QHBoxLayout()
            actions_row.setContentsMargins(0, 6, 0, 0)
            actions_row.setSpacing(8)
            for action_id, _text_key in action_texts:
                btn = QPushButton(self)
                btn.setObjectName(f"EmptyStateAction_{action_id}")
                btn.clicked.connect(
                    lambda _checked=False, aid=action_id: self.action_requested.emit(aid)
                )
                self._action_buttons[action_id] = btn
                actions_row.addWidget(btn)
            actions_row.addStretch()
            layout.addLayout(actions_row)

        self.apply_language(language)

    def apply_language(self, language: str) -> None:
        """Re-translate the title/body/buttons to the given language."""
        self._language = language
        self._title_label.setText(tr(self._title_key, language))
        self._body_label.setText(tr(self._body_key, language))
        for action_id, text_key in self._action_texts:
            btn = self._action_buttons.get(action_id)
            if btn is not None:
                btn.setText(tr(text_key, language))


__all__ = ["EmptyStateHint"]
