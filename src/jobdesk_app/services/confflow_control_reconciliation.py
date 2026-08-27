"""Validation of the durable launcher marker used for response-loss recovery."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from jobdesk_app.application.confflow_client import ConfFlowClientError
from jobdesk_app.services.confflow_control_launcher import canonical_scheduler_type


@dataclass(frozen=True)
class LauncherMetadata:
    scheduler_type: str
    execution_state: str | None
    execute_rc: int | None
    worker_started: bool | None
    worker_rc: int | None
    scheduler_job_id: str | None


def parse_launcher_metadata(
    raw: bytes,
    *,
    run_id: str,
    state_locator: str,
    launcher: dict[str, object],
    scheduler_type: str,
) -> LauncherMetadata:
    try:
        marker = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfFlowClientError("control launcher metadata is malformed JSON") from exc
    if not isinstance(marker, dict):
        raise ConfFlowClientError("control launcher metadata must be a JSON object")
    if marker.get("content_schema") != "jobdesk.confflow.launcher.v1":
        raise ConfFlowClientError("control launcher metadata has an unsupported schema")
    if marker.get("run_id") != run_id:
        raise ConfFlowClientError("control launcher metadata run_id does not match durable state")
    if marker.get("state_root") != state_locator:
        raise ConfFlowClientError("control launcher metadata state root does not match durable state")
    if marker.get("command") != launcher.get("command"):
        raise ConfFlowClientError("control launcher metadata command does not match durable provenance")
    raw_scheduler = marker.get("scheduler_type")
    if canonical_scheduler_type(str(raw_scheduler or "")) != canonical_scheduler_type(scheduler_type):
        raise ConfFlowClientError("control launcher metadata scheduler type does not match durable state")
    execution_state = marker.get("execution_state")
    execute_rc = marker.get("execute_rc")
    worker_started = marker.get("worker_started")
    worker_rc = marker.get("worker_rc")
    if execution_state is not None:
        if not isinstance(execution_state, str) or execution_state not in {"started", "completed", "failed"}:
            raise ConfFlowClientError("control launcher metadata has an invalid execution state")
        if worker_started is not None and type(worker_started) is not bool:
            raise ConfFlowClientError("control launcher metadata has an invalid worker_started flag")
        if worker_rc is not None and (type(worker_rc) is not int or worker_rc < 0):
            raise ConfFlowClientError("control launcher metadata has an invalid worker return code")
        if execution_state == "started" and (worker_started is not True or execute_rc != 0):
            # A malformed started marker remains unresolved; it is not proof
            # of a scheduler acceptance or a license to dispatch again.
            return LauncherMetadata(
                canonical_scheduler_type(str(raw_scheduler or "")),
                execution_state,
                execute_rc if type(execute_rc) is int else None,
                worker_started,
                worker_rc,
                None,
            )
        if type(execute_rc) is not int or execute_rc < 0:
            raise ConfFlowClientError("control launcher metadata has no execution return code")
        if execution_state == "completed" and execute_rc != 0:
            raise ConfFlowClientError("control launcher metadata has inconsistent completion status")
        if execution_state == "failed" and execute_rc == 0:
            raise ConfFlowClientError("control launcher metadata has inconsistent failure status")
    scheduler_job_id = marker.get("scheduler_job_id") or marker.get("pid")
    if scheduler_job_id is not None and (not isinstance(scheduler_job_id, str) or not scheduler_job_id):
        raise ConfFlowClientError("control launcher metadata has no scheduler job id or pid")
    return LauncherMetadata(
        canonical_scheduler_type(str(raw_scheduler or "")),
        execution_state,
        execute_rc if type(execute_rc) is int else None,
        worker_started if type(worker_started) is bool else None,
        worker_rc if type(worker_rc) is int else None,
        scheduler_job_id,
    )


def record_unresolved_dispatch(
    state: dict[str, object],
    *,
    maximum_attempts: int,
    timestamp: str,
    save: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Record one bounded, fail-closed missing-marker reconciliation attempt."""
    raw_attempts = state.get("reconcile_attempts", 0)
    if type(raw_attempts) is not int or raw_attempts < 0:
        raise ConfFlowClientError("control launcher has invalid reconciliation history")
    attempts = min(raw_attempts + 1, maximum_attempts)
    updated = deepcopy(state)
    updated["reconcile_attempts"] = attempts
    updated["last_reconciled_at"] = timestamp
    if attempts >= maximum_attempts:
        updated["recovery_state"] = "operator_review_required"
    save(updated)
    return updated


def reconcile_launcher_metadata(
    raw: bytes,
    *,
    run_id: str,
    state: dict[str, object],
    launcher: dict[str, object],
    state_locator: str,
    load_producer_snapshot: Callable[[], Any],
    is_terminal: Callable[[str], bool],
    apply_snapshot: Callable[[Any], object],
    save: Callable[[dict[str, object]], None],
    mark_submitted: Callable[[str, str], None],
    timestamp: str,
) -> dict[str, object]:
    """Turn one validated launcher marker into the next durable local decision."""
    dispatch_state = state.get("dispatch_state")
    metadata = parse_launcher_metadata(
        raw,
        run_id=run_id,
        state_locator=state_locator,
        launcher=launcher,
        scheduler_type=str(state.get("scheduler_type", "nohup")),
    )
    execution_state = metadata.execution_state
    execute_rc = metadata.execute_rc
    if execution_state == "started" and (metadata.worker_started is not True or execute_rc != 0):
        return state
    if dispatch_state == "submitted":
        if execution_state != "completed":
            return state
        producer_snapshot = load_producer_snapshot()
        if is_terminal(producer_snapshot.state):
            apply_snapshot(producer_snapshot)
            return state
        failed = deepcopy(state)
        failed.update(
            {
                "dispatch_state": "failed",
                "dispatch_outcome": "worker_failed",
                "dispatch_error": (
                    f"control worker exited with code {metadata.worker_rc} before producer terminal state"
                ),
                "dispatch_updated_at": timestamp,
                "recovery_state": "worker_restart_required",
            }
        )
        failed_launcher = dict(launcher)
        failed_launcher.update(
            {
                "execution_state": execution_state,
                "execute_rc": execute_rc,
                "worker_rc": metadata.worker_rc,
            }
        )
        failed["launcher"] = failed_launcher
        save(failed)
        return failed
    scheduler_job_id = metadata.scheduler_job_id
    if not isinstance(scheduler_job_id, str) or not scheduler_job_id:
        raise ConfFlowClientError("control launcher metadata has no scheduler job id or pid")
    updated = deepcopy(state)
    updated_launcher = dict(launcher)
    updated_launcher["scheduler_job_id"] = scheduler_job_id
    if execution_state is not None:
        updated_launcher["execution_state"] = execution_state
        updated_launcher["execute_rc"] = execute_rc
    if execution_state == "failed":
        updated["dispatch_state"] = "failed"
        updated["launcher"] = updated_launcher
        save(updated)
        return updated
    updated["dispatch_state"] = "submitted"
    updated["scheduler_job_id"] = scheduler_job_id
    updated["launcher"] = updated_launcher
    # A restart may observe the same durable marker more than once.  The
    # marker is evidence for the already accepted dispatch, not a new local
    # decision.  Avoid rewriting the SQLite journal (and re-projecting task
    # metadata) when reconciliation has no state change to persist.
    if updated == state:
        return state
    save(updated)
    mark_submitted(canonical_scheduler_type(metadata.scheduler_type), scheduler_job_id)
    return updated
