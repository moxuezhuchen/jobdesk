"""Global theme: QSS stylesheet + table/control helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from .design.tokens import Colors, Metrics, Radius, Shadow


class ThemeMetrics:
    CONTROL_HEIGHT = Metrics.CONTROL_HEIGHT
    TABLE_ROW_HEIGHT = Metrics.TABLE_ROW_HEIGHT
    TABLE_HEADER_HEIGHT = Metrics.TABLE_HEADER_HEIGHT
    SCROLLBAR_THICKNESS = 10


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
    outline: none;
}}
QToolTip {{
    background: {c.BG_SURFACE};
    color: {c.TEXT};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 8px 14px;
    font-size: 16px;
}}
QMainWindow {{
    background: {c.BG_BASE};
    color: {c.TEXT};
}}
QWidget {{
    background: transparent;
    color: {c.TEXT};
}}

/* ─── Labels ─── */
QLabel {{
    background: transparent;
    color: {c.TEXT};
}}
QLabel#PageTitle {{
    color: {c.TEXT};
    font-size: 24px;
    font-weight: 600;
    padding: 0 0 8px 0;
}}

/* ─── Checkboxes ─── */
QCheckBox {{
    spacing: 12px;
    font-size: 16px;
    color: {c.TEXT};
}}
QCheckBox::indicator {{
    width: 20px;
    height: 20px;
    border: 2px solid {c.BORDER};
    border-radius: {Radius.SM}px;
    background: {c.BG_SURFACE};
}}
QCheckBox::indicator:hover {{
    border-color: {c.PRIMARY};
}}
QCheckBox::indicator:checked {{
    background: {c.PRIMARY};
    border-color: {c.PRIMARY};
    image: none;
}}

/* ─── SpinBox ─── */
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0;
    border: none;
}}

/* ─── Push Buttons ─── */
QPushButton {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 0 16px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    color: {c.TEXT};
    font-size: 16px;
    font-weight: 500;
}}
QPushButton:hover {{
    background: {c.CARD_HOVER};
    border-color: {c.PRIMARY};
}}
QPushButton:pressed {{
    background: {c.BORDER_SUBTLE};
    border-color: {c.BORDER};
    padding-top: 1px;
    padding-left: 17px;
}}
QPushButton:disabled {{
    color: {c.TEXT_MUTED};
    background: {c.BORDER_SUBTLE};
    border-color: {c.BORDER};
}}
QPushButton:focus {{
    border-color: {c.PRIMARY};
}}
QPushButton#PrimaryBtn {{
    background: {c.PRIMARY};
    color: {c.PRIMARY_TEXT};
    border: 1px solid {c.PRIMARY};
    font-weight: 600;
}}
QPushButton#PrimaryBtn:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.PRIMARY_HOVER};
}}
QPushButton#PrimaryBtn:pressed {{
    background: {c.PRIMARY_PRESSED};
    border-color: {c.PRIMARY_PRESSED};
}}

/* ─── Button Roles ─── */
QPushButton[buttonRole="primary_action"],
QPushButton[buttonRole="refresh_action"],
QPushButton[buttonRole="transfer_action"],
QPushButton[buttonRole="instant_action"] {{
    background: {c.PRIMARY};
    color: {c.PRIMARY_TEXT};
    border-color: {c.PRIMARY};
    font-weight: 600;
}}
QPushButton[buttonRole="primary_action"]:hover,
QPushButton[buttonRole="refresh_action"]:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.PRIMARY_HOVER};
}}
QPushButton[buttonRole="primary_action"]:pressed,
QPushButton[buttonRole="refresh_action"]:pressed,
QPushButton[buttonRole="transfer_action"]:pressed {{
    background: {c.PRIMARY_PRESSED};
    border-color: {c.PRIMARY_PRESSED};
}}
QPushButton[buttonRole="instant_action"]:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.PRIMARY_HOVER};
}}
QPushButton[buttonRole="danger_action"] {{
    background: {c.ERROR_BG};
    color: {c.ERROR};
    border-color: {c.ERROR_BORDER};
}}
QPushButton[buttonRole="danger_action"]:hover {{
    background: {c.ERROR};
    color: {c.PRIMARY_TEXT};
    border-color: {c.ERROR};
}}
QPushButton[buttonRole="settings_action"],
QPushButton[buttonRole="test_action"] {{
    background: {c.BG_SURFACE};
    color: {c.TEXT};
    border-color: {c.BORDER};
    font-weight: 500;
}}

/* ─── Button Feedback States ─── */
QPushButton[feedbackState="pending"] {{
    background: {c.WARNING_BG};
    color: {c.WARNING};
    border-color: {c.WARNING_BORDER};
}}
QPushButton[feedbackState="success"] {{
    background: {c.SUCCESS_BG};
    color: {c.SUCCESS};
    border-color: {c.SUCCESS_BORDER};
}}
QPushButton[feedbackState="error"] {{
    background: {c.ERROR_BG};
    color: {c.ERROR};
    border-color: {c.ERROR_BORDER};
}}
QPushButton[feedbackState="blocked"] {{
    background: {c.BORDER_SUBTLE};
    color: {c.TEXT_MUTED};
    border-color: {c.BORDER};
}}

/* ─── Input Controls ─── */
QLineEdit, QComboBox, QSpinBox {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    padding: 0 14px;
    font-size: 16px;
    color: {c.TEXT};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
    border-color: {c.PRIMARY};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border-color: {c.PRIMARY};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 28px;
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
    font-size: 16px;
}}

/* ─── GroupBox ─── */
QGroupBox {{
    background: {c.CARD_BG};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    margin-top: 16px;
    padding: 16px;
    font-weight: 600;
    color: {c.TEXT_SECONDARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 16px;
    padding: 0 8px;
    color: {c.TEXT};
    font-weight: 600;
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
    font-size: 16px;
    outline: none;
}}
QTableWidget::item {{
    padding: 12px 16px;
    border: none;
    border-bottom: 1px solid {c.BORDER_SUBTLE};
}}
QTableWidget::item:selected {{
    background: {c.TABLE_SELECTION};
    color: {c.TEXT};
}}
QTableWidget::item:hover {{
    background: {c.TABLE_HOVER};
}}
QHeaderView::section {{
    background: {c.TABLE_HEADER_BG};
    border: none;
    border-bottom: 2px solid {c.BORDER};
    border-right: 1px solid {c.BORDER_SUBTLE};
    padding: 14px 16px;
    min-height: {m.TABLE_HEADER_HEIGHT}px;
    max-height: {m.TABLE_HEADER_HEIGHT}px;
    color: {c.TEXT_SECONDARY};
    font-weight: 600;
    font-size: 15px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
QHeaderView::section:hover {{
    background: {c.BORDER_SUBTLE};
}}
QHeaderView::section:first {{
    border-top-left-radius: {Radius.MD}px;
}}
QHeaderView::section:last {{
    border-top-right-radius: {Radius.MD}px;
    border-right: none;
}}

/* ─── Tabs ─── */
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
    padding: 14px 22px;
    font-weight: 500;
    font-size: 16px;
}}
QTabBar::tab:hover {{
    color: {c.PRIMARY};
    background: {c.INFO_BG};
}}
QTabBar::tab:selected {{
    color: {c.PRIMARY};
    border-bottom-color: {c.PRIMARY};
    font-weight: 600;
}}

/* ─── Splitter ─── */
QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:hover {{
    background: {c.BORDER};
}}
QSplitter::handle:vertical {{
    height: 8px;
    margin: 2px 0;
}}
QSplitter::handle:horizontal {{
    width: 8px;
    margin: 0 2px;
}}

/* ─── Scrollbars ─── */
QScrollBar:vertical {{
    background: transparent;
    width: {scrollbar_thickness}px;
    border: 0;
    margin: 4px 2px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: {scrollbar_thickness}px;
    border: 0;
    margin: 2px 4px;
}}
QScrollBar::handle:vertical {{
    background: {c.BORDER};
    border-radius: {scrollbar_radius}px;
    min-height: 40px;
    margin: 0 2px;
}}
QScrollBar::handle:horizontal {{
    background: {c.BORDER};
    border-radius: {scrollbar_radius}px;
    min-width: 40px;
    margin: 2px 0;
}}
QScrollBar::handle:hover {{
    background: {c.TEXT_MUTED};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ─── Cards ─── */
#BtnCard, #SettingCard, #LocalHeader, #RunsTableCard, #RunsActivityLogCard, #ResultsCard {{
    background: {c.CARD_BG};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
}}

/* ─── Card-embedded controls ─── */
#BtnCard QPushButton, #SettingCard QPushButton, #LocalHeader QPushButton {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 0 14px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
}}
#LocalHeader QPushButton {{
    padding: 0 10px;
}}
#BtnCard QPushButton:pressed, #SettingCard QPushButton:pressed, #LocalHeader QPushButton:pressed {{
    background: {c.BORDER_SUBTLE};
}}
#BtnCard QLineEdit, #SettingCard QLineEdit, #SettingCard QSpinBox, #SettingCard QComboBox {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 0 10px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
}}

/* ─── Progress Bar ─── */
QProgressBar {{
    background: {c.BORDER_SUBTLE};
    border: none;
    border-radius: {Radius.MD}px;
    height: 8px;
    text-align: center;
    font-size: 11px;
    color: {c.TEXT_MUTED};
}}
QProgressBar::chunk {{
    background: {c.PRIMARY};
    border-radius: {Radius.MD}px;
}}

/* ─── Menu ─── */
QMenu {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 6px;
}}
QMenu::item {{
    padding: 12px 20px;
    border-radius: {Radius.SM}px;
    font-size: 16px;
}}
QMenu::item:selected {{
    background: {c.TABLE_HOVER};
    color: {c.PRIMARY};
}}
QMenu::separator {{
    height: 1px;
    background: {c.BORDER_SUBTLE};
    margin: 6px 0;
}}

/* ─── Dialog ─── */
QDialog {{
    background: {c.BG_BASE};
}}
"""


def page_title_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label
