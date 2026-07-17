import logging
from datetime import datetime

from .app_paths import get_logs_dir


def configure_file_logging(logger_name: str = "jobdesk") -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    try:
        logs_dir = get_logs_dir()
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"jobdesk-{datetime.now().strftime('%Y%m%d')}.log"

        resolved_log_path = str(log_path.resolve())
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler) and handler.baseFilename == resolved_log_path:
                return logger

        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    except OSError:
        if not logger.handlers:
            logger.addHandler(logging.NullHandler())
    return logger
