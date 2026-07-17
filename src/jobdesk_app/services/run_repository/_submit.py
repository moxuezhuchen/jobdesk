"""Submit operation journal and lease CAS operations."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

from jobdesk_app.core.lifecycle import TaskStatus

from ._operations_types import OperationRecord
from ._runs import _load_tasks, _replace_tasks
from ._tasks_helpers import _validated_operation_task_ids

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Lease timestamp utilities (was _leases)
# ---------------------------------------------------------------------------


def _utc_lease_timestamp(value: datetime) -> str:
    """Serialize a lease instant in one lexically stable UTC representation."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_lease_timestamp(value: str) -> datetime:
    """Parse an explicitly zoned ISO lease timestamp as a UTC instant."""
    parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    if parsed.tzinfo is None:
        raise ValueError("submit lease timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Submit operations
# ---------------------------------------------------------------------------


def claim_submit_tasks(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    scheduler_type: str,
    resources: dict,
    env_init_scripts: list,
    per_task: bool,
    owner_id: str | None = None,
    lease_seconds: float = 60.0,
) -> tuple[list, list[OperationRecord]]:
    """Claim uploaded tasks and create their submit journal entries atomically."""
    timestamp = datetime.now().isoformat()
    lease_expires_at = (
        _utc_lease_timestamp(datetime.now(timezone.utc) + timedelta(seconds=lease_seconds))
        if owner_id is not None
        else None
    )
    operations: list[OperationRecord] = []
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    current = _load_tasks(connection, run_id)
    claimed = [task.model_copy(deep=True) for task in current if task.status == TaskStatus.uploaded]
    if not claimed:
        return [], []
    claimed_ids = {task.task_id for task in claimed}
    claimed_at = datetime.now()
    _replace_tasks(
        connection,
        run_id,
        [
            task.model_copy(
                update={"status": TaskStatus.submitting, "submitted_at": claimed_at},
                deep=True,
            )
            if task.task_id in claimed_ids
            else task
            for task in current
        ],
    )
    groups = [[task.task_id] for task in claimed] if per_task else [[task.task_id for task in claimed]]
    for task_ids in groups:
        operation_id = str(uuid.uuid4())
        payload: dict = {
            "task_ids": task_ids,
            "scheduler_type": scheduler_type,
            "resources": resources,
            "env_init_scripts": env_init_scripts,
            "results": {},
        }
        payload_json = json.dumps(payload, ensure_ascii=False)
        connection.execute(
            """
            INSERT INTO operations(
                operation_id, run_id, kind, phase, payload_json, last_error,
                created_at, updated_at, completed_at, owner_id, lease_expires_at
            ) VALUES (?, ?, 'submit', 'claimed', ?, NULL, ?, ?, NULL, ?, ?)
            """,
            (
                operation_id,
                run_id,
                payload_json,
                timestamp,
                timestamp,
                owner_id,
                lease_expires_at,
            ),
        )
        operations.append(
            OperationRecord(
                operation_id,
                run_id,
                "submit",
                "claimed",
                dict(json.loads(payload_json)),
                None,
                timestamp,
                timestamp,
                None,
                owner_id,
                lease_expires_at,
            )
        )
    return claimed, operations


def renew_submit_lease(
    connection: sqlite3.Connection,
    operation_id: str,
    owner_id: str,
    *,
    lease_seconds: float = 60.0,
) -> bool:
    timestamp = datetime.now(timezone.utc)
    cursor = connection.execute(
        "UPDATE operations SET lease_expires_at = ?, updated_at = ? "
        "WHERE operation_id = ? AND kind = 'submit' AND owner_id = ? "
        "AND completed_at IS NULL",
        (
            _utc_lease_timestamp(timestamp + timedelta(seconds=lease_seconds)),
            timestamp.isoformat(),
            operation_id,
            owner_id,
        ),
    )
    if cursor.rowcount == 1:
        return True
    completed = connection.execute(
        "SELECT 1 FROM operations WHERE operation_id = ? "
        "AND kind = 'submit' AND owner_id = ? AND completed_at IS NOT NULL",
        (operation_id, owner_id),
    ).fetchone()
    return completed is not None


def acquire_submit_recovery(
    connection: sqlite3.Connection,
    operation_id: str,
    owner_id: str,
    *,
    lease_seconds: float = 60.0,
) -> bool:
    now = datetime.now(timezone.utc)
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT owner_id, lease_expires_at FROM operations "
        "WHERE operation_id = ? AND kind = 'submit' AND completed_at IS NULL",
        (operation_id,),
    ).fetchone()
    if row is None:
        return False
    current_owner = row["owner_id"]
    current_lease = row["lease_expires_at"]
    if current_owner is not None and current_lease is not None:
        try:
            lease_is_live = _parse_lease_timestamp(str(current_lease)) > now
        except (TypeError, ValueError):
            lease_is_live = False
        if lease_is_live:
            return False
    cursor = connection.execute(
        """
        UPDATE operations SET owner_id = ?, lease_expires_at = ?, updated_at = ?
        WHERE operation_id = ? AND kind = 'submit' AND completed_at IS NULL
          AND owner_id IS ? AND lease_expires_at IS ?
        """,
        (
            owner_id,
            _utc_lease_timestamp(now + timedelta(seconds=lease_seconds)),
            now.isoformat(),
            operation_id,
            current_owner,
            current_lease,
        ),
    )
    return cursor.rowcount == 1


def start_submit_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    *,
    owner_id: str | None = None,
) -> bool:
    timestamp = datetime.now().isoformat()
    cursor = connection.execute(
        "UPDATE operations SET phase = 'remote_started', updated_at = ? "
        "WHERE operation_id = ? AND kind = 'submit' AND phase = 'claimed' "
        "AND completed_at IS NULL AND owner_id IS ?",
        (timestamp, operation_id, owner_id),
    )
    return cursor.rowcount == 1


def finish_submit_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    *,
    task_ids: list[str],
    job_ids: dict[str, str],
    error: str | None = None,
    owner_id: str | None = None,
) -> bool:
    """Persist an outcome phase, then complete it in a separate transaction."""
    phase = "uncertain" if error is not None else "confirmed"
    return record_submit_outcome(
        connection,
        operation_id,
        task_ids=task_ids,
        job_ids=job_ids,
        error=error,
        owner_id=owner_id,
    ) and complete_submit_operation(connection, operation_id, phase, owner_id=owner_id)


def record_submit_outcome(
    connection: sqlite3.Connection,
    operation_id: str,
    *,
    task_ids: list[str],
    job_ids: dict[str, str],
    error: str | None = None,
    owner_id: str | None = None,
) -> bool:
    """Atomically persist task results and the durable outcome phase."""
    timestamp = datetime.now().isoformat()
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ? AND kind = 'submit' "
        "AND phase = 'remote_started' AND completed_at IS NULL AND owner_id IS ?",
        (operation_id, owner_id),
    ).fetchone()
    if row is None:
        return False
    operation = _row_to_operation(row)
    scheduler_type = operation.payload.get("scheduler_type")
    if not isinstance(scheduler_type, str) or not scheduler_type.strip():
        connection.execute(
            "UPDATE operations SET last_error = ?, updated_at = ? "
            "WHERE operation_id = ? AND phase = 'remote_started' "
            "AND completed_at IS NULL",
            (
                "remote_started submit payload is invalid",
                timestamp,
                operation_id,
            ),
        )
        return False
    if error is not None and (not isinstance(error, str) or not error.strip()):
        connection.execute(
            "UPDATE operations SET last_error = ?, updated_at = ? "
            "WHERE operation_id = ? AND phase = 'remote_started' "
            "AND completed_at IS NULL",
            (
                "submit outcome error must be a non-empty string",
                timestamp,
                operation_id,
            ),
        )
        return False
    valid_requested_ids = (
        bool(task_ids)
        and all(isinstance(task_id, str) and bool(task_id) for task_id in task_ids)
        and len(set(task_ids)) == len(task_ids)
    )
    current = _load_tasks(connection, operation.run_id)
    selected = _validated_operation_task_ids(operation, current, TaskStatus.submitting)
    if selected is None or not valid_requested_ids or selected != set(task_ids):
        return False
    uncertain = error is not None
    if uncertain and job_ids:
        return False
    if not uncertain and (
        set(job_ids) != selected or any(not isinstance(job_id, str) or not job_id for job_id in job_ids.values())
    ):
        return False
    updated = []
    for task in current:
        if task.task_id not in selected:
            updated.append(task)
            continue
        updated.append(
            task.model_copy(
                update={
                    "status": TaskStatus.uncertain if uncertain else TaskStatus.submitted,
                    "scheduler_type": scheduler_type,
                    "remote_job_id": None if uncertain else job_ids.get(task.task_id),
                    "error_message": error,
                },
                deep=True,
            )
        )
    _replace_tasks(connection, operation.run_id, updated)
    phase = "uncertain" if uncertain else "confirmed"
    payload = dict(operation.payload)
    payload["outcome_phase"] = phase
    payload["results"] = {
        task_id: ({"error": error} if uncertain else {"job_id": job_ids.get(task_id)}) for task_id in task_ids
    }
    cursor = connection.execute(
        """
        UPDATE operations SET phase = ?, payload_json = ?, last_error = ?,
            updated_at = ?
        WHERE operation_id = ? AND phase = 'remote_started' AND completed_at IS NULL
            AND owner_id IS ?
        """,
        (
            phase,
            json.dumps(payload, ensure_ascii=False),
            error,
            timestamp,
            operation_id,
            owner_id,
        ),
    )
    return cursor.rowcount == 1


def complete_submit_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    expected_phase: str,
    *,
    owner_id: str | None = None,
) -> bool:
    if expected_phase not in {"confirmed", "uncertain"}:
        raise ValueError(f"invalid submit outcome phase: {expected_phase}")
    timestamp = datetime.now().isoformat()
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ?",
        (operation_id,),
    ).fetchone()
    if row is None:
        return False
    operation = _row_to_operation(row)
    if operation.owner_id != owner_id:
        return False
    if operation.phase not in {expected_phase, "completed"}:
        return False
    if operation.phase == "completed":
        return (
            operation.completed_at is not None
            and _submit_journal_outcome_validation_error(operation, expected_phase) is None
        )
    current = _load_tasks(connection, operation.run_id)
    if _submit_outcome_validation_error(connection, operation, current, expected_phase) is not None:
        return False
    cursor = connection.execute(
        "UPDATE operations SET phase = 'completed', updated_at = ?, completed_at = ? "
        "WHERE operation_id = ? AND kind = 'submit' AND phase = ? "
        "AND completed_at IS NULL AND owner_id IS ?",
        (timestamp, timestamp, operation_id, expected_phase, owner_id),
    )
    return cursor.rowcount == 1


def _submit_journal_outcome_validation_error(
    operation: OperationRecord,
    expected_phase: str,
) -> str | None:
    invalid = f"{expected_phase} submit outcome is invalid"
    if operation.kind != "submit" or operation.payload.get("outcome_phase") != expected_phase:
        return invalid
    payload_task_ids = operation.payload.get("task_ids")
    if (
        not isinstance(payload_task_ids, list)
        or not payload_task_ids
        or not all(isinstance(task_id, str) and bool(task_id) for task_id in payload_task_ids)
        or len(set(payload_task_ids)) != len(payload_task_ids)
    ):
        return invalid
    task_ids = set(cast(list[str], payload_task_ids))
    results = operation.payload.get("results")
    if not isinstance(results, dict) or set(results) != task_ids:
        return invalid
    typed_results = cast(dict[str, object], results)
    for task_id in task_ids:
        result = typed_results[task_id]
        if not isinstance(result, dict):
            return invalid
        if expected_phase == "confirmed":
            job_id = result.get("job_id")
            if (
                set(result) != {"job_id"}
                or operation.last_error is not None
                or not isinstance(job_id, str)
                or not job_id
            ):
                return invalid
        elif (
            set(result) != {"error"}
            or not isinstance(operation.last_error, str)
            or not operation.last_error
            or result.get("error") != operation.last_error
        ):
            return invalid
    return None


def _submit_outcome_validation_error(
    connection: sqlite3.Connection,
    operation: OperationRecord,
    current: list,
    expected_phase: str,
) -> str | None:
    journal_error = _submit_journal_outcome_validation_error(operation, expected_phase)
    if journal_error is not None:
        return journal_error
    expected_status = TaskStatus.submitted if expected_phase == "confirmed" else TaskStatus.uncertain
    task_ids = _validated_operation_task_ids(operation, current, expected_status)
    results = operation.payload["results"]
    if task_ids is None:
        return f"{expected_phase} submit outcome is invalid"
    typed_results = cast(dict[str, object], results)
    current_by_id = {task.task_id: task for task in current}
    for task_id in task_ids:
        result = typed_results[task_id]
        assert isinstance(result, dict)
        task = current_by_id[task_id]
        if expected_phase == "confirmed":
            job_id = result.get("job_id")
            if task.error_message is not None or job_id != task.remote_job_id:
                return "confirmed submit outcome is invalid"
        elif task.error_message != operation.last_error or task.remote_job_id is not None:
            return "uncertain submit outcome is invalid"
    return None


def release_claimed_submit_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    *,
    owner_id: str | None = None,
) -> bool:
    """Roll back one preflight-only submit claim without touching later phases."""
    timestamp = datetime.now().isoformat()
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ? AND kind = 'submit' "
        "AND phase = 'claimed' AND completed_at IS NULL AND owner_id IS ?",
        (operation_id, owner_id),
    ).fetchone()
    if row is None:
        return False
    operation = _row_to_operation(row)
    current = _load_tasks(connection, operation.run_id)
    validated = _validated_operation_task_ids(operation, current, TaskStatus.submitting)
    if validated is None:
        _record_operation_validation_error(connection, operation, timestamp)
        return False
    released = [
        task.model_copy(
            update={
                "status": TaskStatus.uploaded,
                "submitted_at": None,
                "remote_job_id": None,
                "error_message": None,
            },
            deep=True,
        )
        if task.task_id in validated
        else task
        for task in current
    ]
    _replace_tasks(connection, operation.run_id, released)
    cursor = connection.execute(
        "UPDATE operations SET phase = 'completed', updated_at = ?, completed_at = ? "
        "WHERE operation_id = ? AND phase = 'claimed' AND completed_at IS NULL",
        (timestamp, timestamp, operation_id),
    )
    return cursor.rowcount == 1


def recover_submit_operation(
    connection: sqlite3.Connection,
    operation_id: str,
    *,
    owner_id: str | None = None,
) -> bool:
    """Resolve one interrupted submit operation using its durable phase."""
    timestamp = datetime.now().isoformat()
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    row = connection.execute(
        "SELECT * FROM operations WHERE operation_id = ? AND kind = 'submit' "
        "AND completed_at IS NULL AND owner_id IS ?",
        (operation_id, owner_id),
    ).fetchone()
    if row is None:
        return False
    operation = _row_to_operation(row)
    current = _load_tasks(connection, operation.run_id)
    scheduler_type: str | None = None
    if operation.phase == "claimed":
        expected_status = TaskStatus.submitting
        status = TaskStatus.uploaded
        error = None
    elif operation.phase == "remote_started":
        recorded_scheduler = operation.payload.get("scheduler_type")
        if not isinstance(recorded_scheduler, str) or not recorded_scheduler.strip():
            connection.execute(
                "UPDATE operations SET last_error = ?, updated_at = ? "
                "WHERE operation_id = ? AND phase = 'remote_started' "
                "AND completed_at IS NULL",
                (
                    "remote_started submit payload is invalid",
                    timestamp,
                    operation_id,
                ),
            )
            return False
        scheduler_type = recorded_scheduler
        expected_status = TaskStatus.submitting
        status = TaskStatus.uncertain
        error = "submission interrupted after remote command started"
    elif operation.phase == "confirmed":
        expected_status = TaskStatus.submitted
        status = None
        error = None
    elif operation.phase == "uncertain":
        expected_status = TaskStatus.uncertain
        status = None
        error = None
    else:
        return False
    task_ids = _validated_operation_task_ids(operation, current, expected_status)
    if task_ids is None:
        _record_operation_validation_error(connection, operation, timestamp)
        return False
    if operation.phase in {"confirmed", "uncertain"}:
        outcome_error = _submit_outcome_validation_error(connection, operation, current, operation.phase)
        if outcome_error is not None:
            connection.execute(
                "UPDATE operations SET last_error = ?, updated_at = ? "
                "WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
                (outcome_error, timestamp, operation_id, operation.phase),
            )
            return False
        return (
            connection.execute(
                "UPDATE operations SET phase = 'completed', updated_at = ?, completed_at = ? "
                "WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
                (timestamp, timestamp, operation_id, operation.phase),
            ).rowcount
            == 1
        )
    updated = []
    for task in current:
        if task.task_id not in task_ids or task.status != TaskStatus.submitting:
            updated.append(task)
            continue
        values: dict = {"status": status, "error_message": error}
        if status == TaskStatus.uploaded:
            values.update({"submitted_at": None, "remote_job_id": None})
        if scheduler_type is not None:
            values["scheduler_type"] = scheduler_type
        updated.append(task.model_copy(update=values, deep=True))
    _replace_tasks(connection, operation.run_id, updated)
    return (
        connection.execute(
            "UPDATE operations SET phase = 'completed', last_error = ?, updated_at = ?, "
            "completed_at = ? WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
            (error, timestamp, timestamp, operation_id, operation.phase),
        ).rowcount
        == 1
    )


def resolve_uncertain_tasks(
    connection: sqlite3.Connection,
    run_id: str,
    task_ids: list[str],
    *,
    action: str,
    remote_job_ids: dict[str, str] | None = None,
) -> tuple[list[str], list]:
    """Resolve selected uncertain tasks with one transactional CAS."""
    if action not in {"confirm", "abandon"}:
        raise ValueError(f"unsupported uncertain task resolution: {action}")
    selected = set(task_ids)
    job_ids = remote_job_ids or {}
    accepted: list[str] = []
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    current = _load_tasks(connection, run_id)
    resolved: list = []
    now = datetime.now()
    for task in current:
        if task.task_id not in selected or task.status != TaskStatus.uncertain:
            resolved.append(task)
            continue
        accepted.append(task.task_id)
        if action == "confirm":
            updates: dict = {
                "status": TaskStatus.submitted,
                "error_message": None,
                "submitted_at": task.submitted_at or now,
            }
            if task.task_id in job_ids:
                updates["remote_job_id"] = job_ids[task.task_id]
        else:
            updates = {
                "status": TaskStatus.uploaded,
                "remote_job_id": None,
                "scheduler_type": "nohup",
                "error_message": None,
                "submitted_at": None,
                "started_at": None,
                "completed_at": None,
                "downloaded_at": None,
                "analyzed_at": None,
            }
        resolved.append(task.model_copy(update=updates, deep=True))
    if accepted:
        _replace_tasks(connection, run_id, resolved)
    return accepted, resolved


def _record_operation_validation_error(
    connection: sqlite3.Connection,
    operation: OperationRecord,
    timestamp: str,
) -> None:
    connection.execute(
        "UPDATE operations SET last_error = ?, updated_at = ? "
        "WHERE operation_id = ? AND phase = ? AND completed_at IS NULL",
        (
            f"{operation.phase} operation task set is invalid",
            timestamp,
            operation.operation_id,
            operation.phase,
        ),
    )


def _row_to_operation(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        operation_id=str(row["operation_id"]),
        run_id=str(row["run_id"]),
        kind=str(row["kind"]),
        phase=str(row["phase"]),
        payload=dict(json.loads(row["payload_json"])),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=(None if row["completed_at"] is None else str(row["completed_at"])),
        owner_id=None if row["owner_id"] is None else str(row["owner_id"]),
        lease_expires_at=(None if row["lease_expires_at"] is None else str(row["lease_expires_at"])),
    )
