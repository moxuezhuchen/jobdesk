"""Submit operations for run_service."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunPlan, RunSpec, build_run_plan, remote_run_dir
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.services.file_transfer_service import ensure_safe_remote_path
from jobdesk_app.services.run_repository import (
    MigrationError,
    OperationRecord,
    RunRecord,
    RunRepository,
    _lexical_absolute,
    _reject_reparse_chain,
)
from jobdesk_app.services.submit_ownership import (
    SUBMIT_HEARTBEAT_INTERVAL,
    SUBMIT_LEASE_SECONDS,
    _CheckpointSink,
    _SubmitOwnershipGuard,
)

from ._helpers import _scheduler_type, _status_summary, _tasks_from_plan


def submit_run(
    service,
    run_id: str,
    ssh,
    sftp,
    env_init_scripts: list[str] | None = None,
    scheduler=None,
    resources=None,
) -> SubmitResult:
    """Submit a run's tasks to the remote cluster.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    # Lazy import so tests can monkeypatch jobdesk_app.services.run_service.JobSubmitter
    import jobdesk_app.services.run_service as _rs

    JobSubmitter = _rs.JobSubmitter
    from jobdesk_app.remote.scheduler import ResourceSpec, make_adapter

    record = service.load_run(run_id)

    if env_init_scripts is None:
        env_init_scripts = list(record.env_init_scripts)
    else:
        record.env_init_scripts = list(env_init_scripts)
    if scheduler is None:
        scheduler = make_adapter(record.scheduler_type)
    else:
        record.scheduler_type = _scheduler_type(scheduler)
    if resources is None:
        resources = ResourceSpec.from_dict(record.resources)
    else:
        record.resources = asdict(resources)
    scheduler_type = _scheduler_type(scheduler)
    owner_id = str(uuid4())
    lease_seconds = SUBMIT_LEASE_SECONDS
    tasks, operations = service.repository.claim_submit_tasks(
        run_id,
        scheduler_type=scheduler_type,
        resources=asdict(resources),
        env_init_scripts=list(env_init_scripts),
        per_task=scheduler_type != "nohup",
        owner_id=owner_id,
        lease_seconds=lease_seconds,
    )
    if not tasks:
        return SubmitResult(record.run_id, 0, remote_run_dir(record.remote_dir, record.run_id))
    primary_error: Exception | None = None
    recovery_diagnostics: list[str] = []
    release_diagnostics: list[str] = []

    try:
        with _SubmitOwnershipGuard(
            service.repository,
            [op.operation_id for op in operations],
            owner_id,
            lease_seconds=lease_seconds,
        ) as guard:
            operation_by_task: dict[str, OperationRecord] = {}
            for operation in operations:
                task_ids = operation.payload.get("task_ids")
                if not isinstance(task_ids, list):
                    raise RuntimeError(
                        f"submit operation has invalid task ids: {operation.operation_id}"
                    )
                for task_id in task_ids:
                    operation_by_task[str(task_id)] = operation

            sink = _CheckpointSink(
                repository=service.repository,
                guard=guard,
                operation_by_task=operation_by_task,
            )

            service.repository.update_run(record)
            # Lazy import so that tests can monkeypatch JobSubmitter on the module
            submitter = JobSubmitter(
                tasks=tasks,
                ssh=ssh,
                sftp=sftp,
                max_parallel=record.max_parallel,
                remote_batch_dir=remote_run_dir(record.remote_dir, record.run_id),
                batch_id=record.run_id,
                env_init_scripts=list(env_init_scripts),
                scheduler=scheduler,
                resources=resources,
                task_update_callback=sink.update_tasks,
                remote_started_callback=sink.mark_remote_started,
            )
            result = submitter.submit_batch()
    except Exception as exc:
        primary_error = exc
        guard.stop_heartbeat()
        for operation in operations:
            try:
                service.repository.recover_submit_operation(
                    operation.operation_id, owner_id=owner_id
                )
            except Exception as recovery_exc:
                recovery_diagnostics.append(
                    f"submit recovery failed for {operation.operation_id}: "
                    f"{type(recovery_exc).__name__}: {recovery_exc}"
                )
        raise
    finally:
        guard.stop_heartbeat()
        for operation in operations:
            try:
                service.repository.release_claimed_submit_operation(
                    operation.operation_id, owner_id=owner_id
                )
            except Exception as release_exc:
                release_diagnostics.append(
                    f"submit claim release failed for {operation.operation_id}: "
                    f"{type(release_exc).__name__}: {release_exc}"
                )

        incomplete_ids: set[str] = set()
        try:
            incomplete_ids = {
                operation.operation_id
                for operation in service.repository.list_operations(incomplete_only=True)
            }
        except Exception as inspection_exc:
            release_diagnostics.append(
                "submit cleanup state inspection failed: "
                f"{type(inspection_exc).__name__}: {inspection_exc}"
            )
        for operation in operations:
            if operation.operation_id in incomplete_ids:
                release_diagnostics.append(
                    "submit recovery left operation incomplete: "
                    f"{operation.operation_id}"
                )

        cleanup_diagnostics = recovery_diagnostics + release_diagnostics
        if primary_error is not None:
            for diagnostic in cleanup_diagnostics:
                primary_error.add_note(diagnostic)
        elif cleanup_diagnostics:
            raise RuntimeError(
                "submit cleanup failed: " + "; ".join(cleanup_diagnostics)
            )
    return result


def recover_submit_operations(service, run_id: str | None = None) -> int:
    """Recover incomplete submit operations.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    recovered = (
        service.repository.recover_legacy_orphan_submit_tasks()
        if run_id is None
        else 0
    )
    for operation in service.repository.list_operations(incomplete_only=True):
        recovery_owner = str(uuid4())
        if (
            operation.kind == "submit"
            and (run_id is None or operation.run_id == run_id)
            and service.repository.acquire_submit_recovery(
                operation.operation_id, recovery_owner
            )
            and service.repository.recover_submit_operation(
                operation.operation_id, owner_id=recovery_owner
            )
        ):
            recovered += 1
    service.repository.prune_completed_operations(datetime.now() - timedelta(days=7))
    return recovered
