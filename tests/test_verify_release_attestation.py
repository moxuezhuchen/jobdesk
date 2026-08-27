from __future__ import annotations

import base64
import copy
import hashlib
import json
from pathlib import Path

import pytest
from scripts.verify_release_attestation import (
    DSSE_PAYLOAD_TYPE,
    PROVENANCE_PREDICATE_TYPE,
    AttestationClaims,
    AttestationValidationError,
    main,
    validate_attestation_bundle,
)

REPOSITORY = "moxuezhuchen/jobdesk"
REF = "refs/tags/v0.7.6"
COMMIT = "a" * 40
WORKFLOW = ".github/workflows/release.yml"
MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"


def _subject(tmp_path: Path) -> Path:
    wheel = tmp_path / "jobdesk-0.7.6-py3-none-any.whl"
    wheel.write_bytes(b"wheel bytes used by the attestation fixture")
    return wheel


def _statement(wheel: Path, *, repository: str = REPOSITORY, ref: str = REF, commit: str = COMMIT) -> dict:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": wheel.name, "digest": {"sha256": digest}}],
        "predicateType": PROVENANCE_PREDICATE_TYPE,
        "predicate": {
            "buildDefinition": {
                "buildType": "https://actions.github.io/buildtypes/workflow/v1",
                "externalParameters": {
                    "workflow": {
                        "repository": f"https://github.com/{repository}",
                        "ref": ref,
                        "path": WORKFLOW,
                    }
                },
                "resolvedDependencies": [
                    {
                        "uri": f"git+https://github.com/{repository}@{ref}",
                        "digest": {"gitCommit": commit},
                    }
                ],
            },
            "runDetails": {"builder": {"id": f"https://github.com/{REPOSITORY}/{WORKFLOW}@{REF}"}},
        },
    }


def _bundle(statement: dict) -> dict:
    payload = json.dumps(statement, separators=(",", ":")).encode("utf-8")
    return {
        "mediaType": MEDIA_TYPE,
        "verificationMaterial": {"certificate": {"rawBytes": "Y2VydA=="}, "tlogEntries": []},
        "dsseEnvelope": {
            "payloadType": DSSE_PAYLOAD_TYPE,
            "payload": base64.b64encode(payload).decode("ascii"),
            "signatures": [{"sig": base64.b64encode(b"signature").decode("ascii"), "keyid": ""}],
        },
    }


def _write_bundle(path: Path, bundle: dict) -> None:
    path.write_text(json.dumps(bundle), encoding="utf-8")


def _validate(bundle: Path, wheel: Path) -> AttestationClaims:
    return validate_attestation_bundle(
        bundle,
        wheel,
        repository=REPOSITORY,
        ref=REF,
        commit=COMMIT,
        workflow=WORKFLOW,
    )


def test_valid_github_bundle_matches_final_wheel_and_source_identity(tmp_path: Path) -> None:
    wheel = _subject(tmp_path)
    bundle = tmp_path / "attestation.bundle.json"
    _write_bundle(bundle, _bundle(_statement(wheel)))

    claims = _validate(bundle, wheel)

    assert claims == AttestationClaims(
        subject_name=wheel.name,
        subject_digest=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        repository=REPOSITORY,
        ref=REF,
        commit=COMMIT,
        workflow=WORKFLOW,
    )


@pytest.mark.parametrize(
    ("path", "replacement", "message"),
    [
        ("subject", [{"name": "other.whl", "digest": {"sha256": "0" * 64}}], "subject"),
        ("subject", [{"name": "jobdesk-0.7.6-py3-none-any.whl", "digest": {"sha256": "0" * 64}}], "digest"),
    ],
)
def test_subject_name_and_digest_must_match_final_wheel(
    tmp_path: Path, path: str, replacement: list[dict], message: str
) -> None:
    wheel = _subject(tmp_path)
    statement = _statement(wheel)
    statement[path] = replacement
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, _bundle(statement))

    with pytest.raises(AttestationValidationError, match=message):
        _validate(bundle, wheel)


def test_multiple_subjects_are_rejected_even_when_one_matches(tmp_path: Path) -> None:
    wheel = _subject(tmp_path)
    statement = _statement(wheel)
    statement["subject"].append({"name": "unexpected.whl", "digest": {"sha256": "1" * 64}})
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, _bundle(statement))

    with pytest.raises(AttestationValidationError, match="exactly one subject"):
        _validate(bundle, wheel)


def test_multiple_dsse_envelopes_are_rejected_without_selecting_one(tmp_path: Path) -> None:
    wheel = _subject(tmp_path)
    bundle_document = _bundle(_statement(wheel))
    bundle_document["dsseEnvelope"] = [
        bundle_document["dsseEnvelope"],
        copy.deepcopy(bundle_document["dsseEnvelope"]),
    ]
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, bundle_document)

    with pytest.raises(AttestationValidationError, match="dsseEnvelope"):
        _validate(bundle, wheel)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("repository", "other/repository", "repository"),
        ("ref", "refs/tags/v0.7.2", "ref"),
        ("commit", "b" * 40, "commit"),
    ],
)
def test_source_repository_ref_and_commit_are_bound(tmp_path: Path, field: str, value: str, message: str) -> None:
    wheel = _subject(tmp_path)
    statement = _statement(wheel, **{field: value})
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, _bundle(statement))

    with pytest.raises(AttestationValidationError, match=message):
        _validate(bundle, wheel)


def test_workflow_path_and_builder_identity_are_bound(tmp_path: Path) -> None:
    wheel = _subject(tmp_path)
    statement = _statement(wheel)
    build = statement["predicate"]["buildDefinition"]
    build["externalParameters"]["workflow"]["path"] = ".github/workflows/other.yml"
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, _bundle(statement))

    with pytest.raises(AttestationValidationError, match="workflow path"):
        _validate(bundle, wheel)

    statement = _statement(wheel)
    statement["predicate"]["runDetails"]["builder"][
        "id"
    ] = f"https://github.com/{REPOSITORY}/{WORKFLOW}@refs/tags/v0.7.2"
    _write_bundle(bundle, _bundle(statement))
    with pytest.raises(AttestationValidationError, match="workflow identity"):
        _validate(bundle, wheel)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda bundle: bundle.pop("dsseEnvelope"),
        lambda bundle: bundle["dsseEnvelope"].pop("payload"),
        lambda bundle: bundle["dsseEnvelope"].update(payloadType="text/plain"),
        lambda bundle: bundle["dsseEnvelope"].update(signatures=[]),
        lambda bundle: bundle["verificationMaterial"].pop("certificate"),
        lambda bundle: bundle.update(mediaType="application/json"),
    ],
)
def test_missing_or_invalid_bundle_structure_fails_closed(tmp_path: Path, mutation) -> None:
    wheel = _subject(tmp_path)
    bundle_document = _bundle(_statement(wheel))
    mutation(bundle_document)
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, bundle_document)

    with pytest.raises(AttestationValidationError):
        _validate(bundle, wheel)


def test_malformed_payload_and_duplicate_json_keys_fail_closed(tmp_path: Path) -> None:
    wheel = _subject(tmp_path)
    bundle_document = _bundle(_statement(wheel))
    bundle_document["dsseEnvelope"]["payload"] = "not base64"
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, bundle_document)
    with pytest.raises(AttestationValidationError, match="base64"):
        _validate(bundle, wheel)

    bundle.write_text('{"mediaType": "' + MEDIA_TYPE + '", "mediaType": "' + MEDIA_TYPE + '"}', encoding="utf-8")
    with pytest.raises(AttestationValidationError, match="duplicate"):
        _validate(bundle, wheel)


def test_source_dependency_must_be_unique_and_have_matching_commit(tmp_path: Path) -> None:
    wheel = _subject(tmp_path)
    statement = _statement(wheel)
    dependencies = statement["predicate"]["buildDefinition"]["resolvedDependencies"]
    dependencies.append(copy.deepcopy(dependencies[0]))
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, _bundle(statement))
    with pytest.raises(AttestationValidationError, match="exactly one"):
        _validate(bundle, wheel)

    statement = _statement(wheel)
    dependencies = statement["predicate"]["buildDefinition"]["resolvedDependencies"]
    dependencies[0]["digest"].pop("gitCommit")
    _write_bundle(bundle, _bundle(statement))
    with pytest.raises(AttestationValidationError, match="digest"):
        _validate(bundle, wheel)


def test_cli_success_explicitly_preserves_cryptographic_verification_boundary(tmp_path: Path, capsys) -> None:
    wheel = _subject(tmp_path)
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, _bundle(_statement(wheel)))

    result = main(
        [
            "--bundle",
            str(bundle),
            "--subject",
            str(wheel),
            "--repository",
            REPOSITORY,
            "--ref",
            REF,
            "--commit",
            COMMIT,
            "--workflow",
            WORKFLOW,
        ]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "offline attestation structure" in output
    assert "gh attestation verify" in output


def test_cli_rejects_mismatch_with_nonzero_status(tmp_path: Path, capsys) -> None:
    wheel = _subject(tmp_path)
    statement = _statement(wheel, commit="b" * 40)
    bundle = tmp_path / "bundle.json"
    _write_bundle(bundle, _bundle(statement))

    result = main(
        [
            "--bundle",
            str(bundle),
            "--subject",
            str(wheel),
            "--repository",
            REPOSITORY,
            "--ref",
            REF,
            "--commit",
            COMMIT,
            "--workflow",
            WORKFLOW,
        ]
    )

    assert result == 1
    assert "rejected" in capsys.readouterr().err
