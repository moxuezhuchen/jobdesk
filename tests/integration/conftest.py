"""Phase 3A integration test configuration.

Adds ``src/jobdesk_app/confflow`` to sys.path so that the confflow
subtree is importable as ``confflow.*`` from the jobdesk root, matching
how the confflow subtree tests import the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

# src/jobdesk_app/confflow/confflow/  ← confflow package lives here
_CONFFLOW_ROOT = (
    Path(__file__).resolve().parent.parent.parent  # tests/integration → tests → repo root
    / "src"
    / "jobdesk_app"
    / "confflow"
)
if str(_CONFFLOW_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFFLOW_ROOT))
