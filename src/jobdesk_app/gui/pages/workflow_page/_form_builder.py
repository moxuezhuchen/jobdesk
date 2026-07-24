"""Form building logic for the workflow page.

This module contains methods for constructing the UI widgets used in
WorkflowPage, including headers, tabs, panels, and footers.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...button_feedback import ButtonRole, apply_button_role
from ...design.components import StatusChip
from ...design.tokens import Colors, Metrics, Radius
from ...i18n import tr
from ...theme import help_text, section_title_label


def build_header(
    page: QWidget,
    language: str,
    on_new: Callable[[], None],
    on_validate: Callable[[], None],
) -> tuple[QWidget, QComboBox, QPushButton, QPushButton, QLabel]:
    """Build the workflow page header with preset combo and action buttons.

    Returns:
        Tuple of (header_widget, preset_combo, btn_new, btn_validate, dirty_label)
    """
    panel = QFrame(page)
    panel.setObjectName("WorkflowHeader")
    # Phase 19: streamlined header styling
    panel.setStyleSheet(
        f"#WorkflowHeader {{ background: {Colors.CARD_BG}; "
        f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }}"
    )
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)

    title = QLabel(tr("Workflow", language), panel)
    title.setStyleSheet(f"color: {Colors.TEXT}; font-size: {Metrics.PAGE_TITLE_FONT_PX}px; font-weight: 600;")
    layout.addWidget(title)

    row = QHBoxLayout()
    row.setSpacing(8)
    preset_combo = QComboBox(panel)
    preset_combo.setObjectName("WorkflowPresetCombo")
    preset_combo.setPlaceholderText(tr("No saved workflows", language))
    row.addWidget(preset_combo, 1)

    btn_new = QPushButton(tr("New", language), panel)
    btn_new.clicked.connect(on_new)
    row.addWidget(btn_new)

    btn_validate = QPushButton(tr("Validate", language), panel)
    btn_validate.clicked.connect(on_validate)
    row.addWidget(btn_validate)

    layout.addLayout(row)

    dirty_label = QLabel("", panel)
    dirty_label.setStyleSheet(f"color: {Colors.WARNING}; font-style: italic; font-size: {Metrics.CARD_BODY_FONT_PX}px;")
    layout.addWidget(dirty_label)

    return panel, preset_combo, btn_new, btn_validate, dirty_label


def build_workspace(
    page: QWidget,
    language: str,
    build_left_panel_fn: Callable[[], QWidget],
    build_graph_panel_fn: Callable[[], QWidget],
) -> QSplitter:
    """Build the main workspace splitter with left panel and graph panel."""
    splitter = QSplitter(page)
    splitter.setObjectName("WorkflowAuthoringSplitter")
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(build_left_panel_fn())
    splitter.addWidget(build_graph_panel_fn())
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([420, 900])
    return splitter


def build_left_panel(
    page: QWidget,
    language: str,
    build_step_tab_fn: Callable[[], QWidget],
    build_global_tab_fn: Callable[[], QWidget],
) -> QWidget:
    """Build the left settings panel with tabs."""
    panel = QFrame(page)
    panel.setObjectName("workflowSettingsPanel")
    panel.setMinimumWidth(340)
    panel.setMaximumWidth(560)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(0, 0, 8, 0)
    layout.setSpacing(8)

    settings_tabs = QTabWidget(panel)
    settings_tabs.addTab(build_step_tab_fn(), tr("Step YAML", language))
    settings_tabs.addTab(build_global_tab_fn(), tr("Global YAML", language))
    layout.addWidget(settings_tabs, 1)

    return panel


def build_step_tab(
    page: QWidget,
    language: str,
    step_preset_combo: QComboBox,
    new_step_button: QPushButton,
    step_yaml_editor: QPlainTextEdit,
    step_error_label: QLabel,
    selected_step_label: QLabel,
    inputs_label: QLabel,
    apply_step_preset_btn: QPushButton,
    save_step_preset_btn: QPushButton,
) -> QWidget:
    """Build the step YAML editing tab.

    Args:
        page: Parent widget
        language: UI language code
        step_preset_combo: Pre-created combo box for step presets
        new_step_button: Pre-created new step button with menu
        step_yaml_editor: Pre-created YAML editor widget
        step_error_label: Pre-created error label
        selected_step_label: Pre-created label for selected step
        inputs_label: Pre-created label for step inputs
        apply_step_preset_btn: Pre-created apply preset button
        save_step_preset_btn: Pre-created save preset button

    Returns:
        The constructed tab widget
    """
    tab = QWidget(page)
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(0, 8, 0, 0)

    selected_step_label.setWordWrap(True)
    selected_step_label.setStyleSheet(
        f"font-weight: 600; color: {Colors.TEXT}; font-size: {Metrics.CARD_TITLE_FONT_PX}px;"
    )
    layout.addWidget(selected_step_label)

    inputs_label.setWordWrap(True)
    inputs_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: {Metrics.CARD_BODY_FONT_PX}px;")
    layout.addWidget(inputs_label)

    preset_row = QHBoxLayout()
    preset_row.setSpacing(8)
    step_preset_combo.currentIndexChanged.connect(lambda idx: None)  # placeholder
    preset_row.addWidget(step_preset_combo, 1)
    preset_row.addWidget(new_step_button)
    preset_row.addWidget(apply_step_preset_btn)
    layout.addLayout(preset_row)

    step_yaml_editor.setObjectName("WorkflowStepYamlEditor")
    step_yaml_editor.setPlaceholderText("name: opt\ntype: calc\nparams:\n  iprog: orca\n  itask: opt")
    step_yaml_editor.setStyleSheet(
        f"font-family: Consolas, Menlo, monospace; font-size: {Metrics.CARD_BODY_FONT_PX}px;"
    )
    layout.addWidget(step_yaml_editor, 1)

    step_error_label.setWordWrap(True)
    step_error_label.setStyleSheet(f"color: {Colors.ERROR}; font-size: {Metrics.CARD_BODY_FONT_PX}px;")
    layout.addWidget(step_error_label)

    layout.addWidget(save_step_preset_btn)

    return tab


def build_step_tab_widgets(
    page: QWidget,
    language: str,
) -> tuple[
    QLabel,
    QLabel,
    QComboBox,
    QPushButton,
    QPushButton,
    QPlainTextEdit,
    QLabel,
    QPushButton,
]:
    """Create the widgets needed for the step tab.

    Returns:
        Tuple of (selected_step_label, inputs_label, step_preset_combo,
                  new_step_button, apply_step_preset_btn, step_yaml_editor,
                  step_error_label, save_step_preset_btn)
    """
    selected_step_label = QLabel(tr("Select a workflow step on the graph.", language), page)
    inputs_label = QLabel("", page)

    step_preset_combo = QComboBox(page)

    new_step_button = QPushButton(tr("New step", language), page)
    _new_step_menu = QMenu(new_step_button)
    _new_step_menu.addAction(
        tr("Calculation step (calc)", language),
        lambda: None,  # Will be connected by caller
    )
    _new_step_menu.addAction(
        tr("Conformer generation step (confgen)", language),
        lambda: None,  # Will be connected by caller
    )
    new_step_button.setMenu(_new_step_menu)
    new_step_button.setToolTip(tr("Choose the type for the new step.", language))

    apply_step_preset_btn = QPushButton(tr("Load step", language), page)

    step_yaml_editor = QPlainTextEdit(page)
    step_error_label = QLabel("", page)

    save_step_preset_btn = QPushButton(tr("Save step", language), page)

    return (
        selected_step_label,
        inputs_label,
        step_preset_combo,
        new_step_button,
        apply_step_preset_btn,
        step_yaml_editor,
        step_error_label,
        save_step_preset_btn,
    )


def build_global_tab(
    page: QWidget,
    language: str,
    global_yaml_editor: QPlainTextEdit,
    global_error_label: QLabel,
    on_apply: Callable[[], None],
) -> QWidget:
    """Build the global YAML settings tab."""
    tab = QWidget(page)
    layout = QVBoxLayout(tab)
    layout.setContentsMargins(0, 8, 0, 0)
    layout.setSpacing(8)

    hint = help_text(tr("Workflow-wide resources and molecular settings.", language))
    hint.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.CARD_BODY_FONT_PX}px;")
    layout.addWidget(hint)

    global_yaml_editor.setObjectName("WorkflowGlobalYamlEditor")
    global_yaml_editor.setStyleSheet(
        f"font-family: Consolas, Menlo, monospace; font-size: {Metrics.CARD_BODY_FONT_PX}px;"
    )
    layout.addWidget(global_yaml_editor, 1)

    global_error_label.setWordWrap(True)
    global_error_label.setStyleSheet(f"color: {Colors.ERROR}; font-size: {Metrics.CARD_BODY_FONT_PX}px;")
    layout.addWidget(global_error_label)

    button = apply_button_role(
        QPushButton(tr("Apply global settings", language), tab),
        ButtonRole.PRIMARY_ACTION,
    )
    button.clicked.connect(on_apply)
    layout.addWidget(button)

    return tab


def build_graph_panel(
    page: QWidget,
    language: str,
    flow_body: QWidget,
    flow_layout: QVBoxLayout,
    on_add_step: Callable[[], None],
    on_save: Callable[[], None],
) -> tuple[QWidget, QPushButton, QScrollArea, QPushButton]:
    """Build the graph visualization panel.

    Returns:
        Tuple of (panel, add_step_button, flow_scroll, save_workflow_button)
    """
    panel = QFrame(page)
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(8, 0, 0, 0)
    layout.setSpacing(8)

    title = section_title_label(tr("Workflow flow", language))
    layout.addWidget(title)

    toolbar = QHBoxLayout()
    toolbar.setSpacing(8)

    add_step_button = QPushButton(tr("Add current step", language), panel)
    add_step_button.setToolTip(tr("Add the step currently shown on the left.", language))
    add_step_button.clicked.connect(on_add_step)
    toolbar.addWidget(add_step_button)
    toolbar.addStretch(1)
    layout.addLayout(toolbar)

    flow_scroll = QScrollArea(panel)
    flow_scroll.setWidgetResizable(True)
    flow_scroll.setFrameShape(QFrame.Shape.StyledPanel)
    flow_scroll.setWidget(flow_body)
    layout.addWidget(flow_scroll, 1)

    save_workflow_button = apply_button_role(
        QPushButton(tr("Save workflow", language), panel),
        ButtonRole.PRIMARY_ACTION,
    )
    save_workflow_button.setObjectName("SaveWorkflowButton")
    save_workflow_button.clicked.connect(on_save)
    layout.addWidget(save_workflow_button)

    return panel, add_step_button, flow_scroll, save_workflow_button


def build_preview_box(
    page: QWidget,
    language: str,
) -> tuple[QWidget, QPlainTextEdit, Callable[[bool], None], Callable[[str], None]]:
    """Build the YAML preview box (collapsible).

    Phase 19: the preview is now collapsible via a checkable header,
    allowing users to hide it when not needed to reduce visual competition.

    Returns:
        Tuple of (box, full_yaml_preview, set_expanded_fn, apply_language_fn)
    """
    box = QFrame(page)
    box.setObjectName("WorkflowPreviewBox")
    box.setStyleSheet(
        f"#WorkflowPreviewBox {{ background: {Colors.CARD_BG}; "
        f"border: 1px solid {Colors.BORDER}; border-radius: {Radius.MD}px; }}"
    )
    layout = QVBoxLayout(box)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(8)

    # Collapsible header with toggle
    header = QHBoxLayout()
    header.setSpacing(8)
    title = QLabel(tr("YAML Preview", language), box)
    title.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT}; font-size: {Metrics.SECTION_TITLE_FONT_PX}px;")
    header.addWidget(title)
    header.addStretch(1)

    full_yaml_preview = QPlainTextEdit(box)
    full_yaml_preview.setObjectName("WorkflowYamlPreview")
    full_yaml_preview.setReadOnly(True)
    full_yaml_preview.setMaximumBlockCount(2000)
    full_yaml_preview.setStyleSheet(
        f"font-family: Consolas, Menlo, monospace; font-size: {Metrics.CARD_BODY_FONT_PX}px;"
        f" border: 1px solid {Colors.BORDER_SUBTLE}; border-radius: {Radius.SM}px; padding: 8px;"
    )

    # Toggle button for preview visibility
    # Initial state: collapsed (hidden)
    is_expanded = [False]
    current_language = [language]
    toggle_btn = QPushButton("\u25b6", box)  # ▶
    toggle_btn.setObjectName("PreviewToggleBtn")
    toggle_btn.setFixedSize(24, 24)
    toggle_btn.setStyleSheet(
        f"background: transparent; border: 1px solid {Colors.BORDER}; border-radius: {Radius.SM}px; "
        f"color: {Colors.TEXT_SECONDARY}; font-size: {Metrics.CHIP_FONT_PX}px; "
        "min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; padding: 0;"
    )
    toggle_btn.setToolTip(tr("Show YAML preview", language))

    def set_expanded(expanded: bool):
        """Set the preview expanded state and update UI accordingly."""
        is_expanded[0] = expanded
        full_yaml_preview.setVisible(expanded)
        toggle_btn.setText("\u25b6" if not expanded else "\u25bc")  # ▶ or ▼
        tooltip_key = "Hide YAML preview" if expanded else "Show YAML preview"
        toggle_btn.setToolTip(tr(tooltip_key, current_language[0]))

    def apply_language(new_language: str) -> None:
        current_language[0] = new_language
        title.setText(tr("YAML Preview", new_language))
        tooltip_key = "Hide YAML preview" if is_expanded[0] else "Show YAML preview"
        toggle_btn.setToolTip(tr(tooltip_key, new_language))

    def toggle_preview():
        set_expanded(not is_expanded[0])

    toggle_btn.clicked.connect(toggle_preview)
    header.addWidget(toggle_btn)
    layout.addLayout(header)
    layout.addWidget(full_yaml_preview)
    set_expanded(False)

    return box, full_yaml_preview, set_expanded, apply_language


def build_footer(
    page: QWidget,
    language: str,
    on_use_for_submit: Callable[[], None],
) -> tuple[QWidget, StatusChip, QPushButton]:
    """Build the footer with server status and submit button.

    Returns:
        Tuple of (footer_widget, server_pill, btn_dispatch)
    """
    panel = QFrame(page)
    layout = QHBoxLayout(panel)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)

    server_pill = StatusChip(tr("No server", language), state="neutral")
    layout.addWidget(server_pill)

    layout.addStretch(1)

    btn_dispatch = apply_button_role(
        QPushButton(tr("Use this workflow for submit", language), panel),
        ButtonRole.PRIMARY_ACTION,
    )
    btn_dispatch.setObjectName("WorkflowDispatchBtn")
    btn_dispatch.clicked.connect(on_use_for_submit)
    layout.addWidget(btn_dispatch)

    return panel, server_pill, btn_dispatch
