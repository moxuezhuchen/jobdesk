"""Integration-test fixtures.

Provides the session-scoped real-g16 smoke fixture without altering the
ConfFlow import path used by the integration suite.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.integration._real_g16_smoke import run_smoke


def _has_bash() -> bool:
    return shutil.which("bash") is not None or shutil.which("wsl") is not None


def _wsl_g16_exists() -> bool:
    """Best-effort probe for the real g16 wrapper inside WSL."""
    if shutil.which("wsl") is None:
        return False
    try:
        proc = subprocess.run(
            ["wsl", "bash", "-c", "test -x /opt/g16/g16 && echo OK || echo NO"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.stdout.strip() == "OK"


def g16_smoke_prerequisites() -> tuple[bool, str]:
    """Return prerequisite status for the real-g16 smoke skip marker."""
    if not _has_bash():
        return False, "real-g16 smoke requires bash on PATH (or wsl on Windows)"
    if not _wsl_g16_exists():
        return False, "/opt/g16/g16 not found in WSL - install or license missing"
    return True, ""


@pytest.fixture(scope="session")
def real_g16_smoke_work_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the real-g16 smoke once and return its pulled artifact directory."""
    ok, reason = g16_smoke_prerequisites()
    if not ok:
        pytest.skip(reason)
    base = tmp_path_factory.getbasetemp() / "real_g16_smoke"
    work_dir = run_smoke(base)
    expected_log = work_dir / "g16_opt" / "backups" / "A000001.log"
    if not expected_log.exists():
        pytest.fail(f"smoke finished but {expected_log} is missing - check smoke output above")
    return work_dir
