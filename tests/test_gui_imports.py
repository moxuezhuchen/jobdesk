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


def test_main_window_ui_policy_helpers():
    from jobdesk_app.gui.main_window import main_window_has_status_bar, main_window_shows_log_panel
    from jobdesk_app.gui.theme import build_app_stylesheet

    css = build_app_stylesheet()

    assert main_window_has_status_bar() is False
    assert main_window_shows_log_panel() is False
    assert "selection-background-color: #2563eb" in css
    assert "selection-color: #ffffff" in css


def test_main_navigation_labels_include_projects_first():
    from jobdesk_app.gui.main_window import main_navigation_labels

    assert main_navigation_labels("en") == (
        "Projects",
        "Files",
        "Runs",
        "Results",
        "Servers",
        "Settings",
    )


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


def test_unknown_host_key_error_detection():
    from jobdesk_app.gui.pages.servers_page import format_host_key_prompt, is_unknown_host_key_error

    assert is_unknown_host_key_error("SSHException: Server 'h' not found in known_hosts")
    assert is_unknown_host_key_error("SSHException: not found in known_hosts")
    assert not is_unknown_host_key_error("Authentication failed")
    prompt = format_host_key_prompt("s1", "h", 22, "ssh-ed25519", "00:11")
    assert "s1" in prompt
    assert "ssh-ed25519" in prompt
    assert "00:11" in prompt


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


def test_table_models():
    from jobdesk_app.gui.table_models import load_tsv_to_table, display_dict_as_table
    assert load_tsv_to_table is not None
