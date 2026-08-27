from __future__ import annotations

import base64
import hashlib
import inspect
import json
import re
import shlex
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobdesk_app.application.confflow_client import SubmitRequest
from jobdesk_app.application.configuration_contract import (
    ConfigurationAdmission,
    ConfigurationAdmissionError,
    ConfigurationDiagnostic,
    ConfigurationValidationResult,
)
from jobdesk_app.config.schema import ServerConfig
from jobdesk_app.core.confflow_contract import (
    EXPECTED_ARTIFACTS,
    REFERENCE_BUILD_COMMIT,
    REFERENCE_VERSION,
    REFERENCE_WHEEL_FILENAME,
    REFERENCE_WHEEL_SHA256,
    REQUIRED_COMMANDS,
)
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.remote.confflow_config_contract import (
    ConfigurationContractError,
    parse_contract_response,
    parse_validation_response,
)
from jobdesk_app.remote.ssh import SSHClientWrapper
from jobdesk_app.resources.config_contracts import stable_2_0_0
from jobdesk_app.services.run_coordinator import RunCoordinator
from jobdesk_app.services.run_service import RunService
from jobdesk_app.services.ssh_confflow_client import SSHConfFlowClient
from jobdesk_app.services.ssh_configuration_contract_client import (
    SSHConfigurationContractClient,
    build_config_contract_command,
    build_config_validate_command,
    build_stable_config_validate_command,
)


def _capabilities(
    *,
    version: str = "2.1.0",
    commit: str | None = "candidate-commit",
    wheel_name: str = "confflow-2.1.0.whl",
    wheel_sha: str = "a" * 64,
    executable: str = "/opt/confflow/bin/confflow",
) -> ConfFlowCapabilities:
    build = {"commit": commit, "dirty": False}
    producer = {
        "package": "confflow",
        "version": version,
        "build": dict(build),
        "wheel": {"filename": wheel_name, "sha256": wheel_sha},
        "install_provenance": {"status": "verified", "prefix": "/opt/confflow"},
    }
    executable_payload = {
        "path": executable,
        "realpath": executable,
        "sha256": "b" * 64,
        "python": "/opt/confflow/bin/python",
        "size": 4,
        "mtime_ns": 5,
        "device": 6,
        "inode": 7,
    }
    raw = {
        "schema_version": 4,
        "version": version,
        "capabilities": {"workflow_state": True, "resume": True, "dag": True},
        "artifacts": EXPECTED_ARTIFACTS.__dict__,
        "commands": {name: True for name in REQUIRED_COMMANDS},
        "build": build,
        "producer": producer,
        "executable": executable_payload,
        "install_provenance": producer["install_provenance"],
    }
    return ConfFlowCapabilities(
        4,
        version,
        True,
        True,
        True,
        EXPECTED_ARTIFACTS,
        raw["commands"],
        build,
        producer,
        executable_payload,
        raw,
    )


def _contract_payload(capabilities: ConfFlowCapabilities, *, schema: dict | None = None) -> dict:
    schema = schema or json.loads(stable_2_0_0.schema_bytes())
    schema_bytes = (json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    return {
        "content_schema": "confflow.config.contract-response.v1",
        "contract": {
            "fixture_set": {
                "id": "confflow.config_contract.v2",
                "manifest_sha256": "2" * 64,
            },
            "id": "confflow.config.v2",
            "schema_id": schema["$id"],
            "schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
            "version": 2,
        },
        "producer": {
            "build": capabilities.producer["build"],
            "package": "confflow",
            "version": capabilities.version,
        },
        "workflow_schema": schema,
    }


def _contract(capabilities: ConfFlowCapabilities | None = None, server_id: str = "alpha"):
    capabilities = capabilities or _capabilities()
    return parse_contract_response(
        json.dumps(_contract_payload(capabilities)),
        server_id=server_id,
        configured_executable="/opt/confflow/bin/confflow",
        capabilities=capabilities,
    )


class _SSH:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def run(self, command, timeout=None, check=False, stdin_data=None):
        self.calls.append((command, timeout, check, stdin_data))
        return self.responses.pop(0)


def _response(code: int, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(exit_code=code, stdout=stdout, stderr=stderr)


def test_contract_parser_binds_canonical_schema_and_full_capability_provenance() -> None:
    capabilities = _capabilities()
    contract = _contract(capabilities)
    assert contract.resolved_executable == "/opt/confflow/bin/confflow"
    assert hashlib.sha256(contract.workflow_schema_bytes).hexdigest() == contract.schema_sha256
    provenance = json.loads(contract.producer_provenance)
    assert set(provenance) == {"build", "install_provenance", "producer"}
    assert provenance["producer"]["wheel"]["sha256"] == "a" * 64


def test_contract_parser_accepts_producer_legal_unknown_source_build_fields() -> None:
    capabilities = _capabilities(commit=None)
    capabilities.build["dirty"] = None
    capabilities.producer["build"]["dirty"] = None
    capabilities.raw_payload["build"]["dirty"] = None
    contract = _contract(capabilities)
    assert contract.contract_id == "confflow.config.v2"


@pytest.mark.parametrize("mutation", ["extra", "hash", "producer"])
def test_contract_parser_rejects_frozen_abi_hash_and_producer_mismatches(mutation: str) -> None:
    capabilities = _capabilities()
    payload = _contract_payload(capabilities)
    if mutation == "extra":
        payload["secret"] = "do-not-accept"
    elif mutation == "hash":
        payload["contract"]["schema_sha256"] = "0" * 64
    else:
        payload["producer"]["version"] = "9.9.9"
    with pytest.raises(ConfigurationContractError):
        parse_contract_response(
            json.dumps(payload),
            server_id="alpha",
            configured_executable="/opt/confflow/bin/confflow",
            capabilities=capabilities,
        )


def test_contract_parser_rejects_duplicate_keys_and_extra_json_output() -> None:
    capabilities = _capabilities()
    for raw in ('{"content_schema":"x","content_schema":"y"}', '{}\n{"secret":1}'):
        with pytest.raises(ConfigurationContractError, match="one valid JSON document"):
            parse_contract_response(
                raw,
                server_id="alpha",
                configured_executable="/opt/confflow/bin/confflow",
                capabilities=capabilities,
            )


def test_contract_parser_rejects_non_finite_json_extensions() -> None:
    capabilities = _capabilities()
    payload = _contract_payload(capabilities)
    payload["workflow_schema"]["x-nonfinite"] = float("nan")

    with pytest.raises(ConfigurationContractError, match="one valid JSON document"):
        parse_contract_response(
            json.dumps(payload),
            server_id="alpha",
            configured_executable="/opt/confflow/bin/confflow",
            capabilities=capabilities,
        )


def test_contract_parser_rejects_capability_executable_drift() -> None:
    capabilities = _capabilities()

    with pytest.raises(ConfigurationContractError, match="configured executable"):
        parse_contract_response(
            json.dumps(_contract_payload(capabilities)),
            server_id="alpha",
            configured_executable="/opt/other/bin/confflow",
            capabilities=capabilities,
        )


def test_validation_parser_accepts_frozen_diagnostic_and_rejects_leaking_message() -> None:
    contract = _contract()
    payload = {
        "content_schema": "confflow.config.validate-response.v1",
        "contract": {
            "id": contract.contract_id,
            "schema_sha256": contract.schema_sha256,
            "version": contract.contract_version,
        },
        "diagnostics": [
            {
                "code": "config.semantic_invalid",
                "message": "configuration violates a required semantic rule",
                "path": "$.steps[0].params.iprog",
            }
        ],
        "valid": False,
    }
    result = parse_validation_response(json.dumps(payload), contract=contract)
    assert result.valid is False and result.diagnostics[0].path == "$.steps[0].params.iprog"
    payload["diagnostics"][0]["message"] = "SECRET /home/user/input"
    with pytest.raises(ConfigurationContractError, match="privacy-safe"):
        parse_validation_response(json.dumps(payload), contract=contract)


def test_remote_validate_streams_exact_stdin_without_a_remote_file() -> None:
    capabilities = _capabilities()
    payload = _contract_payload(capabilities)
    contract_stdout = json.dumps(payload)
    contract = _contract(capabilities)
    validation = {
        "content_schema": "confflow.config.validate-response.v1",
        "contract": {
            "id": contract.contract_id,
            "schema_sha256": contract.schema_sha256,
            "version": contract.contract_version,
        },
        "diagnostics": [],
        "valid": True,
    }
    ssh = _SSH([_response(0, contract_stdout), _response(0, json.dumps(validation))])
    client = SSHConfigurationContractClient()
    resolved = client.resolve(
        server_id="alpha",
        configured_executable="/opt/confflow/bin/confflow",
        env_init_scripts=("/opt/site env.sh",),
        ssh=ssh,
        capabilities=capabilities,
    )
    raw = b"global: {}\nsteps: []\n"
    assert client.validate(resolved, raw, env_init_scripts=(), ssh=ssh).valid is True
    assert ssh.calls[1][3] == raw
    assert "mktemp" not in ssh.calls[1][0]
    assert "config validate --json --stdin" in ssh.calls[1][0]
    assert "[ -f '/opt/site env.sh' ]" in ssh.calls[0][0]


def test_commands_quote_selected_executable_and_env_script() -> None:
    assert (
        build_config_contract_command("/opt/space dir/confflow") == "'/opt/space dir/confflow' config contract --json"
    )
    assert build_config_validate_command("x'; touch /tmp/nope; '").startswith("'x'\"'\"'; touch")


def test_verified_cache_isolated_by_server_and_full_provenance() -> None:
    first = _capabilities()
    second = _capabilities(wheel_sha="c" * 64)
    stdout1 = json.dumps(_contract_payload(first))
    stdout2 = json.dumps(_contract_payload(second))
    ssh = _SSH([_response(0, stdout1), _response(0, stdout1), _response(0, stdout1), _response(0, stdout2)])
    client = SSHConfigurationContractClient()
    kwargs = dict(
        configured_executable="/opt/confflow/bin/confflow",
        env_init_scripts=(),
        ssh=ssh,
    )
    one = client.resolve(server_id="alpha", capabilities=first, **kwargs)
    assert client.resolve(server_id="alpha", capabilities=first, **kwargs) is one
    other_server = client.resolve(server_id="beta", capabilities=first, **kwargs)
    other_wheel = client.resolve(server_id="alpha", capabilities=second, **kwargs)
    assert other_server is not one and other_wheel is not one
    assert len(client._verified_cache) == 3


def test_cached_document_never_masks_a_changed_or_invalid_remote_schema() -> None:
    capabilities = _capabilities()
    good = _contract_payload(capabilities)
    bad = _contract_payload(capabilities)
    bad["contract"]["schema_sha256"] = "0" * 64
    ssh = _SSH([_response(0, json.dumps(good)), _response(0, json.dumps(bad))])
    client = SSHConfigurationContractClient()
    kwargs = dict(
        server_id="alpha",
        configured_executable="/opt/confflow/bin/confflow",
        env_init_scripts=(),
        ssh=ssh,
        capabilities=capabilities,
    )
    client.resolve(**kwargs)
    with pytest.raises(ConfigurationContractError, match="schema bytes"):
        client.resolve(**kwargs)


def test_only_exact_approved_stable_identity_can_use_checked_in_fallback() -> None:
    stable = _capabilities(
        version=REFERENCE_VERSION,
        commit=REFERENCE_BUILD_COMMIT,
        wheel_name=REFERENCE_WHEEL_FILENAME,
        wheel_sha=REFERENCE_WHEEL_SHA256,
    )
    unsupported = _response(2, stderr="invalid choice: 'config'")
    client = SSHConfigurationContractClient()
    fallback = client.resolve(
        server_id="stable",
        configured_executable="/opt/confflow/bin/confflow",
        env_init_scripts=(),
        ssh=_SSH([unsupported]),
        capabilities=stable,
    )
    assert fallback.source == "stable-fallback"
    assert fallback.workflow_schema_bytes == stable_2_0_0.schema_bytes()
    response = _response(
        0,
        stdout=json.dumps(
            {
                "content_schema": "confflow.config.validate-response.v1",
                "contract": {
                    "id": fallback.contract_id,
                    "schema_sha256": fallback.schema_sha256,
                    "version": fallback.contract_version,
                },
                "diagnostics": [],
                "valid": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    remote = _SSH([response])
    result = client.validate(fallback, b"{}", env_init_scripts=(), ssh=remote)
    assert result.valid is True and result.source == "stable-fallback"
    command, _timeout, _check, stdin_data = remote.calls[0]
    assert "config validate" not in command and " -c " in command
    assert stdin_data == b"{}"

    candidate = _capabilities()
    with pytest.raises(ConfigurationContractError, match="unsupported"):
        client.resolve(
            server_id="candidate",
            configured_executable="/opt/confflow/bin/confflow",
            env_init_scripts=(),
            ssh=_SSH([unsupported]),
            capabilities=candidate,
        )
    bad_stable = replace(stable, build={"commit": "wrong", "dirty": False})
    bad_stable.raw_payload["build"] = bad_stable.build
    with pytest.raises(ConfigurationContractError, match="exact approved"):
        client.resolve(
            server_id="bad-stable",
            configured_executable="/opt/confflow/bin/confflow",
            env_init_scripts=(),
            ssh=_SSH([unsupported]),
            capabilities=bad_stable,
        )


def test_ssh_optional_stdin_parameter_preserves_existing_call_shape() -> None:
    parameter = inspect.signature(SSHClientWrapper.run).parameters["stdin_data"]
    assert parameter.default is None


def test_stable_validator_wrapper_is_compilable_and_cleanup_hardened() -> None:
    command = build_stable_config_validate_command(_contract())
    python_executable, flag, program = shlex.split(command)
    encoded = re.search(r"b64decode\('([^']+)'\)", program)

    assert python_executable == "/opt/confflow/bin/python" and flag == "-c"
    assert encoded is not None
    source = base64.b64decode(encoded.group(1)).decode("utf-8")
    compile(source, "<stable-validator>", "exec")
    assert "tempfile.mkstemp" in source and "os.fchmod" in source and "os.unlink" in source
    assert "sys.stdin.buffer.read" in source and "config.semantic_invalid" in source


def test_ssh_wrapper_writes_and_half_closes_optional_stdin() -> None:
    stdin = MagicMock()
    channel = MagicMock()
    channel.exit_status_ready.return_value = True
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.recv_exit_status.return_value = 0
    stdout = MagicMock(channel=channel)
    client = MagicMock()
    client.exec_command.return_value = (stdin, stdout, MagicMock())
    wrapper = object.__new__(SSHClientWrapper)
    wrapper._client = client
    wrapper._timeout = 5
    wrapper._server = SimpleNamespace(host="example")

    result = wrapper.run("consumer", stdin_data=b"exact bytes\x00")

    assert result.exit_code == 0
    stdin.write.assert_called_once_with(b"exact bytes\x00")
    stdin.flush.assert_called_once_with()
    stdin.channel.shutdown_write.assert_called_once_with()


def test_run_coordinator_facades_reuse_selected_server_and_session(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    contract = _contract(capabilities)
    adapter = MagicMock()
    adapter.resolve.return_value = contract
    adapter.validate.return_value = SimpleNamespace(valid=True)
    ssh = MagicMock()
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
        env_init_scripts=["/opt/site.sh"],
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda selected_ssh, **kwargs: capabilities,
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: ssh,
        sftp_factory=MagicMock(),
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )

    assert coordinator.resolve_configuration_contract("alpha") is contract
    coordinator.validate_configuration(contract, b"global: {}\nsteps: []\n")

    assert adapter.resolve.call_args.kwargs["ssh"] is ssh
    assert adapter.resolve.call_args.kwargs["configured_executable"] == "/opt/confflow/bin/confflow"
    assert adapter.validate.call_args.kwargs["ssh"] is ssh
    assert adapter.validate.call_args.args[1] == b"global: {}\nsteps: []\n"


def test_run_coordinator_validation_rejects_contract_identity_change(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    original = _contract(capabilities)
    changed = replace(original, schema_sha256="0" * 64)
    adapter = MagicMock()
    adapter.resolve.return_value = changed
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda selected_ssh, **kwargs: capabilities,
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        connect_clients=False,
        configuration_contract_client=adapter,
    )

    with pytest.raises(ValueError, match="changed before validation"):
        coordinator.validate_configuration(original, b"{}")
    adapter.validate.assert_not_called()


def test_admit_configuration_returns_hashed_result_without_sftp_or_storage(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    contract = _contract(capabilities)
    adapter = MagicMock()
    adapter.resolve.return_value = contract
    adapter.validate.return_value = ConfigurationValidationResult(
        content_schema="confflow.config.validate-response.v1",
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        schema_sha256=contract.schema_sha256,
        valid=True,
        diagnostics=(),
        source="remote",
    )
    server = ServerConfig(
        server_id="alpha", host="example", username="user", confflow_executable="/opt/confflow/bin/confflow"
    )
    probe_calls: list[bool] = []
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda ssh, **kw: (probe_calls.append(kw["require_dag"]) or capabilities),
    )
    sftp = MagicMock()
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda _: server,
        ssh_factory=lambda _: MagicMock(),
        sftp_factory=lambda _: sftp,
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )
    payload = b"global: {}\nsteps: []\n"
    admission = coordinator.admit_configuration("alpha", payload, require_dag=True)
    assert admission.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert admission.contract is contract and probe_calls == [True, True]
    sftp.assert_not_called()


def test_admit_configuration_invalid_is_privacy_safe(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    contract = _contract(capabilities)
    adapter = MagicMock()
    adapter.resolve.return_value = contract
    adapter.validate.return_value = ConfigurationValidationResult(
        "confflow.config.validate-response.v1",
        contract.contract_id,
        contract.contract_version,
        contract.schema_sha256,
        False,
        (),
        "remote",
    )
    server = ServerConfig(
        server_id="alpha", host="example", username="user", confflow_executable="/opt/confflow/bin/confflow"
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities", lambda ssh, **kw: capabilities
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda _: server,
        ssh_factory=lambda _: MagicMock(),
        sftp_factory=MagicMock(),
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )
    secret = b"secret-not-to-echo"
    with pytest.raises(ConfigurationAdmissionError) as raised:
        coordinator.admit_configuration("alpha", secret)
    assert raised.value.code == "configuration_invalid" and b"secret" not in str(raised.value).encode()


def test_admit_configuration_keeps_only_safe_invalid_path(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    contract = _contract(capabilities)
    adapter = MagicMock()
    adapter.resolve.return_value = contract
    adapter.validate.return_value = ConfigurationValidationResult(
        "confflow.config.validate-response.v1",
        contract.contract_id,
        contract.contract_version,
        contract.schema_sha256,
        False,
        (ConfigurationDiagnostic("internal.secret", "secret-not-to-echo", "$.steps[0].type"),),
        "remote",
    )
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda ssh, **kwargs: capabilities,
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )

    with pytest.raises(ConfigurationAdmissionError) as raised:
        coordinator.admit_configuration("alpha", b"secret-not-to-echo")

    assert raised.value.code == "configuration_invalid"
    assert raised.value.path == "$.steps[0].type"
    assert "secret-not-to-echo" not in str(raised.value)


def test_admit_configuration_rejects_identity_drift_before_validation(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    original = _contract(capabilities)
    changed = replace(original, schema_sha256="0" * 64)
    adapter = MagicMock()
    adapter.resolve.side_effect = [original, changed]
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda ssh, **kwargs: capabilities,
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )

    with pytest.raises(ConfigurationAdmissionError) as raised:
        coordinator.admit_configuration("alpha", b"global: {}\nsteps: []\n")

    assert raised.value.code == "configuration_admission_unavailable"
    adapter.validate.assert_not_called()


def test_admit_configuration_hides_transport_failure(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    adapter = MagicMock()
    adapter.resolve.side_effect = RuntimeError("transport secret-not-to-echo")
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda ssh, **kwargs: capabilities,
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )

    with pytest.raises(ConfigurationAdmissionError) as raised:
        coordinator.admit_configuration("alpha", b"secret-not-to-echo")

    assert raised.value.code == "configuration_admission_unavailable"
    assert "secret-not-to-echo" not in str(raised.value)
    adapter.validate.assert_not_called()


def test_admit_configuration_accepts_stable_fallback_only_after_remote_validation(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    fallback = replace(_contract(capabilities), source="stable-fallback")
    adapter = MagicMock()
    adapter.resolve.return_value = fallback
    adapter.validate.return_value = ConfigurationValidationResult(
        "confflow.config.validate-response.v1",
        fallback.contract_id,
        fallback.contract_version,
        fallback.schema_sha256,
        True,
        (),
        "stable-fallback",
    )
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda ssh, **kwargs: capabilities,
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )

    admission = coordinator.admit_configuration("alpha", b"global: {}\nsteps: []\n")

    assert admission.contract is fallback
    assert adapter.resolve.call_count == 2
    adapter.validate.assert_called_once()


def test_admission_converts_every_verified_contract_field_to_immutable_binding() -> None:
    contract = _contract()
    result = ConfigurationValidationResult(
        "confflow.config.validate-response.v1",
        contract.contract_id,
        contract.contract_version,
        contract.schema_sha256,
        True,
        (),
        "remote",
    )
    admission = ConfigurationAdmission(
        contract=contract,
        content_sha256="a" * 64,
        validated_at="2026-08-20T12:00:00+00:00",
        validation_result=result,
    )

    binding = admission.to_configuration_binding()

    assert binding.content_sha256 == admission.content_sha256
    assert binding.content_schema == contract.content_schema
    assert binding.contract_id == contract.contract_id
    assert binding.contract_version == str(contract.contract_version)
    assert binding.schema_id == contract.schema_id
    assert binding.schema_sha256 == contract.schema_sha256
    assert binding.fixture_set == contract.fixture_set_id
    assert binding.fixture_sha256 == contract.fixture_manifest_sha256
    assert binding.source == contract.source
    assert binding.configured_executable == contract.configured_executable
    assert binding.resolved_executable == contract.resolved_executable
    assert binding.canonical_executable_identity_json == json.dumps(
        json.loads(contract.executable_identity), sort_keys=True, separators=(",", ":")
    )
    assert binding.canonical_producer_provenance_json == json.dumps(
        json.loads(contract.producer_provenance), sort_keys=True, separators=(",", ":")
    )
    assert binding.validated_at == admission.validated_at


@pytest.mark.parametrize("identity", [b"\xff", b'{"z":1,"a":2}'])
def test_admission_binding_conversion_rejects_noncanonical_identity(identity: bytes) -> None:
    contract = replace(_contract(), executable_identity=identity)
    admission = ConfigurationAdmission(
        contract=contract,
        content_sha256="a" * 64,
        validated_at="2026-08-20T12:00:00+00:00",
    )

    with pytest.raises(ValueError):
        admission.to_configuration_binding()


def test_create_admitted_run_persists_binding_before_any_submission(tmp_path) -> None:
    contract = _contract()
    admission = ConfigurationAdmission(
        contract=contract,
        content_sha256="a" * 64,
        validated_at="2026-08-20T12:00:00+00:00",
    )
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        connect_clients=False,
    )
    spec = RunSpec(
        server_id="alpha",
        remote_dir="/remote/project",
        command_template="confflow --config workflow.yaml",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/a.xyz")],
        workflow_kind=WorkflowKind.confflow,
    )

    outcome = coordinator.create_admitted_run(spec, admission, run_id="admitted")

    assert not outcome.errors
    assert outcome.records[0].run_id == "admitted"
    assert service.load_configuration_binding("admitted") == admission.to_configuration_binding()
    assert service.load_tasks("admitted")[0].confflow_executable == "/opt/confflow/bin/confflow"


def test_create_admitted_run_rejects_task_executable_that_differs_from_admission(tmp_path) -> None:
    contract = _contract()
    admission = ConfigurationAdmission(
        contract=contract,
        content_sha256="a" * 64,
        validated_at="2026-08-20T12:00:00+00:00",
    )
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        connect_clients=False,
    )
    outcome = coordinator.create_admitted_run(
        RunSpec(
            server_id="alpha",
            remote_dir="/remote/project",
            command_template="confflow --config workflow.yaml",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/project/a.xyz")],
            workflow_kind=WorkflowKind.confflow,
            confflow_executable="/different/confflow",
        ),
        admission,
        run_id="mismatched-executable",
    )

    assert outcome.errors[0].code == "configuration_identity_mismatch"


def test_workflow_submit_without_binding_is_rejected_before_remote_access(tmp_path) -> None:
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda server_id: server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
        connect_clients=False,
    )
    record = service.create_run(
        RunSpec(
            server_id="alpha",
            remote_dir="/remote/project",
            command_template="confflow --config workflow.yaml",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/project/a.xyz")],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id="legacy-workflow",
    )

    outcome = coordinator.submit(record.run_id)

    assert outcome.errors[0].code == "configuration_admission_required"


def test_ssh_control_submit_cannot_bypass_missing_configuration_binding(tmp_path) -> None:
    server = ServerConfig(server_id="alpha", host="example", username="user")
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda server_id: server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
        connect_clients=False,
    )
    service.create_run(
        RunSpec(
            server_id="alpha",
            remote_dir="/remote/project",
            command_template="confflow --config workflow.yaml",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/project/a.xyz")],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id="legacy-control-workflow",
    )

    handle, outcome = SSHConfFlowClient(coordinator, "alpha").submit_with_outcome(
        SubmitRequest("legacy-control-workflow")
    )

    assert handle is None
    assert outcome.errors == ["configuration admission is required before workflow submission"]


def test_ssh_control_submit_rejects_binding_identity_drift_before_probe(tmp_path, monkeypatch) -> None:
    capabilities = _capabilities()
    original = _contract(capabilities)
    changed = replace(original, schema_sha256="0" * 64)
    admission = ConfigurationAdmission(
        contract=original,
        content_sha256="a" * 64,
        validated_at="2026-08-20T12:00:00+00:00",
    )
    adapter = MagicMock()
    adapter.resolve.return_value = changed
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    monkeypatch.setattr(
        "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
        lambda ssh, **kwargs: capabilities,
    )
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda server_id: server,
        ssh_factory=lambda selected_server: MagicMock(),
        sftp_factory=MagicMock(),
        close_clients=False,
        connect_clients=False,
        configuration_contract_client=adapter,
    )
    spec = RunSpec(
        server_id="alpha",
        remote_dir="/remote/project",
        command_template="confflow --config workflow.yaml",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/a.xyz")],
        workflow_kind=WorkflowKind.confflow,
    )
    assert not coordinator.create_admitted_run(spec, admission, run_id="drifted").errors
    client = SSHConfFlowClient(coordinator, "alpha")
    client.probe = MagicMock(side_effect=AssertionError("control probe must not run after identity drift"))

    handle, outcome = client.submit_with_outcome(SubmitRequest("drifted"))

    assert handle is None
    assert outcome.errors == ["configuration admission failed [configuration_identity_mismatch]"]
    client.probe.assert_not_called()
