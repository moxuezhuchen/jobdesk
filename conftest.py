"""Root conftest — set basetemp to a safe local directory on Windows."""

import os
import sys
from pathlib import Path


def pytest_configure(config):
    if sys.platform == "win32" and config.option.basetemp is None:
        fallback = Path(os.environ.get("JOBDESK_TEST_BASETEMP", ""))
        if not fallback or not str(fallback).strip():
            fallback = Path(__file__).resolve().parent / ".pytest_tmp_local"
        fallback.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(fallback)
