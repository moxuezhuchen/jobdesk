"""Consumer-side implementation of the frozen ConfFlow control v1 contract.

The producer owns the wire schemas.  This module keeps the JobDesk side small:
it builds the request digest, validates the one-line response, and exposes
typed values to the application facade.  It deliberately contains no SSH or
filesystem policy so the same contract can be exercised with an in-memory
transport.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

PROTOCOL_SCHEMA = "confflow.control.v1"
CONTROL_BACKEND = "control"
LEGACY_BACKEND = "legacy"

_STATES = frozenset({"prepared", "queued", "running", "paused", "completed", "failed", "cancelled"})
_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "unsupported_protocol",
        "unknown_run",
        "idempotency_conflict",
        "invalid_state_transition",
        "invalid_checkpoint",
        "already_running",
        "terminal_run",
        "executable_identity_mismatch",
        "artifact_path_invalid",
        "artifact_integrity_failed",
        "repository_unavailable",
        "internal",
    }
)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PATH_RE = re.compile(r"^(?!/)(?!.*//)(?!.*(?:^|/)\.(?:/|$))(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


class ControlProtocolError(RuntimeError):
    """A typed producer response or malformed control response."""

    def __init__(self, operation: str, code: str, message: str, *, retryable: bool = False) -> None:
        self.operation = operation
        self.code = code
        self.retryable = retryable
        super().__init__(f"control {operation} failed [{code}]: {message}")


class ControlUnsupported(ControlProtocolError):
    """The producer explicitly does not expose control protocol v1."""

    def __init__(self, message: str = "producer does not support control protocol v1") -> None:
        super().__init__("capabilities", "unsupported_protocol", message)


@dataclass(frozen=True)
class ControlSnapshot:
    run_id: str
    revision: int
    state: str


@dataclass(frozen=True)
class ControlEvent:
    cursor: str
    revision: int
    event_type: str


@dataclass(frozen=True)
class ControlEventPage:
    snapshot: ControlSnapshot
    events: tuple[ControlEvent, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ControlArtifact:
    terminal: str
    path: str
    sha256: str
    size: int
    content_schema: str


@dataclass(frozen=True)
class ControlArtifactManifest:
    snapshot: ControlSnapshot
    artifacts: tuple[ControlArtifact, ...]


class ControlTransport(Protocol):
    """Operation-level transport used by the control run handle."""

    def prepare(self, request: dict[str, object]) -> ControlSnapshot: ...

    def execute(self, run_id: str) -> ControlSnapshot: ...

    def status(self, run_id: str) -> ControlSnapshot: ...

    def events(self, run_id: str, *, after: str | None) -> ControlEventPage: ...

    def cancel(self, run_id: str) -> ControlSnapshot: ...

    def resume(self, run_id: str, *, checkpoint: str | None) -> ControlSnapshot: ...

    def artifacts(self, run_id: str) -> ControlArtifactManifest: ...


def build_prepare_request(
    *,
    run_id: str,
    idempotency_key: str,
    workflow_config: Mapping[str, object],
    input_manifest: Mapping[str, object],
    expected_executable_identity: Mapping[str, object],
) -> dict[str, object]:
    """Build a v1 prepare frame and its RFC 8785-compatible digest.

    The frozen prepare schema contains strings, booleans and nested objects;
    the canonical JSON emitted here is therefore the RFC 8785 form (UTF-8,
    sorted object keys, compact separators, and no non-finite numbers).
    """
    request: dict[str, object] = {
        "protocol_schema": PROTOCOL_SCHEMA,
        "operation": "prepare",
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "request_digest": "",
        "workflow_config": dict(workflow_config),
        "input_manifest": dict(input_manifest),
        "expected_executable_identity": dict(expected_executable_identity),
    }
    request["request_digest"] = _request_digest(request)
    return request


def _request_digest(request: Mapping[str, object]) -> str:
    semantic = dict(request)
    semantic.pop("request_digest", None)
    encoded = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_capabilities(stdout: str, *, exit_code: int = 0, stderr: str = "") -> bool:
    """Validate the control capability response and return whether v1 exists."""
    response = _decode_response("capabilities", stdout, exit_code=exit_code, stderr=stderr)
    if not response.get("ok"):
        error = _error_fields(response, "capabilities")
        if error[0] == "unsupported_protocol":
            raise ControlUnsupported(error[1])
        raise ControlProtocolError("capabilities", error[0], error[1], retryable=error[2])
    protocols = response.get("supported_protocols")
    if not isinstance(protocols, list) or not all(isinstance(item, str) for item in protocols):
        raise ControlProtocolError("capabilities", "invalid_request", "supported_protocols is malformed")
    if PROTOCOL_SCHEMA not in protocols:
        raise ControlUnsupported("producer capability response does not include control protocol v1")
    return True


def parse_snapshot_response(operation: str, stdout: str, *, exit_code: int = 0, stderr: str = "") -> ControlSnapshot:
    response = _decode_response(operation, stdout, exit_code=exit_code, stderr=stderr)
    if not response.get("ok"):
        code, message, retryable = _error_fields(response, operation)
        raise ControlProtocolError(operation, code, message, retryable=retryable)
    return _snapshot(response, operation)


def parse_events_response(operation: str, stdout: str, *, exit_code: int = 0, stderr: str = "") -> ControlEventPage:
    response = _decode_response(operation, stdout, exit_code=exit_code, stderr=stderr)
    if not response.get("ok"):
        code, message, retryable = _error_fields(response, operation)
        raise ControlProtocolError(operation, code, message, retryable=retryable)
    snapshot = _snapshot(response, operation)
    next_cursor = response.get("next_cursor")
    if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor):
        raise ControlProtocolError(operation, "invalid_request", "next_cursor is malformed")
    raw_events = response.get("events")
    if not isinstance(raw_events, list):
        raise ControlProtocolError(operation, "invalid_request", "events is malformed")
    events: list[ControlEvent] = []
    seen_cursors: set[str] = set()
    previous_revision = -1
    for raw in raw_events:
        if not isinstance(raw, dict):
            raise ControlProtocolError(operation, "invalid_request", "event is not an object")
        cursor = raw.get("cursor")
        revision = raw.get("revision")
        event_type = raw.get("type")
        if (
            not isinstance(cursor, str)
            or not cursor
            or cursor in seen_cursors
            or type(revision) is not int
            or revision < 0
            or revision < previous_revision
            or not isinstance(event_type, str)
            or not event_type
        ):
            raise ControlProtocolError(operation, "invalid_request", "event fields are malformed or out of order")
        seen_cursors.add(cursor)
        previous_revision = revision
        events.append(ControlEvent(cursor, revision, event_type))
    return ControlEventPage(snapshot, tuple(events), next_cursor)


def parse_artifacts_response(operation: str, stdout: str, *, exit_code: int = 0, stderr: str = "") -> ControlArtifactManifest:
    response = _decode_response(operation, stdout, exit_code=exit_code, stderr=stderr)
    if not response.get("ok"):
        code, message, retryable = _error_fields(response, operation)
        raise ControlProtocolError(operation, code, message, retryable=retryable)
    snapshot = _snapshot(response, operation)
    if snapshot.state not in _TERMINAL_STATES:
        raise ControlProtocolError(operation, "invalid_state_transition", "artifacts response is not terminal")
    raw_artifacts = response.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raise ControlProtocolError(operation, "artifact_path_invalid", "artifacts is malformed")
    artifacts: list[ControlArtifact] = []
    seen: set[tuple[str, str]] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            raise ControlProtocolError(operation, "artifact_path_invalid", "artifact is not an object")
        terminal, path, sha256, size, schema = (
            raw.get("terminal"),
            raw.get("path"),
            raw.get("sha256"),
            raw.get("size"),
            raw.get("content_schema"),
        )
        if (
            not isinstance(terminal, str)
            or not terminal
            or not isinstance(path, str)
            or _PATH_RE.fullmatch(path) is None
            or not isinstance(sha256, str)
            or _DIGEST_RE.fullmatch(sha256) is None
            or type(size) is not int
            or size < 0
            or not isinstance(schema, str)
            or not schema
            or (terminal, path) in seen
        ):
            raise ControlProtocolError(operation, "artifact_path_invalid", "artifact manifest contains unsafe fields")
        seen.add((terminal, path))
        artifacts.append(ControlArtifact(terminal, path, sha256, size, schema))
    return ControlArtifactManifest(snapshot, tuple(artifacts))


def _decode_response(operation: str, stdout: str, *, exit_code: int, stderr: str) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ControlProtocolError(operation, "invalid_request", "control stdout must contain exactly one JSON response")
    try:
        response = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ControlProtocolError(operation, "invalid_request", f"malformed control JSON: {exc}") from exc
    if not isinstance(response, dict):
        raise ControlProtocolError(operation, "invalid_request", "control response must be an object")
    if response.get("protocol_schema") != PROTOCOL_SCHEMA or response.get("operation") != operation:
        raise ControlProtocolError(operation, "unsupported_protocol", "control response protocol or operation mismatch")
    if type(response.get("ok")) is not bool:
        raise ControlProtocolError(operation, "invalid_request", "control response ok must be boolean")
    if exit_code != 0 and response.get("ok"):
        raise ControlProtocolError(operation, "internal", f"control exited with {exit_code}: {stderr.strip()}", retryable=True)
    return response


def _error_fields(response: Mapping[str, object], operation: str) -> tuple[str, str, bool]:
    raw_error = response.get("error")
    if not isinstance(raw_error, dict):
        raise ControlProtocolError(operation, "invalid_request", "typed control error is missing")
    code = raw_error.get("code")
    message = raw_error.get("message")
    retryable = raw_error.get("retryable")
    if (
        not isinstance(code, str)
        or code not in _ERROR_CODES
        or not isinstance(message, str)
        or not message
        or type(retryable) is not bool
    ):
        raise ControlProtocolError(operation, "invalid_request", "typed control error is malformed")
    return code, message, retryable


def _snapshot(response: Mapping[str, object], operation: str) -> ControlSnapshot:
    run_id = response.get("run_id")
    revision = response.get("revision")
    state = response.get("state")
    if (
        not isinstance(run_id, str)
        or not run_id
        or type(revision) is not int
        or revision < 0
        or not isinstance(state, str)
        or state not in _STATES
    ):
        raise ControlProtocolError(operation, "invalid_request", "control snapshot fields are malformed")
    return ControlSnapshot(run_id, revision, state)


def is_terminal_state(state: str) -> bool:
    return state in _TERMINAL_STATES


__all__ = [
    "CONTROL_BACKEND",
    "ControlArtifact",
    "ControlArtifactManifest",
    "ControlEvent",
    "ControlEventPage",
    "ControlProtocolError",
    "ControlSnapshot",
    "ControlTransport",
    "ControlUnsupported",
    "LEGACY_BACKEND",
    "PROTOCOL_SCHEMA",
    "build_prepare_request",
    "is_terminal_state",
    "parse_artifacts_response",
    "parse_capabilities",
    "parse_events_response",
    "parse_snapshot_response",
]
