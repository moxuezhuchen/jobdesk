"""Smoke tests for MainWindow wiring (Phase 1.1)."""
from __future__ import annotations

import os

# Ensure an offscreen Qt platform before any Qt import (Windows CI friendly).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jobdesk_app.gui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _patch_dialog(monkeypatch, captured):
    """Replace WorkflowTourDialog with a fake that records the call args.

    The MainWindow imports ``WorkflowTourDialog`` lazily from
    ``jobdesk_app.gui.dialogs.workflow_tour_dialog`` inside
    ``_show_workflow_tour``, so we patch the symbol on that submodule.
    """
    from jobdesk_app.gui.dialogs import workflow_tour_dialog

    class FakeDialog:
        def __init__(self, parent=None, language="en"):
            captured["parent"] = parent
            captured["language"] = language
            captured["constructed"] = True

        def exec(self):
            captured["exec_called"] = True
            return None

    monkeypatch.setattr(workflow_tour_dialog, "WorkflowTourDialog", FakeDialog)


def test_show_workflow_tour_opens_dialog(qapp, monkeypatch):
    """The MainWindow must have a ``_show_workflow_tour`` method that
    instantiates ``WorkflowTourDialog`` with the window as parent and
    the current language, then calls ``exec()``.
    """
    captured: dict = {}
    _patch_dialog(monkeypatch, captured)

    window = MainWindow()
    try:
        # The method must exist (Phase 1.1 contract).
        assert hasattr(window, "_show_workflow_tour")
        assert callable(window._show_workflow_tour)

        window._show_workflow_tour()

        assert captured.get("constructed") is True
        assert captured["parent"] is window
        assert captured["language"] == window.language
        assert captured.get("exec_called") is True
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()


def test_submit_editor_tour_signal_connected_to_main_window(qapp, monkeypatch):
    """The MainWindow must connect ``self.submit_page.editor.tour_requested``
    to ``self._show_workflow_tour`` during __init__.

    Emitting the editor's signal should drive ``_show_workflow_tour``,
    which (per the test above) opens a dialog.
    """
    captured: dict = {}
    _patch_dialog(monkeypatch, captured)

    window = MainWindow()
    try:
        # submit_page.editor must exist and expose tour_requested.
        assert hasattr(window.submit_page, "editor")
        assert hasattr(window.submit_page.editor, "tour_requested")

        # Emit the signal; this is exactly what happens when the user
        # clicks the "Read 60-second tour" button on the onboarding card.
        window.submit_page.editor.tour_requested.emit()

        assert captured.get("constructed") is True
        assert captured["parent"] is window
        assert captured.get("exec_called") is True
    finally:
        try:
            window.shutdown()
        except Exception:
            pass
        window.close()
        window.deleteLater()
