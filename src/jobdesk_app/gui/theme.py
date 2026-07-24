"""Global theme: QSS stylesheet + table/control helpers."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel

from .design.tokens import Colors, Metrics, Radius


class ThemeMetrics:
    CONTROL_HEIGHT = Metrics.CONTROL_HEIGHT
    TABLE_ROW_HEIGHT = Metrics.TABLE_ROW_HEIGHT
    TABLE_HEADER_HEIGHT = Metrics.TABLE_HEADER_HEIGHT
    # WinSCP-style scrollbar: visually thicker than the modern web-style
    # 6 px/10 px bar so file lists remain comfortable to grab with a
    # mouse on Windows. 14 px is also the value the test
    # ``test_scrollbar_styles_are_thick_enough_for_file_lists`` pins,
    # so ThemeMetrics and the produced CSS both honour that contract.
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
    font-size: {Metrics.BASE_FONT_PX}px;
    outline: none;
}}
QToolTip {{
    background: {c.BG_SURFACE};
    color: {c.TEXT};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    padding: 8px 14px;
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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
    font-size: {Metrics.PAGE_TITLE_FONT_PX}px;
    font-weight: 600;
    padding: 0 0 8px 0;
}}
QLabel#SectionTitle {{
    color: {c.TEXT};
    font-size: {Metrics.SECTION_TITLE_FONT_PX}px;
    font-weight: 600;
    padding: 0;
}}
QLabel#SectionLabel {{
    color: {Colors.TEXT_SECONDARY};
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
    font-weight: 500;
    padding: 0;
    background: transparent;
}}
QLabel#HelpText {{
    color: {Colors.TEXT_SECONDARY};
    font-size: {Metrics.HELP_TEXT_FONT_PX}px;
    padding: 4px 0 8px 0;
    background: transparent;
}}
QLabel#PageDescription {{
    color: {Colors.TEXT_SECONDARY};
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
    padding: 0;
    background: transparent;
}}

/* ─── Checkboxes ─── */
QCheckBox {{
    spacing: 12px;
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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

/* Compact icon buttons opt out of the shared action-control height.  Their
 * geometry is intentionally fixed by the owning widget, so the global
 * 56px button rule must not stretch them vertically. */
QPushButton#PreviewToggleBtn,
QPushButton#SidebarCollapseBtn,
QPushButton#InlineBannerDismiss {{
    min-width: 0;
    max-width: 24px;
    min-height: 0;
    max-height: 24px;
    padding: 0;
}}
QPushButton#WorkflowStepMoveBtn {{
    min-width: 0;
    max-width: 36px;
    min-height: 0;
    max-height: 32px;
    padding: 0;
}}
QPushButton#WorkflowStepRemoveBtn {{
    min-width: 0;
    max-width: 32px;
    min-height: 0;
    max-height: 32px;
    padding: 0;
}}

QPushButton#PrimaryBtn,
QPushButton#FilesSubmitBtn,
QPushButton#WorkflowDispatchBtn {{
    background: {c.PRIMARY};
    color: {c.PRIMARY_TEXT};
    border: 1px solid {c.PRIMARY};
    font-weight: 600;
}}
QPushButton#PrimaryBtn:hover,
QPushButton#FilesSubmitBtn:hover,
QPushButton#WorkflowDispatchBtn:hover {{
    background: {c.PRIMARY_HOVER};
    border-color: {c.PRIMARY_HOVER};
}}
QPushButton#PrimaryBtn:pressed,
QPushButton#FilesSubmitBtn:pressed,
QPushButton#WorkflowDispatchBtn:pressed {{
    background: {c.PRIMARY_PRESSED};
    border-color: {c.PRIMARY_PRESSED};
}}

/* ─── Button Roles ─── */
/* WinSCP-inspired neutral button styling: every action button shares the
 * same surface palette so a screen user can scan by colour only what the
 * feedback state is conveying, not what kind of action is queued. The
 * primary CTAs opt into ``QPushButton#PrimaryBtn`` (above) by id, not by
 * role, so multiple "primary_action" buttons on the same page no longer
 * compete for the bold blue treatment. */
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
    font-weight: 500;
}}
QPushButton[buttonRole="primary_action"]:hover,
QPushButton[buttonRole="refresh_action"]:hover,
QPushButton[buttonRole="transfer_action"]:hover,
QPushButton[buttonRole="danger_action"]:hover,
QPushButton[buttonRole="settings_action"]:hover,
QPushButton[buttonRole="test_action"]:hover,
QPushButton[buttonRole="instant_action"]:hover {{
    background: {c.CARD_HOVER};
    border-color: {c.BORDER};
}}
/* Danger keeps a distinct outline colour but still shares the neutral
 * hover surface — the "destructive" meaning comes from the label and
 * the danger tooltip, not from turning red on hover. */
QPushButton[buttonRole="danger_action"] {{
    border-color: {c.ERROR_BORDER};
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

/* Disabled primary actions still need readable contrast. */
QPushButton#PrimaryBtn:disabled,
QPushButton#FilesSubmitBtn:disabled,
QPushButton#WorkflowDispatchBtn:disabled {{
    background: {c.INFO_BG};
    color: {c.PRIMARY};
    border-color: {c.INFO_BORDER};
    font-weight: 600;
}}

/* ─── Input Controls ─── */
QLineEdit, QComboBox, QSpinBox {{
    background: {c.BG_SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {Radius.MD}px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    padding: 0 14px;
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
    outline: none;
}}
QTableWidget::item {{
    padding: {Metrics.TABLE_CELL_VERTICAL_PADDING}px {Metrics.TABLE_CELL_HORIZONTAL_PADDING}px;
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
    border-bottom: 1px solid {c.BORDER};
    border-right: 1px solid {c.BORDER_SUBTLE};
    padding: {Metrics.TABLE_CELL_VERTICAL_PADDING}px {Metrics.TABLE_CELL_HORIZONTAL_PADDING}px;
    min-height: {m.TABLE_HEADER_HEIGHT}px;
    max-height: {m.TABLE_HEADER_HEIGHT}px;
    color: #64748b;
    font-weight: 500;
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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
    padding: 12px 20px;
    font-weight: 500;
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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

/* ─── Status Chip ─── */
QLabel#StatusChip {{
    background: {Colors.CHIP_BG};
    color: {Colors.CHIP_TEXT};
    border: 1px solid {Colors.CHIP_BORDER};
    border-radius: 10px;
    padding: 2px 10px;
    font-size: {Metrics.CHIP_FONT_PX}px;
    font-weight: 500;
}}
QLabel#StatusChip[chipState="info"] {{
    background: {Colors.CHIP_BG_INFO};
    color: {Colors.CHIP_TEXT_INFO};
    border-color: {Colors.CHIP_BORDER_INFO};
}}
QLabel#StatusChip[chipState="success"] {{
    background: {Colors.CHIP_BG_SUCCESS};
    color: {Colors.CHIP_TEXT_SUCCESS};
    border-color: {Colors.CHIP_BORDER_SUCCESS};
}}
QLabel#StatusChip[chipState="warning"] {{
    background: {Colors.CHIP_BG_WARNING};
    color: {Colors.CHIP_TEXT_WARNING};
    border-color: {Colors.CHIP_BORDER_WARNING};
}}
QLabel#StatusChip[chipState="error"] {{
    background: {Colors.ERROR_BG};
    color: {Colors.ERROR};
    border-color: {Colors.ERROR_BORDER};
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
    font-size: {Metrics.CHIP_FONT_PX}px;
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
    padding: 10px 18px;
    border-radius: {Radius.SM}px;
    font-size: {Metrics.CARD_BODY_FONT_PX}px;
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
    """Return a QLabel styled as the page-level title (30 px, weight 600)."""
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label


def section_title_label(text: str = "") -> QLabel:
    """Return a QLabel styled as a sub-section header on a page.

    The Settings / Runs pages use a dedicated 22 px style instead of
    reusing the page title (30 px / weight 600) for everything from
    "Server Profiles" to "Software Profiles", which made every section
    title compete with the page title for visual attention. This helper
    gives sub-section headers their own ``SectionTitle`` ID (22 px,
    weight 600) and is the canonical way for pages to label cards.
    """
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def section_label(text: str = "") -> QLabel:
    """Return a small neutral label used for taxonomy / category headers
    such as "Saved presets" / "Built-in". Phase 18 visual cleanup: the
    Workflow page previously styled these as 16-18 px muted labels,
    which still out-weighed the actual page title. This helper keeps
    them at the body size so they read as classification, not content.
    """
    label = QLabel(text)
    label.setObjectName("SectionLabel")
    return label


def help_text(text: str = "") -> QLabel:
    """Return a small muted helper line (e.g. {name}={basename} hints)."""
    label = QLabel(text)
    label.setObjectName("HelpText")
    label.setWordWrap(True)
    return label
