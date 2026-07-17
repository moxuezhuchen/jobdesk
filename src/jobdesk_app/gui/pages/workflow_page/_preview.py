"""Preview rendering logic for the workflow page.

This module contains functions for rendering workflow previews, including
YAML preview, flow diagram cards, and validation feedback.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ....core.workflow_spec import WorkflowSpec
from ...design.tokens import Colors, Metrics, Radius
from ...i18n import tr
from ...nodegraph.model import NodeKind
from .workflow_page_helpers import flow_step_detail


def refresh_generated_yaml(
    preview_widget: QWidget,
    build_workflow_yaml_fn: Any,
) -> None:
    """Refresh the YAML preview by calling build_workflow_yaml.

    Args:
        preview_widget: Widget with setPlainText method
        build_workflow_yaml_fn: Callable that returns the workflow YAML string
    """
    try:
        text = build_workflow_yaml_fn()
        WorkflowSpec.from_yaml(text)
        preview_widget.setPlainText(text)
    except Exception as exc:
        preview_widget.setPlainText(f"# Cannot generate workflow YAML\n# {exc}")


def validate_workflow(
    build_workflow_yaml_fn: Any,
    on_error: Any,
    on_success: Any,
) -> None:
    """Validate the current workflow YAML.

    Args:
        build_workflow_yaml_fn: Callable that returns the workflow YAML string
        on_error: Callback(error_message: str)
        on_success: Callback()
    """
    try:
        WorkflowSpec.from_yaml(build_workflow_yaml_fn())
        on_success()
    except Exception as exc:
        on_error(str(exc))


def build_step_card(
    flow_body: QWidget,
    node: Any,
    index: int,
    total: int,
    language: str,
    selected_node_id: str | None,
    on_select: Any,
    on_move: Any,
    on_delete: Any,
) -> QFrame:
    """Build a step card widget for the flow diagram.

    Args:
        flow_body: Parent widget for the card
        node: Node object with id, title, kind, params
        index: Position of this step in the sequence
        total: Total number of steps
        language: UI language code
        selected_node_id: Currently selected node ID
        on_select: Callback(node_id: str)
        on_move: Callback(node_id: str, delta: int)
        on_delete: Callback(node_id: str)

    Returns:
        The constructed card frame widget
    """
    card = QFrame(flow_body)
    selected = node.id == selected_node_id
    accent = Colors.SUCCESS if node.kind is NodeKind.CONF_GEN else Colors.PRIMARY

    card.setStyleSheet(
        f"QFrame {{ background: {Colors.CARD_BG}; "
        f"border: 1px solid {Colors.BORDER if not selected else Colors.PRIMARY}; "
        f"border-left: 4px solid {accent}; "
        f"border-radius: {Radius.MD}px; }}"
    )

    row = QHBoxLayout(card)
    row.setContentsMargins(14, 10, 10, 10)

    content = QVBoxLayout()
    content.setSpacing(2)

    select = QPushButton(f"{index + 1}. {node.title}", card)
    select.setFlat(True)
    select.setStyleSheet(
        f"QPushButton {{ text-align: left; color: {Colors.TEXT}; "
        f"font-size: {Metrics.CARD_TITLE_FONT_PX}px; font-weight: 600; "
        f"border: none; padding: 0; background: transparent; }}"
        f"QPushButton:hover {{ color: {Colors.PRIMARY}; }}"
    )
    select.clicked.connect(lambda _checked=False, nid=node.id: on_select(nid))
    content.addWidget(select)

    detail = QLabel(flow_step_detail(node), card)
    detail.setStyleSheet(
        f"color: {Colors.TEXT_MUTED}; font-size: {Metrics.CARD_BODY_FONT_PX}px; border: none; background: transparent;"
    )
    content.addWidget(detail)

    row.addLayout(content, 1)

    # Move up button
    up = QPushButton("\u2191", card)
    up.setEnabled(index > 0)
    up.setFixedSize(36, 32)
    up.setStyleSheet(
        f"padding: 0; background: {Colors.BG_SURFACE}; border: 1px solid {Colors.BORDER}; border-radius: {Radius.SM}px;"
    )
    up.setToolTip(tr("Move up", language))
    up.clicked.connect(lambda _checked=False, nid=node.id: on_move(nid, -1))
    row.addWidget(up)

    # Move down button
    down = QPushButton("\u2193", card)
    down.setEnabled(index < total - 1)
    down.setFixedSize(36, 32)
    down.setStyleSheet(
        f"padding: 0; background: {Colors.BG_SURFACE}; border: 1px solid {Colors.BORDER}; border-radius: {Radius.SM}px;"
    )
    down.setToolTip(tr("Move down", language))
    down.clicked.connect(lambda _checked=False, nid=node.id: on_move(nid, 1))
    row.addWidget(down)

    # Delete button
    remove = QPushButton("\u00d7", card)
    remove.setFixedSize(32, 32)
    remove.setStyleSheet(
        f"QPushButton {{ color: {Colors.ERROR}; padding: 0; "
        f"border: 1px solid {Colors.ERROR_BORDER}; border-radius: {Radius.SM}px; "
        f"background: {Colors.ERROR_BG}; }}"
        f"QPushButton:hover {{ background: {Colors.ERROR}; color: white; }}"
    )
    remove.clicked.connect(lambda _checked=False, nid=node.id: on_delete(nid))
    row.addWidget(remove)

    return card


def refresh_flow_diagram(
    flow_layout: QVBoxLayout,
    flow_body: QWidget,
    ordered_nodes: list[Any],
    selected_node_id: str | None,
    language: str,
    on_select: Any,
    on_move: Any,
    on_delete: Any,
) -> None:
    """Refresh the flow diagram by rebuilding all step cards.

    Args:
        flow_layout: The VBoxLayout containing the diagram
        flow_body: Parent widget for cards
        ordered_nodes: List of step nodes in topological order
        selected_node_id: Currently selected node ID
        language: UI language code
        on_select: Callback(node_id: str)
        on_move: Callback(node_id: str, delta: int)
        on_delete: Callback(node_id: str)
    """
    # Clear existing widgets
    while flow_layout.count():
        item = flow_layout.takeAt(0)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()

    # Input structure label
    start = QLabel(tr("Input structure", language), flow_body)
    start.setAlignment(Qt.AlignmentFlag.AlignCenter)
    start.setFixedHeight(48)
    start.setStyleSheet(
        f"font-weight: 600; color: {Colors.PRIMARY}; "
        f"background: {Colors.INFO_BG}; border: 1px solid {Colors.INFO_BORDER}; "
        f"border-radius: {Radius.MD}px;"
    )
    flow_layout.addWidget(start)

    # Step cards
    if not ordered_nodes:
        hint = QLabel(tr("Choose a step on the left, then add it to the workflow.", language), flow_body)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setFixedHeight(48)
        hint.setStyleSheet(f"color: {Colors.TEXT_MUTED}; border: none; background: transparent;")
        flow_layout.addWidget(hint)
    else:
        for index, node in enumerate(ordered_nodes):
            # Arrow before card
            arrow = QLabel("\u2193", flow_body)
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setFixedHeight(28)
            arrow.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_MUTED};")
            flow_layout.addWidget(arrow)

            # Step card
            card = build_step_card(
                flow_body,
                node,
                index,
                len(ordered_nodes),
                language,
                selected_node_id,
                on_select,
                on_move,
                on_delete,
            )
            flow_layout.addWidget(card)

        # Arrow after last card
        arrow = QLabel("\u2193", flow_body)
        arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
        arrow.setFixedHeight(28)
        arrow.setStyleSheet(f"font-weight: 600; color: {Colors.TEXT_MUTED};")
        flow_layout.addWidget(arrow)

    # Output label
    output = QLabel(tr("Workflow output", language), flow_body)
    output.setAlignment(Qt.AlignmentFlag.AlignCenter)
    output.setFixedHeight(48)
    output.setStyleSheet(
        f"font-weight: 600; color: {Colors.SUCCESS}; "
        f"background: {Colors.SUCCESS_BG}; border: 1px solid {Colors.SUCCESS_BORDER}; "
        f"border-radius: {Radius.LG}px;"
    )
    flow_layout.addWidget(output)

    # Add stretch to push everything to the top
    flow_layout.addStretch(1)


def build_flow_body_and_layout(
    parent: QWidget,
) -> tuple[QWidget, QVBoxLayout]:
    """Create the flow body widget and its layout.

    Returns:
        Tuple of (flow_body widget, flow_layout)
    """
    flow_body = QWidget(parent)
    flow_layout = QVBoxLayout(flow_body)
    flow_layout.setContentsMargins(20, 16, 20, 16)
    flow_layout.setSpacing(6)
    return flow_body, flow_layout
