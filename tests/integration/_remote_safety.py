from __future__ import annotations

import posixpath
import re
import shlex

import pytest

pytestmark = pytest.mark.integration


def cleanup_remote_test_dir(ssh, remote_dir: str, remote_root: str) -> None:
    """Remove only an isolated child beneath the configured integration-test root."""
    root = posixpath.normpath(remote_root)
    target = posixpath.normpath(remote_dir)
    if root in {"/", "/tmp", "/home", "/root"}:
        raise ValueError(f"remote test root is too broad for cleanup: {remote_root}")
    if target == root or not target.startswith(root.rstrip("/") + "/"):
        raise ValueError(f"remote test cleanup target is outside root: {remote_dir}")
    ssh.run(f"rm -rf -- {shlex.quote(target)}", check=True)


def cleanup_remote_control_state(ssh, run_id: str) -> None:
    """Remove one control state proven to use JobDesk's generated run-id form."""
    if re.fullmatch(r"\d{6}-\d{3,}", run_id) is None:
        raise ValueError(f"invalid test run id for control-state cleanup: {run_id}")
    target = f"/root/.local/state/confflow/jobdesk-{run_id}"
    ssh.run(f"rm -rf -- {shlex.quote(target)}", check=True)
