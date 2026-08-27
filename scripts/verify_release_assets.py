#!/usr/bin/env python3
"""Verify the exact assets and hashes downloaded from a published release.

The release workflow creates ``SHA256SUMS`` before publication.  This helper
checks that the post-publication REST response has exactly the expected asset
names, that a fresh download has exactly the same names, and that every
downloaded byte matches the pre-publication checksum manifest.  It does not
perform network access; the workflow owns the REST read and download command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

_CHECKSUM_LINE = re.compile(r"^([0-9a-fA-F]{64})  ([^\r\n]+)$")


class ReleaseAssetValidationError(ValueError):
    """Raised when a release asset set or downloaded byte does not match."""


def _asset_name(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReleaseAssetValidationError(f"{field} must be a non-empty asset filename")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ReleaseAssetValidationError(f"{field} must be a basename: {value!r}")
    return value


def _asset_names(values: Iterable[Any], *, field: str) -> tuple[str, ...]:
    names = tuple(_asset_name(value, field=field) for value in values)
    if len(set(names)) != len(names):
        raise ReleaseAssetValidationError(f"{field} contains duplicate asset names")
    return tuple(sorted(names))


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseAssetValidationError(f"release response is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseAssetValidationError("release response must be a JSON object")
    return value


def _read_sha256sums(path: Path) -> tuple[bytes, dict[str, str]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseAssetValidationError(f"cannot read pre-publication SHA256SUMS: {path}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseAssetValidationError("pre-publication SHA256SUMS is not UTF-8") from exc
    digests: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise ReleaseAssetValidationError(f"invalid SHA256SUMS line {line_number}")
        digest, name = match.groups()
        name = _asset_name(name, field=f"SHA256SUMS line {line_number} filename")
        if name in digests:
            raise ReleaseAssetValidationError(f"SHA256SUMS contains duplicate asset {name!r}")
        digests[name] = digest.lower()
    if not digests:
        raise ReleaseAssetValidationError("pre-publication SHA256SUMS is empty")
    return raw, digests


def _sha256(path: Path) -> str:
    try:
        if not path.is_file() or path.is_symlink():
            raise ReleaseAssetValidationError(f"downloaded asset is not a regular file: {path.name}")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except ReleaseAssetValidationError:
        raise
    except OSError as exc:
        raise ReleaseAssetValidationError(f"cannot read downloaded asset: {path.name}") from exc


@dataclass(frozen=True)
class ReleaseAssetVerification:
    """Auditable names and digests for one post-publication verification."""

    expected_asset_names: tuple[str, ...]
    remote_asset_names: tuple[str, ...]
    downloaded_asset_names: tuple[str, ...]
    expected_digests: Mapping[str, str]
    downloaded_digests: Mapping[str, str]
    sha256sums_pre_publish_sha256: str
    sha256sums_downloaded_sha256: str
    sha256sums_match: bool
    hash_result: str

    def evidence(self) -> dict[str, Any]:
        value = asdict(self)
        value["expected_asset_names"] = list(self.expected_asset_names)
        value["remote_asset_names"] = list(self.remote_asset_names)
        value["downloaded_asset_names"] = list(self.downloaded_asset_names)
        value["expected_digests"] = dict(sorted(self.expected_digests.items()))
        value["downloaded_digests"] = dict(sorted(self.downloaded_digests.items()))
        value.update(status="verified")
        return value


def verify_release_assets(
    release_document: Mapping[str, Any],
    download_dir: Path,
    sha256sums_path: Path,
    *,
    expected_names: Iterable[str],
) -> ReleaseAssetVerification:
    """Verify release names, downloaded names, and every published byte."""

    expected = _asset_names(expected_names, field="expected asset names")
    if "SHA256SUMS" not in expected:
        raise ReleaseAssetValidationError("expected asset names must include SHA256SUMS")

    remote_assets = release_document.get("assets")
    if not isinstance(remote_assets, list):
        raise ReleaseAssetValidationError("release REST response has no assets list")
    remote_names = _asset_names(
        (asset.get("name") if isinstance(asset, dict) else None for asset in remote_assets),
        field="release REST asset names",
    )
    if remote_names != expected:
        raise ReleaseAssetValidationError(
            f"release asset set mismatch: expected={list(expected)!r}, remote={list(remote_names)!r}"
        )

    if not download_dir.is_dir():
        raise ReleaseAssetValidationError(f"release download directory is missing: {download_dir}")
    try:
        entries = tuple(download_dir.iterdir())
    except OSError as exc:
        raise ReleaseAssetValidationError(f"cannot list release download directory: {download_dir}") from exc
    downloaded_names = _asset_names((entry.name for entry in entries), field="downloaded asset names")
    if downloaded_names != expected:
        raise ReleaseAssetValidationError(
            f"downloaded asset set mismatch: expected={list(expected)!r}, downloaded={list(downloaded_names)!r}"
        )

    pre_publish_raw, expected_digests = _read_sha256sums(sha256sums_path)
    expected_checksum_names = set(expected) - {"SHA256SUMS"}
    if set(expected_digests) != expected_checksum_names:
        raise ReleaseAssetValidationError(
            "pre-publication SHA256SUMS entries do not match the expected non-checksum assets"
        )
    downloaded_checksum = download_dir / "SHA256SUMS"
    downloaded_raw = downloaded_checksum.read_bytes()
    pre_publish_digest = hashlib.sha256(pre_publish_raw).hexdigest()
    downloaded_digest = hashlib.sha256(downloaded_raw).hexdigest()
    if downloaded_raw != pre_publish_raw:
        raise ReleaseAssetValidationError("downloaded SHA256SUMS differs from the pre-publication manifest")

    downloaded_digests = {name: _sha256(download_dir / name) for name in expected}
    for name, expected_digest in expected_digests.items():
        actual_digest = downloaded_digests[name]
        if actual_digest != expected_digest:
            raise ReleaseAssetValidationError(
                f"downloaded asset digest mismatch for {name!r}: expected={expected_digest}, actual={actual_digest}"
            )
    if downloaded_digests["SHA256SUMS"] != pre_publish_digest:
        raise ReleaseAssetValidationError("downloaded SHA256SUMS digest is inconsistent")
    return ReleaseAssetVerification(
        expected_asset_names=expected,
        remote_asset_names=remote_names,
        downloaded_asset_names=downloaded_names,
        expected_digests={**expected_digests, "SHA256SUMS": pre_publish_digest},
        downloaded_digests=downloaded_digests,
        sha256sums_pre_publish_sha256=pre_publish_digest,
        sha256sums_downloaded_sha256=downloaded_digest,
        sha256sums_match=True,
        hash_result="passed",
    )


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        raise ReleaseAssetValidationError(f"cannot write asset verification evidence: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _best_effort_names(path: Path) -> list[str]:
    try:
        return list(_asset_names((entry.name for entry in path.iterdir()), field="downloaded asset names"))
    except (OSError, ReleaseAssetValidationError):
        return []


def _existing_evidence(path: Path) -> dict[str, Any]:
    """Load the workflow's evidence envelope without trusting its contents."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema": "jobdesk.release-post-verification.v1"}
    if not isinstance(value, dict):
        return {"schema": "jobdesk.release-post-verification.v1"}
    return value


def _best_effort_remote_names(release_document: Mapping[str, Any]) -> list[str]:
    assets = release_document.get("assets")
    if not isinstance(assets, list):
        return []
    return [asset["name"] for asset in assets if isinstance(asset, dict) and isinstance(asset.get("name"), str)]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-json", type=Path, required=True)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--sha256sums", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--expected-name", action="append", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    release_document: dict[str, Any] = {}
    try:
        expected = _asset_names(args.expected_name, field="expected asset names")
        release_document = _read_json(args.release_json)
        verification = verify_release_assets(
            release_document,
            args.download_dir,
            args.sha256sums,
            expected_names=expected,
        )
    except ReleaseAssetValidationError as exc:
        asset_payload = {
            "schema": "jobdesk.release-assets-verification.v1",
            "status": "failed",
            "hash_result": "failed",
            "expected_asset_names": sorted(set(args.expected_name)),
            "remote_asset_names": _best_effort_remote_names(release_document),
            "downloaded_asset_names": _best_effort_names(args.download_dir),
            "error": str(exc),
        }
        try:
            payload = _existing_evidence(args.evidence)
            payload.update({key: value for key, value in asset_payload.items() if key != "schema"})
            payload["asset_verification"] = asset_payload
            _atomic_write(args.evidence, payload)
        except ReleaseAssetValidationError as evidence_error:
            print(f"asset verification rejected: {exc}; evidence write failed: {evidence_error}", file=sys.stderr)
        else:
            print(f"asset verification rejected: {exc}", file=sys.stderr)
        return 1
    asset_payload = {"schema": "jobdesk.release-assets-verification.v1", **verification.evidence()}
    payload = _existing_evidence(args.evidence)
    payload.update({key: value for key, value in asset_payload.items() if key != "schema"})
    payload["asset_verification"] = asset_payload
    _atomic_write(args.evidence, payload)
    print("verified exact published release assets and SHA256SUMS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
