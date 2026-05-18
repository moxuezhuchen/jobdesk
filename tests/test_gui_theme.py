import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 not installed")


def test_build_app_stylesheet_contains_core_selectors_and_tokens():
    from jobdesk_app.gui.design.tokens import Colors
    from jobdesk_app.gui.theme import ThemeMetrics, build_app_stylesheet

    css = build_app_stylesheet()

    assert Colors.PRIMARY == "#2563eb"
    assert ThemeMetrics.CONTROL_HEIGHT == 34
    assert "QMainWindow" in css
    assert Colors.PRIMARY in css
    assert "QPushButton#PrimaryBtn" in css
    assert "QHeaderView::section" in css


def test_page_title_helper_sets_object_name_and_text(qt_app):
    from jobdesk_app.gui.theme import page_title_label

    label = page_title_label("Runs")

    assert label.text() == "Runs"
    assert label.objectName() == "PageTitle"


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
