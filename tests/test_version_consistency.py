#!/usr/bin/env python3

"""Pin the producer-side version window across all the surface mirrors.

The single source of truth for the ConfFlow version window that JobDesk
accepts is the structured tuple in
:mod:`jobdesk_app.core.confflow_contract`:

* ``MIN_VERSION = (1, 4, 5)``
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
    assert MIN_VERSION == (1, 4, 5)
    assert MAX_EXCLUSIVE == (2, 0, 0)
    assert version_spec() == ">=1.4.5,<2.0"


def test_pyproject_pin_matches_spec():
    """``pyproject.toml`` ``confflow`` pin must be the version spec."""
    content = _read("pyproject.toml")
    expected = "confflow>=1.4.5,<2.0"
    assert expected in content, f"pyproject.toml must contain {expected!r}"


def test_ci_yaml_uses_version_in_all_four_slots():
    """CI must reference the v1.4.5 tag and released wheel in all four slots."""
    content = _read(".github/workflows/ci.yml")
    assert "1.4.1" not in content, "ci.yml must not contain any 1.4.1 reference"
    assert content.count("ref: v1.4.5") == 2
    assert content.count("confflow-1.4.5-*.whl") == 2
    assert content.count("confflow.__version__ == '1.4.5'") == 2
    assert content.count("7f2d0a6fd9d77ce31197bb304460cb3443c1abaa4cb920443d66a2eacaccb188") == 2


def test_ci_yaml_wheel_glob_matches_wheel_name():
    """PowerShell wheel glob must match the version literal in the assert."""
    content = _read(".github/workflows/ci.yml")
    assert content.count("confflow-1.4.5-*.whl") == 2
    assert content.count("confflow.__version__ == '1.4.5'") == 2


def test_readme_states_version_spec():
    """README must state the version spec and the v4 schema."""
    content = _read("README.md")
    assert "confflow>=1.4.5,<2.0" in content
    assert "1.4.1" not in content
    assert "schema_version=4" in content
    assert "run_summary.json" in content
    assert "workflow_stats.json" in content
    assert ".workflow_state.json" in content
    assert "output_manifest.json" in content
    assert "{basename}.txt" in content
    assert "{basename}min.xyz" in content
    assert "CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md" in content


def test_deployment_doc_mirrors_version_and_capability_contract():
    """The deployment guide must mirror the structured version contract."""
    content = _read("docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md")
    assert "confflow>=1.4.5,<2.0" in content
    assert "confflow-1.4.5-py3-none-any.whl" in content
    assert "1.4.1" not in content
    assert "CONFFLOW_1_4_1" not in content
    assert '"schema_version": 4' in content
    for filename in ("run_summary.json", "workflow_stats.json", ".workflow_state.json", "output_manifest.json", "{basename}.txt", "{basename}min.xyz"):
        assert filename in content


def test_preflight_module_has_no_bare_version_literal():
    """The preflight module must source its version window from the
    structured tuple, not from a string literal.
    """
    content = _read("src/jobdesk_app/core/confflow_preflight.py")
    assert "1.4.5" not in content, (
        "confflow_preflight.py must not contain the bare literal '1.4.5'; "
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

    # Build a v4 payload with a too-old version and assert the validator
    # complaint quotes the structured spec.
    payload = (
        '{"schema_version": 4, "version": "1.4.1", '
        '"capabilities": {"workflow_state": true, "resume": true, "dag": true}, '
        '"artifacts": {"run_summary": "run_summary.json", '
        '"workflow_stats": "workflow_stats.json", '
        '"workflow_state": ".workflow_state.json", "output_manifest": "output_manifest.json", "run_report": "{basename}.txt", "min_xyz": "{basename}min.xyz"}, ' +
        '"commands": {"bash": true, "nohup": true, "setsid": true, "xargs": true, "sha256sum": true, "mktemp": true, "base64": true}, "build": {"commit": "abc1234", "dirty": false}, '
        '"producer": {"package": "confflow", "version": "1.4.1", "build": {"commit": "abc1234", "dirty": false}, "wheel": {"filename": "confflow.whl", "sha256": "deadbeef"}}, '
        '"executable": {"path": "/opt/confflow/bin/confflow", "sha256": "cafebabe", "python": "3.12"}}'
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
        output_manifest="output_manifest.json",
        run_report="{basename}.txt",
        min_xyz="{basename}min.xyz",
    )
