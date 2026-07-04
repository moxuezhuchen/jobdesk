"""Path utilities for repository file operations."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def _lexical_absolute(path: Path) -> Path:
    """Make a path absolute without following links or reparse points."""
    return Path(os.path.abspath(path))


def _is_reparse_point(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(details, "st_file_attributes", 0))
    is_junction = getattr(path, "is_junction", None)
    return bool(
        stat.S_ISLNK(details.st_mode)
        or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or (is_junction is not None and is_junction())
    )


def _reject_reparse_chain(root: Path, path: Path) -> None:
    root = _lexical_absolute(root)
    path = _lexical_absolute(path)
    if not path.is_relative_to(root):
        raise ValueError(f"unsafe path outside managed root: {path}")
    current = root
    for part in path.relative_to(root).parts:
        if _is_reparse_point(current):
            raise ValueError(f"unsafe link or reparse point: {current}")
        current = current / part
    if _is_reparse_point(current):
        raise ValueError(f"unsafe link or reparse point: {current}")
