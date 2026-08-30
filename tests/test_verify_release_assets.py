from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from scripts.verify_release_assets import (
    ReleaseAssetValidationError,
    main,
    verify_release_assets,
)

ASSET_NAMES = (
    "RELEASE_ARTIFACTS.md",
    "SHA256SUMS",
    "attestation-verification.json",
    "attestation.bundle.json",
    "attestation.json",
    "jobdesk-0.7.9-py3-none-any.whl",
    "jobdesk-0.7.9.tar.gz",
    "provenance.json",
    "release-preflight.json",
    "sbom.cdx.json",
)


def _fixture(tmp_path: Path) -> tuple[dict[str, object], Path, Path, Path]:
    source = tmp_path / "dist"
    download = tmp_path / "download"
    source.mkdir()
    download.mkdir()
    for index, name in enumerate(name for name in ASSET_NAMES if name != "SHA256SUMS"):
        (source / name).write_bytes(f"asset-{index}-{name}".encode("utf-8"))
    lines = [
        f"{hashlib.sha256((source / name).read_bytes()).hexdigest()}  {name}"
        for name in sorted(name for name in ASSET_NAMES if name != "SHA256SUMS")
    ]
    (source / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shutil.copytree(source, download, dirs_exist_ok=True)
    release = {"assets": [{"name": name} for name in ASSET_NAMES]}
    release_json = tmp_path / "release.json"
    release_json.write_text(json.dumps(release), encoding="utf-8")
    return release, download, source / "SHA256SUMS", release_json


def test_exact_release_assets_and_prepublication_hashes_pass(tmp_path: Path) -> None:
    release, download, sha256sums, _release_json = _fixture(tmp_path)

    verification = verify_release_assets(
        release,
        download,
        sha256sums,
        expected_names=ASSET_NAMES,
    )

    assert verification.hash_result == "passed"
    assert verification.sha256sums_match is True
    assert verification.expected_asset_names == ASSET_NAMES
    assert verification.remote_asset_names == ASSET_NAMES
    assert verification.downloaded_asset_names == ASSET_NAMES
    assert verification.expected_digests == verification.downloaded_digests


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda release: release["assets"].pop(), "asset set mismatch"),
        (lambda release: release["assets"].append({"name": "unexpected.bin"}), "asset set mismatch"),
        (lambda release: release["assets"].append(release["assets"][0]), "duplicate"),
    ],
)
def test_missing_extra_or_duplicate_remote_assets_fail_closed(tmp_path: Path, mutation, message: str) -> None:
    release, download, sha256sums, _release_json = _fixture(tmp_path)
    mutation(release)

    with pytest.raises(ReleaseAssetValidationError, match=message):
        verify_release_assets(release, download, sha256sums, expected_names=ASSET_NAMES)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda download: (download / "provenance.json").unlink(),
        lambda download: (download / "unexpected.bin").write_bytes(b"unexpected"),
        lambda download: (download / "sbom.cdx.json").write_bytes(b"tampered"),
        lambda download: (download / "SHA256SUMS").write_text("tampered\n", encoding="utf-8"),
    ],
)
def test_missing_extra_or_tampered_downloaded_bytes_fail_closed(tmp_path: Path, mutation) -> None:
    release, download, sha256sums, _release_json = _fixture(tmp_path)
    mutation(download)

    with pytest.raises(ReleaseAssetValidationError):
        verify_release_assets(release, download, sha256sums, expected_names=ASSET_NAMES)


def test_cli_writes_failed_hash_result_evidence(tmp_path: Path, capsys) -> None:
    release, download, sha256sums, release_json = _fixture(tmp_path)
    release["assets"].pop()
    release_json.write_text(json.dumps(release), encoding="utf-8")
    evidence = tmp_path / "post-evidence.json"

    result = main(
        [
            "--release-json",
            str(release_json),
            "--download-dir",
            str(download),
            "--sha256sums",
            str(sha256sums),
            "--evidence",
            str(evidence),
            *sum((("--expected-name", name) for name in ASSET_NAMES), ()),
        ]
    )

    assert result == 1
    assert "rejected" in capsys.readouterr().err
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["schema"] == "jobdesk.release-post-verification.v1"
    assert payload["status"] == "failed"
    assert payload["hash_result"] == "failed"
    assert payload["expected_asset_names"] == list(ASSET_NAMES)
    assert payload["asset_verification"]["hash_result"] == "failed"


def test_cli_merges_verified_asset_evidence_into_post_envelope(tmp_path: Path) -> None:
    release, download, sha256sums, release_json = _fixture(tmp_path)
    evidence = tmp_path / "post-evidence.json"
    evidence.write_text(
        json.dumps(
            {
                "schema": "jobdesk.release-post-verification.v1",
                "stage": "asset_hashes",
                "release_created": True,
            }
        ),
        encoding="utf-8",
    )

    result = main(
        [
            "--release-json",
            str(release_json),
            "--download-dir",
            str(download),
            "--sha256sums",
            str(sha256sums),
            "--evidence",
            str(evidence),
            *sum((("--expected-name", name) for name in ASSET_NAMES), ()),
        ]
    )

    assert result == 0
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["schema"] == "jobdesk.release-post-verification.v1"
    assert payload["stage"] == "asset_hashes"
    assert payload["release_created"] is True
    assert payload["asset_verification"]["hash_result"] == "passed"
    assert payload["sha256sums_match"] is True
    assert payload["downloaded_asset_names"] == list(ASSET_NAMES)
