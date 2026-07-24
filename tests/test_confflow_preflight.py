from __future__ import annotations

import json

import pytest

from jobdesk_app.core.confflow_contract import (
    CAPABILITY_SCHEMA_VERSION,
    EXPECTED_ARTIFACTS,
    MIN_VERSION,
)
from jobdesk_app.core.confflow_preflight import (
    ConfFlowCapabilities,
    parse_confflow_capabilities,
    validate_confflow_capabilities,
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
        },
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
    )
    validate_confflow_capabilities(capabilities, require_dag=True)


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
            ConfFlowCapabilities(2, "1.4.2", True, True, True, artifacts=None),
            False,
            "requires an artifacts block",
        ),
        # Schema==2 but artifacts payload has a wrong filename.
        (
            ConfFlowCapabilities(
                2,
                "1.4.2",
                True,
                True,
                True,
                artifacts=type(EXPECTED_ARTIFACTS)(
                    run_summary="WRONG",  # type: ignore[arg-type]
                    workflow_stats=EXPECTED_ARTIFACTS.workflow_stats,
                    workflow_state=EXPECTED_ARTIFACTS.workflow_state,
                ),
            ),
            False,
            "artifacts contract mismatch",
        ),
        # Schema==2 but version is older than MIN_VERSION.
        (
            ConfFlowCapabilities(2, "1.4.1", True, True, True, artifacts=EXPECTED_ARTIFACTS),
            False,
            "1.4.2",
        ),
        # Schema==2 but version is 1.4.2 prerelease → rejected.
        (
            ConfFlowCapabilities(2, "1.4.2-rc.1", True, True, True, artifacts=EXPECTED_ARTIFACTS),
            False,
            "1.4.2",
        ),
        # Schema==2 but version is >= MAX_EXCLUSIVE.
        (
            ConfFlowCapabilities(2, "2.0.0", True, True, True, artifacts=EXPECTED_ARTIFACTS),
            False,
            "1.4.2",
        ),
        # Schema==2 but version is malformed.
        (
            ConfFlowCapabilities(2, "1.04.2", True, True, True, artifacts=EXPECTED_ARTIFACTS),
            False,
            "semantic version",
        ),
        # Schema==2 but capability flags missing.
        (
            ConfFlowCapabilities(2, "1.4.2", False, True, True, artifacts=EXPECTED_ARTIFACTS),
            False,
            "workflow_state",
        ),
        (
            ConfFlowCapabilities(2, "1.4.2", True, False, True, artifacts=EXPECTED_ARTIFACTS),
            False,
            "resume",
        ),
        (
            ConfFlowCapabilities(2, "1.4.2", True, True, False, artifacts=EXPECTED_ARTIFACTS),
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
        ),
        require_dag=False,
    )


def test_validator_accepts_legal_v2_payload_with_extra_unknown_keys():
    """Forward compatibility: extra top-level keys are tolerated."""
    payload = _payload(experimental_feature=True)
    capabilities = parse_confflow_capabilities(payload)
    validate_confflow_capabilities(capabilities, require_dag=True)
