#!/usr/bin/env python3

"""Pin the producer-side version window across all the surface mirrors.

The single source of truth for the ConfFlow version window that JobDesk
accepts is the structured tuple in
:mod:`jobdesk_app.core.confflow_contract`:

* ``MIN_VERSION = (1, 4, 2)``
* ``MAX_EXCLUSIVE = (2, 0, 0)``

Every other surface — ``pyproject.toml``, the GitHub Actions workflow
(4 slots), the README, and the offline subset validator error messages
— must be a *mirror* of these tuples. Any drift between the source of
truth and a mirror is a real bug (or a release-train bug if it
slipped through CI), and must fail this test module.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jobdesk_app.core import confflow_contract
from jobdesk_app.core.confflow_contract import (
    MAX_EXCLUSIVE,
    MIN_VERSION,
    ConfFlowArtifactContract,
    version_spec,
)
from jobdesk_app.core.confflow_preflight import validate_confflow_capabilities

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_structured_source_of_truth():
    """Lock the structured tuple so the rest of the suite mirrors it."""
    assert MIN_VERSION == (1, 4, 2)
    assert MAX_EXCLUSIVE == (2, 0, 0)
    assert version_spec() == ">=1.4.2,<2.0"


def test_pyproject_pin_matches_spec():
    """``pyproject.toml`` ``confflow`` pin must be the version spec."""
    content = _read("pyproject.toml")
    expected = "confflow>=1.4.2,<2.0"
    assert expected in content, f"pyproject.toml must contain {expected!r}"


def test_ci_yaml_uses_version_in_all_four_slots():
    """CI must reference ``1.4.2`` in all four slots (ref × 2, glob × 2)."""
    content = _read(".github/workflows/ci.yml")
    assert "1.4.1" not in content, "ci.yml must not contain any 1.4.1 reference"
    assert content.count("ref: v1.4.2") == 2
    assert content.count("confflow-1.4.2-*.whl") == 2
    assert content.count("confflow.__version__ == '1.4.2'") == 2


def test_ci_yaml_wheel_glob_matches_wheel_name():
    """PowerShell wheel glob must match the version literal in the assert."""
    content = _read(".github/workflows/ci.yml")
    assert content.count("confflow-1.4.2-*.whl") == 2
    assert content.count("confflow.__version__ == '1.4.2'") == 2


def test_readme_states_version_spec():
    """README must state the version spec and the v2 schema."""
    content = _read("README.md")
    assert "confflow>=1.4.2,<2.0" in content
    assert "1.4.1" not in content
    assert "schema_version=2" in content
    assert "run_summary.json" in content
    assert "workflow_stats.json" in content
    assert ".workflow_state.json" in content
    assert "CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md" in content


def test_deployment_doc_mirrors_version_and_capability_contract():
    """The deployment guide must mirror the structured version contract."""
    content = _read("docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md")
    assert "confflow>=1.4.2,<2.0" in content
    assert content.count("confflow-1.4.2-py3-none-any.whl") == 3
    assert "1.4.1" not in content
    assert "CONFFLOW_1_4_1" not in content
    assert '"schema_version": 2' in content
    for filename in ("run_summary.json", "workflow_stats.json", ".workflow_state.json"):
        assert filename in content


def test_preflight_module_has_no_bare_version_literal():
    """The preflight module must source its version window from the
    structured tuple, not from a string literal.
    """
    content = _read("src/jobdesk_app/core/confflow_preflight.py")
    assert "1.4.2" not in content, (
        "confflow_preflight.py must not contain the bare literal '1.4.2'; "
        "it must source the spec from MIN_VERSION/MAX_EXCLUSIVE."
    )
    assert "2.0.0" not in content, (
        "confflow_preflight.py must not contain the bare literal '2.0.0'; it must source the cap from MAX_EXCLUSIVE."
    )
    # SOURCE_OF_TRUTH imports must be present.
    assert "from .confflow_contract import" in content
    assert "MIN_VERSION" in content
    assert "MAX_EXCLUSIVE" in content
    assert "version_spec" in content


def test_validator_error_message_uses_version_spec():
    """The validator's error message must surface the version spec, not
    a hand-typed literal.
    """
    from jobdesk_app.core import confflow_contract as cc

    # Build a v2 payload with a too-old version and assert the validator
    # complaint quotes the structured spec.
    payload = (
        '{"schema_version": 2, "version": "1.4.1", '
        '"capabilities": {"workflow_state": true, "resume": true, "dag": true}, '
        '"artifacts": {"run_summary": "run_summary.json", '
        '"workflow_stats": "workflow_stats.json", '
        '"workflow_state": ".workflow_state.json"}}'
    )
    from jobdesk_app.core.confflow_preflight import parse_confflow_capabilities

    caps = parse_confflow_capabilities(payload)
    with pytest.raises(ValueError, match=re.escape(cc.version_spec())):
        validate_confflow_capabilities(caps, require_dag=True)


def test_artifact_contract_value_is_pinned():
    """The expected artifact contract is structural; renaming any
    filename is a wire-protocol break.
    """
    assert confflow_contract.EXPECTED_ARTIFACTS == ConfFlowArtifactContract(
        run_summary="run_summary.json",
        workflow_stats="workflow_stats.json",
        workflow_state=".workflow_state.json",
    )
