"""Durable JobDesk-owned provenance for one selected ConfFlow backend."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from jobdesk_app.core.atomic_write import atomic_write_text
from jobdesk_app.infrastructure.persistence.sqlite_runs._control_decisions import ControlDecision, projection_bytes

CONTROL_STATE_FILENAME = "control_backend.json"


def state_path(service, run_id: str) -> Path:
    return service._run_dir(run_id) / CONTROL_STATE_FILENAME  # noqa: SLF001 - service owns run-dir validation


def load_state(service, run_id: str) -> dict[str, object] | None:
    path = state_path(service, run_id)
    repository = service.repository
    decision = repository.load_confflow_control_decision(run_id)
    if isinstance(decision, ControlDecision):
        desired = projection_bytes(decision.control_state)
        try:
            actual = path.read_bytes() if isinstance(path, Path) and path.is_file() else None
        except OSError:
            actual = None
        if actual is None or hashlib.sha256(actual).hexdigest() != decision.projection_sha256:
            _write_projection(path, desired)
        return deepcopy(decision.control_state)
    if not isinstance(path, Path) or not path.is_file():
        return None
    value = _load_legacy_state(path, run_id)
    # Validate the real owning run before importing an otherwise untrusted
    # sidecar into SQLite.  Import intentionally leaves the source JSON bytes
    # untouched for old readers and rollback.
    repository.load_run(run_id)
    repository.import_legacy_confflow_control_decision(run_id, value)
    return value


def save_state(service, run_id: str, value: dict[str, object]) -> None:
    path = state_path(service, run_id)
    repository = service.repository
    previous = _previous_decision(service, run_id)
    decision = repository.commit_confflow_control_decision(
        run_id,
        value,
        expected_previous_revision=0 if previous is None else previous.decision_revision,
    )
    _write_projection(path, projection_bytes(decision.control_state))


def save_state_with_task_projection(service, run_id: str, value: dict[str, object], tasks: list) -> None:
    """Commit control state and the complete task projection in one SQLite transaction."""
    path = state_path(service, run_id)
    repository = service.repository
    previous = _previous_decision(service, run_id)
    decision = repository.commit_confflow_control_decision_and_replace_tasks(
        run_id,
        value,
        tasks,
        expected_previous_revision=0 if previous is None else previous.decision_revision,
    )
    _write_projection(path, projection_bytes(decision.control_state))


def projection_matches_authority(service, run_id: str) -> bool:
    """Return whether the rollback-compatible JSON equals the SQLite decision."""
    decision = service.repository.load_confflow_control_decision(run_id)
    if not isinstance(decision, ControlDecision):
        return False
    try:
        payload = state_path(service, run_id).read_bytes()
    except OSError:
        return False
    return hashlib.sha256(payload).hexdigest() == decision.projection_sha256


def require_projection_matches_authority(service, run_id: str) -> None:
    """Fail closed before a pre-JD2b reader is allowed to rely on JSON."""
    if not projection_matches_authority(service, run_id):
        raise ValueError(f"control JSON projection is stale for {run_id}; regenerate it before rollback")


def rollback_projection_errors(service) -> list[str]:
    """Return all reasons a pre-JD2b rollback must be refused.

    This is intentionally a read-only check.  A missing journal, a missing
    projection, a malformed journal, or a digest mismatch is unsafe to hand
    to an old reader, so each condition is reported as a blocking error.
    Legacy sidecars are *not* imported here: the caller must first let the
    normal state-loading path bind them to SQLite, preserving this command's
    property of having no writes or repair side effects.
    """
    errors: list[str] = []
    try:
        run_ids = {record.run_id for record in service.repository.list_runs()}
        run_ids.update(
            operation.run_id
            for operation in service.repository.list_operations()
            if getattr(operation, "kind", None) == "confflow_control"
        )
        # Include sidecars that are not represented by a current run record.
        # Such a file would still be consumed by a pre-JD2b reader and must
        # therefore not escape the rollback gate.
        for child in service.runs_dir.iterdir():
            if child.is_dir() and (child / CONTROL_STATE_FILENAME).is_file():
                run_ids.add(child.name)
    except OSError as exc:
        return [f"unable to enumerate control state for rollback: {exc}"]

    for run_id in sorted(run_ids):
        try:
            path = state_path(service, run_id)
        except (OSError, ValueError) as exc:
            errors.append(f"{run_id}: invalid control state path: {exc}")
            continue

        try:
            has_projection = path.is_file()
            decision = service.repository.load_confflow_control_decision(run_id)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{run_id}: unable to verify control state: {exc}")
            continue

        if not isinstance(decision, ControlDecision):
            if has_projection:
                errors.append(f"{run_id}: control JSON has no authoritative SQLite decision")
            continue

        try:
            require_projection_matches_authority(service, run_id)
        except ValueError as exc:
            errors.append(str(exc))

    return errors


def require_all_projections_match_authority(service) -> None:
    """Refuse rollback unless every control JSON matches SQLite authority."""
    errors = rollback_projection_errors(service)
    if errors:
        raise ValueError("\n".join(errors))


def _load_legacy_state(path: Path, run_id: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid durable ConfFlow backend state for {run_id}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid durable ConfFlow backend state for {run_id}: expected object")
    backend = value.get("backend")
    if backend != "control":
        raise ValueError(
            f"run {run_id} uses retired ConfFlow backend {backend!r}; legacy runs cannot be resumed after Phase F"
        )
    if value.get("run_id") != run_id:
        raise ValueError(f"invalid durable ConfFlow backend state for {run_id}: run_id mismatch")
    return deepcopy(value)


def _previous_decision(service, run_id: str):
    """Import a pre-JD2b sidecar before replacing it with a new decision."""
    repository = service.repository
    previous = repository.load_confflow_control_decision(run_id)
    if previous is None and state_path(service, run_id).is_file():
        load_state(service, run_id)
        previous = repository.load_confflow_control_decision(run_id)
    return previous


def _write_projection(path: Path, payload: bytes) -> None:
    # ``projection_bytes`` already models the native legacy line endings.
    # Disable a second text-mode conversion on Windows.
    atomic_write_text(path, payload.decode("utf-8"), newline="")


__all__ = [
    "CONTROL_STATE_FILENAME",
    "load_state",
    "require_all_projections_match_authority",
    "projection_matches_authority",
    "rollback_projection_errors",
    "require_projection_matches_authority",
    "save_state",
    "save_state_with_task_projection",
    "state_path",
]
