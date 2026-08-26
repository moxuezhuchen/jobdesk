#!/usr/bin/env python3
"""Validate a local ConfFlow candidate wheel without SSH or a workload.

This is intentionally a small, offline gate.  It binds the installed CLI
probes to the exact wheel file supplied by the caller, then checks the
producer-owned capability/configuration contract and workflow-schema digest.
It does not run a calculation, open a network connection, or publish an
artifact.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from email.policy import default
from pathlib import Path
from typing import Mapping

from jobdesk_app.core.confflow_contract import (
    CAPABILITY_SCHEMA_VERSION,
    EXPECTED_ARTIFACTS,
    REQUIRED_COMMANDS,
)
from jobdesk_app.core.confflow_preflight import parse_confflow_capabilities

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_WHEEL_NAME = re.compile(r"^confflow-[0-9]+\.[0-9]+\.[0-9]+-py3-none-any\.whl$")


class CandidateCompatibilityError(ValueError):
    """A local candidate failed an offline compatibility assertion."""


@dataclass(frozen=True)
class WheelIdentity:
    path: Path
    filename: str
    sha256: str
    metadata_sha256: str
    name: str
    version: str
    requires_python: str | None
    requires_dist: tuple[str, ...]
    build_commit: str | None
    build_dirty: bool | None
    producer_contract: Mapping[str, object]


def _literal_assignments(source: str) -> dict[str, object]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise CandidateCompatibilityError("candidate contract source is not valid Python") from exc
    values: dict[str, object] = {}
    for node in ast.walk(tree):
        target: ast.expr | None = None
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value_node = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value_node = node.target, node.value
        if isinstance(target, ast.Name) and value_node is not None:
            try:
                values[target.id] = ast.literal_eval(value_node)
            except (ValueError, TypeError, SyntaxError):
                continue
    return values


def _require_digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CandidateCompatibilityError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _read_metadata(raw: bytes, filename: str) -> tuple[str, str, str | None, tuple[str, ...]]:
    message = Parser(policy=default).parsestr(raw.decode("utf-8"))
    name = message.get("Name")
    version = message.get("Version")
    if name != "confflow" or not version:
        raise CandidateCompatibilityError("candidate wheel metadata name/version is invalid")
    if filename != f"confflow-{version}-py3-none-any.whl":
        raise CandidateCompatibilityError("candidate wheel filename does not match metadata version")
    return name, version, message.get("Requires-Python"), tuple(message.get_all("Requires-Dist", []))


def inspect_wheel(path: Path, *, expected_sha256: str, expected_metadata_sha256: str | None = None) -> WheelIdentity:
    """Inspect and bind one exact local candidate wheel."""
    if not path.is_file():
        raise CandidateCompatibilityError(f"candidate wheel does not exist: {path}")
    filename = path.name
    if _WHEEL_NAME.fullmatch(filename) is None:
        raise CandidateCompatibilityError("candidate wheel filename is malformed")
    expected_sha256 = _require_digest(expected_sha256, "expected wheel digest")
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise CandidateCompatibilityError("candidate wheel digest does not match the pinned digest")

    with zipfile.ZipFile(path) as archive:
        metadata_names = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1 or not metadata_names[0].startswith("confflow-"):
            raise CandidateCompatibilityError("candidate wheel metadata member is ambiguous or missing")
        metadata_raw = archive.read(metadata_names[0])
        name, version, requires_python, requires_dist = _read_metadata(metadata_raw, filename)
        contract_source = archive.read("confflow/contract.py").decode("utf-8")
        build_source = archive.read("confflow/__build__.py").decode("utf-8")

    metadata_sha256 = hashlib.sha256(metadata_raw).hexdigest()
    if expected_metadata_sha256 is not None and metadata_sha256 != _require_digest(
        expected_metadata_sha256, "expected metadata digest"
    ):
        raise CandidateCompatibilityError("candidate wheel metadata digest does not match the pinned digest")

    contract = _literal_assignments(contract_source)
    expected_contract = {
        "CAPABILITY_SCHEMA_VERSION": CAPABILITY_SCHEMA_VERSION,
        "RUN_SUMMARY_FILE": EXPECTED_ARTIFACTS.run_summary,
        "WORKFLOW_STATS_FILE": EXPECTED_ARTIFACTS.workflow_stats,
        "WORKFLOW_STATE_FILE": EXPECTED_ARTIFACTS.workflow_state,
        "OUTPUT_MANIFEST_FILE": EXPECTED_ARTIFACTS.output_manifest,
        "RUN_REPORT_FILE": EXPECTED_ARTIFACTS.run_report,
        "RUN_MIN_XYZ_TEMPLATE": EXPECTED_ARTIFACTS.min_xyz,
    }
    for key, expected in expected_contract.items():
        if contract.get(key) != expected:
            raise CandidateCompatibilityError(f"candidate capability contract mismatch: {key}")
    required_commands = contract.get("REQUIRED_COMMANDS")
    if tuple(required_commands or ()) != REQUIRED_COMMANDS:
        raise CandidateCompatibilityError("candidate capability contract required commands mismatch")

    build = _literal_assignments(build_source)
    commit = build.get("COMMIT")
    dirty = build.get("DIRTY")
    if commit is not None and (not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None):
        raise CandidateCompatibilityError("candidate build commit provenance is malformed")
    if dirty is not None and type(dirty) is not bool:
        raise CandidateCompatibilityError("candidate build dirty provenance is malformed")
    return WheelIdentity(
        path=path,
        filename=filename,
        sha256=actual_sha256,
        metadata_sha256=metadata_sha256,
        name=name,
        version=version,
        requires_python=requires_python,
        requires_dist=requires_dist,
        build_commit=commit,
        build_dirty=dirty,
        producer_contract=contract,
    )


def _document(text: str, label: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CandidateCompatibilityError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise CandidateCompatibilityError(f"{label} must be a JSON object")
    return value


def _bind_local_provenance(payload: dict[str, object], wheel: WheelIdentity) -> dict[str, object]:
    """Bind local wheel identity while rejecting conflicting producer claims."""
    producer = payload.get("producer")
    if not isinstance(producer, dict):
        raise CandidateCompatibilityError("capability producer provenance is missing")
    wheel_claim = producer.get("wheel")
    if not isinstance(wheel_claim, dict):
        raise CandidateCompatibilityError("capability wheel provenance is missing")
    for key, expected in (("filename", wheel.filename), ("sha256", wheel.sha256)):
        claimed = wheel_claim.get(key)
        if claimed is not None and claimed != expected:
            raise CandidateCompatibilityError(f"capability producer wheel {key} does not match local wheel")
        wheel_claim[key] = expected
    install = payload.get("install_provenance", producer.get("install_provenance"))
    if install is not None and (not isinstance(install, dict) or install.get("status") not in {"missing", "verified"}):
        raise CandidateCompatibilityError("capability install provenance is malformed")
    install = {"status": "verified", "source": "local-candidate-wheel", "sha256": wheel.sha256}
    producer["install_provenance"] = install
    payload["install_provenance"] = install
    return payload


def validate_capabilities(text: str, wheel: WheelIdentity) -> dict[str, object]:
    payload = _bind_local_provenance(_document(text, "capability output"), wheel)
    capabilities = parse_confflow_capabilities(json.dumps(payload, sort_keys=True))
    if capabilities.schema_version != CAPABILITY_SCHEMA_VERSION or capabilities.version != wheel.version:
        raise CandidateCompatibilityError("capability schema/version does not match local wheel")
    if capabilities.producer is None or capabilities.producer.get("package") != wheel.name:
        raise CandidateCompatibilityError("capability producer package does not match local wheel")
    if capabilities.producer.get("version") != wheel.version:
        raise CandidateCompatibilityError("capability producer version does not match local wheel")
    build = payload.get("build")
    producer_build = capabilities.producer.get("build")
    if not isinstance(build, dict) or not isinstance(producer_build, dict) or producer_build != build:
        raise CandidateCompatibilityError("capability producer build provenance is inconsistent")
    if build.get("commit") != wheel.build_commit or build.get("dirty") != wheel.build_dirty:
        raise CandidateCompatibilityError("capability build provenance does not match local wheel")
    if capabilities.artifacts != EXPECTED_ARTIFACTS:
        raise CandidateCompatibilityError("capability artifact contract does not match JobDesk")
    if capabilities.commands is None or any(type(capabilities.commands.get(name)) is not bool for name in REQUIRED_COMMANDS):
        raise CandidateCompatibilityError("capability command contract is malformed")
    return payload


def validate_configuration_contract(text: str, wheel: WheelIdentity) -> dict[str, object]:
    payload = _document(text, "configuration contract")
    expected_keys = {
        "schema",
        "workflow_schema_version",
        "workflow_schema_sha256",
        "workflow_schema",
        "producer",
        "validation_response_schema",
    }
    if set(payload) != expected_keys:
        raise CandidateCompatibilityError("candidate configuration contract fields drifted")
    if payload["schema"] != "confflow.configuration-contract.v1":
        raise CandidateCompatibilityError("candidate configuration contract schema is unsupported")
    if payload["validation_response_schema"] != "confflow.configuration-validation.v1":
        raise CandidateCompatibilityError("candidate validation response schema is unsupported")
    if payload["workflow_schema_version"] != "confflow.workflow.v2":
        raise CandidateCompatibilityError("candidate workflow schema version is unsupported")
    schema = payload["workflow_schema"]
    if not isinstance(schema, dict):
        raise CandidateCompatibilityError("candidate workflow schema is not an object")
    canonical_schema = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    schema_sha256 = hashlib.sha256(canonical_schema.encode("utf-8")).hexdigest()
    if payload["workflow_schema_sha256"] != schema_sha256:
        raise CandidateCompatibilityError("candidate workflow schema digest does not match bytes")
    producer = payload["producer"]
    if not isinstance(producer, dict) or set(producer) != {"package", "version", "commit", "dirty"}:
        raise CandidateCompatibilityError("candidate configuration producer provenance is malformed")
    if producer["package"] != wheel.name or producer["version"] != wheel.version:
        raise CandidateCompatibilityError("candidate configuration producer does not match wheel metadata")
    if producer["commit"] != wheel.build_commit or producer["dirty"] != wheel.build_dirty:
        raise CandidateCompatibilityError("candidate configuration producer provenance does not match wheel")
    return payload


def _probe(kind: str) -> str:
    try:
        cli = importlib.import_module("confflow.cli")
    except ImportError as exc:
        raise CandidateCompatibilityError("the local environment does not have the candidate ConfFlow installed") from exc
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        if kind == "capabilities":
            result = cli.main(["--capabilities", "--json"])
        else:
            result = cli.main(["config", "contract", "--json"])
    if result not in (0, None):
        raise CandidateCompatibilityError(f"ConfFlow {kind} probe failed with exit code {result}")
    return output.getvalue()


def _load_manifest(path: Path) -> tuple[Path, str, str, str]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        artifact = manifest["artifact"]
        wheel = path.parents[2] / artifact["relative_path"]
        return wheel, artifact["sha256"], artifact["metadata_sha256"], artifact["version"]
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise CandidateCompatibilityError(f"invalid local wheel manifest: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--manifest", type=Path, default=Path("requirements/locks/jobdesk-chem-wheel-manifest.json"))
    parser.add_argument("--expected-sha256")
    parser.add_argument("--expected-metadata-sha256")
    parser.add_argument("--capabilities-json", type=Path)
    parser.add_argument("--contract-json", type=Path)
    args = parser.parse_args(argv)
    try:
        manifest_wheel, manifest_sha, manifest_metadata_sha, manifest_version = _load_manifest(args.manifest)
        wheel_path = args.wheel or manifest_wheel
        wheel = inspect_wheel(
            wheel_path,
            expected_sha256=args.expected_sha256 or manifest_sha,
            expected_metadata_sha256=args.expected_metadata_sha256 or manifest_metadata_sha,
        )
        if wheel.version != manifest_version:
            raise CandidateCompatibilityError("candidate wheel version does not match the local lock manifest")
        capabilities_text = (
            args.capabilities_json.read_text(encoding="utf-8") if args.capabilities_json else _probe("capabilities")
        )
        contract_text = args.contract_json.read_text(encoding="utf-8") if args.contract_json else _probe("contract")
        validate_capabilities(capabilities_text, wheel)
        contract = validate_configuration_contract(contract_text, wheel)
        print(
            json.dumps(
                {
                    "status": "compatible",
                    "wheel": wheel.filename,
                    "version": wheel.version,
                    "sha256": wheel.sha256,
                    "metadata_sha256": wheel.metadata_sha256,
                    "workflow_schema_sha256": contract["workflow_schema_sha256"],
                    "producer_commit": wheel.build_commit,
                    "producer_dirty": wheel.build_dirty,
                },
                sort_keys=True,
            )
        )
        return 0
    except (CandidateCompatibilityError, OSError, zipfile.BadZipFile) as exc:
        print(f"local ConfFlow candidate rejected: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
