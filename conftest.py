"""Root conftest — set basetemp to a safe unique directory on Windows."""

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest


def pytest_configure(config):
    """Override basetemp when no explicit --basetemp is given on Windows."""
    if sys.platform == "win32" and config.option.basetemp is None:
        env_val = os.environ.get("JOBDESK_TEST_BASETEMP", "").strip()
        if env_val:
            fallback = Path(env_val)
        else:
            fallback = Path(tempfile.gettempdir()) / f"jobdesk_pytest_{uuid.uuid4().hex[:8]}"
        fallback.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(fallback)


@pytest.fixture(autouse=True)
def _drain_background_workers():
    """Join and clear leftover BackgroundWorker threads after each test.

    BackgroundWorker keeps started threads in a process-global registry. Without
    this, a worker leaked by one test can intermittently break a later test's
    shutdown path across Python versions.
    """
    yield
    try:
        from jobdesk_app.gui.workers import BackgroundWorker
    except Exception:
        return
    BackgroundWorker.wait_all()
    BackgroundWorker._active.clear()
