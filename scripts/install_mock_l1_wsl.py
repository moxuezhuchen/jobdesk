#!/usr/bin/env python3
"""Install the mock l1.exe into WSL for Phase 9A smoke tests.

Phase 9A smoke needs Gaussian to write a normal ``.log`` so the
wizard's result-download pipeline can be exercised end-to-end. The
real ``/opt/g16/l1.exe`` is 31 MB and dumps core without a license,
so we install a 3 KB mock in its place — but only at ``l1.exe``
(not at ``g16``, which is the Phase-8C-recovered wrapper).

Always backs up the existing ``l1.exe`` to ``l1.exe.real``. Restore
via ``--restore``.

Safety: refuses to install the mock if the upstream ``/opt/g16/g16``
wrapper is itself a JOBDESK_MOCK-tainted shell script. This guards
against the Phase 6 issue where a mock g16 was deployed to the real
``/opt/g16/g16`` path, breaking the recovered wrapper. See
``docs/PHASE9H3_ORCA_MOCK_CLEANUP.md``.

Usage::

    python scripts/install_mock_l1_wsl.py              # install (default)
    python scripts/install_mock_l1_wsl.py --restore    # restore real binary
    python scripts/install_mock_l1_wsl.py --dry-run     # show what would happen
    python scripts/install_mock_l1_wsl.py --yes         # skip the safety prompt
"""
from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

WSL_DEST = "/opt/g16/l1.exe"
WSL_BACKUP = "/opt/g16/l1.exe.real"
WSL_WRAPPER = "/opt/g16/g16"
SOURCE = Path(__file__).resolve().parent / "mock-gaussian" / "mock_l1_exe"
MOCK_SENTINEL = b"JOBDESK_MOCK"  # written into mock_l1_exe and grep-detectable

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

REMOTE_PROBE_PY = """\
import pathlib, sys
p = pathlib.Path('/opt/g16/g16')
if not p.exists():
    print('MISSING')
    sys.exit(0)
try:
    head = p.open('rb').read(4096)
except OSError as exc:
    print(f'UNREADABLE:{exc}', file=sys.stderr)
    sys.exit(2)
if b'JOBDESK_MOCK' in head:
    print('MOCK')
elif head.startswith(b'#!') and b'/bin/sh' in head.split(b'\\n', 1)[0]:
    print('SHELL')
else:
    print('BINARY')
"""


def stream(py_template: str, payload: bytes | None) -> subprocess.CompletedProcess[bytes]:
    encoded = base64.b64encode(payload).decode("ascii") if payload else ""
    py_quoted = "'" + py_template.replace("'", "'\"'\"'") + "'"
    cmd = ["wsl", "bash", "-c", f"python3 -u -c {py_quoted}"]
    return subprocess.run(
        cmd, input=encoded.encode("ascii"), capture_output=True, check=False,
    )


def probe_wrapper() -> str:
    """Probe ``/opt/g16/g16`` and return one of ``BINARY/SHELL/MOCK/MISSING/UNREADABLE``.

    The mock install must refuse to overwrite ``/opt/g16/l1.exe`` if the
    upstream wrapper at ``/opt/g16/g16`` is already a JOBDESK_MOCK-tainted
    shell script. That's the Phase 6 foot-gun we don't want to repeat.
    """
    proc = stream(REMOTE_PROBE_PY, None)
    return proc.stdout.decode("utf-8", errors="replace").strip() or "MISSING"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true", help="Restore the real l1.exe.")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without executing.")
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the safety prompt when the upstream g16 wrapper is a mock/shell script.",
    )
    args = parser.parse_args()

    if args.dry_run:
        if args.restore:
            print(f"dry-run: cp {WSL_BACKUP} -> {WSL_DEST}")
        else:
            print(f"dry-run: probe {WSL_WRAPPER} for JOBDESK_MOCK marker")
            print(f"dry-run: cp {WSL_DEST} -> {WSL_BACKUP} (if not already backed up)")
            print(f"dry-run: install {SOURCE.name} -> {WSL_DEST}")
        return 0

    if not args.restore:
        if not SOURCE.exists():
            print(f"missing source: {SOURCE}", file=sys.stderr)
            return 1
        # Safety: refuse to install the mock if the upstream wrapper itself
        # is JOBDESK_MOCK-tainted. That's the Phase 6 issue this guard
        # exists to prevent.
        wrapper_kind = probe_wrapper()
        if wrapper_kind == "MOCK":
            msg = (
                f"REFUSING to install mock l1.exe: {WSL_WRAPPER} is itself a "
                "JOBDESK_MOCK-tainted shell script. Restore the real g16 "
                "wrapper first (see docs/PHASE9H3_ORCA_MOCK_CLEANUP.md)."
            )
            if not args.yes:
                print(msg, file=sys.stderr)
                return 3
            print(f"WARNING: --yes given, proceeding despite tainted wrapper", file=sys.stderr)
        elif wrapper_kind == "SHELL":
            print(
                f"WARNING: {WSL_WRAPPER} is a shell script but not JOBDESK_MOCK-tagged. "
                "Proceeding — confirm this is the recovered Phase 8C wrapper before testing.",
                file=sys.stderr,
            )
        elif wrapper_kind == "MISSING":
            print(
                f"WARNING: {WSL_WRAPPER} does not exist on the WSL side. "
                "The Phase 8C recovered wrapper is missing — Gaussian runs will fail.",
                file=sys.stderr,
            )

    if args.restore:
        proc = stream(REMOTE_RESTORE_PY, None)
    else:
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