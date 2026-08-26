"""Typed helpers for the byte-compatible local control-state projection."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from jobdesk_app.application.confflow_client import ConfFlowClientError
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.services.confflow_control import CONTROL_BACKEND, PROTOCOL_SCHEMA, ControlSnapshot
from jobdesk_app.services.confflow_control_state import save_state


def audit_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def record_unknown_dispatch(service: Any, run_id: str, state: dict[str, object], error: object) -> None:
    """Persist an unknown scheduler acceptance result before surfacing it."""
    unknown = deepcopy(state)
    unknown.update(
        {
            "dispatch_outcome": "unknown",
            "dispatch_error": str(error),
            "dispatch_updated_at": audit_timestamp(),
        }
    )
    save_state(service, run_id, unknown)


def state_identity(state: dict[str, object] | None) -> dict[str, object]:
    identity = state.get("producer_identity") if state else None
    return deepcopy(identity) if isinstance(identity, dict) else {}


def control_expected_identity(identity: dict[str, object]) -> dict[str, object]:
    digest = identity.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest):
        raise ConfFlowClientError("control producer identity has no SHA-256")
    expected: dict[str, object] = {"sha256": digest.lower()}
    realpath = identity.get("realpath")
    if isinstance(realpath, str) and realpath:
        expected["realpath"] = realpath
    device = identity.get("device")
    inode = identity.get("inode")
    if isinstance(device, int) and isinstance(inode, int):
        expected["device_inode"] = f"{device}:{inode}"
    return expected


def state_locator(state: dict[str, object] | None) -> str | None:
    value = state.get("state_locator") if state else None
    return value if isinstance(value, str) and value else None


def state_key(state: dict[str, object] | None, run_id: str) -> str:
    value = state.get("idempotency_key") if state else None
    return value if isinstance(value, str) and value else f"jobdesk.{run_id}"


def optional_string(state: dict[str, object] | None, key: str) -> str | None:
    value = state.get(key) if state else None
    return value if isinstance(value, str) and value else None


def capability_payload(capabilities: ConfFlowCapabilities | None) -> dict[str, object] | None:
    if capabilities is None or not isinstance(capabilities.raw_payload, dict):
        return None
    return deepcopy(capabilities.raw_payload)


def control_state(
    run_id: str,
    *,
    state_locator: str,
    capability: ConfFlowCapabilities | None,
    producer_identity: dict[str, object],
    request_frame: dict[str, object],
    snapshot: ControlSnapshot,
    previous: dict[str, object] | None,
    workflow_path: str,
    input_path: str,
    worker_handoff: dict[str, object] | None = None,
    worker_attempt_root: str | None = None,
    worker_work_dir: str | None = None,
    worker_executable: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = deepcopy(previous or {})
    value.update(
        {
            "content_schema": "jobdesk.confflow.backend.v1",
            "run_id": run_id,
            "backend": CONTROL_BACKEND,
            "protocol_schema": PROTOCOL_SCHEMA,
            "state_locator": state_locator,
            "idempotency_key": request_frame["idempotency_key"],
            "request_digest": request_frame["request_digest"],
            "request": deepcopy(request_frame),
            "capability": capability_payload(capability) or value.get("capability", {}),
            "producer_identity": deepcopy(producer_identity),
            "workflow_config_path": workflow_path,
            "input_manifest_path": input_path,
            "revision": snapshot.revision,
            "state": snapshot.state,
        }
    )
    if worker_handoff is not None:
        value["worker_handoff"] = deepcopy(worker_handoff)
    if worker_attempt_root is not None:
        value["worker_attempt_root"] = worker_attempt_root
    if worker_work_dir is not None:
        value["worker_work_dir"] = worker_work_dir
    if worker_executable is not None:
        value["worker_executable"] = worker_executable
    return value
