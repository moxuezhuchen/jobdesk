from __future__ import annotations

import base64
import hashlib
import inspect
import json
import re
import shlex
from copy import deepcopy
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
from jobdesk_app.core.confflow_contract import EXPECTED_ARTIFACTS, REQUIRED_COMMANDS
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.remote.confflow_config_contract import (
    ConfigurationContractError,
    parse_contract_response,
    parse_validation_response,
)
from jobdesk_app.remote.ssh import SSHClientWrapper
from jobdesk_app.resources.config_contracts import stable_2_0_0
from jobdesk_app.services.run_coordinator import OperationFailure, RunCoordinator
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
    schema_bytes = json.dumps(
        schema,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {
        "schema": "confflow.configuration-contract.v1",
        "workflow_schema_version": "confflow.workflow.v2",
        "workflow_schema_sha256": hashlib.sha256(schema_bytes).hexdigest(),
        "workflow_schema": schema,
        "producer": {
            "package": "confflow",
            "version": capabilities.version,
            "commit": capabilities.producer["build"]["commit"],
            "dirty": capabilities.producer["build"]["dirty"],
        },
        "validation_response_schema": "confflow.configuration-validation.v1",
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
    assert contract.contract_id == "confflow.configuration-contract.v1"


@pytest.mark.parametrize("mutation", ["extra", "hash", "producer"])
def test_contract_parser_rejects_frozen_abi_hash_and_producer_mismatches(mutation: str) -> None:
    capabilities = _capabilities()
    payload = _contract_payload(capabilities)
    if mutation == "extra":
        payload["secret"] = "do-not-accept"
    elif mutation == "hash":
        payload["workflow_schema_sha256"] = "0" * 64
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
    for raw in ('{"schema":"x","schema":"y"}', '{}\n{"secret":1}'):
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


def test_validation_parser_accepts_canonical_issue_and_rejects_unknown_issue_fields() -> None:
    contract = _contract()
    payload = {
        "schema": "confflow.configuration-validation.v1",
        "workflow_schema_sha256": contract.schema_sha256,
        "issues": [{"message": "steps must be a list", "path": "steps"}],
        "valid": False,
    }
    result = parse_validation_response(json.dumps(payload), contract=contract)
    assert result.valid is False and result.diagnostics[0].path == "steps"
    payload["issues"][0]["code"] = "undeclared"
    with pytest.raises(ConfigurationContractError, match="fields"):
        parse_validation_response(json.dumps(payload), contract=contract)


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("extra", "fields"),
        ("schema", "unsupported"),
        ("hash", "binding"),
        ("valid-type", "boolean"),
        ("issues-type", "array"),
        ("issue-path", "path"),
        ("issue-message", "message"),
        ("valid-with-issues", "disagree"),
        ("invalid-without-issues", "disagree"),
    ],
)
def test_validation_parser_rejects_canonical_abi_mutations(mutation: str, match: str) -> None:
    contract = _contract()
    payload: dict[str, object] = {
        "schema": "confflow.configuration-validation.v1",
        "workflow_schema_sha256": contract.schema_sha256,
        "issues": [],
        "valid": True,
    }
    if mutation == "extra":
        payload["unknown"] = True
    elif mutation == "schema":
        payload["schema"] = "confflow.configuration-validation.v2"
    elif mutation == "hash":
        payload["workflow_schema_sha256"] = "0" * 64
    elif mutation == "valid-type":
        payload["valid"] = 1
    elif mutation == "issues-type":
        payload["issues"] = {}
    elif mutation == "issue-path":
        payload.update(valid=False, issues=[{"path": 1, "message": "bad"}])
    elif mutation == "issue-message":
        payload.update(valid=False, issues=[{"path": "steps", "message": "bad\nleak"}])
    elif mutation == "valid-with-issues":
        payload["issues"] = [{"path": "steps", "message": "bad"}]
    else:
        payload["valid"] = False
    with pytest.raises(ConfigurationContractError, match=match):
        parse_validation_response(json.dumps(payload), contract=contract)


@pytest.mark.parametrize("contract_exit,valid", [(0, False), (1, True)])
def test_client_rejects_validation_exit_status_disagreement(contract_exit: int, valid: bool) -> None:
    contract = _contract()
    payload = {
        "schema": "confflow.configuration-validation.v1",
        "workflow_schema_sha256": contract.schema_sha256,
        "issues": [] if valid else [{"path": "steps", "message": "bad"}],
        "valid": valid,
    }
    client = SSHConfigurationContractClient()
    with pytest.raises(ConfigurationContractError, match="exit status"):
        client.validate(
            contract,
            b"{}",
            env_init_scripts=(),
            ssh=_SSH([_response(contract_exit, json.dumps(payload))]),
        )


@pytest.mark.parametrize(
    "mutation", ["producer-extra", "package", "commit", "dirty", "schema-version", "validation-schema"]
)
def test_contract_parser_rejects_exact_producer_and_schema_mutations(mutation: str) -> None:
    capabilities = _capabilities()
    payload = deepcopy(_contract_payload(capabilities))
    producer = payload["producer"]
    assert isinstance(producer, dict)
    if mutation == "producer-extra":
        producer["build"] = {}
    elif mutation == "package":
        producer["package"] = "other"
    elif mutation == "commit":
        producer["commit"] = "wrong"
    elif mutation == "dirty":
        producer["dirty"] = True
    elif mutation == "schema-version":
        payload["workflow_schema_version"] = "confflow.workflow.v3"
    else:
        payload["validation_response_schema"] = "confflow.configuration-validation.v2"
    with pytest.raises(ConfigurationContractError):
        parse_contract_response(
            json.dumps(payload),
            server_id="alpha",
            configured_executable="/opt/confflow/bin/confflow",
            capabilities=capabilities,
        )


def test_remote_validate_transcodes_yaml_to_producer_canonical_json_without_a_remote_file() -> None:
    capabilities = _capabilities()
    payload = _contract_payload(capabilities)
    contract_stdout = json.dumps(payload)
    contract = _contract(capabilities)
    validation = {
        "schema": "confflow.configuration-validation.v1",
        "workflow_schema_sha256": contract.schema_sha256,
        "issues": [],
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
    assert ssh.calls[1][3] == b'{"global":{},"steps":[]}'
    assert "mktemp" not in ssh.calls[1][0]
    assert "config validate --json --stdin" in ssh.calls[1][0]
    assert "[ -f '/opt/site env.sh' ]" in ssh.calls[0][0]


def test_remote_validate_preserves_yaml_semantics_and_unicode_in_canonical_json() -> None:
    contract = _contract()
    validation = {
        "schema": "confflow.configuration-validation.v1",
        "workflow_schema_sha256": contract.schema_sha256,
        "issues": [],
        "valid": True,
    }
    ssh = _SSH([_response(0, json.dumps(validation))])

    result = SSHConfigurationContractClient().validate(
        contract,
        "steps: []\nglobal:\n  label: 甲烷\n  enabled: true\n".encode(),
        env_init_scripts=(),
        ssh=ssh,
    )

    assert result.valid is True
    assert ssh.calls[0][3] == '{"global":{"enabled":true,"label":"甲烷"},"steps":[]}'.encode()


@pytest.mark.parametrize(
    "raw",
    (
        b"global: [unterminated\n",
        "global:\n  when: 2026-08-28\nsteps: []\n".encode(),
        b"global:\n  threshold: .nan\nsteps: []\n",
        b"\xff",
    ),
)
def test_remote_validate_rejects_yaml_that_cannot_cross_the_json_abi(raw: bytes) -> None:
    ssh = _SSH([])

    with pytest.raises(ConfigurationContractError, match="cannot be represented by the producer JSON ABI"):
        SSHConfigurationContractClient().validate(
            _contract(),
            raw,
            env_init_scripts=(),
            ssh=ssh,
        )

    assert ssh.calls == []


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
    bad["workflow_schema_sha256"] = "0" * 64
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
        version="2.0.0",
        commit="69819350d340a6aeccf95aa175edfd1c3f63404b",
        wheel_name="confflow-2.0.0-py3-none-any.whl",
        wheel_sha="04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f",
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
    assert json.loads(fallback.workflow_schema_bytes) == json.loads(stable_2_0_0.schema_bytes())
    response = _response(
        0,
        stdout=json.dumps(
            {
                "schema": "confflow.configuration-validation.v1",
                "workflow_schema_sha256": fallback.schema_sha256,
                "issues": [],
                "valid": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    remote = _SSH([response])
    stable_yaml = b"global: {}\nsteps: []\n"
    result = client.validate(fallback, stable_yaml, env_init_scripts=(), ssh=remote)
    assert result.valid is True and result.source == "stable-fallback"
    command, _timeout, _check, stdin_data = remote.calls[0]
    assert "config validate" not in command and " -c " in command
    assert stdin_data == stable_yaml

    candidate = _capabilities(
        version="2.1.6",
        commit="45bfac11f721b2152eeff5ee26e50463fcc6f657",
        wheel_name="confflow-2.1.6-py3-none-any.whl",
        wheel_sha="d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548",
    )
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
    assert "sys.stdin.buffer.read" in source and "configuration violates a required semantic rule" in source


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
        content_schema="confflow.configuration-validation.v1",
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
        "confflow.configuration-validation.v1",
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
        "confflow.configuration-validation.v1",
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
        "confflow.configuration-validation.v1",
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
        "confflow.configuration-validation.v1",
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


@pytest.mark.parametrize(
    ("failure_stage", "cause_code", "retryable"),
    [
        ("connect", "transport_error", True),
        ("capability_probe", "timeout", True),
        ("contract_resolve", "producer_unavailable", True),
        ("identity_compare", "identity_mismatch", False),
    ],
)
def test_submit_binding_reverification_reports_safe_stage_without_dispatch(
    tmp_path, monkeypatch, failure_stage, cause_code, retryable
) -> None:
    capabilities = _capabilities()
    original = _contract(capabilities)
    admission = ConfigurationAdmission(
        contract=original,
        content_sha256="a" * 64,
        validated_at="2026-08-20T12:00:00+00:00",
    )
    adapter = MagicMock()
    adapter.resolve.return_value = original
    server = ServerConfig(
        server_id="alpha",
        host="example",
        username="user",
        confflow_executable="/opt/confflow/bin/confflow",
    )
    ssh_factory = MagicMock(return_value=MagicMock())
    if failure_stage == "connect":
        ssh_factory.side_effect = OSError("private transport detail")
    elif failure_stage == "capability_probe":
        monkeypatch.setattr(
            "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
            MagicMock(side_effect=TimeoutError("private capability detail")),
        )
    else:
        monkeypatch.setattr(
            "jobdesk_app.services.run_coordinator.probe_confflow_capabilities",
            MagicMock(return_value=capabilities),
        )
    if failure_stage == "contract_resolve":
        adapter.resolve.side_effect = RuntimeError("private producer stderr and YAML")
    elif failure_stage == "identity_compare":
        adapter.resolve.return_value = replace(original, schema_sha256="0" * 64)

    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda server_id: server,
        ssh_factory=ssh_factory,
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
    assert not coordinator.create_admitted_run(spec, admission, run_id=f"blocked-{failure_stage}").errors
    scheduler = MagicMock(side_effect=AssertionError("scheduler must not be selected"))
    dispatch = MagicMock(side_effect=AssertionError("submit service must not run"))
    monkeypatch.setattr("jobdesk_app.services.run_coordinator.scheduler_from_server", scheduler)
    monkeypatch.setattr(service, "submit_run", dispatch)

    outcome = coordinator.submit(f"blocked-{failure_stage}")

    failure = outcome.structured_failures[0]
    assert failure.stage == failure_stage
    assert failure.code == (
        "configuration_identity_mismatch"
        if failure_stage == "identity_compare"
        else "configuration_admission_unavailable"
    )
    assert failure.cause_code == cause_code
    assert failure.retryable is retryable
    assert str(failure) == f"configuration admission failed [{failure.code}]"
    assert "private" not in str(failure)
    scheduler.assert_not_called()
    dispatch.assert_not_called()


def test_ssh_control_submit_preserves_structured_admission_failure_and_legacy_text(
    tmp_path, monkeypatch
) -> None:
    server = ServerConfig(server_id="alpha", host="example", username="user")
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
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
        run_id="structured-failure",
    )
    binding = MagicMock()
    monkeypatch.setattr(service, "load_configuration_binding", MagicMock(return_value=binding))
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda server_id: server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
        connect_clients=False,
    )
    error = ConfigurationAdmissionError(
        "configuration_admission_unavailable",
        stage="contract_resolve",
        cause_code="producer_unavailable",
        retryable=True,
    )
    monkeypatch.setattr(coordinator, "verify_configuration_binding", MagicMock(side_effect=error))
    client = SSHConfFlowClient(coordinator, "alpha")
    monkeypatch.setattr(client, "_submit_control", MagicMock())

    handle, result = client.submit_with_outcome(SubmitRequest("structured-failure"))

    assert handle is None
    assert result.errors == ["configuration admission failed [configuration_admission_unavailable]"]
    assert result.error_messages == result.errors
    assert len(result.structured_failures) == 1
    failure = result.structured_failures[0]
    assert isinstance(failure, OperationFailure)
    assert failure.as_dict() == {
        "stage": "contract_resolve",
        "code": "configuration_admission_unavailable",
        "message": "configuration admission failed [configuration_admission_unavailable]",
        "retryable": True,
        "task_id": None,
        "cause_code": "producer_unavailable",
    }
    client._submit_control.assert_not_called()
