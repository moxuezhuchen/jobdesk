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


def test_all_third_party_actions_are_pinned_to_reviewed_commits() -> None:
    expected = {
        ("actions/checkout", "v4"): "11d5960a326750d5838078e36cf38b85af677262",
        ("actions/setup-python", "v5"): "a26af69be951a213d495a4c3e4e4022e16d87065",
        ("actions/upload-artifact", "v4"): "ea165f8d65b6e75b540449e92b4886f43607fa02",
        (
            "actions/attest-build-provenance",
            "v2",
        ): "e8998f949152b193b063cb0ec769d69d929409be",
        ("astral-sh/setup-uv", "v6"): "d0cc045d04ccac9d8b7881df0226f9e82c39688e",
    }
    found: set[tuple[str, str]] = set()
    workflow_dir = ROOT / ".github" / "workflows"
    pattern = re.compile(r"^\s*(?:-\s*)?uses:\s+([^@\s]+)@([0-9a-f]{40})\s+#\s+(v\d+)\s*$")
    for path in workflow_dir.glob("*.yml"):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "uses:" not in line:
                continue
            match = pattern.match(line)
            assert match is not None, f"{path.name}:{line_number} must pin a full SHA and retain its tag comment"
            action, commit, tag = match.groups()
            assert expected.get((action, tag)) == commit, f"unreviewed action pin at {path.name}:{line_number}"
            found.add((action, tag))
    assert found == set(expected)


def test_dependabot_tracks_github_actions_updates() -> None:
    document = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert document == {
        "version": 2,
        "updates": [
            {
                "package-ecosystem": "github-actions",
                "directory": "/",
                "schedule": {"interval": "weekly"},
                "open-pull-requests-limit": 5,
            }
        ],
    }


def test_next_patch_candidate_is_bound_to_the_release_workflow() -> None:
    version = _project_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert version != "0.7.10"

    text = _workflow_text()
    document = _workflow_document()
    trigger = document.get("on", document.get(True))
    assert isinstance(trigger, dict)
    assert "workflow_dispatch" not in trigger
    assert isinstance(trigger.get("push"), dict)
    assert 'tags:\n      - "v*.*.*"' in text
    assert "v0.7.2" not in text
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in changelog
    assert f"unreleased JobDesk\n  `{version}` source candidate" in changelog


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


def test_release_workflow_installs_complete_qt_runtime_before_artifact_gui_smokes() -> None:
    document = _workflow_document()
    jobs = document.get("jobs")
    assert isinstance(jobs, dict)
    build = jobs.get("build")
    assert isinstance(build, dict)
    steps = build.get("steps")
    assert isinstance(steps, list)

    named_steps = {
        step.get("name"): (index, step)
        for index, step in enumerate(steps)
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    }
    qt_index, qt_step = named_steps["Install Linux Qt runtime"]
    wheel_index, _ = named_steps["Install and smoke-test the final wheel outside the checkout"]
    sdist_index, _ = named_steps["Install and smoke-test the final sdist outside the checkout"]
    assert qt_index < wheel_index < sdist_index
    assert qt_step.get("run") == (
        "sudo apt-get update\n"
        "sudo apt-get install -y libdbus-1-3 libegl1 libgl1 "
        "libxkbcommon0 libxkbcommon-x11-0\n"
    )


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
    assert '-m pip install "$wheel"' in text
    assert 'python -m venv "$SDIST_VENV"' in text
    assert '-m pip install "$sdist"' in text
    assert "jobdesk-${VERSION}.tar.gz" in text
    assert 'pushd "$RUNNER_TEMP"' in text
    assert 'importlib.metadata.version("jobdesk")' in text
    assert "python scripts/verify_jobdesk_distributions.py" in text
    assert text.count('cp scripts/smoke_gui_offscreen.py "$RUNNER_TEMP/') == 2
    assert text.count("JOBDESK_SMOKE_EXPECT_SITE_PACKAGES=1") == 2
    assert text.count("JOBDESK_SMOKE_FORBIDDEN_SOURCE_ROOT") == 2
    assert text.count("PYTHONPATH='' QT_QPA_PLATFORM=offscreen") == 2
    assert text.count("smoke_gui_offscreen.py") >= 2
    smoke = (ROOT / "scripts" / "smoke_gui_offscreen.py").read_text(encoding="utf-8")
    assert "JOBDESK_SMOKE_EXPECT_SITE_PACKAGES" in smoke
    assert "site-packages" in smoke
    assert "actions/attest-build-provenance@e8998f949152b193b063cb0ec769d69d929409be # v2" in text
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
                "metadata": {"component": {"type": "application", "name": "jobdesk", "version": "0.7.10"}},
                "components": [{"type": "library", "name": "packaging", "version": "24.0"}],
            }
        ),
        encoding="utf-8",
    )
    validate_sbom(sbom, package="jobdesk", version="0.7.10")

    sbom.write_text(
        sbom.read_text(encoding="utf-8").replace('"version": "0.7.10"', '"version": "0.7.9"'),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="top-level"):
        validate_sbom(sbom, package="jobdesk", version="0.7.10")
