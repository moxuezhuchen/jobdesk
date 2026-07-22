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
import hashlib
import re
import subprocess
import sys
from pathlib import Path

WSL_DEST = "/opt/g16/l1.exe"
WSL_BACKUP = "/opt/g16/l1.exe.real"
WSL_BACKUP_MANIFEST = "/opt/g16/l1.exe.real.jobdesk.json"
WSL_WRAPPER = "/opt/g16/g16"
SOURCE = Path(__file__).resolve().parent / "mock-gaussian" / "mock_l1_exe"
MOCK_SENTINEL = b"JOBDESK_MOCK"  # written into mock_l1_exe and grep-detectable

REMOTE_INSTALL_PY = """\
import base64, hashlib, json, os, stat, pathlib, shutil, sys, tempfile

src_data = base64.b64decode(sys.stdin.read().strip())
dest = '/opt/g16/l1.exe'
backup = '/opt/g16/l1.exe.real'
manifest = '/opt/g16/l1.exe.real.jobdesk.json'

dest_path = pathlib.Path(dest)
backup_path = pathlib.Path(backup)
manifest_path = pathlib.Path(manifest)

def reject_backup(reason):
    print(f'unsafe backup {backup}: {reason}; {dest} was not modified', file=sys.stderr)
    sys.exit(3)

def sync_parent(path):
    # WSL is POSIX; syncing the directory makes each rename durable as well
    # as atomic. Windows lacks a portable directory fsync, so unit tests and
    # non-WSL callers safely skip that platform-specific operation.
    if os.name != 'posix':
        return
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

def authentic_real(path):
    if path.is_symlink() or not path.exists():
        return None
    try:
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size < 1048576:
            return None
        file_data = path.read_bytes()
    except OSError:
        return None
    file_head = file_data[:4096]
    if (
        b'JOBDESK_MOCK' in file_head
        or file_head.startswith(b'#!')
        or not file_head.startswith(b'\\x7fELF')
    ):
        return None
    return file_stat, hashlib.sha256(file_data).hexdigest()

def same_file(left, right):
    return (
        left is not None
        and right is not None
        and left[0].st_size == right[0].st_size
        and stat.S_IMODE(left[0].st_mode) == stat.S_IMODE(right[0].st_mode)
        and left[1] == right[1]
    )

def manifest_matches(backup_info):
    if backup_info is None or manifest_path.is_symlink() or not manifest_path.is_file():
        return False
    try:
        metadata = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    backup_stat, digest = backup_info
    return (
        metadata.get('version') == 1
        and metadata.get('source') == dest
        and metadata.get('backup') == backup
        and metadata.get('size') == backup_stat.st_size
        and metadata.get('mode') == stat.S_IMODE(backup_stat.st_mode)
        and metadata.get('sha256') == digest
    )

def publish_manifest(backup_stat, backup_digest):
    manifest_data = {
        'version': 1,
        'source': dest,
        'backup': backup,
        'size': backup_stat.st_size,
        'mode': stat.S_IMODE(backup_stat.st_mode),
        'sha256': backup_digest,
    }
    fd, manifest_tmp_name = tempfile.mkstemp(prefix='.l1.exe.manifest-', dir=str(manifest_path.parent))
    manifest_tmp = pathlib.Path(manifest_tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as manifest_file:
            manifest_file.write(json.dumps(manifest_data, sort_keys=True) + '\\n')
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        os.replace(manifest_tmp, manifest_path)
        sync_parent(manifest_path)
    finally:
        if manifest_tmp.exists():
            manifest_tmp.unlink()

backup_artifacts_exist = (
    backup_path.exists() or backup_path.is_symlink() or manifest_path.exists() or manifest_path.is_symlink()
)
if dest_path.is_symlink():
    reject_backup('destination is a symbolic link')
if backup_artifacts_exist:
    backup_info = authentic_real(backup_path)
    dest_info = authentic_real(dest_path)
    if manifest_matches(backup_info):
        if dest_info is not None and not same_file(dest_info, backup_info):
            reject_backup('current real destination differs from the trusted backup')
    elif backup_info is not None and dest_info is not None and same_file(dest_info, backup_info) and not manifest_path.is_symlink():
        publish_manifest(*backup_info)
        print(f'repaired trusted backup manifest {manifest}', file=sys.stderr)
    else:
        reject_backup('existing backup or trusted manifest is missing, invalid, or incomplete')
    print(f'reusing trusted backup {backup}', file=sys.stderr)
elif dest_path.exists():
    source_info = authentic_real(dest_path)
    if source_info is None:
        reject_backup('destination is not an authentic real ELF for first backup')
    source_stat, source_digest = source_info
    fd, backup_tmp_name = tempfile.mkstemp(prefix='.l1.exe.backup-', dir=str(backup_path.parent))
    os.close(fd)
    backup_tmp = pathlib.Path(backup_tmp_name)
    try:
        shutil.copy2(dest_path, backup_tmp)
        os.chmod(backup_tmp, stat.S_IMODE(source_stat.st_mode))
        with backup_tmp.open('r+b') as backup_file:
            os.fsync(backup_file.fileno())
        backup_info = authentic_real(backup_tmp)
        if not same_file(source_info, backup_info):
            reject_backup('temporary backup does not match an authentic source ELF')
        os.replace(backup_tmp, backup_path)
        sync_parent(backup_path)
        publish_manifest(*backup_info)
        print(f'backed up {dest} -> {backup}', file=sys.stderr)
        print(f'recorded trusted backup manifest {manifest}', file=sys.stderr)
    finally:
        if backup_tmp.exists():
            backup_tmp.unlink()

expected = hashlib.sha256(src_data).hexdigest()
existing_mode = stat.S_IMODE(dest_path.stat().st_mode) if dest_path.exists() else 0o755
desired_mode = existing_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
fd, tmp_name = tempfile.mkstemp(prefix='.l1.exe.install-', dir=str(dest_path.parent))
tmp_path = pathlib.Path(tmp_name)
try:
    with os.fdopen(fd, 'wb') as tmp_file:
        tmp_file.write(src_data)
        tmp_file.flush()
        os.fchmod(tmp_file.fileno(), desired_mode)
        os.fsync(tmp_file.fileno())
    tmp_data = tmp_path.read_bytes()
    digest = hashlib.sha256(tmp_data).hexdigest()
    if tmp_data != src_data or digest != expected:
        print(f'install verification failed: temporary {dest} content differs from source', file=sys.stderr)
        sys.exit(2)
    os.replace(tmp_path, dest_path)
    sync_parent(dest_path)
finally:
    if tmp_path.exists():
        tmp_path.unlink()
print(f'mock installed at {dest} ({len(src_data)} bytes) sha256={expected}')
"""

REMOTE_RESTORE_PY = """\
import hashlib, json, os, pathlib, shutil, stat, sys, tempfile

dest = '/opt/g16/l1.exe'
backup = '/opt/g16/l1.exe.real'
manifest = '/opt/g16/l1.exe.real.jobdesk.json'
dest_path = pathlib.Path(dest)
backup_path = pathlib.Path(backup)
manifest_path = pathlib.Path(manifest)

def reject(reason):
    print(f'unsafe backup {backup}: {reason}; {dest} was not modified', file=sys.stderr)
    sys.exit(3)

def sync_parent(path):
    if os.name != 'posix':
        return
    directory_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

if backup_path.is_symlink():
    reject('backup is a symlink')
if not backup_path.exists():
    reject('backup is missing')
backup_stat = backup_path.lstat()
if not stat.S_ISREG(backup_stat.st_mode):
    reject('backup is not a regular file')
if backup_stat.st_size < 1048576:
    reject('backup is smaller than the 1 MiB safety floor')
try:
    backup_data = backup_path.read_bytes()
except OSError as exc:
    reject(f'backup is unreadable: {exc}')
backup_head = backup_data[:4096]
if b'JOBDESK_MOCK' in backup_head:
    reject('backup contains the JOBDESK_MOCK marker')
if backup_head.startswith(b'#!'):
    reject('backup is a script')
if not backup_head.startswith(b'\\x7fELF'):
    reject('backup is not an ELF executable')
if manifest_path.is_symlink() or not manifest_path.is_file():
    reject('trusted backup manifest is missing or unsafe')
try:
    metadata = json.loads(manifest_path.read_text(encoding='utf-8'))
except (OSError, ValueError) as exc:
    reject(f'trusted backup manifest is invalid: {exc}')
digest = hashlib.sha256(backup_data).hexdigest()
if (
    metadata.get('version') != 1
    or metadata.get('source') != dest
    or metadata.get('backup') != backup
    or metadata.get('size') != backup_stat.st_size
    or metadata.get('mode') != stat.S_IMODE(backup_stat.st_mode)
    or metadata.get('sha256') != digest
):
    reject('trusted backup manifest does not match the backup')

fd, tmp_name = tempfile.mkstemp(prefix='.l1.exe.restore-', dir=str(dest_path.parent))
os.close(fd)
tmp_path = pathlib.Path(tmp_name)
try:
    shutil.copy2(backup_path, tmp_path)
    os.chmod(tmp_path, stat.S_IMODE(backup_stat.st_mode))
    with tmp_path.open('r+b') as tmp_file:
        tmp_file.flush()
        os.fsync(tmp_file.fileno())
    copied_stat = tmp_path.lstat()
    copied_digest = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
    if copied_digest != digest or stat.S_IMODE(copied_stat.st_mode) != stat.S_IMODE(backup_stat.st_mode):
        reject('temporary restore copy checksum differs from the validated backup')
    os.replace(tmp_path, dest_path)
    sync_parent(dest_path)
finally:
    if tmp_path.exists():
        tmp_path.unlink()
st = dest_path.stat()
digest = hashlib.sha256(dest_path.read_bytes()).hexdigest()
expected = hashlib.sha256(backup_path.read_bytes()).hexdigest()
if digest != expected:
    print(f'restore verification failed: {dest} checksum differs from {backup}', file=sys.stderr)
    sys.exit(2)
print(f'restored {dest} from {backup} ({st.st_size} bytes) sha256={digest}')
"""

REMOTE_PROBE_BACKUP_PY = """\
import hashlib, json, pathlib, stat, sys

dest = '/opt/g16/l1.exe'
backup = '/opt/g16/l1.exe.real'
manifest = '/opt/g16/l1.exe.real.jobdesk.json'
backup_path = pathlib.Path(backup)
manifest_path = pathlib.Path(manifest)

if backup_path.is_symlink():
    print('SYMLINK'); sys.exit(0)
if not backup_path.exists():
    print('MISSING'); sys.exit(0)
try:
    backup_stat = backup_path.lstat()
    if not stat.S_ISREG(backup_stat.st_mode):
        print('NOT_REGULAR'); sys.exit(0)
    if backup_stat.st_size < 1048576:
        print('SMALL'); sys.exit(0)
    backup_data = backup_path.read_bytes()
except OSError as exc:
    print(f'UNREADABLE:{exc}', file=sys.stderr); sys.exit(2)
head = backup_data[:4096]
if b'JOBDESK_MOCK' in head:
    print('MOCK'); sys.exit(0)
if head.startswith(b'#!'):
    print('SCRIPT'); sys.exit(0)
if not head.startswith(b'\\x7fELF'):
    print('NON_ELF'); sys.exit(0)
if manifest_path.is_symlink() or not manifest_path.is_file():
    print('MANIFEST_MISSING'); sys.exit(0)
try:
    metadata = json.loads(manifest_path.read_text(encoding='utf-8'))
except (OSError, ValueError):
    print('MANIFEST_INVALID'); sys.exit(0)
digest = hashlib.sha256(backup_data).hexdigest()
if (
    metadata.get('version') != 1
    or metadata.get('source') != dest
    or metadata.get('backup') != backup
    or metadata.get('size') != backup_stat.st_size
    or metadata.get('mode') != stat.S_IMODE(backup_stat.st_mode)
    or metadata.get('sha256') != digest
):
    print('HASH_MISMATCH'); sys.exit(0)
print('SAFE')
"""

# Verify the actual WSL destination after either operation. The expected
# digest and whether the JOBDESK_MOCK marker is required are supplied on
# stdin, so the check does not trust local SOURCE bytes or a stale path.
REMOTE_VERIFY_PY = """\
import hashlib, pathlib, sys

lines = sys.stdin.read().splitlines()
expected = lines[0].strip() if lines else ''
require_mock = len(lines) > 1 and lines[1].strip() == '1'
p = pathlib.Path('/opt/g16/l1.exe')
try:
    data = p.read_bytes()
except OSError as exc:
    print(f'UNREADABLE:{exc}', file=sys.stderr)
    sys.exit(2)
digest = hashlib.sha256(data).hexdigest()
if expected and digest != expected:
    print(f'checksum mismatch: expected {expected}, got {digest}', file=sys.stderr)
    sys.exit(3)
if require_mock and b'JOBDESK_MOCK' not in data:
    print('sentinel missing from installed mock', file=sys.stderr)
    sys.exit(4)
print(f'verified /opt/g16/l1.exe size={len(data)} sha256={digest}')
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

# Probe /opt/g16/l1.exe (the file the mock will overwrite). Returns one of:
#   MOCK    - l1.exe is JOBDESK_MOCK-tainted (already polluted by a prior run)
#   SHELL   - l1.exe is a #!/bin/sh script (anomalous — real l1.exe is ELF)
#   SMALL   - l1.exe exists and is small (< L1_SIZE_FLOOR) — likely mock-sized
#   REAL    - l1.exe exists and is >= L1_SIZE_FLOOR — probably the real binary
#   SYMLINK - l1.exe is a symbolic link, including a dangling one (unsafe)
#   MISSING - l1.exe does not exist (fresh install path)
REMOTE_PROBE_L1_PY = """\
import os, pathlib, sys
p = pathlib.Path('/opt/g16/l1.exe')
if p.is_symlink():
    print('SYMLINK')
    sys.exit(0)
if not p.exists():
    print('MISSING')
    sys.exit(0)
size = p.stat().st_size
try:
    head = p.open('rb').read(4096)
except OSError as exc:
    print(f'UNREADABLE:{exc}', file=sys.stderr)
    sys.exit(2)
if b'JOBDESK_MOCK' in head:
    print('MOCK')
elif head.startswith(b'#!') and b'/bin/sh' in head.split(b'\\n', 1)[0]:
    print('SHELL')
elif size < 1048576:
    print('SMALL')
else:
    print('REAL')
"""

# Anything >= 1 MiB is treated as "too big to safely overwrite with a 3 KB mock".
# Real Gaussian's l1.exe is ~31 MB. A genuine mock should never approach this.
L1_SIZE_FLOOR = 1_048_576  # 1 MiB


def stream(py_template: str, payload: bytes | None) -> subprocess.CompletedProcess[bytes]:
    encoded = base64.b64encode(payload).decode("ascii") if payload else ""
    py_quoted = "'" + py_template.replace("'", "'\"'\"'") + "'"
    cmd = ["wsl", "bash", "-c", f"python3 -u -c {py_quoted}"]
    return subprocess.run(
        cmd, input=encoded.encode("ascii"), capture_output=True, check=False,
    )


def _probe_status(proc: subprocess.CompletedProcess[bytes], allowed: set[str]) -> str:
    """Normalize a remote probe result, failing closed on execution errors."""
    if proc.returncode != 0:
        return "ERROR"
    status = proc.stdout.decode("utf-8", errors="replace").strip() or "MISSING"
    return status if status in allowed else "ERROR"


def probe_wrapper() -> str:
    """Probe ``/opt/g16/g16`` and return a safe, known status.

    The mock install must refuse to overwrite ``/opt/g16/l1.exe`` if the
    upstream wrapper at ``/opt/g16/g16`` is already a JOBDESK_MOCK-tainted
    shell script. That's the Phase 6 foot-gun we don't want to repeat.
    """
    try:
        proc = stream(REMOTE_PROBE_PY, None)
    except OSError as exc:
        print(f"wrapper probe failed: {exc}", file=sys.stderr)
        return "ERROR"
    return _probe_status(proc, {"BINARY", "SHELL", "MOCK", "MISSING"})


def probe_l1() -> str:
    """Probe ``/opt/g16/l1.exe`` and return a safe, known status.

    Guards against a second class of pollution: if l1.exe is already
    JOBDESK_MOCK-tainted (a prior mock run that wasn't restored), refuse to
    re-overwrite. Also flags ``REAL`` (>= 1 MiB ELF) so the caller can warn
    loudly before clobbering what is almost certainly the real binary.
    """
    try:
        proc = stream(REMOTE_PROBE_L1_PY, None)
    except OSError as exc:
        print(f"l1.exe probe failed: {exc}", file=sys.stderr)
        return "ERROR"
    return _probe_status(proc, {"MOCK", "SHELL", "SMALL", "REAL", "SYMLINK", "MISSING"})


def probe_backup() -> str:
    """Validate the restore backup and its trusted manifest without mutation."""
    try:
        proc = stream(REMOTE_PROBE_BACKUP_PY, None)
    except OSError as exc:
        print(f"backup probe failed: {exc}", file=sys.stderr)
        return "ERROR"
    return _probe_status(
        proc,
        {
            "SAFE",
            "MISSING",
            "SYMLINK",
            "NOT_REGULAR",
            "SMALL",
            "SCRIPT",
            "MOCK",
            "NON_ELF",
            "MANIFEST_MISSING",
            "MANIFEST_INVALID",
            "HASH_MISMATCH",
        },
    )


def _extract_sha256(text: str) -> str | None:
    match = re.search(r"(?:^|\s)sha256=([0-9a-fA-F]{64})(?:\s|$)", text)
    return match.group(1).lower() if match else None


def _extract_size(text: str) -> int | None:
    match = re.search(r"(?:^|\s)size=(\d+)(?:\s|$)", text)
    return int(match.group(1)) if match else None


def audit_log(action: str, dest: str, size: int, sha256: str | None = None) -> None:
    """Append one JSON line to ``~/.jobdesk-mock-l1.log`` for post-mortem traceability.

    Records action, dest path, byte size, ISO timestamp, and the SHA-256 of
    the verified WSL destination when supplied by ``main``. The local SOURCE
    hash is retained only as a backwards-compatible fallback for direct
    callers that do not have a remote digest.
    Failures are non-fatal — we never want audit logging to block the install.
    """
    import datetime as _dt
    import json as _json
    import os as _os

    entry: dict[str, object] = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "action": action,
        "dest": dest,
        "size": size,
    }
    try:
        if sha256:
            # The operation/verification scripts compute this digest on the
            # WSL side after writing the destination. Keep the value itself in
            # the audit record; do not claim that a local checksum file exists.
            entry["sha256"] = sha256
            entry["sha256_source"] = (
                "wsl-restored-destination" if action == "restore" else "wsl-installed-destination"
            )
        elif action == "install":
            entry["sha256"] = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
            entry["sha256_source"] = "local-source-fallback"
        elif action == "restore":
            entry["hash_error"] = "remote restore checksum unavailable"
    except OSError as exc:
        entry["hash_error"] = str(exc)

    log_path = Path(_os.path.expanduser("~")) / ".jobdesk-mock-l1.log"
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"WARNING: audit log write failed: {exc}", file=sys.stderr)


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
            print("WARNING: --yes given, proceeding despite tainted wrapper", file=sys.stderr)
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
        elif wrapper_kind == "ERROR":
            print(
                f"ERROR: unable to safely probe {WSL_WRAPPER}; refusing to install.",
                file=sys.stderr,
            )
            return 6

        # Second-layer safety: probe the l1.exe we are about to overwrite.
        #   MOCK  -> already polluted, refuse to re-overwrite (would mask
        #            the prior run's contents).
        #   REAL  -> l1.exe is >= 1 MiB and almost certainly the real binary;
        #            refuse without --yes (mirrors the wrapper MOCK gate).
        #   SHELL -> l1.exe is a shell script (anomalous; real l1.exe is ELF);
        #            warn loudly but proceed.
        #   SMALL -> l1.exe exists but is mock-sized; safe to overwrite.
        #   SYMLINK -> unsafe indirection (including dangling links); refuse.
        #   MISSING -> fresh install path.
        l1_kind = probe_l1()
        if l1_kind == "MOCK":
            msg = (
                f"REFUSING to install mock l1.exe: {WSL_DEST} is already "
                "JOBDESK_MOCK-tainted. A prior mock install was not restored; "
                "run --restore first, or pass --yes to force-overwrite."
            )
            if not args.yes:
                print(msg, file=sys.stderr)
                return 4
            print("WARNING: --yes given, overwriting already-mock l1.exe", file=sys.stderr)
        elif l1_kind == "REAL":
            msg = (
                f"REFUSING to install mock l1.exe: {WSL_DEST} appears to be the "
                f"real Gaussian l1.exe (>= {L1_SIZE_FLOOR // 1048576} MiB). "
                "If this is intentional, pass --yes to force-overwrite."
            )
            if not args.yes:
                print(msg, file=sys.stderr)
                return 5
            print(f"WARNING: --yes given, overwriting {WSL_DEST} (real-sized)", file=sys.stderr)
        elif l1_kind == "SHELL":
            print(
                f"WARNING: {WSL_DEST} is a shell script but not JOBDESK_MOCK-tagged. "
                "Real l1.exe is an ELF binary; this looks anomalous.",
                file=sys.stderr,
            )
        elif l1_kind == "SYMLINK":
            print(
                f"REFUSING to install mock l1.exe: {WSL_DEST} is a symbolic link; "
                "replace it with an explicitly validated regular file first.",
                file=sys.stderr,
            )
            return 7
        elif l1_kind == "MISSING":
            print(
                f"INFO: {WSL_DEST} does not exist on the WSL side; first mock install.",
                file=sys.stderr,
            )
        elif l1_kind == "ERROR":
            print(
                f"ERROR: unable to safely probe {WSL_DEST}; refusing to install.",
                file=sys.stderr,
            )
            return 7

        backup_kind = probe_backup()
        repairable_manifest_states = {"MANIFEST_MISSING", "MANIFEST_INVALID", "HASH_MISMATCH"}
        if backup_kind in repairable_manifest_states:
            print(
                f"WARNING: {WSL_BACKUP} has backup status {backup_kind}; "
                "the remote installer will attempt safe repair only if the backup "
                f"exactly matches the current authentic REAL {WSL_DEST}.",
                file=sys.stderr,
            )
        elif backup_kind not in {"SAFE", "MISSING"}:
            print(
                f"REFUSING install: {WSL_BACKUP} failed authenticity validation "
                f"({backup_kind}); {WSL_DEST} was not modified.",
                file=sys.stderr,
            )
            return 8

    if args.restore:
        backup_kind = probe_backup()
        if backup_kind != "SAFE":
            print(
                f"REFUSING restore: {WSL_BACKUP} failed authenticity validation "
                f"({backup_kind}); {WSL_DEST} was not modified.",
                file=sys.stderr,
            )
            return 8

    try:
        if args.restore:
            proc = stream(REMOTE_RESTORE_PY, None)
        else:
            proc = stream(REMOTE_INSTALL_PY, SOURCE.read_bytes())
    except OSError as exc:
        print(f"wsl operation failed: {exc}", file=sys.stderr)
        return 1

    out = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    if out:
        print(out, end="")
    if err:
        print(err, end="", file=sys.stderr)
    if proc.returncode != 0:
        print(f"wsl install exit={proc.returncode}", file=sys.stderr)
        return proc.returncode

    # The restore script reports the hash of the copied backup. Use that as
    # the expected value for the independent destination verification.
    restore_sha256: str | None = None
    if args.restore:
        restore_sha256 = _extract_sha256(out)
        if restore_sha256 is None:
            print("restore succeeded but did not report a checksum; refusing to audit", file=sys.stderr)
            return 1
        expected_sha256 = restore_sha256
    else:
        try:
            expected_sha256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        except OSError as exc:
            print(f"cannot hash install source: {exc}", file=sys.stderr)
            return 1

    verify_script = "'" + REMOTE_VERIFY_PY.replace("'", "'\"'\"'") + "'"
    verify_input = f"{expected_sha256}\n{0 if args.restore else 1}\n".encode("ascii")
    try:
        verify = subprocess.run(
            ["wsl", "bash", "-c", f"python3 -u -c {verify_script}"],
            input=verify_input, capture_output=True, check=False,
        )
    except OSError as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    verify_out = verify.stdout.decode("utf-8", errors="replace")
    verify_err = verify.stderr.decode("utf-8", errors="replace")
    if verify_out:
        print(verify_out, end="")
    if verify.returncode != 0:
        if verify_err:
            print(verify_err, end="", file=sys.stderr)
        print(f"verification failed (exit={verify.returncode})", file=sys.stderr)
        return verify.returncode or 1

    verified_sha256 = _extract_sha256(verify_out)
    if verified_sha256 != expected_sha256:
        print("verification succeeded without the expected destination checksum", file=sys.stderr)
        return 1
    verified_size = _extract_size(verify_out)
    if verified_size is None:
        print("verification succeeded without the destination size", file=sys.stderr)
        return 1

    # Audit-log entry is written only after the remote hash/sentinel
    # verification succeeds, and always records the verified WSL digest.
    audit_log(
        "restore" if args.restore else "install",
        WSL_DEST,
        verified_size,
        sha256=verified_sha256,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
