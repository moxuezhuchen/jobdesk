"""Submit ownership guard and checkpoint sink for run submission.

Extracts the heartbeat/lease-renewal logic and checkpoint callbacks from
`RunService.submit_run` into reusable components.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..core.lifecycle import TaskStatus
from ..core.manifest import TaskRecord
from .run_repository import OperationRecord, RunRepository

SUBMIT_LEASE_SECONDS = 60.0
SUBMIT_HEARTBEAT_INTERVAL = SUBMIT_LEASE_SECONDS / 3


@dataclass
class _SubmitOwnershipGuard:
    """Context manager: holds submit ownership leases and renews them via heartbeat.

    Usage:
        with _SubmitOwnershipGuard(repository, operation_ids, owner_id) as guard:
            # guard.is_lost() — True if any lease was lost
            # guard.renew()   — manually renew all leases
            ...
        # __exit__ stops the heartbeat thread
    """

    repository: RunRepository
    operation_ids: list[str]
    owner_id: str
    lease_seconds: float = SUBMIT_LEASE_SECONDS

    _stop_heartbeat: threading.Event = field(default_factory=threading.Event, init=False)
    _lost: bool = field(default=False, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)

    def renew(self) -> bool:
        """Renew all owned leases. Returns False if any renewal failed (lease lost)."""
        for op_id in self.operation_ids:
            ok = self.repository.renew_submit_lease(op_id, self.owner_id, lease_seconds=self.lease_seconds)
            if not ok:
                self._lost = True
                return False
        return True

    def renew_one(self, operation_id: str) -> bool:
        """Renew a single operation's lease. Returns False if renewal failed."""
        ok = self.repository.renew_submit_lease(operation_id, self.owner_id, lease_seconds=self.lease_seconds)
        if not ok:
            self._lost = True
        return ok

    def is_lost(self) -> bool:
        return self._lost

    def __enter__(self) -> "_SubmitOwnershipGuard":
        # Lazy read of the interval so that tests can patch
        # run_service.SUBMIT_HEARTBEAT_INTERVAL before the thread starts.
        import jobdesk_app.services.run_service as _rs

        def heartbeat() -> None:
            while not self._stop_heartbeat.wait(_rs.SUBMIT_HEARTBEAT_INTERVAL):
                if not self.renew():
                    return

        self._thread = threading.Thread(target=heartbeat, name=f"submit-lease-{self.owner_id}", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args) -> None:
        self._stop_heartbeat.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def stop_heartbeat(self) -> None:
        self._stop_heartbeat.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)


@dataclass
class _CheckpointSink:
    """Wraps submit checkpoint callbacks, auto-renewing leases before each persistence call.

    Replaces the ``checkpoint_task_updates`` and ``checkpoint_remote_started`` closures
    from ``submit_run``.  Both methods raise ``RuntimeError`` if the lease was lost or
    could not be renewed.
    """

    repository: RunRepository
    guard: _SubmitOwnershipGuard
    operation_by_task: dict[str, OperationRecord]
    started_operation_ids: set[str] = field(default_factory=set)

    def update_tasks(self, updates: list[TaskRecord]) -> None:
        groups: dict[str, list[TaskRecord]] = {}
        for update in updates:
            operation = self.operation_by_task[update.task_id]
            groups.setdefault(operation.operation_id, []).append(update)

        for operation_id, changed in groups.items():
            if not self.guard.renew():
                raise RuntimeError(f"submit operation ownership lost: {operation_id}")
            error = next(
                (task.error_message for task in changed if task.status == TaskStatus.uncertain),
                None,
            )
            if not self.repository.finish_submit_operation(
                operation_id,
                task_ids=[task.task_id for task in changed],
                job_ids={task.task_id: task.remote_job_id for task in changed if task.remote_job_id is not None},
                error=error,
                owner_id=self.guard.owner_id,
            ):
                raise RuntimeError(f"submit operation could not finish: {operation_id}")

    def mark_remote_started(self, task_ids: list[str]) -> None:
        operations = {
            self.operation_by_task[task_id].operation_id: self.operation_by_task[task_id] for task_id in task_ids
        }
        for operation_id, operation in operations.items():
            if operation_id in self.started_operation_ids:
                continue
            if not self.guard.renew_one(operation.operation_id):
                raise RuntimeError(f"submit operation ownership lost: {operation.operation_id}")
            if not self.repository.start_submit_operation(operation.operation_id, owner_id=self.guard.owner_id):
                raise RuntimeError(f"submit operation could not start: {operation.operation_id}")
            self.started_operation_ids.add(operation_id)
