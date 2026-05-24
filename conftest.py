"""Root conftest — set basetemp to a safe local directory on Windows."""

import os
import sys
from pathlib import Path


def pytest_configure(config):
    """Override basetemp when no explicit --basetemp is given on Windows."""
    if sys.platform == "win32" and config.option.basetemp is None:
        env_val = os.environ.get("JOBDESK_TEST_BASETEMP", "").strip()
        if env_val:
            fallback = Path(env_val)
        else:
            fallback = Path(__file__).resolve().parent / ".pytest_tmp_local"
        fallback.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(fallback)
