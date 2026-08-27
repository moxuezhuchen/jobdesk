#!/usr/bin/env python3

"""Pin the producer-side version window across all the surface mirrors.

The single source of truth for the ConfFlow version window that JobDesk
accepts is the structured tuple in
:mod:`jobdesk_app.core.confflow_contract`:

* ``MIN_VERSION = (2, 0, 0)``
* ``MAX_EXCLUSIVE = (3, 0, 0)``

Every other surface (``pyproject.toml``, the GitHub Actions workflow
(4 slots), the README, and the offline subset validator error messages
must be a *mirror* of these tuples. Any drift between the source of
truth and a mirror is a real bug (or a release-train bug if it
slipped through CI), and must fail this test module.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from jobdesk_app.core import confflow_contract
from jobdesk_app.core.confflow_contract import (
    MAX_EXCLUSIVE,
    MIN_VERSION,
    REFERENCE_WHEEL_SHA256,
    ConfFlowArtifactContract,
    version_spec,
)
from jobdesk_app.core.confflow_preflight import validate_confflow_capabilities

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_structured_source_of_truth():
    """Lock the structured tuple so the rest of the suite mirrors it."""
    assert MIN_VERSION == (2, 0, 0)
    assert MAX_EXCLUSIVE == (3, 0, 0)
    assert version_spec() == ">=2.0,<3.0"


def test_formal_confflow_216_artifact_identity_is_pinned():
    """The chemistry manifest must identify the independently published wheel."""
    assert confflow_contract.REFERENCE_VERSION == "2.1.6"
    assert confflow_contract.REFERENCE_BUILD_COMMIT == "45bfac11f721b2152eeff5ee26e50463fcc6f657"
    assert confflow_contract.REFERENCE_WHEEL_FILENAME == "confflow-2.1.6-py3-none-any.whl"
    assert (
        confflow_contract.REFERENCE_WHEEL_SHA256 == "d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548"
    )

    manifest = json.loads(_read("requirements/locks/jobdesk-chem-wheel-manifest.json"))
    artifact = manifest["artifact"]
    assert artifact["filename"] == confflow_contract.REFERENCE_WHEEL_FILENAME
    assert artifact["sha256"] == confflow_contract.REFERENCE_WHEEL_SHA256
    assert artifact["metadata_sha256"] == "ccbdcf2dd308451f3532f21b35ff703aef8ca453edfd40f721550a00eb689afb"
    assert artifact["version"] == confflow_contract.REFERENCE_VERSION
    assert artifact["requires_python"] == ">=3.10"
    assert artifact["requires_dist"][:10] == [
        "numpy>=2.2.6",
        "scipy>=1.15.3",
        "pyyaml>=6.0.3",
        "psutil>=7.2.2",
        "rich>=15.0.0",
        "pydantic>=2.13.3",
        "rdkit>=2026.3.2",
        "jsonschema>=4.23.0",
        "referencing>=0.30.0",
        "rfc8785>=0.1.4",
    ]
    for lock in manifest["locks"]:
        content = _read(lock["path"])
        assert "confflow==2.1.6" in content
        assert confflow_contract.REFERENCE_WHEEL_SHA256 in content


def test_pyproject_pin_matches_spec():
    """``pyproject.toml`` ``confflow`` pin must be the version spec."""
    content = _read("pyproject.toml")
    expected = "confflow>=2.0,<3.0"
    assert expected in content, f"pyproject.toml must contain {expected!r}"


def test_ci_yaml_uses_version_in_all_four_slots():
    """CI must reference the v2.1.6 tag and released wheel in all four slots."""
    content = _read(".github/workflows/ci.yml")
    assert "1.4.1" not in content, "ci.yml must not contain any 1.4.1 reference"
    assert content.count("ref: v2.1.6") == 2
    assert content.count("confflow-2.1.6-*.whl") == 2
    assert content.count("confflow.__version__ == '2.1.6'") == 2
    assert content.count("d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548") == 2


def test_ci_yaml_wheel_glob_matches_wheel_name():
    """PowerShell wheel glob must match the version literal in the assert."""
    content = _read(".github/workflows/ci.yml")
    assert content.count("confflow-2.1.6-*.whl") == 2
    assert content.count("confflow.__version__ == '2.1.6'") == 2


def test_windows_matrix_rechecks_chemistry_locks_against_released_wheel():
    """CI must mechanically reject a stale lock or substituted producer wheel."""
    content = _read(".github/workflows/ci.yml")
    assert "Set up uv for chemistry lock verification" in content
    assert (
        "Copy-Item -LiteralPath $wheel.FullName -Destination .matrix-artifacts\\confflow-2.1.6-py3-none-any.whl"
        in content
    )
    assert "scripts\\compile_chem_locks.ps1 -Check" in content
    assert "Get-FileHash -Algorithm SHA256 $wheel.FullName" in content
    assert REFERENCE_WHEEL_SHA256 in content
    compile_script = _read("scripts/compile_chem_locks.ps1")
    assert "$normalisedExpectedManifest = $expectedManifest -replace" in compile_script
    assert "$normalisedManifestText = $manifestText -replace" in compile_script


def test_windows_wheel_hash_gate_rejects_tampered_bytes(tmp_path: Path):
    """The CI digest gate must reject a byte-level substitution of the wheel."""
    if os.name != "nt":
        pytest.skip("the CI gate runs in PowerShell on Windows")
    wheel = REPO_ROOT / ".matrix-artifacts" / "confflow-2.1.6-py3-none-any.whl"
    if not wheel.is_file():
        pytest.skip("the independently downloaded ConfFlow wheel is not present")
    tampered = tmp_path / wheel.name
    tampered.write_bytes(wheel.read_bytes() + b"tampered")
    command = (
        "Import-Module Microsoft.PowerShell.Utility; "
        "$actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $env:TEST_WHEEL).Hash.ToLower(); "
        "if ($actual -ne $env:EXPECTED_WHEEL_SHA256) { exit 1 }"
    )
    environment = os.environ | {
        "TEST_WHEEL": str(wheel),
        "EXPECTED_WHEEL_SHA256": REFERENCE_WHEEL_SHA256,
    }
    accepted = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr
    environment["TEST_WHEEL"] = str(tampered)
    rejected = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0


def test_optional_coverage_uses_the_same_released_wheel():
    """The optional Linux job must not silently exercise an older producer."""
    content = _read(".github/workflows/optional-coverage.yml")
    assert content.count("ref: v2.1.6") == 1
    assert content.count("gh release download v2.1.6") == 1
    assert content.count("confflow-2.1.6-*.whl") == 1
    assert "d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548" in content
    assert "confflow.__version__ == '2.1.6'" in content


def test_candidate_compatibility_matrix_pins_stable_and_next_wheels():
    """The candidate matrix must not silently drift from its wheel digests."""
    content = _read(".github/workflows/confflow-compatibility-matrix.yml")
    assert "version: 2.1.6" in content
    assert "schema_snapshot: v2.1.3" in content
    assert "label: historical-v1.5.3" in content
    assert "version: 1.5.0" in content
    assert f"wheel_sha256: {REFERENCE_WHEEL_SHA256}" in content
    assert "wheel_sha256: 213eba551b344c7146450fa1135a884e3c00896371507a1edbf2eb18c7c0c5d6" in content
    assert "wheel_sha256: d9ac87410f1b73b91e19eb740298431663ee5f07bd4ffaeb19779c3a53c2e8dc" in content
    assert "EXPECTED_WHEEL_SHA256" in content
    assert content.count("schema_snapshot: v") == 3
    assert "EXPECTED_SCHEMA_SNAPSHOT" in content
    assert '"releases" / expected_schema_snapshot' in content
    assert "pinned build provenance is not clean" in content
    assert "producer/consumer schema drift" in content
    assert 'python -m pip install "$wheel"' in content
    assert 'pip install --no-deps "$wheel"' not in content
    assert 'python-version: "3.13"' in content
    assert "validate_confflow_capabilities(capabilities, require_dag=False)" in content


def test_readme_states_version_spec():
    """README must state the version spec and the v4 schema."""
    content = _read("README.md")
    assert "confflow>=2.0,<3.0" in content
    assert "1.4.1" not in content
    assert "schema_version=4" in content
    assert "run_summary.json" in content
    assert "workflow_stats.json" in content
    assert ".workflow_state.json" in content
    assert "output_manifest.json" in content
    assert "{basename}.txt" in content
    assert "{basename}min.xyz" in content
    assert "CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md" in content


def test_chinese_readme_states_version_spec():
    """The Chinese README must mirror the current producer contract."""
    content = _read("README.zh.md")
    assert "confflow>=2.0,<3.0" in content
    assert "confflow-2.1.6-py3-none-any.whl" in content


def test_deployment_doc_mirrors_version_and_capability_contract():
    """The deployment guide must mirror the structured version contract."""
    content = _read("docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md")
    assert "confflow-2.1.6-py3-none-any.whl" in content
    assert "1.4.1" not in content
    assert "CONFFLOW_1_4_1" not in content
    assert '"schema_version": 4' in content
    for filename in (
        "run_summary.json",
        "workflow_stats.json",
        ".workflow_state.json",
        "output_manifest.json",
        "{basename}.txt",
        "{basename}min.xyz",
    ):
        assert filename in content


def test_preflight_module_has_no_bare_version_literal():
    """The preflight module must source its version window from the
    structured tuple, not from a string literal.
    """
    content = _read("src/jobdesk_app/core/confflow_preflight.py")
    assert "1.5.0" not in content, (
        "confflow_preflight.py must not contain the bare literal '1.5.0'; "
        "it must source the spec from MIN_VERSION/MAX_EXCLUSIVE."
    )
    assert (
        "2.0.0" not in content
    ), "confflow_preflight.py must not contain the bare literal '2.0.0'; it must source the cap from MAX_EXCLUSIVE."
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
        '"workflow_state": ".workflow_state.json", "output_manifest": "output_manifest.json", "run_report": "{basename}.txt", "min_xyz": "{basename}min.xyz"}, '
        + '"commands": {"bash": true, "nohup": true, "setsid": true, "xargs": true, "sha256sum": true, "mktemp": true, "base64": true}, "build": {"commit": "abc1234", "dirty": false}, '
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
