import logging

from jobdesk_app.app_paths import get_app_data_dir, get_logs_dir
from jobdesk_app.app_logging import configure_file_logging


def test_app_paths_use_appdata_jobdesk(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))

    assert get_app_data_dir() == tmp_path / "JobDesk"
    assert get_logs_dir() == tmp_path / "JobDesk" / "logs"


def test_configure_file_logging_writes_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    logger = configure_file_logging("jobdesk-test")

    logger.info("hello from test")
    for handler in logger.handlers:
        handler.flush()

    log_files = list((tmp_path / "JobDesk" / "logs").glob("jobdesk-*.log"))
    assert log_files
    assert "hello from test" in log_files[0].read_text(encoding="utf-8")
    assert logger.level == logging.INFO
