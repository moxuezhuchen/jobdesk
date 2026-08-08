from __future__ import annotations

import json

import pytest

from jobdesk_app.core.confflow_contract import (
    CAPABILITY_SCHEMA_VERSION,
    EXPECTED_ARTIFACTS,
    LEGACY_REFERENCE_BUILD_COMMIT,
    LEGACY_REFERENCE_VERSION,
    LEGACY_REFERENCE_WHEEL_FILENAME,
    LEGACY_REFERENCE_WHEEL_SHA256,
    MIN_VERSION,
    REFERENCE_VERSION,
    REQUIRED_COMMANDS,
    version_spec,
)
from jobdesk_app.core.confflow_preflight import (
    ConfFlowCapabilities,
    parse_confflow_capabilities,
    validate_confflow_capabilities,
    validate_confflow_production_capability,
)


def _payload(**overrides) -> str:
    value = {
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "version": ".".join(map(str, MIN_VERSION)),
        "capabilities": {
            "workflow_state": True,
            "resume": True,
            "dag": True,
        },
        "artifacts": {
            "run_summary": EXPECTED_ARTIFACTS.run_summary,
            "workflow_stats": EXPECTED_ARTIFACTS.workflow_stats,
            "workflow_state": EXPECTED_ARTIFACTS.workflow_state,
            "output_manifest": EXPECTED_ARTIFACTS.output_manifest,
            "run_report": EXPECTED_ARTIFACTS.run_report,
            "min_xyz": EXPECTED_ARTIFACTS.min_xyz,
        },
        "commands": {name: True for name in REQUIRED_COMMANDS},
        "build": {"commit": "abc1234", "dirty": False},
        "producer": {
            "package": "confflow",
            "version": ".".join(map(str, MIN_VERSION)),
            "build": {"commit": "abc1234", "dirty": False},
            "wheel": {"filename": "confflow.whl", "sha256": "deadbeef"},
        },
        "executable": {"path": "/opt/confflow/bin/confflow", "sha256": "cafebabe", "python": "3.12"},
    }
    value.update(overrides)
    return json.dumps(value)


def test_parse_and_validate_supported_capabilities():
    capabilities = parse_confflow_capabilities(_payload())

    assert capabilities == ConfFlowCapabilities(
        CAPABILITY_SCHEMA_VERSION,
        ".".join(map(str, MIN_VERSION)),
        True,
        True,
        True,
        artifacts=EXPECTED_ARTIFACTS,
        commands={name: True for name in REQUIRED_COMMANDS},
        build={"commit": "abc1234", "dirty": False},
    )
    validate_confflow_capabilities(capabilities, require_dag=True)


def test_parser_reads_optional_control_worker_capability():
    payload = json.loads(_payload())
    payload["capabilities"]["control_worker"] = True

    capabilities = parse_confflow_capabilities(json.dumps(payload))

    assert capabilities.control_worker is True


def test_parser_rejects_non_boolean_control_worker_capability():
    payload = json.loads(_payload())
    payload["capabilities"]["control_worker"] = "yes"

    with pytest.raises(ValueError, match="control_worker must be boolean"):
        parse_confflow_capabilities(json.dumps(payload))


def test_legacy_stable_is_allowed_only_for_legacy_provenance_path():
    payload = json.loads(_payload())
    payload["version"] = LEGACY_REFERENCE_VERSION
    payload["build"] = {"commit": LEGACY_REFERENCE_BUILD_COMMIT, "dirty": False}
    payload["producer"] = {
        "package": "confflow",
        "version": LEGACY_REFERENCE_VERSION,
        "build": {"commit": LEGACY_REFERENCE_BUILD_COMMIT, "dirty": False},
        "wheel": {
            "filename": LEGACY_REFERENCE_WHEEL_FILENAME,
            "sha256": LEGACY_REFERENCE_WHEEL_SHA256,
        },
        "install_provenance": {"status": "verified"},
    }
    payload["executable"] = {
        "path": "/opt/confflow/bin/confflow",
        "sha256": "a" * 64,
        "python": "/opt/confflow/bin/python3.12",
    }
    capabilities = parse_confflow_capabilities(json.dumps(payload))

    with pytest.raises(ValueError, match=version_spec()):
        validate_confflow_capabilities(capabilities, require_dag=True)
    validate_confflow_capabilities(capabilities, require_dag=True, allow_legacy_stable=True)
    identity = validate_confflow_production_capability(capabilities, allow_legacy_stable=True)
    assert identity["path"] == "/opt/confflow/bin/confflow"


@pytest.mark.parametrize(
    "stdout, message",
    [
        ("", "empty"),
        ("not-json", "malformed"),
        ('{"schema_version":1,"schema_version":1}', "duplicate JSON key"),
        ('{"schema_version":2}', "version"),
        (
            _payload(capabilities={"workflow_state": True, "resume": True}),
            "dag must be boolean",
        ),
        (
            _payload(capabilities={"workflow_state": 1, "resume": True, "dag": True}),
            "must be boolean",
        ),
        ("[]", "expected an object"),
    ],
)
def test_parser_rejects_missing_or_malformed_output(stdout, message):
    with pytest.raises(ValueError, match=message):
        parse_confflow_capabilities(stdout)


def test_parser_tolerates_missing_artifacts_block_in_v1_payload():
    """v1 payloads (no artifacts) parse cleanly; the validator rejects them."""
    # v1 payload — no artifacts key. The parser must still return a value
    # whose artifacts is None so the validator can identify the schema
    # mismatch as the root cause.
    payload = json.dumps(
        {
            "schema_version": 1,
            "version": "1.4.1",
            "capabilities": {
                "workflow_state": True,
                "resume": True,
                "dag": True,
            },
        }
    )
    capabilities = parse_confflow_capabilities(payload)
    assert capabilities.schema_version == 1
    assert capabilities.artifacts is None
    with pytest.raises(ValueError, match="unsupported ConfFlow capability schema"):
        validate_confflow_capabilities(capabilities, require_dag=True)


@pytest.mark.parametrize(
    "capabilities, require_dag, message",
    [
        # Wrong schema → reset to v1 → rejected even with artifacts=None.
        (
            ConfFlowCapabilities(1, "1.4.1", True, True, True, artifacts=None),
            False,
            "unsupported ConfFlow capability schema",
        ),
        # Schema==2 but artifacts missing → still rejected.
        (
            ConfFlowCapabilities(4, REFERENCE_VERSION, True, True, True, artifacts=None, commands={name: True for name in REQUIRED_COMMANDS}),
            False,
            "requires an artifacts block",
        ),
        # Schema==2 but artifacts payload has a wrong filename.
        (
            ConfFlowCapabilities(
                4,
                REFERENCE_VERSION,
                True,
                True,
                True,
                artifacts=type(EXPECTED_ARTIFACTS)(
                    run_summary="WRONG",  # type: ignore[arg-type]
                    workflow_stats=EXPECTED_ARTIFACTS.workflow_stats,
                    workflow_state=EXPECTED_ARTIFACTS.workflow_state,
                    output_manifest=EXPECTED_ARTIFACTS.output_manifest,
                ),
            ),
            False,
            "artifacts contract mismatch",
        ),
        # Schema==2 but version is older than MIN_VERSION.
        (
            ConfFlowCapabilities(4, "1.4.4", True, True, True, artifacts=EXPECTED_ARTIFACTS, commands={name: True for name in REQUIRED_COMMANDS}),
            False,
            version_spec(),
        ),
        # Schema==2 but version is 1.4.2 prerelease → rejected.
        (
            ConfFlowCapabilities(4, "1.4.6-rc.1", True, True, True, artifacts=EXPECTED_ARTIFACTS, commands={name: True for name in REQUIRED_COMMANDS}),
            False,
            version_spec(),
        ),
        # Schema==2 but version is >= MAX_EXCLUSIVE.
        (
            ConfFlowCapabilities(4, "2.0.0", True, True, True, artifacts=EXPECTED_ARTIFACTS, commands={name: True for name in REQUIRED_COMMANDS}),
            False,
            version_spec(),
        ),
        # Schema==2 but version is malformed.
        (
            ConfFlowCapabilities(4, "1.04.3", True, True, True, artifacts=EXPECTED_ARTIFACTS, commands={name: True for name in REQUIRED_COMMANDS}),
            False,
            "semantic version",
        ),
        # Schema==2 but capability flags missing.
        (
            ConfFlowCapabilities(4, REFERENCE_VERSION, False, True, True, artifacts=EXPECTED_ARTIFACTS, commands={name: True for name in REQUIRED_COMMANDS}),
            False,
            "workflow_state",
        ),
        (
            ConfFlowCapabilities(4, REFERENCE_VERSION, True, False, True, artifacts=EXPECTED_ARTIFACTS, commands={name: True for name in REQUIRED_COMMANDS}),
            False,
            "resume",
        ),
        (
            ConfFlowCapabilities(4, REFERENCE_VERSION, True, True, False, artifacts=EXPECTED_ARTIFACTS, commands={name: True for name in REQUIRED_COMMANDS}),
            True,
            "dag",
        ),
    ],
)
def test_validator_fails_closed_on_incompatible_contract(capabilities, require_dag, message):
    with pytest.raises(ValueError, match=message):
        validate_confflow_capabilities(capabilities, require_dag=require_dag)


def test_linear_workflow_does_not_require_dag_capability():
    """ConfFlow 1.x prerelease > MIN_VERSION is accepted when dag is not needed."""
    validate_confflow_capabilities(
        ConfFlowCapabilities(
            CAPABILITY_SCHEMA_VERSION,
            "1.9.0-rc.1",
            True,
            True,
            False,
            artifacts=EXPECTED_ARTIFACTS,
            commands={name: True for name in REQUIRED_COMMANDS},
        ),
        require_dag=False,
    )


@pytest.mark.parametrize("version", ("1.9.0rc1", "1.9.0-rc.1"))
def test_validator_accepts_prerelease_above_minimum(version):
    validate_confflow_capabilities(
        ConfFlowCapabilities(
            CAPABILITY_SCHEMA_VERSION,
            version,
            True,
            True,
            True,
            artifacts=EXPECTED_ARTIFACTS,
            commands={name: True for name in REQUIRED_COMMANDS},
        ),
        require_dag=True,
    )


@pytest.mark.parametrize("version", ("1.4.6rc1", "1.4.6-rc.1"))
def test_validator_rejects_prerelease_at_minimum(version):
    with pytest.raises(ValueError, match="1.4.6"):
        validate_confflow_capabilities(
            ConfFlowCapabilities(
                CAPABILITY_SCHEMA_VERSION,
                version,
                True,
                True,
                True,
                artifacts=EXPECTED_ARTIFACTS,
                commands={name: True for name in REQUIRED_COMMANDS},
            ),
            require_dag=True,
        )



@pytest.mark.parametrize("missing_name", ("run_summary", "workflow_stats", "workflow_state", "output_manifest", "run_report", "min_xyz"))
def test_validator_rejects_missing_v4_artifacts(missing_name):
    payload = json.loads(_payload())
    del payload["artifacts"][missing_name]
    capabilities = parse_confflow_capabilities(json.dumps(payload))
    with pytest.raises(ValueError, match="requires an artifacts block"):
        validate_confflow_capabilities(capabilities, require_dag=False)


def test_validator_rejects_missing_or_false_commands():
    payload = json.loads(_payload())
    payload["commands"].pop("bash")
    capabilities = parse_confflow_capabilities(json.dumps(payload))
    with pytest.raises(ValueError, match="missing commands: bash"):
        validate_confflow_capabilities(capabilities, require_dag=False)
    payload = json.loads(_payload())
    payload["commands"]["bash"] = False
    capabilities = parse_confflow_capabilities(json.dumps(payload))
    with pytest.raises(ValueError, match="missing commands: bash"):
        validate_confflow_capabilities(capabilities, require_dag=False)


def test_parser_rejects_invalid_commands_and_build_types():
    with pytest.raises(ValueError, match="commands must map"):
        parse_confflow_capabilities(_payload(commands={"bash": "yes"}))
    with pytest.raises(ValueError, match="build.dirty"):
        parse_confflow_capabilities(_payload(build={"commit": "abc1234", "dirty": "no"}))
def test_validator_accepts_legal_v3_payload_with_extra_unknown_keys():
    """Forward compatibility: extra top-level keys are tolerated."""
    payload = _payload(experimental_feature=True)
    capabilities = parse_confflow_capabilities(payload)
    validate_confflow_capabilities(capabilities, require_dag=True)
