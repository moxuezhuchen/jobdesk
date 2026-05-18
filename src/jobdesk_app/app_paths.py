import os
from pathlib import Path


def get_app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "JobDesk"


def get_logs_dir() -> Path:
    return get_app_data_dir() / "logs"


def get_cache_dir() -> Path:
    return get_app_data_dir() / "cache"
