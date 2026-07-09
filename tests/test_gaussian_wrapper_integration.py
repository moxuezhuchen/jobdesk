"""Integration test: the recovered Gaussian wrapper + mock l1.exe → parseable log.

Phase 9A: validates the full backend pipeline (mock binary →
.log → JobDesk parser) without requiring a real Gaussian license.

Strategy
--------
The mock `l1.exe` (`scripts/mock-gaussian/mock_l1_exe`) ships with the
repo. The integration test writes a tiny `.gjf`, invokes the mock
directly via ``bash``, then parses the resulting `.log`` with the real
``parse_gaussian_log``. Because the mock is a regular sh script and
the integration test only depends on ``subprocess`` + ``shutil``, it
runs on Linux CI without any WSL-specific setup.

This file is skipped in two cases:
1. ``bash`` isn't on PATH (native Windows without WSL or Git Bash).
2. Running on Windows CI where ``bash`` resolves to the WSL shim
   (``C:\\Windows\\System32\\bash.exe``) that has no distros installed.
CI default (``-m 'not integration'``) also skips it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jobdesk_app.core.parsers.gaussian import parse_gaussian_log

REPO = Path(__file__).resolve().parents[1]
MOCK_L1 = REPO / "scripts" / "mock-gaussian" / "mock_l1_exe"


def _to_bash_path(p: Path) -> str:
    """Convert a Windows Path to a path that bash on Windows understands.

    Git Bash doesn't ship here; instead ``bash`` is WSL bash via the
    ``%WINDIR%\\system32\\bash`` Win32 shim, which converts ``C:\\...``
    arguments into ``/mnt/c/...``. We do the same translation here so
    the test runs identically on Windows and on POSIX.
    """
    raw = str(p)
    if raw.startswith("\\\\"):
        return raw  # UNC path; leave it
    if len(raw) >= 2 and raw[1] == ":":
        drive = raw[0].lower()
        rest = raw[2:].replace("\\", "/")
        return f"/mnt/{drive}{rest}"
    return raw.replace("\\", "/")

METHANE_GJF = """\
%chk=methane.chk
%mem=1GB
%nproc=2
# b3lyp/6-31g(d) sp

methane

0 1
C   0.000000   0.000000   0.000000
H   0.629118   0.629118   0.629118
H  -0.629118  -0.629118   0.629118
H  -0.629118   0.629118  -0.629118
H   0.629118  -0.629118  -0.629118
"""

WATER_GJF = """\
%chk=water.chk
%mem=512MB
%nproc=1
# hf/sto-3g sp

water

0 1
O  0.000000   0.000000   0.117
H  0.000000   0.756000  -0.469
H  0.000000  -0.756000  -0.469
"""


def _run_g16(tmp_path: Path, gjf_text: str, basename: str = "methane") -> Path:
    gjf = tmp_path / f"{basename}.gjf"
    gjf.write_text(gjf_text, encoding="utf-8", newline="\n")  # force LF for bash
    log = tmp_path / f"{basename}.log"
    env = dict(os.environ)
    env["JOBDESK_MOCK_L1_DELAY"] = "0"  # skip sleep for tests
    # Write the mock script to a temp file with explicit LF line endings,
    # then invoke it via bash. The script is sourced as a regular file
    # (not via stdin) so Git Bash line-ending handling stays out of the way.
    mock_source = MOCK_L1.read_text(encoding="utf-8")
    mock_source = mock_source.replace("\r\n", "\n").replace("\r", "\n")
    script_path = tmp_path / "_mock_l1.sh"
    script_path.write_text(mock_source, encoding="utf-8", newline="\n")
    bash_script_path = _to_bash_path(script_path)
    proc = subprocess.run(
        ["bash", bash_script_path, f"{basename}.gjf"],
        cwd=tmp_path, env=env,
        capture_output=True, text=False, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"mock l1.exe failed: stderr={proc.stderr!r}"
    return log


# Skip everything in this file if bash isn't available. The mock l1.exe is
# a POSIX shell script and we can't exec it on native Windows. WSL/Git Bash
# /Linux CI all have bash.  Also skip on Windows CI where bash resolves to
# the WSL shim (no distros installed).
pytestmark = [
    pytest.mark.skipif(
        shutil.which("bash") is None,
        reason="bash not on PATH (WSL or Git Bash required)",
    ),
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows CI bash is WSL shim with no distros; mock requires POSIX bash",
    ),
    pytest.mark.integration,
]


@pytest.mark.skipif(not MOCK_L1.exists(), reason="mock l1.exe missing from scripts/")
def test_g16_wrapper_produces_normal_termination_log(tmp_path: Path):
    """The mock g16 wrapper writes a log with Normal termination."""
    log = _run_g16(tmp_path, METHANE_GJF)
    assert log.exists(), f"log not produced at {log}"
    text = log.read_text(encoding="utf-8")
    assert "Normal termination of Gaussian" in text


@pytest.mark.skipif(not MOCK_L1.exists(), reason="mock l1.exe missing from scripts/")
def test_g16_wrapper_log_parses_to_gaussian_result(tmp_path: Path):
    """The produced .log is parseable by JobDesk's parse_gaussian_log."""
    log = _run_g16(tmp_path, METHANE_GJF)
    result = parse_gaussian_log(log)
    assert result.normal_termination is True
    assert result.error_termination is False
    assert result.error_message is None
    assert result.final_energy_au == pytest.approx(-75.123456, abs=1e-6)


@pytest.mark.skipif(not MOCK_L1.exists(), reason="mock l1.exe missing from scripts/")
def test_g16_wrapper_extracts_geometry(tmp_path: Path):
    """Geometry is recovered from the Standard orientation block."""
    log = _run_g16(tmp_path, METHANE_GJF)
    result = parse_gaussian_log(log)
    assert result.atom_symbols == ["C", "H", "H", "H", "H"]
    assert result.final_xyz is not None
    # 5 atoms → 5 non-empty lines
    assert len(result.final_xyz.splitlines()) == 5
    # First atom is C
    first = result.final_xyz.splitlines()[0]
    assert first.strip().startswith("C")


@pytest.mark.skipif(not MOCK_L1.exists(), reason="mock l1.exe missing from scripts/")
def test_g16_wrapper_water_energy_is_hf(tmp_path: Path):
    """Different method/basis → different mock energy."""
    log = _run_g16(tmp_path, WATER_GJF, basename="water")
    result = parse_gaussian_log(log)
    assert result.final_energy_au == pytest.approx(-40.478900, abs=1e-6)
    assert result.atom_symbols == ["O", "H", "H"]


@pytest.mark.skipif(not MOCK_L1.exists(), reason="mock l1.exe missing from scripts/")
def test_g16_wrapper_writes_result_xyz(tmp_path: Path):
    """The mock l1.exe also writes a result .xyz (for confflow assembly)."""
    _run_g16(tmp_path, METHANE_GJF)
    xyz = tmp_path / "methane.xyz"
    assert xyz.exists()
    lines = xyz.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "5"  # atom count
    assert lines[1].startswith("methane")


@pytest.mark.skipif(not MOCK_L1.exists(), reason="mock l1.exe missing from scripts/")
def test_g16_wrapper_exits_zero(tmp_path: Path):
    """Mock wrapper exits 0 on success (mock l1.exe is well-behaved)."""
    gjf = tmp_path / "methane.gjf"
    gjf.write_text(METHANE_GJF, encoding="utf-8", newline="\n")  # force LF
    env = dict(os.environ)
    env["JOBDESK_MOCK_L1_DELAY"] = "0"
    mock_source = MOCK_L1.read_text(encoding="utf-8")
    mock_source = mock_source.replace("\r\n", "\n").replace("\r", "\n")
    script_path = tmp_path / "_mock_l1.sh"
    script_path.write_text(mock_source, encoding="utf-8", newline="\n")
    bash_script_path = _to_bash_path(script_path)
    proc = subprocess.run(
        ["bash", bash_script_path, "methane.gjf"],
        cwd=tmp_path, env=env,
        capture_output=True, text=False, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0
