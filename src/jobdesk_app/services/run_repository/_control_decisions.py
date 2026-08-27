"""SQLite-authoritative journal records for ConfFlow control decisions.

The compatibility ``control_backend.json`` file is deliberately not a
transaction participant.  This module commits the decision first; callers
then materialise that byte-compatible projection and can safely regenerate it
after an interrupted filesystem write.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime

from ._operations_types import OperationRecord
from ._provenance import record_run_provenance
from ._runs import _replace_tasks

CONTROL_DECISION_KIND = "confflow_control"
CONTROL_DECISION_SCHEMA = "jobdesk.confflow.control-decision.v1"


@dataclass(frozen=True)
class ControlDecision:
    """One validated, immutable-in-meaning control decision revision."""

    operation_id: str
    run_id: str
    decision_revision: int
    expected_previous_revision: int
    control_state: dict[str, object]
    projection_sha256: str


def canonical_json_bytes(value: object) -> bytes:
    """Encode a stable JSON representation suitable for a durable digest."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def projection_sha256(control_state: dict[str, object]) -> str:
    """Return the digest of the exact legacy JSON projection payload."""
    _validate_control_state(control_state, str(control_state.get("run_id", "")))
    return hashlib.sha256(projection_bytes(control_state)).hexdigest()


def projection_bytes(control_state: dict[str, object]) -> bytes:
    """Render precisely the platform-native pre-JD2b JSON byte layout."""
    _validate_control_state(control_state, str(control_state.get("run_id", "")))
    text = json.dumps(control_state, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return text.replace("\n", os.linesep).encode("utf-8")


def load_control_decision(connection: sqlite3.Connection, run_id: str) -> ControlDecision | None:
    """Load the sole authoritative decision for ``run_id`` or return ``None``."""
    rows = connection.execute(
        "SELECT * FROM operations WHERE run_id = ? AND kind = ? ORDER BY created_at, operation_id",
        (run_id, CONTROL_DECISION_KIND),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError(f"run {run_id} has multiple ConfFlow control decisions")
    return _decision_from_operation(_row_to_operation(rows[0]))


def commit_control_decision(
    connection: sqlite3.Connection,
    run_id: str,
    control_state: dict[str, object],
    *,
    expected_previous_revision: int,
) -> ControlDecision:
    """Commit a compare-and-swap decision under SQLite's writer lock.

    The operation row remains open for the life of a run: it is a current
    durable decision, not a short-lived job.  Its revision is local to the
    journal and intentionally independent from ConfFlow's producer revision.
    """
    _validate_control_state(control_state, run_id)
    if type(expected_previous_revision) is not int or expected_previous_revision < 0:
        raise ValueError("expected control decision revision must be a non-negative integer")
    if not connection.in_transaction:
        connection.execute("BEGIN IMMEDIATE")
    current = load_control_decision(connection, run_id)
    current_revision = 0 if current is None else current.decision_revision
    if current_revision != expected_previous_revision:
        raise ValueError(
            "stale ConfFlow control decision: "
            f"expected revision {expected_previous_revision}, found {current_revision}"
        )
    decision_revision = current_revision + 1
    state_copy = deepcopy(control_state)
    digest = projection_sha256(state_copy)
    payload = {
        "content_schema": CONTROL_DECISION_SCHEMA,
        "decision_revision": decision_revision,
        "expected_previous_revision": expected_previous_revision,
        "control_state": state_copy,
        "projection_sha256": digest,
    }
    payload_json = canonical_json_bytes(payload).decode("utf-8")
    timestamp = datetime.now().isoformat()
    if current is None:
        operation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"jobdesk/confflow-control/{run_id}"))
        connection.execute(
            """INSERT INTO operations(
                   operation_id, run_id, kind, phase, payload_json, last_error,
                   created_at, updated_at, completed_at
               ) VALUES (?, ?, ?, 'committed', ?, NULL, ?, ?, NULL)""",
            (operation_id, run_id, CONTROL_DECISION_KIND, payload_json, timestamp, timestamp),
        )
    else:
        operation_id = current.operation_id
        cursor = connection.execute(
            """UPDATE operations
               SET phase = 'committed', payload_json = ?, last_error = NULL, updated_at = ?
               WHERE operation_id = ? AND kind = ?""",
            (payload_json, timestamp, operation_id, CONTROL_DECISION_KIND),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("ConfFlow control decision journal update was lost")
    _record_state_provenance(connection, run_id, state_copy)
    return ControlDecision(
        operation_id=operation_id,
        run_id=run_id,
        decision_revision=decision_revision,
        expected_previous_revision=expected_previous_revision,
        control_state=state_copy,
        projection_sha256=digest,
    )


def import_legacy_control_decision(
    connection: sqlite3.Connection, run_id: str, control_state: dict[str, object]
) -> ControlDecision:
    """Idempotently bind a validated legacy JSON state without rewriting it."""
    _validate_control_state(control_state, run_id)
    current = load_control_decision(connection, run_id)
    if current is not None:
        if canonical_json_bytes(current.control_state) != canonical_json_bytes(control_state):
            raise ValueError("legacy control state differs from the authoritative SQLite decision")
        return current
    return commit_control_decision(
        connection,
        run_id,
        control_state,
        expected_previous_revision=0,
    )


def commit_control_decision_and_replace_tasks(
    connection: sqlite3.Connection,
    run_id: str,
    control_state: dict[str, object],
    tasks: list,
    *,
    expected_previous_revision: int,
) -> ControlDecision:
    """Commit state and its monotonic local task projection atomically."""
    decision = commit_control_decision(
        connection,
        run_id,
        control_state,
        expected_previous_revision=expected_previous_revision,
    )
    _replace_tasks(connection, run_id, tasks)
    return decision


def _decision_from_operation(operation: OperationRecord) -> ControlDecision:
    if operation.kind != CONTROL_DECISION_KIND:
        raise ValueError("operation is not a ConfFlow control decision")
    payload = operation.payload
    if payload.get("content_schema") != CONTROL_DECISION_SCHEMA:
        raise ValueError("ConfFlow control decision has an unsupported schema")
    decision_revision = payload.get("decision_revision")
    expected_previous_revision = payload.get("expected_previous_revision")
    control_state = payload.get("control_state")
    digest = payload.get("projection_sha256")
    if (
        type(decision_revision) is not int
        or decision_revision < 1
        or type(expected_previous_revision) is not int
        or expected_previous_revision != decision_revision - 1
        or not isinstance(control_state, dict)
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ValueError("ConfFlow control decision payload is malformed")
    _validate_control_state(control_state, operation.run_id)
    if projection_sha256(control_state) != digest:
        raise ValueError("ConfFlow control decision projection digest does not match state")
    return ControlDecision(
        operation_id=operation.operation_id,
        run_id=operation.run_id,
        decision_revision=decision_revision,
        expected_previous_revision=expected_previous_revision,
        control_state=deepcopy(control_state),
        projection_sha256=digest,
    )


def _validate_control_state(control_state: dict[str, object], run_id: str) -> None:
    if not isinstance(control_state, dict):
        raise ValueError("control decision state must be an object")
    if not run_id or control_state.get("run_id") != run_id:
        raise ValueError("control decision state run_id mismatch")
    if control_state.get("backend") != "control":
        raise ValueError("control decision state backend must be control")


def _record_state_provenance(connection: sqlite3.Connection, run_id: str, control_state: dict[str, object]) -> None:
    """Keep accepted producer provenance in the same journal transaction."""
    capability = control_state.get("capability")
    identity = control_state.get("producer_identity")
    if not isinstance(capability, dict) or not capability:
        return
    if not isinstance(identity, dict):
        identity = {}
    executable = identity.get("path")
    realpath = identity.get("realpath")
    record_run_provenance(
        connection,
        run_id,
        capability,
        resolved_executable=str(executable if isinstance(executable, str) else realpath or ""),
        resolved_realpath=str(realpath if isinstance(realpath, str) else ""),
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
        completed_at=None if row["completed_at"] is None else str(row["completed_at"]),
        owner_id=None if row["owner_id"] is None else str(row["owner_id"]),
        lease_expires_at=None if row["lease_expires_at"] is None else str(row["lease_expires_at"]),
    )


__all__ = [
    "CONTROL_DECISION_KIND",
    "CONTROL_DECISION_SCHEMA",
    "ControlDecision",
    "canonical_json_bytes",
    "commit_control_decision",
    "commit_control_decision_and_replace_tasks",
    "import_legacy_control_decision",
    "load_control_decision",
    "projection_bytes",
    "projection_sha256",
]
