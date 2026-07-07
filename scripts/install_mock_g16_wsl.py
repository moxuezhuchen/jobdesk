#!/usr/bin/env python3
"""Install the mock Gaussian into WSL.

Phase 7 lesson: never overwrite real software in ``/opt/g16/g16`` — that
path holds a legitimate Gaussian 16 wrapper script and clobbering it
breaks real Gaussian usage. The default mode (``staging``) writes the
mock into a per-user directory (``~/.local/bin/g16``) and lets the
YAML's ``gaussian_path:`` field point at it. Use ``--mode system``
only when you explicitly want to clobber the system binary (and only
after backing it up to ``/opt/g16/g16.real``).

Usage::

    python scripts/install_mock_g16_wsl.py                       # staging (safe)
    python scripts/install_mock_g16_wsl.py --mode system --backup  # overwrite (Phase 6 legacy)
"""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
from pathlib import Path

DEFAULT_STAGING_DEST = "/home/${USER}/.local/bin/g16"
SYSTEM_DEST = "/opt/g16/g16"
BACKUP_PATH = "${WSL_DEST_REAL:-/opt/g16/g16.real}"

REMOTE_PY = """\
import base64, os, stat, pathlib, sys, shutil
data = base64.b64decode(sys.stdin.read().strip()).decode('utf-8')
dest = sys.argv[1]
mode = sys.argv[2]
backup_target = sys.argv[3] if len(sys.argv) > 3 else ''

if mode == 'system':
    p = pathlib.Path(dest)
    if p.exists() and backup_target:
        shutil.copy2(p, backup_target)
        print(f'backed up {dest} -> {backup_target}')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding='utf-8', newline='\\n')
    st = p.stat()
    p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f'installed {dest} ({len(data)} bytes) [mode=system]')
else:
    # staging mode — write to a per-user location and PATH-link it.
    p = pathlib.Path(dest)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data, encoding='utf-8', newline='\\n')
    st = p.stat()
    p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    link = '/usr/local/bin/g16'
    if os.path.lexists(link) and not os.path.islink(link):
        print(f'NOTE: /usr/local/bin/g16 exists and is not a symlink; leaving untouched', file=sys.stderr)
    else:
        if os.path.lexists(link):
            os.remove(link)
        try:
            os.symlink(str(p), link)
            print(f'linked {link} -> {p}')
        except OSError as exc:
            print(f'symlink failed: {exc}', file=sys.stderr)
    print(f'installed {dest} ({len(data)} bytes) [mode=staging]')
"""


def stream_install(dest: str, mode: str, backup: str, payload: bytes) -> subprocess.CompletedProcess[bytes]:
    encoded = base64.b64encode(payload).decode("ascii")
    py_quoted = "'" + REMOTE_PY.replace("'", "'\"'\"'") + "'"
    cmd = ["wsl", "bash", "-c", f"python3 -u -c {py_quoted} {dest} {mode} {backup}"]
    return subprocess.run(cmd, input=encoded.encode("ascii"), capture_output=True, check=False)


def expand_user(path: str) -> str:
    """Resolve ${USER} on the WSL side via a single bash round-trip."""
    proc = subprocess.run(
        ["wsl", "bash", "-c", f"echo {path.replace('$', '\\$')}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=str(Path(__file__).resolve().parent / "mock-gaussian" / "g16"),
    )
    parser.add_argument("--mode", choices=("staging", "system"), default="staging")
    parser.add_argument(
        "--staging-dest",
        default=DEFAULT_STAGING_DEST,
        help="Where to install in staging mode. Use ${USER} for expansion.",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        help="In system mode, copy the existing /opt/g16/g16 to .real first.",
    )
    args = parser.parse_args()

    src = Path(args.source)
    if not src.exists():
        print(f"missing source: {src}", file=__import__("sys").stderr)
        return 1
    payload = src.read_bytes()

    if args.mode == "staging":
        dest = expand_user(args.staging_dest)
        backup_arg = ""  # ignored in staging mode
    else:
        dest = SYSTEM_DEST
        backup_arg = BACKUP_PATH if args.backup else ""

    proc = stream_install(dest, args.mode, backup_arg, payload)
    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=__import__("sys").stderr)
    if proc.returncode != 0:
        print(f"wsl install exit={proc.returncode}", file=__import__("sys").stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())