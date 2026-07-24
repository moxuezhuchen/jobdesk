from __future__ import annotations

import json

import pytest

from jobdesk_app.core.confflow_preflight import (
    ConfFlowCapabilities,
    parse_confflow_capabilities,
    validate_confflow_capabilities,
)


def _payload(**overrides) -> str:
    value = {
        "schema_version": 1,
        "version": "1.4.1",
        "capabilities": {
            "workflow_state": True,
            "resume": True,
            "dag": True,
        },
    }
    value.update(overrides)
    return json.dumps(value)


def test_parse_and_validate_supported_capabilities():
    capabilities = parse_confflow_capabilities(_payload())

    assert capabilities == ConfFlowCapabilities(1, "1.4.1", True, True, True)
    validate_confflow_capabilities(capabilities, require_dag=True)


@pytest.mark.parametrize(
    "stdout, message",
    [
        ("", "empty"),
        ("not-json", "malformed"),
        ('{"schema_version":1,"schema_version":1}', "duplicate JSON key"),
        ('{"schema_version":1}', "version"),
        (_payload(capabilities={"workflow_state": True, "resume": True}), "dag must be boolean"),
        (_payload(capabilities={"workflow_state": 1, "resume": True, "dag": True}), "must be boolean"),
        ("[]", "expected an object"),
    ],
)
def test_parser_rejects_missing_or_malformed_output(stdout, message):
    with pytest.raises(ValueError, match=message):
        parse_confflow_capabilities(stdout)


@pytest.mark.parametrize(
    "capabilities, require_dag, message",
    [
        (ConfFlowCapabilities(2, "1.4.1", True, True, True), False, "schema"),
        (ConfFlowCapabilities(1, "1.3.9", True, True, True), False, ">=1.4.1,<2.0"),
        (ConfFlowCapabilities(1, "1.4.0", True, True, True), False, ">=1.4.1,<2.0"),
        (ConfFlowCapabilities(1, "1.4.0-alpha.1", True, True, True), False, ">=1.4.1,<2.0"),
        (ConfFlowCapabilities(1, "2.0.0", True, True, True), False, ">=1.4.1,<2.0"),
        (ConfFlowCapabilities(1, "1.04.0", True, True, True), False, "semantic version"),
        (ConfFlowCapabilities(1, "1.4.1", False, True, True), False, "workflow_state"),
        (ConfFlowCapabilities(1, "1.4.1", True, False, True), False, "resume"),
        (ConfFlowCapabilities(1, "1.4.1", True, True, False), True, "dag"),
    ],
)
def test_validator_fails_closed_on_incompatible_contract(capabilities, require_dag, message):
    with pytest.raises(ValueError, match=message):
        validate_confflow_capabilities(capabilities, require_dag=require_dag)


def test_linear_workflow_does_not_require_dag_capability():
    validate_confflow_capabilities(
        ConfFlowCapabilities(1, "1.9.0-rc.1", True, True, False),
        require_dag=False,
    )
