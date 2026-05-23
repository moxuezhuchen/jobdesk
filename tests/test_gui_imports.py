"""M7.0 测试: GUI import 级别验证 — 不启动真实 event loop。"""

import pytest

# 这些 import 可能因为没有显示器而失败，用 skipif 保护
pytest.importorskip("PySide6", reason="PySide6 not installed")


def test_import_app():
    from jobdesk_app.gui.app import main
    assert callable(main)


def test_import_main_window():
    from jobdesk_app.gui.main_window import MainWindow
    assert MainWindow is not None




def test_import_pages():
    from jobdesk_app.gui.pages.file_transfer_page import FileTransferPage
    from jobdesk_app.gui.pages.servers_page import ServersPage
    from jobdesk_app.gui.pages.runs_page import RunsPage
    from jobdesk_app.gui.pages.results_page import ResultsPage
    from jobdesk_app.gui.pages.settings_page import SettingsPage
    assert ServersPage is not None
    assert FileTransferPage is not None
    assert RunsPage is not None
    assert ResultsPage is not None
    assert SettingsPage is not None



def test_app_state_create():
    from jobdesk_app.gui.state import AppState
    s = AppState()
    assert s.current_project_root is None
    assert s.current_batch_id is None
    assert s.last_error is None


def test_worker_create():
    from jobdesk_app.gui.workers import BackgroundWorker
    w = BackgroundWorker(lambda: 42)
    assert w is not None


def test_legacy_runs_page_has_no_workflow_launch_action(qtbot):
    from jobdesk_app.gui.pages.runs_page import RunsPage
    from jobdesk_app.gui.state import AppState

    page = RunsPage(AppState(), log_cb=lambda message: None, status_cb=lambda message: None)
    qtbot.addWidget(page)

    assert not hasattr(page, "new_workflow_btn")
    assert not hasattr(page, "_start_workflow")
