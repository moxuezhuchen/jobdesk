"""Root conftest — fix Windows TEMP directory permission issue for pytest tmp_path."""

import os
import sys
from pathlib import Path


def pytest_configure(config):
    """Override basetemp when the default TEMP path has permission issues."""
    if sys.platform == "win32" and config.option.basetemp is None:
        fallback = Path(os.environ.get("JOBDESK_TEST_BASETEMP", r"C:\dft\tmp\pytest"))
        fallback.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(fallback)
