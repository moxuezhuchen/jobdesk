"""Fail-closed SBOM and release-distribution checks.

Attestation policy lives only in ``verify_release_attestation.py``; this
module intentionally exposes no alternate attestation verifier.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
        if not records:
            raise ValueError(f"{path} is not JSON or JSONL")
        return records


def prepare_sbom(path: Path, *, package: str, version: str) -> None:
    """Add a deterministic top-level application component and validate it."""

    document = _read_json_or_jsonl(path)
    if not isinstance(document, dict):
        raise ValueError("SBOM is not a JSON object")
    metadata = document.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("SBOM metadata is not an object")
    metadata.pop("timestamp", None)
    metadata["component"] = {
        "bom-ref": f"pkg:pypi/{package}@{version}",
        "name": package,
        "purl": f"pkg:pypi/{package}@{version}",
        "type": "application",
        "version": version,
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validate_sbom(path, package=package, version=version)


def validate_sbom(path: Path, *, package: str, version: str, spec_version: str = "1.6") -> None:
    document = _read_json_or_jsonl(path)
    if not isinstance(document, dict):
        raise ValueError("SBOM is not a JSON object")
    if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != spec_version:
        raise ValueError("SBOM format/specification is not the pinned CycloneDX version")
    metadata = document.get("metadata")
    component = metadata.get("component") if isinstance(metadata, dict) else None
    if not isinstance(component, dict) or component.get("name") != package or component.get("version") != version:
        raise ValueError("SBOM top-level JobDesk component/version does not match")
    if isinstance(metadata, dict) and "timestamp" in metadata:
        raise ValueError("SBOM contains a non-reproducible timestamp")
    if not isinstance(document.get("components"), list):
        raise ValueError("SBOM has no components list")


def validate_release_dist(dist: Path, *, version: str) -> None:
    expected_prefix = f"jobdesk-{version}-"
    wheels = sorted(path.name for path in dist.glob(f"{expected_prefix}*.whl"))
    sdists = sorted(path.name for path in dist.glob(f"jobdesk-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(f"release package set is incomplete: wheels={wheels}, sdists={sdists}")
    expected = {
        wheels[0],
        sdists[0],
        "sbom.cdx.json",
        "attestation.bundle.json",
        "attestation.json",
        "provenance.json",
        "release-preflight.json",
        "RELEASE_ARTIFACTS.md",
        "SHA256SUMS",
    }
    actual = {path.name for path in dist.iterdir() if path.is_file()}
    if actual != expected:
        raise ValueError(f"release dist allowlist mismatch: expected={sorted(expected)}, actual={sorted(actual)}")


def _main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    sbom = subparsers.add_parser("sbom")
    sbom.add_argument("path", type=Path)
    sbom.add_argument("--package", required=True)
    sbom.add_argument("--version", required=True)

    release_dist = subparsers.add_parser("dist")
    release_dist.add_argument("path", type=Path)
    release_dist.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.command == "sbom":
        prepare_sbom(args.path, package=args.package, version=args.version)
    else:
        validate_release_dist(args.path, version=args.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
