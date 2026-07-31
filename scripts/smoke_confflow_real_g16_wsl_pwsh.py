#!/usr/bin/env python3
"""Locate PowerShell and launch the same strict WSL smoke script."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).resolve().with_name("smoke_confflow_real_g16_wsl.py")
REPO_ROOT = SCRIPT.parent.parent


def main() -> int:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if powershell is None:
        print("[win] FAIL: neither pwsh nor powershell is available", file=sys.stderr)
        return 1
    if not SCRIPT.is_file():
        print(f"[win] FAIL: strict smoke script is missing: {SCRIPT}", file=sys.stderr)
        return 1
    command = """
$ErrorActionPreference = 'Stop'
$repo = $args[0]
$python = $args[1]
$script = $args[2]
$forward = @()
if ($args.Count -gt 3) { $forward = @($args[3..($args.Count - 1)]) }
Set-Location -LiteralPath $repo
& $python $script @forward
exit $LASTEXITCODE
"""
    proc = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
            str(REPO_ROOT),
            sys.executable,
            str(SCRIPT),
            *sys.argv[1:],
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.stdout:
        print(proc.stdout, end="")
    if proc.stderr:
        print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
