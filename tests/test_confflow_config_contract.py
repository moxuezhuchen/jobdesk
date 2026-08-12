"""Boundary tests for the producer config-contract candidate."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from jobdesk_app.core.confflow_contract import (
    REFERENCE_VERSION,
    ROLLBACK_REFERENCE_BUILD_COMMIT,
    ROLLBACK_REFERENCE_VERSION,
    ROLLBACK_REFERENCE_WHEEL_FILENAME,
    ROLLBACK_REFERENCE_WHEEL_SHA256,
)
from jobdesk_app.core.confflow_executable import ConfFlowExecutableIdentity
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.remote.ssh import SSHResult
from jobdesk_app.remote.submitter import JobSubmitter
from jobdesk_app.services.confflow_config_contract import (
    CONFIG_CONTRACT_SCHEMA,
    SEMANTIC_CONTRACT_VERSION,
    ConfigContractResolutionError,
    ConfigContractResolver,
    parse_config_contract,
    validate_vendored_schema_bundle_bytes,
    vendored_schema_bundle,
)

_DIGEST = "a" * 64
_IDENTITY = ConfFlowExecutableIdentity(
    path="/opt/confflow/bin/confflow",
    realpath="/opt/confflow/bin/confflow",
    sha256=_DIGEST,
    python="/opt/confflow/bin/python3.12",
    size=10,
    mtime_ns=20,
    device=30,
    inode=40,
)


def _capabilities(
    *,
    config_command: bool = True,
    executable_sha256: str = _DIGEST,
    approved_identity: bool = False,
) -> ConfFlowCapabilities:
    commands = {
        "config_contract": True,
        "bash": True,
    } if config_command else {}
    version = ROLLBACK_REFERENCE_VERSION if approved_identity else REFERENCE_VERSION
    build = {
        "commit": ROLLBACK_REFERENCE_BUILD_COMMIT if approved_identity else "commit-2",
        "dirty": False,
    }
    producer = {
        "package": "confflow",
        "version": version,
        "build": build,
        "wheel": {
            "filename": ROLLBACK_REFERENCE_WHEEL_FILENAME if approved_identity else "confflow.whl",
            "sha256": ROLLBACK_REFERENCE_WHEEL_SHA256 if approved_identity else _DIGEST,
        },
    }
    return ConfFlowCapabilities(
        schema_version=4,
        version=version,
        workflow_state=True,
        resume=True,
        dag=True,
        commands=commands,
        build=build,
        producer=producer,
        executable={"sha256": executable_sha256},
        raw_payload={},
    )


def _contract_payload(resolver: ConfigContractResolver) -> dict[str, object]:
    return {
        "response_schema": CONFIG_CONTRACT_SCHEMA,
        "workflow_schema": {
            "version": "v1",
            "sha256": resolver._workflow_schema_sha256,
            "resource": "workflow_config_v1.schema.json",
        },
        "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
        "producer": {
            "distribution": "confflow",
            "version": REFERENCE_VERSION,
            "configuration_contract": SEMANTIC_CONTRACT_VERSION,
        },
    }


def _response(payload: dict[str, object], *, exit_code: int = 0, stdout: str | None = None) -> SSHResult:
    return SSHResult(
        command="config contract",
        exit_code=exit_code,
        stdout=stdout if stdout is not None else json.dumps(payload),
        stderr="",
        duration_seconds=0.01,
    )


def test_config_contract_parser_requires_pure_json_stdout() -> None:
    payload = {"response_schema": CONFIG_CONTRACT_SCHEMA}
    assert parse_config_contract("\n" + json.dumps(payload) + "\n") == payload
    with pytest.raises(ValueError, match="malformed"):
        parse_config_contract("warning\n" + json.dumps(payload))
    with pytest.raises(ValueError, match="malformed"):
        parse_config_contract(json.dumps(payload) + "\nwarning")


def test_vendored_bundle_bytes_and_digest_are_verified() -> None:
    bundle = vendored_schema_bundle()
    assert validate_vendored_schema_bundle_bytes(bundle.bytes, bundle.digest) == bundle.bytes
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_vendored_schema_bundle_bytes(bundle.bytes + b"x", bundle.digest)


def test_config_contract_accepts_bound_contract_and_cache_key_is_identity_bound() -> None:
    resolver = ConfigContractResolver()
    ssh = MagicMock()
    ssh.run.return_value = _response(_contract_payload(resolver))
    result = resolver.resolve(
        ssh,
        server_id="server-a",
        executable="/opt/confflow/bin/confflow",
        capabilities=_capabilities(),
        executable_identity=_IDENTITY,
    )
    assert result.accepted is True
    assert result.mode == "contract"
    assert result.remote_identity.as_dict()["server_id"] == "server-a"
    assert result.remote_identity.value
    assert result.remote_identity.value != resolver.resolve(
        ssh,
        server_id="server-b",
        executable="/opt/confflow/bin/confflow",
        capabilities=_capabilities(),
        executable_identity=_IDENTITY,
    ).remote_identity.value


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("response_schema", "wrong", "response schema mismatch"),
        ("workflow_version", "v2", "workflow schema version mismatch"),
        ("semantic_contract_version", "2.1", "semantic contract version mismatch"),
        ("workflow_schema_sha256", "b" * 64, "workflow schema hash mismatch"),
        ("workflow_resource", "other.json", "workflow schema resource mismatch"),
    ],
)
def test_contract_mismatches_fail_closed(field: str, value: object, message: str) -> None:
    resolver = ConfigContractResolver()
    payload = _contract_payload(resolver)
    if field == "response_schema" or field == "semantic_contract_version":
        payload[field] = value
    elif field == "workflow_version":
        payload["workflow_schema"]["version"] = value  # type: ignore[index]
    elif field == "workflow_schema_sha256":
        payload["workflow_schema"]["sha256"] = value  # type: ignore[index]
    else:
        payload["workflow_schema"]["resource"] = value  # type: ignore[index]
    ssh = MagicMock()
    ssh.run.return_value = _response(payload)
    with pytest.raises(ConfigContractResolutionError, match=message):
        resolver.resolve(
            ssh,
            server_id="server-a",
            executable=None,
            capabilities=_capabilities(),
            executable_identity=_IDENTITY,
        )


def test_producer_and_executable_binding_mismatch_fail_closed() -> None:
    resolver = ConfigContractResolver()
    payload = _contract_payload(resolver)
    ssh = MagicMock()
    ssh.run.return_value = _response(payload)
    capabilities = _capabilities()
    capabilities = ConfFlowCapabilities(
        **{**capabilities.__dict__, "executable": {"sha256": "b" * 64}},  # type: ignore[attr-defined]
    )
    with pytest.raises(ConfigContractResolutionError, match="executable binding"):
        resolver.resolve(
            ssh,
            server_id="server-a",
            executable=None,
            capabilities=capabilities,
            executable_identity=_IDENTITY,
        )


def test_stable_v2_without_command_has_explicit_compatibility_result() -> None:
    resolver = ConfigContractResolver()
    result = resolver.resolve(
        MagicMock(),
        server_id="server-a",
        executable=None,
        capabilities=_capabilities(config_command=False, approved_identity=True),
        executable_identity=_IDENTITY,
    )
    assert result.mode == "approved-identity-compatibility"
    assert "approved rollback v2.0.0" in result.reason


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("package", "not-confflow"),
        ("version", REFERENCE_VERSION),
        ("wheel_filename", "confflow-tampered.whl"),
    ],
)
def test_compatibility_requires_exact_rollback_producer_pairing(field: str, value: str) -> None:
    capabilities = _capabilities(config_command=False, approved_identity=True)
    producer = dict(capabilities.producer or {})
    wheel = dict(producer.get("wheel") or {})
    if field == "wheel_filename":
        wheel["filename"] = value
        producer["wheel"] = wheel
    else:
        producer[field] = value
    capabilities = replace(capabilities, producer=producer, commands={"config_contract": False})

    with pytest.raises(ConfigContractResolutionError, match="unknown producer identity"):
        ConfigContractResolver().resolve(
            MagicMock(),
            server_id="server-a",
            executable=None,
            capabilities=capabilities,
            executable_identity=_IDENTITY,
        )


def test_current_v211_without_config_command_cannot_use_rollback_compatibility() -> None:
    ssh = MagicMock()
    ssh.run.return_value = SSHResult("config contract", 127, "", "command not found", 0.01)
    with pytest.raises(ConfigContractResolutionError, match="not the approved rollback v2.0.0"):
        ConfigContractResolver().resolve(
            ssh,
            server_id="server-a",
            executable=None,
            capabilities=_capabilities(config_command=False),
            executable_identity=_IDENTITY,
        )


def test_unknown_identity_cannot_use_compatibility() -> None:
    resolver = ConfigContractResolver()
    with pytest.raises(ConfigContractResolutionError, match="unknown executable identity"):
        resolver.resolve(
            MagicMock(),
            server_id="server-a",
            executable=None,
            capabilities=_capabilities(
                config_command=False,
                executable_sha256="b" * 64,
                approved_identity=True,
            ),
            executable_identity=_IDENTITY,
        )


def test_contract_command_failure_is_fail_closed() -> None:
    resolver = ConfigContractResolver()
    ssh = MagicMock()
    ssh.run.return_value = SSHResult("config contract", 1, "", "producer failed", 0.01)
    with pytest.raises(ConfigContractResolutionError, match="command failed"):
        resolver.resolve(
            ssh,
            server_id="server-a",
            executable=None,
            capabilities=_capabilities(),
            executable_identity=_IDENTITY,
        )


def test_submitter_resolves_before_upload_and_stops_on_contract_failure(monkeypatch) -> None:
    task = TaskRecord(
        task_id="water",
        batch_id="batch",
        task_files=["water.xyz"],
        remote_job_dir="/remote/water",
        remote_task_files=["water.xyz"],
        rendered_command="confflow water.xyz",
        status=TaskStatus.uploaded,
        workflow_kind="confflow",
    )
    ssh = MagicMock()
    sftp = MagicMock()
    monkeypatch.setattr(
        "jobdesk_app.remote.submitter.probe_confflow_capabilities",
        lambda *_args, **_kwargs: _capabilities(),
    )
    monkeypatch.setattr("jobdesk_app.remote.submitter.validate_confflow_production_capability", lambda *_args, **_kwargs: None)
    resolver = MagicMock()
    resolver.resolve.side_effect = ConfigContractResolutionError("contract rejected")
    submitter = JobSubmitter(
        tasks=[task],
        ssh=ssh,
        sftp=sftp,
        remote_batch_dir="/remote/batch",
        batch_id="batch",
        config_contract_resolver=resolver,
    )
    submitter._probe_executable_identity = lambda _capabilities: _IDENTITY
    result = submitter.submit_batch()
    assert result.submitted_task_count == 0
    assert result.errors == ["contract rejected"]
    assert resolver.resolve.called is True
    assert sftp.upload_file.called is False
