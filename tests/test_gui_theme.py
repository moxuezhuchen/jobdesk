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


def test_button_feedback_styles_are_global_not_page_only():
    from jobdesk_app.gui.theme import build_app_stylesheet

    css = build_app_stylesheet()

    assert "#BtnCard QPushButton" in css
    assert 'QPushButton[buttonRole="primary_action"]' in css
    assert 'QPushButton[buttonRole="danger_action"]' in css


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
