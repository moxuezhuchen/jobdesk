"""Fail-closed offline checks for a GitHub artifact attestation bundle.

The GitHub attestation action emits a Sigstore bundle containing a DSSE
envelope.  This module checks the *claims* in that envelope against the final
wheel and the expected GitHub Actions source identity.  It deliberately does
not verify a certificate, transparency log proof, or DSSE signature.  A
workflow must run ``gh attestation verify`` as the cryptographic verification
step before relying on this result.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

SIGSTORE_BUNDLE_MEDIA_TYPES = frozenset(
    {
        "application/vnd.dev.sigstore.bundle.v0.3+json",
        "application/vnd.dev.sigstore.bundle+json;version=0.3",
    }
)
DSSE_PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PROVENANCE_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
GITHUB_WORKFLOW_BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"
_HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_HEX_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
_REPOSITORY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class AttestationValidationError(ValueError):
    """Raised when an attestation bundle fails an offline policy check."""


@dataclass(frozen=True)
class AttestationClaims:
    """Claims from the one statement accepted by :func:`validate_attestation_bundle`."""

    subject_name: str
    subject_digest: str
    repository: str
    ref: str
    commit: str
    workflow: str


@dataclass(frozen=True)
class _SourceDependency:
    repository: str
    ref: str | None
    digest: dict[str, Any]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AttestationValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AttestationValidationError(f"cannot read bundle {path}: {exc}") from exc
    try:
        document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except AttestationValidationError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise AttestationValidationError(f"bundle is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise AttestationValidationError("bundle root must be a JSON object")
    return document


def _require_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise AttestationValidationError(f"{field} must be a non-empty string")
    return value


def _repository_path(value: str, *, field: str) -> str:
    path = value.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or any(not _REPOSITORY_PART.fullmatch(part) for part in parts):
        raise AttestationValidationError(f"{field} is not a GitHub owner/repository")
    return "/".join(parts).lower()


def _canonical_repository(value: Any, *, field: str) -> str:
    text = _require_string(value, field=field)
    if "://" not in text:
        return _repository_path(text, field=field)

    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError as exc:
        raise AttestationValidationError(f"{field} has an invalid URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AttestationValidationError(f"{field} must be an https://github.com repository URL")
    return _repository_path(parsed.path, field=field)


def _canonical_ref(value: Any, *, field: str) -> str:
    text = _require_string(value, field=field)
    if not text.startswith("refs/") or any(char.isspace() or ord(char) < 32 for char in text):
        raise AttestationValidationError(f"{field} must be a fully qualified Git ref")
    return text


def _canonical_commit(value: Any, *, field: str) -> str:
    text = _require_string(value, field=field)
    if not _HEX_COMMIT.fullmatch(text):
        raise AttestationValidationError(f"{field} must be a full 40-character commit SHA")
    return text.lower()


def _workflow_path(value: Any, *, field: str) -> str:
    text = _require_string(value, field=field)
    parts = text.split("/")
    if (
        "\\" in text
        or text.startswith("/")
        or "//" in text
        or any(part in {"", ".", ".."} for part in parts)
        or not text.startswith(".github/workflows/")
        or not text.endswith((".yml", ".yaml"))
    ):
        raise AttestationValidationError(f"{field} must be a relative .github/workflows YAML path")
    return text


def _decode_base64(value: Any, *, field: str) -> bytes:
    text = _require_string(value, field=field)
    try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise AttestationValidationError(f"{field} is not canonical base64") from exc
    if base64.b64encode(raw).decode("ascii") != text:
        raise AttestationValidationError(f"{field} is not canonical base64")
    return raw


def _sha256_file(path: Path) -> str:
    try:
        if not path.is_file():
            raise AttestationValidationError(f"subject wheel is not a regular file: {path}")
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except AttestationValidationError:
        raise
    except OSError as exc:
        raise AttestationValidationError(f"cannot read subject wheel {path}: {exc}") from exc


def _github_dependency_uri(value: str) -> tuple[str, str | None] | None:
    """Return the GitHub repository/ref encoded in a resolved dependency URI."""

    text = value[4:] if value.startswith("git+") else value
    parsed = urlsplit(text)
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    path = parsed.path.lstrip("/")
    if "@" in path:
        repository, ref = path.rsplit("@", 1)
    else:
        repository, ref = path, None
    try:
        return _repository_path(repository, field="resolved dependency URI"), ref
    except AttestationValidationError:
        return None


def _workflow_identity(value: str) -> tuple[str, str, str]:
    """Parse a GitHub workflow builder URI into repository, path, and ref."""

    if "@" not in value:
        raise AttestationValidationError("runDetails.builder.id has no source ref")
    uri, ref = value.rsplit("@", 1)
    parsed_ref = _canonical_ref(ref, field="runDetails.builder.id ref")
    parsed = urlsplit(uri)
    try:
        port = parsed.port
    except ValueError as exc:
        raise AttestationValidationError("runDetails.builder.id has an invalid URL") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
    ):
        raise AttestationValidationError("runDetails.builder.id must be a GitHub workflow URL")
    parts = parsed.path.lstrip("/").split("/")
    if len(parts) < 3:
        raise AttestationValidationError("runDetails.builder.id has no workflow path")
    parsed_repository = _repository_path("/".join(parts[:2]), field="runDetails.builder.id repository")
    parsed_workflow = _workflow_path("/".join(parts[2:]), field="runDetails.builder.id workflow")
    return parsed_repository, parsed_workflow, parsed_ref


def _validate_statement(
    statement: dict[str, Any],
    *,
    subject_name: str,
    subject_digest: str,
    repository: str,
    ref: str,
    commit: str,
    workflow: str,
) -> AttestationClaims:
    if statement.get("_type") != STATEMENT_TYPE:
        raise AttestationValidationError("statement _type is not in-toto Statement/v1")

    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        raise AttestationValidationError("statement subject must be a list")
    if len(subjects) != 1:
        raise AttestationValidationError("statement must contain exactly one subject")
    subject = subjects[0]
    if not isinstance(subject, dict):
        raise AttestationValidationError("statement subject must be an object")
    actual_name = _require_string(subject.get("name"), field="statement subject.name")
    if actual_name != subject_name:
        raise AttestationValidationError(
            f"statement subject.name {actual_name!r} does not match final wheel {subject_name!r}"
        )
    digest = subject.get("digest")
    if not isinstance(digest, dict) or set(digest) != {"sha256"}:
        raise AttestationValidationError("statement subject.digest must contain only sha256")
    actual_digest = _require_string(digest.get("sha256"), field="statement subject.digest.sha256")
    if not _HEX_SHA256.fullmatch(actual_digest):
        raise AttestationValidationError("statement subject.digest.sha256 is not a SHA256 hex digest")
    if actual_digest.lower() != subject_digest.lower():
        raise AttestationValidationError("statement subject digest does not match the final wheel")

    if statement.get("predicateType") != PROVENANCE_PREDICATE_TYPE:
        raise AttestationValidationError("statement predicateType is not SLSA provenance v1")
    predicate = statement.get("predicate")
    if not isinstance(predicate, dict):
        raise AttestationValidationError("statement predicate must be an object")
    build_definition = predicate.get("buildDefinition")
    if not isinstance(build_definition, dict):
        raise AttestationValidationError("predicate.buildDefinition must be an object")
    if build_definition.get("buildType") != GITHUB_WORKFLOW_BUILD_TYPE:
        raise AttestationValidationError(
            "predicate.buildDefinition.buildType is not GitHub Actions workflow provenance"
        )

    external_parameters = build_definition.get("externalParameters")
    if not isinstance(external_parameters, dict):
        raise AttestationValidationError("predicate.buildDefinition.externalParameters is missing")
    workflow_claim = external_parameters.get("workflow")
    if not isinstance(workflow_claim, dict):
        raise AttestationValidationError("predicate.buildDefinition.externalParameters.workflow is missing")
    actual_repository = _canonical_repository(workflow_claim.get("repository"), field="workflow.repository")
    if actual_repository != repository:
        raise AttestationValidationError("attested workflow repository does not match expected repository")
    actual_ref = _canonical_ref(workflow_claim.get("ref"), field="workflow.ref")
    if actual_ref != ref:
        raise AttestationValidationError("attested workflow ref does not match expected ref")
    actual_workflow = _workflow_path(workflow_claim.get("path"), field="workflow.path")
    if actual_workflow != workflow:
        raise AttestationValidationError("attested workflow path does not match expected workflow")
    for optional_key in ("sha", "commit"):
        if optional_key in workflow_claim:
            optional_commit = _canonical_commit(workflow_claim[optional_key], field=f"workflow.{optional_key}")
            if optional_commit != commit:
                raise AttestationValidationError(f"workflow.{optional_key} does not match expected commit")

    run_details = predicate.get("runDetails")
    if not isinstance(run_details, dict):
        raise AttestationValidationError("predicate.runDetails is missing")
    builder = run_details.get("builder")
    if not isinstance(builder, dict):
        raise AttestationValidationError("predicate.runDetails.builder is missing")
    builder_id = _require_string(builder.get("id"), field="runDetails.builder.id")
    actual_builder = _workflow_identity(builder_id)
    if actual_builder != (repository, workflow, ref):
        raise AttestationValidationError("runDetails.builder.id does not match expected workflow identity")

    dependencies = build_definition.get("resolvedDependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise AttestationValidationError("predicate.buildDefinition.resolvedDependencies is missing")
    source_dependencies: list[_SourceDependency] = []
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, dict):
            raise AttestationValidationError(f"resolvedDependencies[{index}] must be an object")
        uri = _require_string(dependency.get("uri"), field=f"resolvedDependencies[{index}].uri")
        dependency_digest = dependency.get("digest")
        if not isinstance(dependency_digest, dict) or not dependency_digest:
            raise AttestationValidationError(f"resolvedDependencies[{index}].digest is missing")
        parsed_uri = _github_dependency_uri(uri)
        if parsed_uri is not None and parsed_uri[0] == repository:
            source_dependencies.append(_SourceDependency(parsed_uri[0], parsed_uri[1], dependency_digest))
    if len(source_dependencies) != 1:
        raise AttestationValidationError(
            "resolvedDependencies must contain exactly one expected GitHub source repository"
        )
    source = source_dependencies[0]
    if source.ref != ref:
        raise AttestationValidationError("resolved source dependency ref does not match expected ref")
    source_commit = _canonical_commit(
        source.digest.get("gitCommit"), field="resolved source dependency digest.gitCommit"
    )
    if source_commit != commit:
        raise AttestationValidationError("resolved source dependency commit does not match expected commit")

    return AttestationClaims(
        subject_name=subject_name,
        subject_digest=actual_digest.lower(),
        repository=repository,
        ref=ref,
        commit=commit,
        workflow=workflow,
    )


def validate_attestation_bundle(
    bundle: str | Path,
    subject: str | Path,
    *,
    repository: str,
    ref: str,
    commit: str,
    workflow: str,
) -> AttestationClaims:
    """Validate one GitHub Sigstore bundle against a final wheel.

    The return value contains the structurally matched claims.  Successful
    return means only that the decoded statement is bound to the supplied
    files and source identity; it is not cryptographic signature verification.
    """

    bundle_path = Path(bundle)
    subject_path = Path(subject)
    expected_repository = _canonical_repository(repository, field="expected repository")
    expected_ref = _canonical_ref(ref, field="expected ref")
    expected_commit = _canonical_commit(commit, field="expected commit")
    expected_workflow = _workflow_path(workflow, field="expected workflow")
    subject_digest = _sha256_file(subject_path)
    document = _read_json(bundle_path)

    media_type = document.get("mediaType")
    if media_type not in SIGSTORE_BUNDLE_MEDIA_TYPES:
        raise AttestationValidationError("bundle mediaType is not a supported Sigstore v0.3 bundle")
    verification_material = document.get("verificationMaterial")
    if not isinstance(verification_material, dict) or not verification_material:
        raise AttestationValidationError("bundle verificationMaterial is missing")
    if not any(
        isinstance(verification_material.get(key), dict) and verification_material[key]
        for key in ("certificate", "publicKey")
    ):
        raise AttestationValidationError("bundle verificationMaterial has no certificate or public key")

    envelope = document.get("dsseEnvelope")
    if not isinstance(envelope, dict):
        raise AttestationValidationError("bundle dsseEnvelope is missing")
    if envelope.get("payloadType") != DSSE_PAYLOAD_TYPE:
        raise AttestationValidationError("bundle DSSE payloadType is not in-toto JSON")
    signatures = envelope.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise AttestationValidationError("bundle DSSE signatures are missing")
    for index, signature in enumerate(signatures):
        if not isinstance(signature, dict):
            raise AttestationValidationError(f"DSSE signature[{index}] must be an object")
        _decode_base64(signature.get("sig"), field=f"DSSE signature[{index}].sig")
    payload = _decode_base64(envelope.get("payload"), field="DSSE payload")
    try:
        statement = json.loads(payload.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except AttestationValidationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise AttestationValidationError("DSSE payload is not valid JSON") from exc
    if not isinstance(statement, dict):
        raise AttestationValidationError("DSSE payload statement must be a JSON object")

    return _validate_statement(
        statement,
        subject_name=subject_path.name,
        subject_digest=subject_digest,
        repository=expected_repository,
        ref=expected_ref,
        commit=expected_commit,
        workflow=expected_workflow,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Check GitHub attestation claims offline. This does not verify signatures; "
            "run gh attestation verify separately."
        )
    )
    parser.add_argument("--bundle", type=Path, required=True, help="Sigstore attestation bundle JSON")
    parser.add_argument("--subject", type=Path, required=True, help="final wheel path")
    parser.add_argument("--repository", required=True, help="expected owner/repository")
    parser.add_argument("--ref", required=True, help="expected fully qualified Git ref")
    parser.add_argument("--commit", required=True, help="expected full source commit SHA")
    parser.add_argument("--workflow", required=True, help="expected .github/workflows/*.yml path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        claims = validate_attestation_bundle(
            args.bundle,
            args.subject,
            repository=args.repository,
            ref=args.ref,
            commit=args.commit,
            workflow=args.workflow,
        )
    except AttestationValidationError as exc:
        print(f"attestation structure rejected: {exc}", file=sys.stderr)
        return 1
    print(
        f"offline attestation structure matches {claims.subject_name} at {claims.repository}@{claims.ref}; "
        "cryptographic signature verification remains required via gh attestation verify"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
