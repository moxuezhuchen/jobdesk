#!/usr/bin/env python3
"""Install the mock l1.exe into WSL for Phase 9A smoke tests.

Phase 9A smoke needs Gaussian to write a normal ``.log`` so the
wizard's result-download pipeline can be exercised end-to-end. The
real ``/opt/g16/l1.exe`` is 31 MB and dumps core without a license,
so we install a 3 KB mock in its place — but only at ``l1.exe``
(not at ``g16``, which is the Phase-8C-recovered wrapper).

Always backs up the existing ``l1.exe`` to ``l1.exe.real``. Restore
via ``--restore``.

Usage::

    python scripts/install_mock_l1_wsl.py              # install (default)
    python scripts/install_mock_l1_wsl.py --restore    # restore real binary
    python scripts/install_mock_l1_wsl.py --dry-run     # show what would happen
"""
from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

WSL_DEST = "/opt/g16/l1.exe"
WSL_BACKUP = "/opt/g16/l1.exe.real"
SOURCE = Path(__file__).resolve().parent / "mock-gaussian" / "mock_l1_exe"

REMOTE_INSTALL_PY = """\
import base64, os, stat, pathlib, shutil, sys

src_text = base64.b64decode(sys.stdin.read().strip()).decode('utf-8')
dest = '/opt/g16/l1.exe'
backup = '/opt/g16/l1.exe.real'

dest_path = pathlib.Path(dest)
backup_path = pathlib.Path(backup)

if dest_path.exists():
    if not backup_path.exists():
        shutil.copy2(dest_path, backup_path)
        print(f'backed up {dest} -> {backup}', file=sys.stderr)
    else:
        print(f'backup {backup} already exists, skipping', file=sys.stderr)

dest_path.write_text(src_text, encoding='utf-8', newline='\\n')
st = dest_path.stat()
dest_path.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print(f'mock installed at {dest} ({len(src_text)} bytes)')
"""

REMOTE_RESTORE_PY = """\
import pathlib, shutil, sys

dest = '/opt/g16/l1.exe'
backup = '/opt/g16/l1.exe.real'
dest_path = pathlib.Path(dest)
backup_path = pathlib.Path(backup)

if not backup_path.exists():
    print(f'no backup at {backup}; cannot restore', file=sys.stderr)
    sys.exit(1)
if dest_path.exists() or dest_path.is_symlink():
    dest_path.unlink()
shutil.copy2(backup_path, dest_path)
st = dest_path.stat()
print(f'restored {dest} from {backup} ({st.st_size} bytes)')
"""


def stream(py_template: str, payload: bytes | None) -> subprocess.CompletedProcess[bytes]:
    encoded = base64.b64encode(payload).decode("ascii") if payload else ""
    py_quoted = "'" + py_template.replace("'", "'\"'\"'") + "'"
    cmd = ["wsl", "bash", "-c", f"python3 -u -c {py_quoted}"]
    return subprocess.run(
        cmd, input=encoded.encode("ascii"), capture_output=True, check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true", help="Restore the real l1.exe.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without executing.")
    args = parser.parse_args()

    if args.dry_run:
        if args.restore:
            print(f"dry-run: cp {WSL_BACKUP} -> {WSL_DEST}")
        else:
            print(f"dry-run: cp {WSL_DEST} -> {WSL_BACKUP} (if not already backed up)")
            print(f"dry-run: install {SOURCE.name} -> {WSL_DEST}")
        return 0

    if args.restore:
        proc = stream(REMOTE_RESTORE_PY, None)
    else:
        if not SOURCE.exists():
            print(f"missing source: {SOURCE}", file=sys.stderr)
            return 1
        proc = stream(REMOTE_INSTALL_PY, SOURCE.read_bytes())

    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)
    if proc.returncode != 0:
        print(f"wsl install exit={proc.returncode}", file=sys.stderr)
        return proc.returncode

    verify = subprocess.run(
        ["wsl", "bash", "-c", f"file {WSL_DEST} && wc -c {WSL_DEST}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if verify.returncode == 0:
        print("verify:", verify.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())