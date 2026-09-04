"""Immutable identity helpers for the approved remote ConfFlow executable."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_STAT_SCRIPT = (
    "import os, sys; stat_result = os.stat(sys.argv[1]); "
    "print(f'{stat_result.st_size}|{stat_result.st_mtime_ns}|{stat_result.st_dev}|{stat_result.st_ino}')"
)


@dataclass(frozen=True)
class ConfFlowExecutableIdentity:
    """A point-in-time identity snapshot for one remote executable."""

    path: str
    realpath: str
    sha256: str
    python: str
    size: int
    mtime_ns: int
    device: int
    inode: int

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "realpath": self.realpath,
            "sha256": self.sha256,
            "python": self.python,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "device": self.device,
            "inode": self.inode,
        }


def build_executable_identity_probe(path: str, python_executable: str) -> str:
    """Build a shell probe that emits realpath, stat tuple, and SHA-256."""
    path_q = _quote_path(path)
    python_q = _quote_path(python_executable)
    stat_command = f'{python_q} -c {shlex.quote(_STAT_SCRIPT)} "$_jobdesk_identity_path"'
    return "\n".join(
        [
            "set +e",
            f"_jobdesk_identity_path={path_q}",
            'if ! _jobdesk_identity_realpath=$(readlink -f -- "$_jobdesk_identity_path"); then exit 125; fi',
            f"if ! _jobdesk_identity_stat=$({stat_command}); then exit 125; fi",
            "if ! _jobdesk_identity_sha=$(sha256sum -- \"$_jobdesk_identity_path\" | awk '{print $1}'); then exit 125; fi",
            'printf "%s\\n%s\\n%s\\n" "$_jobdesk_identity_realpath" "$_jobdesk_identity_stat" "$_jobdesk_identity_sha"',
        ]
    )


def parse_executable_identity_probe(
    stdout: str,
    *,
    path: str,
    python_executable: str,
) -> ConfFlowExecutableIdentity:
    """Parse the three-line output of :func:`build_executable_identity_probe`."""
    lines = stdout.splitlines()
    if len(lines) != 3 or any(not line for line in lines):
        raise ValueError("ConfFlow executable identity probe returned malformed output")
    realpath, stat_value, sha256 = lines
    stat_parts = stat_value.split("|")
    if len(stat_parts) != 4:
        raise ValueError("ConfFlow executable identity stat tuple is malformed")
    try:
        size, mtime_ns, device, inode = (int(value) for value in stat_parts)
    except ValueError as exc:
        raise ValueError("ConfFlow executable identity stat tuple is not numeric") from exc
    if min(size, mtime_ns, device, inode) < 0:
        raise ValueError("ConfFlow executable identity stat tuple contains a negative value")
    if _SHA256_RE.fullmatch(sha256) is None:
        raise ValueError("ConfFlow executable identity digest is malformed")
    return ConfFlowExecutableIdentity(
        path=path,
        realpath=realpath,
        sha256=sha256.lower(),
        python=python_executable,
        size=size,
        mtime_ns=mtime_ns,
        device=device,
        inode=inode,
    )


def identity_matches(expected: ConfFlowExecutableIdentity, actual: ConfFlowExecutableIdentity) -> bool:
    """Return whether two snapshots identify the same immutable file."""
    return expected == actual


def build_executable_identity_guard(identity: ConfFlowExecutableIdentity, task_id: str) -> list[str]:
    """Return shell lines that fail a runner before invoking ConfFlow."""
    path_q = _quote_path(identity.path)
    realpath_q = _quote_path(identity.realpath)
    expected_stat = f"{identity.size}|{identity.mtime_ns}|{identity.device}|{identity.inode}"
    expected_stat_q = shlex.quote(expected_stat)
    expected_sha_q = shlex.quote(identity.sha256)
    expected_python_q = _quote_path(identity.python)
    stat_command = f'$_jobdesk_expected_python -c {shlex.quote(_STAT_SCRIPT)} "$_jobdesk_expected_path"'
    task_q = shlex.quote(task_id)
    return [
        "# JobDesk: immutable ConfFlow executable identity guard",
        f"_jobdesk_expected_path={path_q}",
        f"_jobdesk_expected_realpath={realpath_q}",
        f"_jobdesk_expected_stat={expected_stat_q}",
        f"_jobdesk_expected_sha256={expected_sha_q}",
        f"_jobdesk_expected_python={expected_python_q}",
        "_jobdesk_identity_error=0",
        'if ! _jobdesk_current_realpath=$(readlink -f -- "$_jobdesk_expected_path"); then _jobdesk_identity_error=1; fi',
        f"if ! _jobdesk_current_stat=$({stat_command}); then _jobdesk_identity_error=1; fi",
        "if ! _jobdesk_current_sha256=$(sha256sum -- \"$_jobdesk_expected_path\" | awk '{print $1}'); then _jobdesk_identity_error=1; fi",
        'if [ "$_jobdesk_identity_error" -ne 0 ] || [ "$_jobdesk_current_realpath" != "$_jobdesk_expected_realpath" ] || [ "$_jobdesk_current_stat" != "$_jobdesk_expected_stat" ] || [ "$_jobdesk_current_sha256" != "$_jobdesk_expected_sha256" ]; then',
        f'  printf "%s\\n" "ConfFlow executable identity mismatch for {task_q}" > .jobdesk_submit.log',
        "  echo 126 > .jobdesk_exit_code",
        "  echo 'failed' > .jobdesk_status",
        f'  printf "%s\\n" "DONE {task_id} 126" >> ../_batch/events.log',
        "  exit 126",
        "fi",
    ]


def _quote_path(value: str) -> str:
    if not isinstance(value, str) or not value or any(char in value for char in "\x00\r\n"):
        raise ValueError("ConfFlow executable path must be a non-empty single-line string")
    return shlex.quote(value)
