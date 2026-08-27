from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml
from scripts.release_policy import validate_sbom

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as stream:
        return str(tomllib.load(stream)["project"]["version"])


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow_document() -> dict[object, object]:
    document = yaml.safe_load(_workflow_text())
    assert isinstance(document, dict)
    return document


def test_next_patch_candidate_is_bound_to_the_release_workflow() -> None:
    version = _project_version()
    assert version == "0.7.5"

    text = _workflow_text()
    document = _workflow_document()
    trigger = document.get("on", document.get(True))
    assert isinstance(trigger, dict)
    assert "workflow_dispatch" not in trigger
    assert isinstance(trigger.get("push"), dict)
    assert 'tags:\n      - "v*.*.*"' in text
    assert "v0.7.2" not in text
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}] - Candidate" in changelog


def test_release_workflow_requires_provenance_permissions_and_clean_identity() -> None:
    text = _workflow_text()
    document = _workflow_document()
    permissions = document.get("permissions")
    assert permissions == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert 'test -z "$(git status --porcelain)"' in text
    assert 'test "$(git cat-file -t "$EXPECTED_TAG")" = "tag"' in text
    assert 'LOCAL_PEELED_SHA="$(git rev-parse "$EXPECTED_TAG^{commit}")"' in text
    assert 'test "$(git rev-parse HEAD)" = "$LOCAL_PEELED_SHA"' in text
    assert 'test "$EXPECTED_TAG" = "v$VERSION"' in text
    assert 'gh api --include --silent "repos/${GITHUB_REPOSITORY}/releases/tags/${EXPECTED_TAG}"' in text
    assert "release_status=$?" in text
    assert "HTTP/[0-9.]+[[:space:]]+404" in text
    assert "release existence query failed closed" in text
    assert "persist-credentials: false" in text
    assert 'LOCAL_PEELED_SHA="$(git rev-parse "$EXPECTED_TAG^{commit}")"' in text
    assert "workflow_dispatch" not in text
    assert 'test "$GITHUB_REF" = "refs/tags/$EXPECTED_TAG"' in text
    assert 'test "$GITHUB_SHA" = "$LOCAL_PEELED_SHA"' in text
    assert '"event_sha"' in text
    assert '"event_ref"' in text
    assert "release-preflight.json" in text
    assert "REMOTE_TAG_TYPE" in text
    assert "REMOTE_PEELED_SHA" in text
    assert "git/ref/tags/${EXPECTED_TAG}" in text
    assert "git/tags/${REMOTE_TAG_OBJECT_SHA}" in text
    assert 'test "$REMOTE_PEELED_SHA" = "$LOCAL_PEELED_SHA"' in text
    assert "immutable-releases" not in text
    assert "RELEASE_IMMUTABLE_PREFLIGHT_SHA" in text
    assert "vars.RELEASE_IMMUTABLE_PREFLIGHT_SHA" in text
    assert 'test "$RELEASE_IMMUTABLE_PREFLIGHT_SHA" = "$GITHUB_SHA"' in text
    assert 'test "$RELEASE_IMMUTABLE_PREFLIGHT_SHA" = "$LOCAL_PEELED_SHA"' in text
    assert 'test "$RELEASE_IMMUTABLE_PREFLIGHT_SHA" = "$REMOTE_PEELED_SHA"' in text
    assert '"immutable_preflight_sha"' in text
    assert 'gh release view "$EXPECTED_TAG" --repo "$GITHUB_REPOSITORY" --json isImmutable' in text
    assert 'test "$IMMUTABLE_CLI" = "true"' in text


def test_release_workflow_initializes_and_traps_failure_evidence() -> None:
    text = _workflow_text()
    assert text.index("Initialize release failure evidence") < text.index("- name: Checkout")
    assert '"stage": "before_checkout"' in text
    assert "os.replace(temporary, path)" in text
    assert "scripts/update_release_evidence.py" in text
    assert "trap 'rc=$?;" in text
    assert text.count("if-no-files-found: error") >= 2


def test_release_workflow_builds_once_and_publishes_verifiable_bundle() -> None:
    text = _workflow_text()
    assert 'python -m build --sdist --outdir "$BUILD_DIR"' in text
    assert 'python -m build --wheel --outdir "$BUILD_DIR"' in text
    assert text.count("python -m build") == 2
    assert "cyclonedx-py requirements" in text
    assert "requirements/locks/jobdesk-dev-py312-win_amd64.txt" in text
    assert 'python -m venv "$WHEEL_VENV"' in text
    assert '-m pip install --no-deps "$wheel"' in text
    assert 'python -m venv "$SDIST_VENV"' in text
    assert '-m pip install --no-deps "$sdist"' in text
    assert "jobdesk-${VERSION}.tar.gz" in text
    assert 'pushd "$RUNNER_TEMP"' in text
    assert 'importlib.metadata.version("jobdesk")' in text
    assert "actions/attest-build-provenance@v2" in text
    for filename in (
        "attestation.bundle.json",
        "attestation-verification.json",
        "attestation.json",
        "provenance.json",
        "sbom.cdx.json",
        "SHA256SUMS",
        "RELEASE_ARTIFACTS.md",
    ):
        assert filename in text
    assert 'gh release create "$EXPECTED_TAG"' in text
    assert '--repo "$GITHUB_REPOSITORY"' in text
    assert "--verify-tag" in text
    assert "release_files=(" in text
    assert '"${release_files[@]}"' in text
    assert "build==1.5.0" in text
    assert "cyclonedx-bom==7.3.1" in text
    assert "--spec-version 1.6" in text
    assert "--output-reproducible" in text
    assert "python scripts/release_policy.py sbom" in text
    assert "python scripts/verify_release_attestation.py" in text
    assert "--workflow .github/workflows/release.yml" in text
    assert "python scripts/release_policy.py attestation" not in text
    assert "gh attestation verify" in text
    assert '--signer-workflow "$GITHUB_REPOSITORY/.github/workflows/release.yml"' in text
    assert "--signer-repo" not in text
    assert "--source-ref" in text
    assert "--source-digest" in text
    assert "release-post-verification" in text
    assert 'cp -- "$ATTESTATION_VERIFICATION" dist/attestation-verification.json' in text
    assert '"attestation_verification_sha256"' in text
    assert 'gh release download "$EXPECTED_TAG"' in text
    assert "scripts/verify_release_assets.py" in text
    assert "--sha256sums dist/SHA256SUMS" in text
    assert 'DOWNLOAD_DIR="${RUNNER_TEMP}/jobdesk-release-assets"' in text
    assert "expected_asset_args" in text
    assert '"downloaded_hashes_verified": True' in text


def test_release_workflow_keeps_artifact_version_checks_dynamic() -> None:
    text = _workflow_text()
    assert re.search(r"VERSION=\$\(python -c .+pyproject\.toml", text)
    assert 'f"Version: {version}"' in text
    assert 'f"jobdesk-{version}-*.whl"' in text
    assert "jobdesk-${VERSION}.tar.gz" in text


def test_release_workflow_is_tag_push_only_and_disables_weak_attestation_cli() -> None:
    text = _workflow_text()
    assert "workflow_dispatch" not in text
    policy = (ROOT / "scripts" / "release_policy.py").read_text(encoding="utf-8")
    assert 'add_parser("attestation")' not in policy
    assert "validate_attestation" not in policy


def test_release_policy_cli_is_limited_to_sbom_and_dist() -> None:
    policy = (ROOT / "scripts" / "release_policy.py").read_text(encoding="utf-8")
    assert 'add_parser("sbom")' in policy
    assert 'add_parser("dist")' in policy
    assert 'add_parser("attestation")' not in policy


def test_sbom_policy_requires_reproducible_top_level_jobdesk(tmp_path: Path) -> None:
    sbom = tmp_path / "sbom.json"
    sbom.write_text(
        json.dumps(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "metadata": {"component": {"type": "application", "name": "jobdesk", "version": "0.7.5"}},
                "components": [{"type": "library", "name": "packaging", "version": "24.0"}],
            }
        ),
        encoding="utf-8",
    )
    validate_sbom(sbom, package="jobdesk", version="0.7.5")

    sbom.write_text(
        sbom.read_text(encoding="utf-8").replace('"version": "0.7.5"', '"version": "0.7.4"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="top-level"):
        validate_sbom(sbom, package="jobdesk", version="0.7.5")
