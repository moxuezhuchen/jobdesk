#!/usr/bin/env python3
"""Restore the real Gaussian 16 wrapper at /opt/g16/g16.

Phase 5/6 accidentally clobbered /opt/g16/g16 with a mock shell script.
This script copies the recovered wrapper from
``scripts/restore_g16_wrapper/recovered_g16.sh`` into WSL and:

  1. Backs up the current ``/opt/g16/g16`` to ``/opt/g16/g16.clobbered``
     (we never silently overwrite, even when the user asks for recovery).
  2. Installs the recovered wrapper to ``/opt/g16/g16``.
  3. Removes the ``/usr/local/bin/g16`` symlink that earlier mock
     installs may have left pointing at the mock.

After this runs, ``g16 input.gjf`` on WSL invokes the real binary tree.

Usage::

    python scripts/restore_g16_wsl.py                 # default install
    python scripts/restore_g16_wsl.py --dry-run       # show what would happen
"""
from __future__ import annotations

import argparse
import base64
import subprocess
import sys
from pathlib import Path

WSL_DEST = "/opt/g16/g16"
WSL_BACKUP = "/opt/g16/g16.clobbered"
WSL_SYMLINK = "/usr/local/bin/g16"
SOURCE = Path(__file__).resolve().parent / "restore_g16_wrapper" / "recovered_g16.sh"

REMOTE_PY = """\
import base64, os, stat, pathlib, shutil, sys

src_text = base64.b64decode(sys.stdin.read().strip()).decode('utf-8')
dest = '/opt/g16/g16'
backup = '/opt/g16/g16.clobbered'
symlink = '/usr/local/bin/g16'

dest_path = pathlib.Path(dest)
backup_path = pathlib.Path(backup)

# 1) Backup whatever is at /opt/g16/g16 right now (mock or otherwise).
if dest_path.exists():
    if backup_path.exists():
        backup_path.unlink()
    shutil.copy2(dest_path, backup_path)
    print(f'backed up {dest} -> {backup}', file=sys.stderr)

# 2) Write the recovered wrapper.
dest_path.write_text(src_text, encoding='utf-8', newline='\\n')
st = dest_path.stat()
dest_path.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print(f'installed {dest} ({len(src_text)} bytes)')

# 3) Remove the dangling symlink that earlier mock installs may have left.
if os.path.lexists(symlink):
    target = os.readlink(symlink) if os.path.islink(symlink) else None
    if target is not None and target != dest:
        os.remove(symlink)
        print(f'removed dangling symlink {symlink} -> {target}')
    elif target == dest:
        print(f'{symlink} already points to {dest}; leaving intact')
"""


def stream(payload: bytes) -> subprocess.CompletedProcess[bytes]:
    encoded = base64.b64encode(payload).decode("ascii")
    py_quoted = "'" + REMOTE_PY.replace("'", "'\"'\"'") + "'"
    cmd = ["wsl", "bash", "-c", f"python3 -u -c {py_quoted}"]
    return subprocess.run(cmd, input=encoded.encode("ascii"), capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the operations that would happen without executing them.",
    )
    args = parser.parse_args()

    if not SOURCE.exists():
        print(f"missing recovered wrapper: {SOURCE}", file=sys.stderr)
        return 1
    payload = SOURCE.read_bytes()

    if args.dry_run:
        print("dry-run: would run these steps on WSL")
        print("  1. cp  /opt/g16/g16 -> /opt/g16/g16.clobbered")
        print(f"  2. install {SOURCE.name} ({len(payload)} bytes) -> {WSL_DEST}")
        print(f"  3. remove dangling symlink {WSL_SYMLINK} (if it points elsewhere)")
        return 0

    proc = stream(payload)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)
    if proc.returncode != 0:
        print(f"wsl restore exit={proc.returncode}", file=sys.stderr)
        return proc.returncode

    # Verification: file should now be the recovered wrapper, not the mock.
    verify = subprocess.run(
        ["wsl", "bash", "-c", f"file {WSL_DEST} && wc -c {WSL_DEST}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if verify.returncode == 0:
        print("verify:", verify.stdout.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
