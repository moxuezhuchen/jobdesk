"""M7.0 测试: GUI import 级别验证 — 不启动真实 event loop。"""

from unittest.mock import MagicMock, patch

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
    from jobdesk_app.gui.pages.runs_results_page import RunsResultsPage
    from jobdesk_app.gui.pages.settings_servers_page import SettingsServersPage

    assert FileTransferPage is not None
    assert RunsResultsPage is not None
    assert SettingsServersPage is not None


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


def test_worker_stop_safely_uses_default_timeout():
    from jobdesk_app.gui.workers import BackgroundWorker

    worker = BackgroundWorker(lambda: 42)
    worker.quit = MagicMock()
    worker.wait = MagicMock(return_value=True)

    worker.stop_safely()

    worker.quit.assert_called_once_with()
    worker.wait.assert_called_once_with(3000)


def test_worker_suppresses_result_after_interruption():
    from jobdesk_app.gui.workers import BackgroundWorker

    worker = BackgroundWorker(lambda: 42)
    seen = []
    worker.result.connect(seen.append)

    with patch.object(worker, "isInterruptionRequested", return_value=True):
        worker.run()

    assert seen == []
