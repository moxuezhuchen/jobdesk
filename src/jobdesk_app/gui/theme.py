from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSizePolicy, QTableWidget


class ThemeColors:
    BACKGROUND = "#f5f7fb"
    SURFACE = "#ffffff"
    SURFACE_ALT = "#f8fafc"
    TEXT = "#20242c"
    MUTED_TEXT = "#475569"
    BORDER = "#cbd5e1"
    BORDER_SUBTLE = "#e2e8f0"
    HEADER = "#e8edf5"
    NAV_BACKGROUND = "#1f2937"
    NAV_TEXT = "#e5e7eb"
    ACCENT = "#2563eb"
    ACCENT_SOFT = "#eef4ff"
    ACCENT_PRESSED = "#dbeafe"
    ACCENT_BORDER = "#93c5fd"
    WHITE = "#ffffff"


class ThemeMetrics:
    CONTROL_HEIGHT = 36
    PAGE_MARGIN = 14
    PAGE_SPACING = 10
    RADIUS = 6
    NAV_MIN_WIDTH = 140
    NAV_MAX_WIDTH = 190


APP_FONT_FAMILIES = '"Microsoft YaHei UI", "Segoe UI", Arial'


def build_app_stylesheet() -> str:
    c = ThemeColors
    m = ThemeMetrics
    return f"""
QMainWindow, QWidget {{
    background: {c.BACKGROUND};
    color: {c.TEXT};
    font-family: {APP_FONT_FAMILIES};
    font-size: 10pt;
    font-weight: 600;
}}
QLabel#PageTitle {{
    color: {c.TEXT};
    font-size: 14pt;
    font-weight: 700;
    padding: 0 0 4px 0;
}}
QListWidget {{
    background: {c.NAV_BACKGROUND};
    color: {c.NAV_TEXT};
    border: 0;
    padding: 10px 6px;
    outline: 0;
}}
QListWidget::item {{
    padding: 10px 12px;
    border-radius: {m.RADIUS}px;
    font-weight: 600;
}}
QListWidget::item:hover {{
    background: #334155;
}}
QListWidget::item:selected {{
    background: {c.ACCENT};
    color: {c.WHITE};
}}
QPushButton {{
    background: {c.SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS}px;
    padding: 0 12px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {c.ACCENT_SOFT};
    border-color: {c.ACCENT_BORDER};
}}
QPushButton:pressed {{
    background: {c.ACCENT_PRESSED};
}}
QPushButton:disabled {{
    color: #94a3b8;
    background: #f1f5f9;
    border-color: {c.BORDER_SUBTLE};
}}
QLineEdit, QComboBox, QSpinBox, QTextEdit, QTableWidget, QGroupBox {{
    background: {c.SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS}px;
}}
QLineEdit, QComboBox, QSpinBox {{
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    padding: 0 8px;
    font-weight: 600;
}}
QGroupBox {{
    margin-top: 12px;
    padding: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {c.MUTED_TEXT};
}}
QHeaderView::section {{
    background: {c.HEADER};
    border: 0;
    border-right: 1px solid {c.BORDER};
    border-bottom: 1px solid {c.BORDER};
    padding: 6px 8px;
    font-weight: 700;
}}
QTableWidget {{
    gridline-color: {c.BORDER_SUBTLE};
    selection-background-color: {c.ACCENT};
    selection-color: {c.WHITE};
    alternate-background-color: {c.SURFACE_ALT};
    font-weight: 600;
}}
QTableWidget::item:selected {{
    background: {c.ACCENT};
    color: {c.WHITE};
}}
QSplitter::handle {{
    background: #d8dee9;
}}
QSplitter::handle:hover {{
    background: {c.ACCENT_BORDER};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    border: 0;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: #cbd5e1;
    border-radius: 4px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{
    background: #94a3b8;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
"""


def page_title_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label


def normalize_control_heights(*widgets) -> None:
    for widget in widgets:
        widget.setMinimumHeight(ThemeMetrics.CONTROL_HEIGHT)
        widget.setMaximumHeight(ThemeMetrics.CONTROL_HEIGHT)
        widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(), QSizePolicy.Fixed)


def configure_standard_table(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)
    table.verticalHeader().setVisible(False)
