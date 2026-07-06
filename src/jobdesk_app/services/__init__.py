"""Service layer for JobDesk.

Public re-exports are intentionally limited to a stable set so callers can
do ``from jobdesk_app.services import RunCoordinator``.
"""

from __future__ import annotations

from .multi_server_coordinator import (
    CancelResult,
    MultiServerCoordinator,
    RefreshResult,
)
from .run_coordinator import RunCoordinator, RunOperationOutcome
from .run_repository import RunRecord
from .run_service import RunService
from .session_pool import SessionLease, SessionPool

__all__ = [
    "CancelResult",
    "MultiServerCoordinator",
    "RefreshResult",
    "RunCoordinator",
    "RunOperationOutcome",
    "RunRecord",
    "RunService",
    "SessionLease",
    "SessionPool",
]
