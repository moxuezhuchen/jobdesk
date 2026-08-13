"""Parity tests for the pinned producer schema bundle."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

_SCHEMA_ROOT = Path(__file__).parents[1] / "confflow" / "schemas" / "control"
_PINNED_SCHEMA_HASHES = {
    "common.schema.json": "494983e47ba7570c73e0d72b77df32b3ec877a2122ded40818c6369054830bc1",
    "requests.schema.json": "72b0beab10e6cb380d66e11b5757a750efe1271d43be0098166e87b59af623c3",
    "responses.schema.json": "312e7b88047a20015080877903b63aa52df850c07a2a45fb023a30179e7d86b3",
    "input-manifest.schema.json": "b0a98bf2b758733de054c67baaf440d2839be37013ba40d09365d73f790daf97",
    "worker-handoff.schema.json": "8c8bed4cc9550a466bc8fc7b010bd2857d4d34efc6b381f5a7a62573f3169459",
}
_SCHEMA_NAMES = tuple(_PINNED_SCHEMA_HASHES)
_RELEASE_SCHEMA_HASHES = {
    "v2.0.0": {
        "common.schema.json": "494983e47ba7570c73e0d72b77df32b3ec877a2122ded40818c6369054830bc1",
        "requests.schema.json": "72b0beab10e6cb380d66e11b5757a750efe1271d43be0098166e87b59af623c3",
        "responses.schema.json": "11c70a0d40063409e1f6aff3a74a3951cda0c573fe0ea7f4850c38c000dd886b",
        "input-manifest.schema.json": "b0a98bf2b758733de054c67baaf440d2839be37013ba40d09365d73f790daf97",
        "worker-handoff.schema.json": "8c8bed4cc9550a466bc8fc7b010bd2857d4d34efc6b381f5a7a62573f3169459",
    },
    "v1.5.3": {
        "common.schema.json": "494983e47ba7570c73e0d72b77df32b3ec877a2122ded40818c6369054830bc1",
        "requests.schema.json": "72b0beab10e6cb380d66e11b5757a750efe1271d43be0098166e87b59af623c3",
        "responses.schema.json": "11c70a0d40063409e1f6aff3a74a3951cda0c573fe0ea7f4850c38c000dd886b",
        "input-manifest.schema.json": "b0a98bf2b758733de054c67baaf440d2839be37013ba40d09365d73f790daf97",
        "worker-handoff.schema.json": "8c8bed4cc9550a466bc8fc7b010bd2857d4d34efc6b381f5a7a62573f3169459",
    },
    "v1.5.0": {
        "common.schema.json": "494983e47ba7570c73e0d72b77df32b3ec877a2122ded40818c6369054830bc1",
        "requests.schema.json": "72b0beab10e6cb380d66e11b5757a750efe1271d43be0098166e87b59af623c3",
        "responses.schema.json": "11c70a0d40063409e1f6aff3a74a3951cda0c573fe0ea7f4850c38c000dd886b",
        "input-manifest.schema.json": "b0a98bf2b758733de054c67baaf440d2839be37013ba40d09365d73f790daf97",
    },
}
_DIGEST = "a" * 64


def _load(name: str) -> dict[str, Any]:
    value = json.loads((_SCHEMA_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_release(version: str, name: str) -> dict[str, Any]:
    value = json.loads(
        (_SCHEMA_ROOT / "releases" / version / name).read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


@pytest.fixture(scope="module")
def schemas() -> dict[str, Draft202012Validator]:
    documents = [_load(name) for name in _SCHEMA_NAMES]
    registry = Registry().with_resources(
        (str(document["$id"]), Resource.from_contents(document)) for document in documents
    )
    return {
        name: Draft202012Validator(document, registry=registry)
        for name, document in zip(_SCHEMA_NAMES, documents, strict=True)
    }


def _prepare_request() -> dict[str, object]:
    return {
        "protocol_schema": "confflow.control.v1",
        "operation": "prepare",
        "run_id": "run-1",
        "idempotency_key": "jobdesk.run-1",
        "request_digest": _DIGEST,
        "workflow_config": {"path": "workflow.yaml", "sha256": _DIGEST},
        "input_manifest": {"path": "input-manifest.json", "sha256": _DIGEST},
        "expected_executable_identity": {
            "realpath": "/opt/confflow/bin/python3.12",
            "sha256": _DIGEST,
            "device_inode": "8:1234",
        },
    }


def _snapshot(operation: str, state: str, *, revision: int = 2) -> dict[str, object]:
    return {
        "protocol_schema": "confflow.control.v1",
        "operation": operation,
        "ok": True,
        "run_id": "run-1",
        "revision": revision,
        "state": state,
    }


def test_snapshot_matches_pinned_producer_bundle() -> None:
    for name, expected in _PINNED_SCHEMA_HASHES.items():
        canonical = json.dumps(
            _load(name), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == expected


def test_release_snapshots_match_immutable_producer_bundles() -> None:
    for version, expected_files in _RELEASE_SCHEMA_HASHES.items():
        release_root = _SCHEMA_ROOT / "releases" / version
        assert {path.name for path in release_root.glob("*.json")} == set(expected_files)
        for name, expected in expected_files.items():
            canonical = json.dumps(
                _load_release(version, name),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            assert hashlib.sha256(canonical).hexdigest() == expected


@pytest.mark.parametrize("version", tuple(_RELEASE_SCHEMA_HASHES))
def test_release_snapshots_keep_terminal_cancel_contract(version: str) -> None:
    cancel = _load_release(version, "responses.schema.json")["$defs"]["cancel"]
    state = cancel["allOf"][2]["properties"]["state"]
    assert state == {"const": "cancelled"}


def test_candidate_snapshot_remains_async_cancel_contract() -> None:
    cancel = _load("responses.schema.json")["$defs"]["cancel"]
    state = cancel["allOf"][2]["properties"]["state"]
    assert state == {"enum": ["queued", "running", "paused", "cancelled"]}


def test_bundle_contains_pinned_release_schema_files() -> None:
    assert {path.name for path in _SCHEMA_ROOT.glob("*.json")} == set(_SCHEMA_NAMES)


def test_worker_handoff_schema_has_pinned_release_digest() -> None:
    name = "worker-handoff.schema.json"
    expected = _PINNED_SCHEMA_HASHES[name]
    canonical = json.dumps(_load(name), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    assert hashlib.sha256(canonical).hexdigest() == expected


def test_prepare_request_uses_producer_locator_and_identity_shape(schemas) -> None:
    request = _prepare_request()
    assert list(schemas["requests.schema.json"].iter_errors(request)) == []


@pytest.mark.parametrize(
    "response",
    [
        {
            "protocol_schema": "confflow.control.v1",
            "operation": "capabilities",
            "ok": True,
            "supported_protocols": ["confflow.control.v1"],
        },
        _snapshot("prepare", "prepared"),
        _snapshot("execute", "queued"),
        _snapshot("status", "paused"),
        {
            **_snapshot("events", "running"),
            "events": [{"cursor": "cursor-2", "revision": 2, "type": "run.paused"}],
            "next_cursor": "cursor-2",
        },
        _snapshot("cancel", "running"),
        _snapshot("resume", "running"),
        {
            **_snapshot("artifacts", "completed"),
            "artifacts": [
                {
                    "terminal": "g16",
                    "path": "g16/output.log",
                    "sha256": _DIGEST,
                    "size": 1,
                    "content_schema": "confflow.output.v1",
                }
            ],
        },
    ],
)
def test_producer_response_examples_validate(response, schemas) -> None:
    assert list(schemas["responses.schema.json"].iter_errors(response)) == []


def test_old_jobdesk_only_shape_is_rejected(schemas) -> None:
    stale = {
        "protocol_schema": "confflow.control.v1",
        "operation": "capabilities",
        "ok": True,
        "supported_operations": ["capabilities", "prepare"],
    }
    assert list(schemas["responses.schema.json"].iter_errors(stale))


def test_wrong_status_field_is_rejected(schemas) -> None:
    stale = _snapshot("status", "running")
    stale["status"] = stale.pop("state")
    assert list(schemas["responses.schema.json"].iter_errors(stale))


def test_prepare_digest_fields_are_rejected(schemas) -> None:
    stale = _prepare_request()
    stale["workflow_config_digest"] = stale.pop("workflow_config")["sha256"]  # type: ignore[index]
    stale["input_manifest_digest"] = stale.pop("input_manifest")["sha256"]  # type: ignore[index]
    stale["expected_executable"] = stale.pop("expected_executable_identity")
    assert list(schemas["requests.schema.json"].iter_errors(stale))


def test_artifact_paths_remain_relative_and_safe(schemas) -> None:
    response = {
        **_snapshot("artifacts", "completed"),
        "artifacts": [
            {
                "terminal": "g16",
                "path": "../escape",
                "sha256": _DIGEST,
                "size": 1,
                "content_schema": "confflow.output.v1",
            }
        ],
    }
    assert list(schemas["responses.schema.json"].iter_errors(response))


def test_input_manifest_schema_rejects_duplicate_or_unsafe_shape(schemas) -> None:
    manifest = {
        "content_schema": "confflow.control.input-manifest.v1",
        "inputs": [{"ordinal": 0, "path": "../escape", "sha256": _DIGEST, "size": 1}],
    }
    assert list(schemas["input-manifest.schema.json"].iter_errors(manifest))


def test_worker_handoff_schema_binds_one_absolute_task(schemas) -> None:
    handoff = {
        "content_schema": "confflow.control.worker-handoff.v1",
        "run_id": "run-1",
        "workflow_config": {"path": "/tmp/run-1/workflow.yaml", "sha256": _DIGEST},
        "tasks": [
            {
                "task_id": "methane",
                "input_xyz": "/tmp/run-1/methane.xyz",
                "work_dir": "/tmp/run-1/methane_work",
                "sha256": _DIGEST,
            }
        ],
    }
    assert list(schemas["worker-handoff.schema.json"].iter_errors(handoff)) == []
    handoff["tasks"] = [handoff["tasks"][0], handoff["tasks"][0]]  # type: ignore[list-item]
    assert list(schemas["worker-handoff.schema.json"].iter_errors(handoff))


def test_schema_negative_copy_is_not_mutated_by_validation(schemas) -> None:
    response = _snapshot("status", "running")
    candidate = deepcopy(response)
    candidate["extra"] = True
    assert list(schemas["responses.schema.json"].iter_errors(candidate))
    assert response == _snapshot("status", "running")
