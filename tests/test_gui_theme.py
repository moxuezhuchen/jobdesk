import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 not installed")


def test_build_app_stylesheet_contains_core_selectors_and_tokens():
    from jobdesk_app.gui.design.tokens import Colors
    from jobdesk_app.gui.theme import ThemeMetrics, build_app_stylesheet

    css = build_app_stylesheet()

    assert Colors.PRIMARY == "#315f95"
    assert ThemeMetrics.CONTROL_HEIGHT == 56
    assert "QMainWindow" in css
    assert Colors.PRIMARY in css
    assert Colors.ERROR in css
    assert Colors.SUCCESS in css
    assert "QPushButton#PrimaryBtn" in css
    assert "QPushButton#FilesSubmitBtn" in css
    assert "QPushButton#WorkflowDispatchBtn" in css
    assert 'QPushButton[buttonRole="primary_action"]' in css
    assert 'QPushButton[buttonRole="refresh_action"]' in css
    assert 'QPushButton[buttonRole="transfer_action"]' in css
    assert 'QPushButton[buttonRole="danger_action"]' in css
    assert 'QPushButton[buttonRole="settings_action"]' in css
    assert 'QPushButton[buttonRole="test_action"]' in css
    assert 'QPushButton[buttonRole="instant_action"]' in css
    assert "QPushButton#FilesSubmitBtn:disabled" in css
    assert 'QPushButton[feedbackState="pending"]' in css
    assert 'QPushButton[feedbackState="success"]' in css
    assert 'QPushButton[feedbackState="error"]' in css
    assert 'QPushButton[feedbackState="blocked"]' in css
    assert "QHeaderView::section" in css


def test_fixed_icon_buttons_keep_their_geometry_under_app_stylesheet(qt_app):
    from PySide6.QtWidgets import QPushButton, QWidget

    from jobdesk_app.gui.pages.workflow_page._form_builder import build_preview_box
    from jobdesk_app.gui.theme import build_app_stylesheet

    previous_stylesheet = qt_app.styleSheet()
    qt_app.setStyleSheet(build_app_stylesheet())
    buttons = []
    try:
        for object_name, size in (
            ("PreviewToggleBtn", (24, 24)),
            ("SidebarCollapseBtn", (24, 24)),
            ("InlineBannerDismiss", (24, 24)),
            ("WorkflowStepMoveBtn", (36, 32)),
            ("WorkflowStepRemoveBtn", (32, 32)),
        ):
            button = QPushButton("x")
            button.setObjectName(object_name)
            button.setFixedSize(*size)
            button.setStyleSheet("padding: 0; border: 1px solid #ccc;")
            button.show()
            buttons.append(button)
        qt_app.processEvents()

        assert [(button.width(), button.height()) for button in buttons] == [
            (24, 24),
            (24, 24),
            (24, 24),
            (36, 32),
            (32, 32),
        ]

        parent = QWidget()
        preview_box, _preview, _set_expanded, _apply_language = build_preview_box(parent, "en")
        parent.show()
        qt_app.processEvents()
        preview_toggle = preview_box.findChild(QPushButton, "PreviewToggleBtn")
        assert preview_toggle is not None
        assert (preview_toggle.width(), preview_toggle.height()) == (24, 24)
        parent.deleteLater()
    finally:
        for button in buttons:
            button.deleteLater()
        qt_app.setStyleSheet(previous_stylesheet)


def test_page_title_helper_sets_object_name_and_text(qt_app):
    from jobdesk_app.gui.theme import page_title_label

    label = page_title_label("Runs")

    assert label.text() == "Runs"
    assert label.objectName() == "PageTitle"


def test_sidebar_active_state_emits_accessibility_state_change(qt_app):
    from unittest.mock import patch

    from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QAccessibleStateChangeEvent

    from jobdesk_app.gui.design.components import Sidebar

    sidebar = Sidebar([("settings", "Settings"), ("files", "Files")])
    item = sidebar._items[0]
    with patch("PySide6.QtGui.QAccessible.updateAccessibility") as update:
        item.active = True
        item.active = False

    assert update.call_count == 2
    for call in update.call_args_list:
        event = call.args[0]
        assert isinstance(event, QAccessibleStateChangeEvent)
        assert event.changedStates().selected

    interface = QAccessible.queryAccessibleInterface(item)
    assert interface.role() == QAccessible.Role.PageTab
    assert not interface.state().selected
    assert interface.state().selectable
    assert QAccessibleActionInterface.pressAction() in interface.actionNames()

    sidebar_interface = QAccessible.queryAccessibleInterface(sidebar)
    assert sidebar_interface.role() == QAccessible.Role.PageTabList
    assert sidebar_interface.childCount() == 2
    assert [sidebar_interface.child(index).role() for index in range(sidebar_interface.childCount())] == [
        QAccessible.Role.PageTab,
        QAccessible.Role.PageTab,
    ]
    assert interface.parent().role() == QAccessible.Role.PageTabList

    item.active = True
    assert interface.state().selected

    interface.doAction(QAccessibleActionInterface.pressAction())
    assert sidebar._current == 0


def test_sidebar_accessibility_only_emits_real_selection_changes(qt_app):
    from unittest.mock import patch

    from PySide6.QtGui import QAccessible

    from jobdesk_app.gui.design.components import Sidebar

    sidebar = Sidebar([("settings", "Settings"), ("files", "Files"), ("runs", "Runs")])
    with patch("PySide6.QtGui.QAccessible.updateAccessibility") as update:
        sidebar.set_current(0)
        assert update.call_count == 1

        sidebar.set_current(0)
        assert update.call_count == 1

        sidebar.set_current(1)
        assert update.call_count == 3

    assert [QAccessible.queryAccessibleInterface(item).state().selected for item in sidebar._items] == [
        False,
        True,
        False,
    ]


def test_sidebar_accessibility_selection_interface_is_single_select(qt_app):
    from PySide6.QtGui import QAccessible

    from jobdesk_app.gui.design.components import Sidebar

    sidebar = Sidebar([("settings", "Settings"), ("files", "Files")])
    interface = QAccessible.queryAccessibleInterface(sidebar)
    selection = interface.selectionInterface()
    first, second = interface.child(0), interface.child(1)

    assert selection is not None
    assert selection.selectedItemCount() == 0
    assert selection.select(second)
    assert sidebar._current == 1
    assert selection.selectedItemCount() == 1
    assert selection.selectedItems() == [second]
    assert selection.isSelected(second)

    assert selection.select(first)
    assert sidebar._current == 0
    assert not selection.unselect(first)
    assert not selection.clear()
    assert not selection.selectAll()


def test_sidebar_starts_icon_only_and_can_expand_programmatically(qt_app):
    from PySide6.QtTest import QTest

    from jobdesk_app.gui.design.components import Sidebar
    from jobdesk_app.gui.design.tokens import Metrics

    sidebar = Sidebar([("settings", "Settings"), ("files", "Files")])
    item = sidebar._items[0]
    assert sidebar.width() == Metrics.SIDEBAR_COLLAPSED_WIDTH
    assert sidebar.width() == Metrics.SIDEBAR_WIDTH
    assert item._compact
    assert not sidebar._collapse_btn.isVisible()
    assert sidebar._collapse_btn.toolTip() == "Expand sidebar"

    sidebar.toggle_collapse()
    QTest.qWait(Sidebar.ANIM_DURATION_MS + 50)

    assert sidebar.width() == Metrics.SIDEBAR_EXPANDED_WIDTH
    assert not item._compact
    assert sidebar._collapse_btn.toolTip() == "Collapse sidebar"

    sidebar.toggle_collapse()
    QTest.qWait(Sidebar.ANIM_DURATION_MS + 50)

    assert sidebar.width() == Metrics.SIDEBAR_COLLAPSED_WIDTH
    assert item._compact
    assert sidebar._collapse_btn.toolTip() == "Expand sidebar"


def test_button_feedback_styles_are_global_not_page_only():
    from jobdesk_app.gui.theme import build_app_stylesheet

    css = build_app_stylesheet()

    assert "#BtnCard QPushButton" in css
    assert 'QPushButton[buttonRole="primary_action"]' in css
    assert 'QPushButton[buttonRole="danger_action"]' in css


def test_scrollbar_styles_are_thick_enough_for_file_lists():
    from jobdesk_app.gui.theme import ThemeMetrics, build_app_stylesheet

    css = build_app_stylesheet()

    assert ThemeMetrics.SCROLLBAR_THICKNESS == 14
    assert "width: 14px;" in css
    assert "height: 14px;" in css
    assert "border-radius: 7px;" in css


def test_styled_tables_use_compact_reference_density(qt_app):
    from jobdesk_app.gui.design.components import StyledTableWidget
    from jobdesk_app.gui.design.tokens import Metrics

    table = StyledTableWidget()
    table.setColumnCount(2)
    table.setRowCount(1)
    table.resize(480, 180)
    table.show()
    qt_app.processEvents()

    assert table.verticalHeader().defaultSectionSize() == Metrics.TABLE_ROW_HEIGHT
    assert table.rowHeight(0) == Metrics.TABLE_ROW_HEIGHT
    assert table.horizontalHeader().height() == Metrics.TABLE_HEADER_HEIGHT


def test_readable_font_scale_matches_codex_baseline():
    from jobdesk_app.gui.design.tokens import Metrics

    assert Metrics.BASE_FONT_PX == 26
    assert Metrics.CARD_BODY_FONT_PX == 26
    assert Metrics.TABLE_ROW_HEIGHT >= 52
    assert Metrics.TABLE_HEADER_HEIGHT >= Metrics.TABLE_ROW_HEIGHT


def test_navigation_icons_have_distinct_registered_glyphs():
    from jobdesk_app.gui.design.icons import _ICONS

    navigation_icons = ("folder", "workflow", "bar-chart", "settings")
    assert all(name in _ICONS for name in navigation_icons)
    assert len({_ICONS[name] for name in navigation_icons}) == len(navigation_icons)


def test_button_role_styles_are_winscp_neutral_not_type_colored():
    from jobdesk_app.gui.design.tokens import Colors
    from jobdesk_app.gui.theme import build_app_stylesheet

    css = build_app_stylesheet()
    role_section = css[
        css.index('QPushButton[buttonRole="primary_action"]') : css.index('QPushButton[feedbackState="pending"]')
    ]

    for role in [
        "primary_action",
        "refresh_action",
        "transfer_action",
        "danger_action",
        "settings_action",
        "test_action",
        "instant_action",
    ]:
        assert f'QPushButton[buttonRole="{role}"]' in role_section

    assert Colors.PRIMARY not in role_section
    assert Colors.SUCCESS not in role_section
    assert Colors.SUCCESS_BG not in role_section
    assert Colors.ERROR not in role_section
    assert Colors.ERROR_BG not in role_section
    assert Colors.WARNING not in role_section


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
