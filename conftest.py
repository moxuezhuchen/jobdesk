"""Root conftest — set basetemp to a safe unique directory on Windows."""

import os
import sys
import uuid
from pathlib import Path


def pytest_configure(config):
    """Override basetemp when no explicit --basetemp is given on Windows."""
    if sys.platform == "win32" and config.option.basetemp is None:
        env_val = os.environ.get("JOBDESK_TEST_BASETEMP", "").strip()
        if env_val:
            fallback = Path(env_val)
        else:
            repo_root = Path(__file__).resolve().parent
            fallback = repo_root / f".pytest_tmp_session_{uuid.uuid4().hex[:8]}"
        fallback.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(fallback)
