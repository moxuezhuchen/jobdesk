"""NUL 伪影调查脚本（P-M9）.

Run the 5 commands documented in ``docs/NUL_INVESTIGATION.md`` and print
structured results. Designed to be safe in CI: read-only, no writes.

Usage::

    python scripts/investigate_nul_artifact.py

Exit code: 0 always (the artifact is informational, not a build failure).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_git(args: list[str]) -> list[str]:
    cmd = ["git", "-C", str(REPO_ROOT), *args]
    if not shutil.which("git"):
        return ["<git not on PATH>"]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return ["<timeout>"]
    if result.returncode != 0:
        return [f"<exit {result.returncode}: {result.stderr.strip()}>"]
    if not result.stdout:
        return []
    return [item for item in result.stdout.split("\0") if item]


def _powershell_items(script: str) -> list[str]:
    if not shutil.which("powershell"):
        return ["<powershell not on PATH>"]
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return ["<timeout>"]
    if result.returncode != 0:
        return [f"<exit {result.returncode}: {result.stderr.strip()}>"]
    out = result.stdout.strip()
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def investigate() -> dict[str, list[str]]:
    nul_literal = str(REPO_ROOT / "NUL")
    return {
        "git_status_porcelain": _nul_filter_only(_run_git(["status", "--porcelain=v1", "-z"])),
        "git_ls_files": _nul_filter_only(_run_git(["ls-files", "-z"])),
        "git_ls_files_others": _nul_filter_only(_run_git(["ls-files", "--others", "--exclude-standard", "-z"])),
        "git_ls_files_stage": _nul_filter_only(_run_git(["ls-files", "--stage", "-z"])),
        "win32_test_path": _powershell_items(f"Test-Path -LiteralPath '{nul_literal}' -PathType Any"),
        "win32_get_item": _powershell_items(
            f"Get-Item -LiteralPath '{nul_literal}' -ErrorAction SilentlyContinue | "
            "Select-Object FullName, Mode, Attributes | Format-List"
        ),
    }


def _nul_filter_only(items: list[str]) -> list[str]:
    return [item for item in items if "NUL" in item]


def render(report: dict[str, list[str]]) -> str:
    labels = {
        "git_status_porcelain": "git status --porcelain=v1 -z (split on NUL)",
        "git_ls_files": "git ls-files -z (tracked)",
        "git_ls_files_others": "git ls-files --others --exclude-standard -z (untracked)",
        "git_ls_files_stage": "git ls-files --stage -z (staged)",
        "win32_test_path": "Test-Path -LiteralPath NUL (Win32)",
        "win32_get_item": "Get-Item -LiteralPath NUL (Win32)",
    }
    lines = ["NUL artifact report (P-M9)", "=" * 40]
    for key, label in labels.items():
        items = report[key]
        lines.append("")
        lines.append(f"[{label}]")
        if not items:
            lines.append("  (empty)")
        else:
            for item in items:
                lines.append(f"  {item}")
    return "\n".join(lines)


def main() -> int:
    print(render(investigate()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
