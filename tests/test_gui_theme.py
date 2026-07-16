import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 not installed")


def test_build_app_stylesheet_contains_core_selectors_and_tokens():
    from jobdesk_app.gui.design.tokens import Colors
    from jobdesk_app.gui.theme import ThemeMetrics, build_app_stylesheet

    css = build_app_stylesheet()

    assert Colors.PRIMARY == "#315f95"
    assert ThemeMetrics.CONTROL_HEIGHT == 38
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
    assert 'QPushButton[feedbackState="pending"]' in css
    assert 'QPushButton[feedbackState="success"]' in css
    assert 'QPushButton[feedbackState="error"]' in css
    assert 'QPushButton[feedbackState="blocked"]' in css
    assert "QHeaderView::section" in css


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
    assert [
        sidebar_interface.child(index).role()
        for index in range(sidebar_interface.childCount())
    ] == [QAccessible.Role.PageTab, QAccessible.Role.PageTab]
    assert interface.parent().role() == QAccessible.Role.PageTabList

    item.active = True
    assert interface.state().selected

    interface.doAction(QAccessibleActionInterface.pressAction())
    assert sidebar._current == 0


def test_sidebar_accessibility_only_emits_real_selection_changes(qt_app):
    from unittest.mock import patch

    from PySide6.QtGui import QAccessible

    from jobdesk_app.gui.design.components import Sidebar

    sidebar = Sidebar(
        [("settings", "Settings"), ("files", "Files"), ("runs", "Runs")]
    )
    with patch("PySide6.QtGui.QAccessible.updateAccessibility") as update:
        sidebar.set_current(0)
        assert update.call_count == 1

        sidebar.set_current(0)
        assert update.call_count == 1

        sidebar.set_current(1)
        assert update.call_count == 3

    assert [
        QAccessible.queryAccessibleInterface(item).state().selected
        for item in sidebar._items
    ] == [False, True, False]


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


def test_button_role_styles_are_winscp_neutral_not_type_colored():
    from jobdesk_app.gui.design.tokens import Colors
    from jobdesk_app.gui.theme import build_app_stylesheet

    css = build_app_stylesheet()
    role_section = css[
        css.index('QPushButton[buttonRole="primary_action"]'):
        css.index('QPushButton[feedbackState="pending"]')
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
