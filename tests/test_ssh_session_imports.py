"""Import boundaries for the GUI-facing SSH session factories."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).parents[1]


def _run_isolated_imports(*modules: str) -> subprocess.CompletedProcess[str]:
    source_root = str(_REPOSITORY_ROOT / "src")
    python_path = os.pathsep.join(filter(None, (source_root, os.environ.get("PYTHONPATH", ""))))
    module_imports = "\n".join(f"import {module}" for module in modules)
    script = f"{module_imports}\nimport sys\nassert 'paramiko' not in sys.modules\n"
    env = os.environ.copy()
    env["PYTHONPATH"] = python_path
    return subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def test_gui_session_and_files_controller_do_not_import_paramiko() -> None:
    result = _run_isolated_imports(
        "jobdesk_app.gui.session",
        "jobdesk_app.application.files_connections",
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_ssh_factory_loads_paramiko_only_when_requested() -> None:
    source_root = str(_REPOSITORY_ROOT / "src")
    python_path = os.pathsep.join(filter(None, (source_root, os.environ.get("PYTHONPATH", ""))))
    script = """
import sys
from jobdesk_app.services.ssh_session import create_ssh_client

assert 'paramiko' not in sys.modules
from jobdesk_app.config.schema import ServerConfig

create_ssh_client(ServerConfig(host='example', username='user'))
assert 'paramiko' in sys.modules
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = python_path
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_main_window_import_does_not_load_paramiko() -> None:
    result = _run_isolated_imports("jobdesk_app.gui.main_window")

    assert result.returncode == 0, result.stderr or result.stdout
