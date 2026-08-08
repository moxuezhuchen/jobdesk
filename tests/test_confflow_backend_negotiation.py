from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from jobdesk_app.core.confflow_contract import (
    CAPABILITY_SCHEMA_VERSION,
    EXPECTED_ARTIFACTS,
    LEGACY_REFERENCE_BUILD_COMMIT,
    LEGACY_REFERENCE_VERSION,
    LEGACY_REFERENCE_WHEEL_FILENAME,
    LEGACY_REFERENCE_WHEEL_SHA256,
    REFERENCE_BUILD_COMMIT,
    REFERENCE_VERSION,
    REFERENCE_WHEEL_FILENAME,
    REFERENCE_WHEEL_SHA256,
    REQUIRED_COMMANDS,
)
from jobdesk_app.core.confflow_preflight import parse_confflow_capabilities
from jobdesk_app.services.ssh_confflow_client import SSHConfFlowClient


class _Coordinator:
    def __init__(self, executable: str = "/opt/confflow/bin/confflow") -> None:
        self.server = SimpleNamespace(confflow_executable=executable, env_init_scripts=[])

    def _server_lookup(self, _server_id: str):
        return self.server


def _capability(
    *,
    version: str,
    commit: str,
    wheel_filename: str,
    wheel_sha256: str,
    executable: str = "/opt/confflow/bin/confflow",
):
    payload = {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "version": version,
        "capabilities": {"workflow_state": True, "resume": True, "dag": True},
        "artifacts": {
            "run_summary": EXPECTED_ARTIFACTS.run_summary,
            "workflow_stats": EXPECTED_ARTIFACTS.workflow_stats,
            "workflow_state": EXPECTED_ARTIFACTS.workflow_state,
            "output_manifest": EXPECTED_ARTIFACTS.output_manifest,
            "run_report": EXPECTED_ARTIFACTS.run_report,
            "min_xyz": EXPECTED_ARTIFACTS.min_xyz,
        },
        "commands": {name: True for name in REQUIRED_COMMANDS},
        "build": {"commit": commit, "dirty": False},
        "producer": {
            "package": "confflow",
            "version": version,
            "build": {"commit": commit, "dirty": False},
            "wheel": {"filename": wheel_filename, "sha256": wheel_sha256},
            "install_provenance": {"status": "verified"},
        },
        "install_provenance": {"status": "verified"},
        "executable": {
            "path": executable,
            "realpath": executable,
            "sha256": "a" * 64,
            "python": "/opt/confflow/bin/python3.12",
        },
    }
    return parse_confflow_capabilities(json.dumps(payload))


def _current_capability():
    return _capability(
        version=REFERENCE_VERSION,
        commit=REFERENCE_BUILD_COMMIT,
        wheel_filename=REFERENCE_WHEEL_FILENAME,
        wheel_sha256=REFERENCE_WHEEL_SHA256,
    )


def _legacy_capability():
    return _capability(
        version=LEGACY_REFERENCE_VERSION,
        commit=LEGACY_REFERENCE_BUILD_COMMIT,
        wheel_filename=LEGACY_REFERENCE_WHEEL_FILENAME,
        wheel_sha256=LEGACY_REFERENCE_WHEEL_SHA256,
    )


def test_explicit_control_requires_current_production_provenance() -> None:
    coordinator = _Coordinator()
    client = SSHConfFlowClient(
        coordinator,
        "server",
        control_capability_factory=lambda: "/tmp/control-state",
        backend_mode="control",
    )

    client._negotiate_backend(_current_capability())
    assert client._selected_backend == "control"

    with pytest.raises(ValueError, match="approved release|version"):
        client._negotiate_backend(_legacy_capability())


def test_auto_selects_legacy_only_after_exact_stable_rollback_gate() -> None:
    coordinator = _Coordinator()
    client = SSHConfFlowClient(coordinator, "server", backend_mode="auto")

    client._negotiate_backend(_legacy_capability())
    assert client._selected_backend == "legacy"

    next_candidate = _capability(
        version="1.5.1",
        commit="b" * 40,
        wheel_filename="confflow-1.5.1-py3-none-any.whl",
        wheel_sha256="b" * 64,
    )
    with pytest.raises(ValueError, match="approved release|version"):
        client._negotiate_backend(next_candidate)
