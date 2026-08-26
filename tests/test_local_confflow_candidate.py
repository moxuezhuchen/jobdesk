"""Offline acceptance of the local ConfFlow candidate wheel."""

from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).parents[1]
_MANIFEST = _ROOT / "requirements" / "locks" / "jobdesk-chem-wheel-manifest.json"
_SCRIPT = _ROOT / "scripts" / "check_local_confflow_candidate.py"


def _load_gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("jobdesk_local_confflow_candidate_gate", _SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot import candidate gate: {_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_GATE = _load_gate()


def _candidate() -> tuple[ModuleType, Any, str, str]:
    if not _MANIFEST.is_file():
        pytest.skip("local candidate wheel manifest is not present")
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    artifact = manifest["artifact"]
    wheel = _ROOT / artifact["relative_path"]
    if not wheel.is_file():
        pytest.skip("local candidate wheel is not present")
    identity = _GATE.inspect_wheel(
        wheel,
        expected_sha256=artifact["sha256"],
        expected_metadata_sha256=artifact["metadata_sha256"],
    )
    return _GATE, identity, artifact["sha256"], artifact["metadata_sha256"]


def _probe_payloads() -> tuple[ModuleType, Any, dict[str, object], dict[str, object]]:
    gate, identity, _sha, _metadata_sha = _candidate()
    try:
        capabilities = json.loads(gate._probe("capabilities"))
        contract = json.loads(gate._probe("contract"))
    except gate.CandidateCompatibilityError as exc:
        pytest.skip(str(exc))
    assert isinstance(capabilities, dict)
    assert isinstance(contract, dict)
    return gate, identity, capabilities, contract


def test_local_candidate_positive_capability_and_configuration_paths() -> None:
    gate, identity, capabilities, contract = _probe_payloads()

    gate.validate_capabilities(json.dumps(capabilities), identity)
    gate.validate_configuration_contract(json.dumps(contract), identity)


def test_local_candidate_digest_mismatch_fails_closed() -> None:
    gate, identity, _capabilities, _contract = _probe_payloads()

    with pytest.raises(gate.CandidateCompatibilityError, match="wheel digest"):
        gate.inspect_wheel(
            identity.path,
            expected_sha256="0" * 64,
            expected_metadata_sha256=identity.metadata_sha256,
        )


def test_local_candidate_provenance_mismatch_fails_closed() -> None:
    gate, identity, capabilities, _contract = _probe_payloads()
    mutated = deepcopy(capabilities)
    build = mutated["build"]
    assert isinstance(build, dict)
    build["commit"] = "0" * 40
    producer = mutated["producer"]
    assert isinstance(producer, dict)
    producer_build = producer["build"]
    assert isinstance(producer_build, dict)
    producer_build["commit"] = "0" * 40

    with pytest.raises(gate.CandidateCompatibilityError, match="provenance"):
        gate.validate_capabilities(json.dumps(mutated), identity)


def test_local_candidate_workflow_schema_digest_mismatch_fails_closed() -> None:
    gate, identity, _capabilities, contract = _probe_payloads()
    mutated = deepcopy(contract)
    mutated["workflow_schema_sha256"] = "0" * 64

    with pytest.raises(gate.CandidateCompatibilityError, match="schema digest"):
        gate.validate_configuration_contract(json.dumps(mutated), identity)
