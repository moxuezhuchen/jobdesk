"""Golden and rejection fixtures for the frozen Phase B control protocol."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

_SCHEMA_ROOT = Path(__file__).parents[1] / "confflow" / "schemas" / "control"
_DIGEST = "a" * 64


@pytest.fixture(scope="module")
def schemas() -> dict[str, Draft202012Validator]:
    return {
        path.name: Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(_SCHEMA_ROOT.glob("*.json"))
    }


@pytest.fixture(scope="module")
def golden() -> dict[str, dict[str, object]]:
    base = {"protocol_schema": "confflow.control.v1", "run_id": "run-1", "revision": 2}
    return {
        "capabilities.response.json": {
            "protocol_schema": "confflow.control.v1",
            "supported_operations": ["capabilities", "prepare", "execute", "status", "events", "cancel", "resume", "artifacts"],
            "capabilities": {"producer_version": "1.5.1", "protocol_major": 1, "protocol_minor": 0},
        },
        "prepare.request.json": {
            "protocol_schema": "confflow.control.v1",
            "run_id": "run-1",
            "idempotency_key": "key-1",
            "workflow_config_digest": _DIGEST,
            "input_manifest_digest": _DIGEST,
            "expected_executable": {"realpath": "/opt/confflow/bin/confflow", "sha256": _DIGEST},
        },
        "prepare.response.json": {**base, "status": "prepared", "request_digest": _DIGEST},
        "execute.response.json": {**base, "status": "running"},
        "status.response.json": {**base, "status": "paused", "checkpoint": "checkpoint-1"},
        "events.response.json": {
            **base,
            "events": [{"cursor": "cursor-2", "revision": 2, "type": "run.paused", "data": {}}],
            "next_cursor": "cursor-2",
        },
        "cancel.response.json": {**base, "status": "cancelled"},
        "resume.response.json": {**base, "status": "running", "checkpoint": "checkpoint-1"},
        "artifacts.response.json": {
            **base,
            "artifacts": [{"terminal": "g16", "path": "g16/output.log", "sha256": _DIGEST, "size": 1, "content_schema": "confflow.output.v1"}],
        },
    }


def _errors(validator: Draft202012Validator, payload: dict[str, object]) -> list[object]:
    return list(validator.iter_errors(payload))


def test_all_expected_operation_schemas_are_frozen(schemas: dict[str, Draft202012Validator]) -> None:
    assert set(schemas) == {
        "artifacts.response.json",
        "cancel.response.json",
        "capabilities.response.json",
        "events.response.json",
        "execute.response.json",
        "prepare.request.json",
        "prepare.response.json",
        "resume.response.json",
        "status.response.json",
    }


@pytest.mark.parametrize("name", [
    "capabilities.response.json", "prepare.request.json", "prepare.response.json", "execute.response.json",
    "status.response.json", "events.response.json", "cancel.response.json", "resume.response.json", "artifacts.response.json",
])
def test_golden_fixtures_validate(name: str, schemas: dict[str, Draft202012Validator], golden: dict[str, dict[str, object]]) -> None:
    assert _errors(schemas[name], golden[name]) == []


@pytest.mark.parametrize("name", ["prepare.request.json", "prepare.response.json", "execute.response.json", "status.response.json", "events.response.json", "cancel.response.json", "resume.response.json", "artifacts.response.json"])
def test_run_scoped_response_rejects_missing_required_field(name: str, schemas: dict[str, Draft202012Validator], golden: dict[str, dict[str, object]]) -> None:
    payload = deepcopy(golden[name])
    payload.pop("run_id")
    assert _errors(schemas[name], payload)


@pytest.mark.parametrize("name", ["prepare.response.json", "execute.response.json", "status.response.json", "events.response.json", "cancel.response.json", "resume.response.json", "artifacts.response.json"])
def test_responses_reject_wrong_revision_type_and_extra_field(name: str, schemas: dict[str, Draft202012Validator], golden: dict[str, dict[str, object]]) -> None:
    wrong_type = deepcopy(golden[name])
    wrong_type["revision"] = "two"
    assert _errors(schemas[name], wrong_type)
    extra = deepcopy(golden[name])
    extra["unexpected"] = True
    assert _errors(schemas[name], extra)


def test_prepare_rejects_wrong_digest_and_extra_identity_field(schemas: dict[str, Draft202012Validator], golden: dict[str, dict[str, object]]) -> None:
    wrong_digest = deepcopy(golden["prepare.request.json"])
    wrong_digest["workflow_config_digest"] = "not-a-digest"
    assert _errors(schemas["prepare.request.json"], wrong_digest)
    extra_identity = deepcopy(golden["prepare.request.json"])
    expected = extra_identity["expected_executable"]
    assert isinstance(expected, dict)
    expected["path"] = "/untrusted"
    assert _errors(schemas["prepare.request.json"], extra_identity)


@pytest.mark.parametrize("name", ["execute.response.json", "status.response.json", "cancel.response.json", "resume.response.json"])
def test_state_responses_reject_unknown_status(name: str, schemas: dict[str, Draft202012Validator], golden: dict[str, dict[str, object]]) -> None:
    payload = deepcopy(golden[name])
    payload["status"] = "invented"
    assert _errors(schemas[name], payload)


@pytest.mark.parametrize("unsafe_path", ["/etc/passwd", "../escape", "a/../../escape", "C:/escape", "g16\\output.log"])
def test_artifacts_reject_unsafe_paths(unsafe_path: str, schemas: dict[str, Draft202012Validator], golden: dict[str, dict[str, object]]) -> None:
    payload = deepcopy(golden["artifacts.response.json"])
    artifacts = payload["artifacts"]
    assert isinstance(artifacts, list)
    artifact = artifacts[0]
    assert isinstance(artifact, dict)
    artifact["path"] = unsafe_path
    assert _errors(schemas["artifacts.response.json"], payload)


def test_events_reject_malformed_event(schemas: dict[str, Draft202012Validator], golden: dict[str, dict[str, object]]) -> None:
    payload = deepcopy(golden["events.response.json"])
    events = payload["events"]
    assert isinstance(events, list)
    events[0] = {"cursor": "cursor-2", "revision": "two", "type": "run.paused"}
    assert _errors(schemas["events.response.json"], payload)
