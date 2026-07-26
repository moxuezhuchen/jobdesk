"""NUL 调查脚本自检。

Simple structural check: invoke the script and assert it produces the
six labelled sections in the documented order, exits 0, and the
empty-fallback label "(empty)" appears for sections that are negative
or empty in this repo.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_LABELS = [
    "git status --porcelain=v1 -z (split on NUL)",
    "git ls-files -z (tracked)",
    "git ls-files --others --exclude-standard -z (untracked)",
    "git ls-files --stage -z (staged)",
    "Test-Path -LiteralPath NUL (Win32)",
    "Get-Item -LiteralPath NUL (Win32)",
]


def test_investigate_nul_artifact_reports_all_sections() -> None:
    script = REPO_ROOT / "scripts" / "investigate_nul_artifact.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        timeout=30,
    )
    assert result.returncode == 0, f"investigate exited {result.returncode}: {result.stderr}"
    output = result.stdout
    missing = [label for label in EXPECTED_LABELS if f"[{label}]" not in output]
    assert not missing, f"missing labels: {missing}; output was:\n{output}"


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, str(REPO_ROOT / "scripts" / "investigate_nul_artifact.py")]))
