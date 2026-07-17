"""Tests for the WorkflowTourDialog (Phase 1.1)."""

from __future__ import annotations

import os

# Ensure an offscreen Qt platform before any Qt import (Windows CI friendly).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QStackedWidget

from jobdesk_app.gui.dialogs.workflow_tour_dialog import WorkflowTourDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _find_button(dialog: WorkflowTourDialog, obj_name: str) -> QPushButton:
    btn = dialog.findChild(QPushButton, obj_name)
    assert btn is not None, f"button {obj_name!r} not found"
    return btn


def _stacked(dialog: WorkflowTourDialog) -> QStackedWidget:
    stack = dialog.findChild(QStackedWidget)
    assert stack is not None, "QStackedWidget not found"
    return stack


def test_dialog_constructs_with_default_slide_1(qapp):
    dialog = WorkflowTourDialog(language="en")
    stack = _stacked(dialog)
    assert stack.count() == 6
    assert stack.currentIndex() == 0
    assert dialog.windowTitle() == "Workflow tour"
    dialog.close()
    dialog.deleteLater()


def test_next_advances_slide(qapp):
    dialog = WorkflowTourDialog(language="en")
    stack = _stacked(dialog)
    next_btn = _find_button(dialog, "workflowTourNextButton")

    next_btn.click()
    assert stack.currentIndex() == 1

    next_btn.click()
    assert stack.currentIndex() == 2

    dialog.close()
    dialog.deleteLater()


def test_back_disabled_on_slide_1(qapp):
    dialog = WorkflowTourDialog(language="en")
    back_btn = _find_button(dialog, "workflowTourBackButton")
    assert not back_btn.isEnabled()
    dialog.close()
    dialog.deleteLater()


def test_back_regresses(qapp):
    dialog = WorkflowTourDialog(language="en")
    stack = _stacked(dialog)
    back_btn = _find_button(dialog, "workflowTourBackButton")
    next_btn = _find_button(dialog, "workflowTourNextButton")

    next_btn.click()
    next_btn.click()
    assert stack.currentIndex() == 2
    assert back_btn.isEnabled()

    back_btn.click()
    assert stack.currentIndex() == 1

    dialog.close()
    dialog.deleteLater()


def test_close_button_replaces_next_on_last_slide(qapp):
    dialog = WorkflowTourDialog(language="en")
    stack = _stacked(dialog)
    next_btn = _find_button(dialog, "workflowTourNextButton")
    close_btn = _find_button(dialog, "workflowTourCloseButton")

    # Advance to the last slide (index 5). Use isHidden() instead of
    # isVisible() because the dialog has not been shown yet, so
    # isVisible() returns False for every child.
    for _ in range(5):
        if next_btn.isHidden():
            break
        next_btn.click()
    assert stack.currentIndex() == 5
    assert next_btn.isHidden()
    assert not close_btn.isHidden()
    assert close_btn.text() == "Close"

    dialog.close()
    dialog.deleteLater()


def test_apply_language_retranslates(qapp):
    from PySide6.QtWidgets import QLabel

    dialog = WorkflowTourDialog(language="en")
    stack = _stacked(dialog)
    first_page = stack.widget(0)

    title_en = first_page.findChild(QLabel, "workflowTourTitle1")
    assert title_en is not None
    assert title_en.text() == "Set up a server"

    dialog.apply_language("zh")
    title_zh = first_page.findChild(QLabel, "workflowTourTitle1")
    assert title_zh is not None
    assert any("\u4e00" <= ch <= "\u9fff" for ch in title_zh.text()), (
        f"expected Chinese characters in: {title_zh.text()!r}"
    )
    assert dialog.windowTitle() == "\u5de5\u4f5c\u6d41\u5bfc\u89c8"
    dialog.close()
    dialog.deleteLater()


def test_esc_closes_dialog(qapp, qtbot):
    from PySide6.QtWidgets import QDialog

    dialog = WorkflowTourDialog(language="en")
    dialog.show()
    qtbot.waitUntil(lambda: dialog.isVisible(), timeout=500)

    with qtbot.waitSignal(dialog.finished, timeout=1000) as sig:
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
    # QDialog.reject() emits finished with QDialog.Rejected (=0).
    assert sig.args[0] == int(QDialog.DialogCode.Rejected.value)
