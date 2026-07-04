"""Cross-platform path comparison utilities."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def paths_equal(a: Path, b: Path) -> bool:
    """Return True if two paths are equivalent across platforms.

    On Windows, comparison is case-insensitive and slash-insensitive.
    On POSIX, comparison is strict lexical equality.
    """
    if sys.platform == "win32":
        return os.path.normcase(str(a)) == os.path.normcase(str(b))
    return a == b
