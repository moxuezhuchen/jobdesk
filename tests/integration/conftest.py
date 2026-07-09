"""Integration-test fixtures.

Provides a session-scoped ``real_g16_smoke_work_dir`` fixture that wraps the
Phase 9G real-g16 ConFlow smoke (originally a standalone script under
``scripts/``) into pytest's setup/teardown lifecycle.  Artifact tree lives
under pytest's ``tmp_path_factory`` basetemp instead of the hardcoded
``tmp60f7j8ix/phase9g_real_g16`` path, so test collections stay hermetic.

Skip conditions are evaluated up-front: when bash or ``/opt/g16/g16`` are
absent on the host, every dependent test is no-op'd via the module-level
``pytestmark`` on the consumer (we don't skip the fixture itself — that
would mask other, faster integration tests in the same file).
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
    """Best-effort probe for ``/opt/g16/g16`` inside WSL.

    Returns ``False`` when bash/wsl is unavailable, when the probe fails,
    or when the file is missing.  Never raises.
    """
    if shutil.which("wsl") is None:
        return False
    try:
        proc = subprocess.run(
            ["wsl", "bash", "-c", "test -x /opt/g16/g16 && echo OK || echo NO"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=False, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return proc.stdout.strip() == "OK"


def g16_smoke_prerequisites() -> tuple[bool, str]:
    """Return ``(ok, reason)`` for use in ``pytest.mark.skipif`` predicates."""
    if not _has_bash():
        return False, "real-g16 smoke requires bash on PATH (or wsl on Windows)"
    if not _wsl_g16_exists():
        return False, "/opt/g16/g16 not found in WSL — install or license missing"
    return True, ""


@pytest.fixture(scope="session")
def real_g16_smoke_work_dir(tmp_path_factory) -> Path:
    """Run the real-g16 ConFlow smoke once per pytest session.

    Stages the artifact tree under ``tmp_path_factory.getbasetemp() /
    real_g16_smoke /``.  The fixture holds the path so individual tests
    can assert against sub-paths (``g16_opt/backups/A000001.log`` etc.).
    """
    ok, reason = g16_smoke_prerequisites()
    if not ok:
        pytest.skip(reason)
    base = tmp_path_factory.getbasetemp() / "real_g16_smoke"
    work_dir = run_smoke(base)
    if not (work_dir / "g16_opt" / "backups" / "A000001.log").exists():
        pytest.fail(
            f"smoke finished but {work_dir / 'g16_opt' / 'backups' / 'A000001.log'} "
            f"is missing — check smoke output above"
        )
    return work_dir
