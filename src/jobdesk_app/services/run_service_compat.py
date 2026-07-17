"""Backward-compatibility shim.

``jobdesk_app.services.run_service`` is now the ``jobdesk_app.services.run_service``
sub-package. This module re-exports everything from that sub-package so that
existing imports (e.g. in tests and monkeypatch paths) continue to work without
modification.
"""

from __future__ import annotations

from jobdesk_app.services.run_service import (
    SUBMIT_HEARTBEAT_INTERVAL,
    JobSubmitter,
    RunService,
    _declared_outputs,
    _safe_declared_result_path,
    _scheduler_type,
    _status_summary,
    _tasks_from_plan,
)

__all__ = [
    "JobSubmitter",
    "RunService",
    "SUBMIT_HEARTBEAT_INTERVAL",
    "_declared_outputs",
    "_safe_declared_result_path",
    "_scheduler_type",
    "_status_summary",
    "_tasks_from_plan",
]
