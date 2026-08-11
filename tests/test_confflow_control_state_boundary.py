from __future__ import annotations

import os
from pathlib import Path

import pytest

from jobdesk_app.services.confflow_control_state import (
    CONTROL_STATE_FILENAME,
    load_state,
    save_state,
    state_path,
)
from jobdesk_app.services.run_service import RunService

_GOLDEN_STATE = {
    "backend": "control",
    "content_schema": "jobdesk.confflow.backend.v1",
    "metadata": {"label": "氢", "priority": 1},
    "run_id": "run-golden",
    "state": "prepared",
}
_GOLDEN_TEXT = (
    "{\n"
    '  "backend": "control",\n'
    '  "content_schema": "jobdesk.confflow.backend.v1",\n'
    '  "metadata": {\n'
    '    "label": "氢",\n'
    '    "priority": 1\n'
    "  },\n"
    '  "run_id": "run-golden",\n'
    '  "state": "prepared"\n'
    "}\n"
)
_GOLDEN_BYTES = _GOLDEN_TEXT.replace("\n", os.linesep).encode("utf-8")


@pytest.fixture
def service(tmp_path: Path) -> RunService:
    return RunService(tmp_path, runs_dir=tmp_path / "runs")


def test_control_state_path_keeps_existing_filename(service: RunService) -> None:
    assert state_path(service, "run-golden") == service.runs_dir / "run-golden" / CONTROL_STATE_FILENAME


def test_save_state_preserves_golden_bytes_and_loads_a_copy(service: RunService) -> None:
    save_state(service, "run-golden", _GOLDEN_STATE)
    path = state_path(service, "run-golden")

    assert path.read_bytes() == _GOLDEN_BYTES
    loaded = load_state(service, "run-golden")
    assert loaded == _GOLDEN_STATE
    assert loaded is not _GOLDEN_STATE
    assert loaded["metadata"] is not _GOLDEN_STATE["metadata"]

    loaded_metadata = loaded["metadata"]
    assert isinstance(loaded_metadata, dict)
    loaded_metadata["label"] = "changed"
    assert load_state(service, "run-golden") == _GOLDEN_STATE
    assert path.read_bytes() == _GOLDEN_BYTES


def test_load_state_accepts_existing_golden_bytes_without_rewriting(service: RunService) -> None:
    path = state_path(service, "run-golden")
    path.parent.mkdir(parents=True)
    path.write_bytes(_GOLDEN_BYTES)

    assert load_state(service, "run-golden") == _GOLDEN_STATE
    assert path.read_bytes() == _GOLDEN_BYTES


def test_missing_control_state_is_distinguished_from_invalid_state(service: RunService) -> None:
    assert load_state(service, "run-missing") is None

    path = state_path(service, "run-invalid")
    path.parent.mkdir(parents=True)
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid durable ConfFlow backend state"):
        load_state(service, "run-invalid")


@pytest.mark.parametrize(
    ("raw_json", "message"),
    [
        ('["control"]', "expected object"),
        ('{"backend": "legacy", "run_id": "run-invalid"}', "retired ConfFlow backend"),
        ('{"backend": "control", "run_id": "other"}', "run_id mismatch"),
    ],
)
def test_load_state_fail_closed_validation(
    service: RunService,
    raw_json: str,
    message: str,
) -> None:
    path = state_path(service, "run-invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_json, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_state(service, "run-invalid")


def test_save_state_rejects_invalid_backend_before_replacing_existing_bytes(service: RunService) -> None:
    save_state(service, "run-golden", _GOLDEN_STATE)
    path = state_path(service, "run-golden")

    with pytest.raises(ValueError, match="backend must be control"):
        save_state(service, "run-golden", {**_GOLDEN_STATE, "backend": "legacy"})

    assert path.read_bytes() == _GOLDEN_BYTES
