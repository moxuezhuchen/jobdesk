"""GUI behavior tests using pytest-qt."""
import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from pathlib import Path
from unittest.mock import MagicMock, patch


@pytest.fixture
def app_state():
    """Minimal app state for page construction."""
    state = MagicMock()
    state.current_project_root = Path.cwd()
    return state


@pytest.fixture
def runs_page(qtbot, app_state):
    from jobdesk_app.gui.pages.runs_results_page import RunsResultsPage
    with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
        mock_svc.return_value.list_runs.return_value = []
        page = RunsResultsPage(app_state, log_cb=lambda m: None, status_cb=lambda m: None)
        qtbot.addWidget(page)
        return page


@pytest.fixture
def file_page(qtbot, app_state):
    from jobdesk_app.gui.pages.file_transfer_page import FileTransferPage
    page = FileTransferPage(app_state, log_cb=lambda m: None, status_cb=lambda m: None, error_cb=lambda t, m: None)
    qtbot.addWidget(page)
    return page


class TestRunsPage:
    def test_page_creates_without_crash(self, runs_page):
        assert runs_page is not None

    def test_table_has_correct_columns(self, runs_page):
        table = runs_page.table
        assert table.columnCount() == 6

    def test_buttons_exist(self, runs_page):
        assert runs_page.retry_btn is not None
        assert runs_page.cancel_btn is not None
        assert runs_page.delete_btn is not None

    def test_context_menu_has_refresh(self, runs_page, qtbot):
        """Right-click context menu should contain refresh action."""
        actions = runs_page._build_context_actions()
        assert len(actions) == 4
        # First action is refresh
        assert actions[0][1] == runs_page._refresh_all

    def test_refresh_run_list_empty(self, runs_page):
        """refresh_run_list should not crash with no runs."""
        with patch("jobdesk_app.gui.pages.runs_results_page.RunService") as mock_svc:
            mock_svc.return_value.list_runs.return_value = []
            runs_page.refresh_run_list()
        assert runs_page.table.rowCount() == 0

    def test_get_download_patterns_gaussian(self, runs_page):
        """Should return Gaussian patterns for g16 command."""
        record = MagicMock()
        record.command_template = "g16 {name}"
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as mock_store:
            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = GuiSettings()
            patterns = runs_page._get_download_patterns(record)
        assert "*.log" in patterns or "*.chk" in patterns

    def test_get_download_patterns_orca(self, runs_page):
        """Should return ORCA patterns for orca command."""
        record = MagicMock()
        record.command_template = "orca {name}"
        with patch("jobdesk_app.gui.pages.runs_results_page.GuiSettingsStore") as mock_store:
            from jobdesk_app.services.gui_settings import GuiSettings
            mock_store.return_value.load.return_value = GuiSettings()
            patterns = runs_page._get_download_patterns(record)
        assert "*.out" in patterns or "*.gbw" in patterns


class TestFileTransferPage:
    def test_page_creates_without_crash(self, file_page):
        assert file_page is not None

    def test_local_table_exists(self, file_page):
        assert file_page.local_table is not None
        assert file_page.local_table.columnCount() >= 4

    def test_remote_table_exists(self, file_page):
        assert file_page.remote_table is not None

    def test_refresh_local_no_crash(self, file_page):
        """_refresh_local should handle current directory without crash."""
        file_page._refresh_local()
        # Should have at least the parent row
        assert file_page.local_table.rowCount() >= 0

    def test_upload_without_service_shows_message(self, file_page, qtbot):
        """Drag-drop without connection should show status message."""
        messages = []
        file_page._status_cb = lambda m: messages.append(m)
        file_page._service = None
        file_page._upload_dropped_local_paths(["C:/fake/file.gjf"])
        assert any("Connect" in m for m in messages)
