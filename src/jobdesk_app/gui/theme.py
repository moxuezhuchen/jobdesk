"""Global theme: QSS stylesheet + table/control helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSizePolicy, QTableWidget

from .design.tokens import Colors, Metrics, Radius, Spacing


class ThemeMetrics:
    CONTROL_HEIGHT = Metrics.CONTROL_HEIGHT
    PAGE_MARGIN = Spacing.XL
    PAGE_SPACING = Spacing.MD
    RADIUS = Radius.MD
    TABLE_ROW_HEIGHT = Metrics.TABLE_ROW_HEIGHT
    TABLE_HEADER_HEIGHT = Metrics.TABLE_HEADER_HEIGHT


def build_app_stylesheet() -> str:
    c = Colors
    m = Metrics
    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
}}
QMainWindow, QWidget {{
    background: {c.BG_BASE};
    color: {c.TEXT};
}}
QLabel {{
    background: transparent;
}}
QLabel#PageTitle {{
    color: {c.TEXT};
    font-size: 13pt;
    font-weight: 600;
    padding: 0 0 2px 0;
}}

/* ─── Checkboxes ─── */
QCheckBox {{
    spacing: 10px;
}}
QCheckBox::indicator {{
    width: 20px;
    height: 20px;
    border: 2px solid {c.BORDER};
    border-radius: 4px;
    background: {c.BG_SURFACE};
}}
QCheckBox::indicator:hover {{
    border-color: #93c5fd;
}}
QCheckBox::indicator:checked {{
    background: #2563eb;
    border-color: #2563eb;
}}
/* ─── SpinBox: hide up/down buttons ─── */
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0;
    border: none;
}}
/* ─── GroupBox fix ─── */
QGroupBox {{
    padding-top: 28px;
    margin-top: 8px;
}}
QPushButton {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 0 14px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {c.INFO_BG};
    border-color: #93c5fd;
}}
QPushButton:pressed {{
    background: #dbeafe;
}}
QPushButton:disabled {{
    color: {c.TEXT_MUTED};
    background: {c.BORDER_SUBTLE};
    border-color: {c.BORDER};
}}
QPushButton#PrimaryBtn {{
    background: {c.BG_SURFACE};
    color: {c.TEXT};
    border: 1px solid {c.BORDER};
    font-weight: 600;
}}
QPushButton#PrimaryBtn:hover {{
    background: {c.INFO_BG};
    border-color: #93c5fd;
}}
QPushButton#PrimaryBtn:pressed {{
    background: {c.PRIMARY_PRESSED};
}}

/* ─── Inputs ─── */
QLineEdit, QComboBox, QSpinBox {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    padding: 0 8px;
}}
QComboBox::drop-down {{
    width: 28px;
}}
QComboBox::down-arrow {{
    width: 14px;
    height: 14px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {c.BORDER_FOCUS};
}}
QTextEdit {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
}}

/* ─── Groups ─── */
QGroupBox {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    margin-top: 14px;
    padding: 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {c.TEXT};
    font-weight: 500;
}}

/* ─── Tables ─── */
QTableWidget {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    gridline-color: {c.BORDER_SUBTLE};
    alternate-background-color: {c.TABLE_ALT_ROW};
    selection-background-color: {c.TABLE_SELECTION};
    selection-color: {c.TEXT};
}}
QTableWidget::item:selected {{
    background: {c.TABLE_SELECTION};
    color: {c.TEXT};
}}
QHeaderView::section {{
    background: {c.TABLE_HEADER_BG};
    border: none;
    border-right: 1px solid {c.BORDER};
    border-bottom: 1px solid {c.BORDER};
    padding: 0 8px;
    min-height: {m.TABLE_HEADER_HEIGHT}px;
    max-height: {m.TABLE_HEADER_HEIGHT}px;
    color: {c.TEXT};
}}

/* ─── Tabs (inside pages like Settings) ─── */
QTabWidget::pane {{
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    background: {c.BG_SURFACE};
}}
QTabBar::tab {{
    background: transparent;
    color: {c.TEXT_SECONDARY};
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 16px;
    font-weight: 500;
}}
QTabBar::tab:hover {{
    color: {c.TEXT};
}}
QTabBar::tab:selected {{
    color: {c.PRIMARY};
    border-bottom-color: {c.PRIMARY};
}}

/* ─── Splitter ─── */
QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:hover {{
    background: {c.BORDER};
}}

/* ─── Scrollbar ─── */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    border: 0;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
    border: 0;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {c.BORDER};
    border-radius: 4px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{
    background: {c.TEXT_MUTED};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0;
}}
"""


def page_title_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label


def normalize_control_heights(*widgets) -> None:
    for w in widgets:
        w.setMinimumHeight(Metrics.CONTROL_HEIGHT)
        w.setMaximumHeight(Metrics.CONTROL_HEIGHT)
        w.setSizePolicy(w.sizePolicy().horizontalPolicy(), QSizePolicy.Fixed)


def configure_standard_table(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(Metrics.TABLE_ROW_HEIGHT)
    table.horizontalHeader().setMinimumHeight(Metrics.TABLE_HEADER_HEIGHT)
    table.horizontalHeader().setMaximumHeight(Metrics.TABLE_HEADER_HEIGHT)
