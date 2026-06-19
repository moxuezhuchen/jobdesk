from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

_REPLACE_RETRY_DELAYS = (0.01, 0.05, 0.1)


def atomic_write_text(path: Path, content: str, *, encoding: str = "utf-8", newline: str | None = None) -> None:
    """Write text through a unique sibling temporary file and atomically replace the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline=newline,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_permission_retries(tmp_path, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except (OSError, AttributeError):
            pass  # directory fsync unsupported (e.g. Windows); replace is durable enough there
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _replace_with_permission_retries(tmp_path: Path, path: Path) -> None:
    for delay in _REPLACE_RETRY_DELAYS:
        try:
            tmp_path.replace(path)
            return
        except PermissionError:
            time.sleep(delay)
    tmp_path.replace(path)
