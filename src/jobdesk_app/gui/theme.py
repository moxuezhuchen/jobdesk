"""Global theme: QSS stylesheet + table/control helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from .design.tokens import Colors, Metrics, Radius


class ThemeMetrics:
    CONTROL_HEIGHT = Metrics.CONTROL_HEIGHT
    TABLE_ROW_HEIGHT = Metrics.TABLE_ROW_HEIGHT
    TABLE_HEADER_HEIGHT = Metrics.TABLE_HEADER_HEIGHT
    SCROLLBAR_THICKNESS = 14


def build_app_stylesheet() -> str:
    c = Colors
    m = Metrics
    scrollbar_thickness = ThemeMetrics.SCROLLBAR_THICKNESS
    scrollbar_radius = scrollbar_thickness // 2
    from pathlib import Path
    arrow_path = str(Path(__file__).parent / "resources" / "chevron-down.svg").replace("\\", "/")
    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
}}
QToolTip {{
    background: {c.BG_SURFACE};
    color: {c.TEXT};
    border: 1px solid {c.BORDER};
    padding: 4px 8px;
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
    background: {c.BORDER_FOCUS};
    border-color: {c.BORDER_FOCUS};
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
    padding: 0 10px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    color: {c.TEXT};
    font-weight: 400;
}}
QPushButton:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.BORDER_FOCUS};
}}
QPushButton:pressed {{
    background: {c.PRIMARY_PRESSED};
    border-color: {c.BORDER_FOCUS};
    padding-top: 1px;
    padding-left: 11px;
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
    font-weight: 400;
}}
QPushButton#PrimaryBtn:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.BORDER_FOCUS};
}}
QPushButton#PrimaryBtn:pressed {{
    background: {c.PRIMARY_PRESSED};
}}

QPushButton:focus {{
    border-color: {c.BORDER_FOCUS};
}}
QPushButton[buttonRole="primary_action"],
QPushButton[buttonRole="refresh_action"],
QPushButton[buttonRole="transfer_action"],
QPushButton[buttonRole="danger_action"],
QPushButton[buttonRole="settings_action"],
QPushButton[buttonRole="test_action"],
QPushButton[buttonRole="instant_action"] {{
    background: {c.BG_SURFACE};
    color: {c.TEXT};
    border-color: {c.BORDER};
    font-weight: 400;
}}
QPushButton[buttonRole="primary_action"]:hover,
QPushButton[buttonRole="refresh_action"]:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.BORDER_FOCUS};
}}
QPushButton[buttonRole="primary_action"]:pressed,
QPushButton[buttonRole="refresh_action"]:pressed,
QPushButton[buttonRole="transfer_action"]:pressed,
QPushButton[buttonRole="danger_action"]:pressed,
QPushButton[buttonRole="settings_action"]:pressed,
QPushButton[buttonRole="test_action"]:pressed,
QPushButton[buttonRole="instant_action"]:pressed {{
    background: {c.PRIMARY_PRESSED};
    border-color: {c.BORDER_FOCUS};
}}
QPushButton[buttonRole="instant_action"]:hover,
QPushButton[buttonRole="transfer_action"]:hover,
QPushButton[buttonRole="danger_action"]:hover,
QPushButton[buttonRole="test_action"]:hover,
QPushButton[buttonRole="settings_action"]:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.BORDER_FOCUS};
}}
QPushButton[feedbackState="pending"] {{
    background: {c.WARNING_BG};
    color: {c.WARNING};
    border-color: #c9a849;
}}
QPushButton[feedbackState="success"] {{
    background: {c.SUCCESS_BG};
    color: {c.SUCCESS};
    border-color: #8ab58f;
}}
QPushButton[feedbackState="error"] {{
    background: {c.ERROR_BG};
    color: {c.ERROR};
    border-color: #c28f8f;
}}
QPushButton[feedbackState="blocked"] {{
    background: {c.BORDER_SUBTLE};
    color: {c.TEXT_MUTED};
    border-color: {c.BORDER};
}}
QPushButton[buttonRole="danger_action"][feedbackState="pending"],
QPushButton[buttonRole="danger_action"][feedbackState="error"] {{
    background: {c.ERROR_BG};
    color: {c.ERROR};
    border-color: #c28f8f;
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
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {c.BORDER_FOCUS};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({arrow_path});
    width: 14px;
    height: 14px;
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
    width: {scrollbar_thickness}px;
    border: 0;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: {scrollbar_thickness}px;
    border: 0;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {c.BORDER};
    border-radius: {scrollbar_radius}px;
    min-height: 24px;
    min-width: 24px;
}}
QScrollBar::handle:hover {{
    background: {c.TEXT_MUTED};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0;
}}

/* ─── Card-embedded controls (shared by BtnCard / SettingCard / LocalHeader) ─── */
#BtnCard QPushButton, #SettingCard QPushButton, #LocalHeader QPushButton {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 0 10px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
}}
#LocalHeader QPushButton {{
    padding: 0 8px;
}}
#BtnCard QPushButton:pressed, #SettingCard QPushButton:pressed, #LocalHeader QPushButton:pressed {{
    background: {c.PRIMARY_PRESSED};
    border-color: {c.BORDER_FOCUS};
}}
#BtnCard QLineEdit, #SettingCard QLineEdit, #SettingCard QSpinBox, #SettingCard QComboBox {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 0 8px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
}}
"""


def page_title_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label
