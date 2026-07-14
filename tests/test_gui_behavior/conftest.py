"""Shared fixtures and helpers for GUI behavior tests.

This module is auto-discovered by pytest as a conftest in the
``test_gui_behavior/`` subdirectory.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")


@pytest.fixture(autouse=True)
def _isolated_gui_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


@pytest.fixture
def app_state(tmp_path):
    """Minimal app state for page construction."""
    state = MagicMock()
    state.current_project_root = tmp_path
    return state


@pytest.fixture
def runs_page(qtbot, app_state):
    from jobdesk_app.gui.pages.runs_results_page import RunsResultsPage

    with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
        mock_svc.return_value.list_runs.return_value = []
        page = RunsResultsPage(app_state, log_cb=lambda m: None, status_cb=lambda m: None)
    qtbot.addWidget(page)
    yield page
    page.shutdown()


@pytest.fixture
def file_page(qtbot, app_state, tmp_path):
    from jobdesk_app.gui.pages.file_transfer_page import FileTransferPage
    from jobdesk_app.services.gui_settings import GuiSettings, GuiSettingsStore

    store = GuiSettingsStore(tmp_path / "gui_settings.yaml")
    store.save(GuiSettings(auto_connect=False))
    with patch("jobdesk_app.gui.pages.file_transfer_page.GuiSettingsStore", return_value=store):
        page = FileTransferPage(
            app_state,
            log_cb=lambda m: None,
            status_cb=lambda m: None,
            error_cb=lambda t, m: None,
        )
    qtbot.addWidget(page)
    yield page
    page.shutdown()


class _FakeSignal:
    def __init__(self):
        self._callbacks: list[object] = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeWorker:
    def __init__(self):
        self.progress = _FakeSignal()
        self.result = _FakeSignal()
        self.error = _FakeSignal()
        self.log = _FakeSignal()
        self.finished = _FakeSignal()
        self.start = MagicMock()
        self.stop_safely = MagicMock()
        self.deleteLater = MagicMock()
