from __future__ import annotations

import posixpath
import shlex


def cleanup_remote_test_dir(ssh, remote_dir: str, remote_root: str) -> None:
    """Remove only an isolated child beneath the configured integration-test root."""
    root = posixpath.normpath(remote_root)
    target = posixpath.normpath(remote_dir)
    if root in {"/", "/tmp", "/home", "/root"}:
        raise ValueError(f"remote test root is too broad for cleanup: {remote_root}")
    if target == root or not target.startswith(root.rstrip("/") + "/"):
        raise ValueError(f"remote test cleanup target is outside root: {remote_dir}")
    ssh.run(f"rm -rf -- {shlex.quote(target)}", check=True)
