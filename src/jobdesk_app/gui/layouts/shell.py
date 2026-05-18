"""AppShell — sidebar + content area top-level container."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QWidget

from ..design.animations import fade_switch
from ..design.components import Sidebar
from ..design.tokens import Colors


class AppShell(QWidget):
    """Sidebar navigation + page stack with fade transitions."""

    page_changed = Signal(int)

    def __init__(self, nav_items: list[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar(nav_items)
        root.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.pages.setStyleSheet(f"background:{Colors.BG_BASE};")
        root.addWidget(self.pages, 1)

        self.sidebar.current_changed.connect(self._on_nav)

    def add_page(self, widget: QWidget) -> int:
        return self.pages.addWidget(widget)

    def set_current(self, index: int) -> None:
        self.sidebar.set_current(index)

    def set_nav_label(self, index: int, text: str) -> None:
        self.sidebar.set_item_label(index, text)

    def _on_nav(self, index: int) -> None:
        fade_switch(self.pages, index)
        self.page_changed.emit(index)
