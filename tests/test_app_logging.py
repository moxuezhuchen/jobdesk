from __future__ import annotations

import logging

from jobdesk_app.app_logging import configure_file_logging


def test_submodule_logs_go_to_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    namespace_logger = logging.getLogger("jobdesk_app")
    original_handlers = list(namespace_logger.handlers)
    original_level = namespace_logger.level
    original_propagate = namespace_logger.propagate

    try:
        configure_file_logging()
        logging.getLogger("jobdesk_app.services.run_monitor").warning("submodule warning")
        for handler in namespace_logger.handlers:
            handler.flush()

        log_file = next((tmp_path / "JobDesk" / "logs").glob("jobdesk-*.log"))
        assert "submodule warning" in log_file.read_text(encoding="utf-8")
    finally:
        for handler in list(namespace_logger.handlers):
            if handler not in original_handlers:
                namespace_logger.removeHandler(handler)
                handler.close()
        namespace_logger.handlers[:] = original_handlers
        namespace_logger.setLevel(original_level)
        namespace_logger.propagate = original_propagate
