"""SSH adapter for the producer-owned ConfFlow configuration contract."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
from collections.abc import Iterable

import yaml

from jobdesk_app.application.configuration_contract import (
    ConfigurationValidationResult,
    VerifiedConfigurationContract,
)
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities
from jobdesk_app.remote.confflow_config_contract import (
    CONTRACT_RESPONSE_SCHEMA,
    VALIDATE_RESPONSE_SCHEMA,
    WORKFLOW_SCHEMA_VERSION,
    ConfigurationContractError,
    parse_contract_response,
    parse_validation_response,
    producer_canonical_json_bytes,
)
from jobdesk_app.remote.confflow_probe import (
    build_confflow_preflight_shell,
    quote_confflow_executable,
)
from jobdesk_app.resources.config_contracts import stable_2_0_0

from .protocols import SSHClient


def build_config_contract_command(executable: str | None = None) -> str:
    return f"{quote_confflow_executable(executable)} config contract --json"


def build_config_validate_command(executable: str | None = None) -> str:
    return f"{quote_confflow_executable(executable)} config validate --json --stdin"


def _producer_validation_input(configuration: bytes) -> bytes:
    """Translate JobDesk's YAML document to ConfFlow's canonical JSON wire ABI."""

    try:
        document = yaml.safe_load(configuration.decode("utf-8"))
        return producer_canonical_json_bytes(document)
    except (UnicodeDecodeError, yaml.YAMLError, ConfigurationContractError) as exc:
        raise ConfigurationContractError("configuration cannot be represented by the producer JSON ABI") from exc


_STABLE_VALIDATOR_SCRIPT = r"""
import json
import os
import sys
import tempfile
import yaml

from confflow.config.models import load_workflow_model
from confflow.shared.config_validation import validate_yaml_config

SCHEMA_SHA256 = __SCHEMA_SHA256__

def _document(valid, issues):
    return {
        "schema": "confflow.configuration-validation.v1",
        "workflow_schema_sha256": SCHEMA_SHA256,
        "valid": valid,
        "issues": issues,
    }

def _issue(message, path=""):
    return {"message": message, "path": path}

path = None
issues = []
exit_code = 0
try:
    content = sys.stdin.buffer.read()
    descriptor, path = tempfile.mkstemp(prefix=".jobdesk-config-", suffix=".yaml")
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
    try:
        raw = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError):
        issues = [_issue("configuration input is not valid YAML")]
    else:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            issues = [_issue("configuration does not satisfy the workflow schema")]
        else:
            try:
                load_workflow_model(path)
            except Exception:
                issues = [_issue("configuration is invalid")]
            else:
                try:
                    errors = validate_yaml_config(raw)
                except Exception:
                    issues = [_issue("configuration command failed internally")]
                    exit_code = 2
                else:
                    issues = [_issue("configuration violates a required semantic rule") for _ in errors]
    if issues and exit_code == 0:
        exit_code = 1
except Exception:
    issues = [_issue("configuration command failed internally")]
    exit_code = 2
finally:
    if path is not None:
        try:
            os.unlink(path)
        except Exception:
            issues = [_issue("configuration command failed internally")]
            exit_code = 2

sys.stdout.write(json.dumps(_document(not issues, issues), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
raise SystemExit(exit_code)
"""

_STABLE_FALLBACK_VERSION = "2.0.0"
_STABLE_FALLBACK_COMMIT = "69819350d340a6aeccf95aa175edfd1c3f63404b"
_STABLE_FALLBACK_WHEEL = "confflow-2.0.0-py3-none-any.whl"
_STABLE_FALLBACK_WHEEL_SHA256 = "04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f"


def build_stable_config_validate_command(contract: VerifiedConfigurationContract) -> str:
    """Build the exact-2.0 remote validator command without a shell temp file."""

    try:
        identity = json.loads(contract.executable_identity)
        python_executable = identity["python"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationContractError("stable producer identity has no validated Python executable") from exc
    if not isinstance(python_executable, str) or not python_executable:
        raise ConfigurationContractError("stable producer identity has no validated Python executable")
    source = _STABLE_VALIDATOR_SCRIPT.replace("__SCHEMA_SHA256__", repr(contract.schema_sha256))
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    program = f"import base64;exec(base64.b64decode({encoded!r}))"
    return f"{quote_confflow_executable(python_executable)} -c {shlex.quote(program)}"


def _shell(command: str, env_init_scripts: Iterable[str]) -> str:
    return build_confflow_preflight_shell(command, env_init_scripts)


def _unsupported_command(exit_code: int, stdout: str, stderr: str) -> bool:
    if exit_code not in {2, 126, 127}:
        return False
    detail = f"{stdout}\n{stderr}".lower()
    return any(
        marker in detail
        for marker in (
            "invalid choice",
            "no such command",
            "not found",
            "unrecognized arguments",
            "unknown command",
        )
    )


class SSHConfigurationContractClient:
    """Per-coordinator client; its verified cache is never process-global."""

    def __init__(self) -> None:
        self._verified_cache: dict[tuple[object, ...], VerifiedConfigurationContract] = {}

    def resolve(
        self,
        *,
        server_id: str,
        configured_executable: str,
        env_init_scripts: tuple[str, ...],
        ssh: SSHClient,
        capabilities: ConfFlowCapabilities,
    ) -> VerifiedConfigurationContract:
        command = _shell(build_config_contract_command(configured_executable), env_init_scripts)
        try:
            response = ssh.run(command, timeout=30)
        except Exception as exc:
            raise ConfigurationContractError("configuration contract probe failed") from exc
        if response.exit_code == 0:
            contract = parse_contract_response(
                response.stdout,
                server_id=server_id,
                configured_executable=configured_executable,
                capabilities=capabilities,
            )
        elif _unsupported_command(response.exit_code, response.stdout, response.stderr):
            contract = self._stable_fallback(
                server_id=server_id,
                configured_executable=configured_executable,
                capabilities=capabilities,
            )
        else:
            raise ConfigurationContractError("configuration contract command failed")
        cached = self._verified_cache.get(contract.cache_key)
        if cached is not None:
            return cached
        self._verified_cache[contract.cache_key] = contract
        return contract

    def validate(
        self,
        contract: VerifiedConfigurationContract,
        configuration: bytes,
        *,
        env_init_scripts: tuple[str, ...],
        ssh: SSHClient,
    ) -> ConfigurationValidationResult:
        if contract.source == "stable-fallback":
            command = _shell(build_stable_config_validate_command(contract), env_init_scripts)
            validation_input = configuration
        else:
            command = _shell(build_config_validate_command(contract.configured_executable), env_init_scripts)
            validation_input = _producer_validation_input(configuration)
        try:
            response = ssh.run(command, timeout=30, stdin_data=validation_input)
        except Exception as exc:
            raise ConfigurationContractError("configuration validation command failed") from exc
        if response.exit_code not in {0, 1}:
            raise ConfigurationContractError("configuration validation command failed")
        result = parse_validation_response(response.stdout, contract=contract)
        if response.exit_code == 0 and not result.valid:
            raise ConfigurationContractError("validation exit status disagrees with response")
        if response.exit_code == 1 and result.valid:
            raise ConfigurationContractError("validation exit status disagrees with response")
        return result

    def _stable_fallback(
        self,
        *,
        server_id: str,
        configured_executable: str,
        capabilities: ConfFlowCapabilities,
    ) -> VerifiedConfigurationContract:
        if capabilities.version != _STABLE_FALLBACK_VERSION:
            raise ConfigurationContractError("configuration contract command is unsupported")
        payload = capabilities.raw_payload
        producer = capabilities.producer
        if not isinstance(payload, dict) or not isinstance(producer, dict):
            raise ConfigurationContractError("configuration fallback requires the exact approved stable producer")
        expected_build = {"commit": _STABLE_FALLBACK_COMMIT, "dirty": False}
        if payload.get("build") != expected_build or producer.get("build") != expected_build:
            raise ConfigurationContractError("configuration fallback requires the exact approved stable producer")
        if producer.get("package") != "confflow" or producer.get("version") != _STABLE_FALLBACK_VERSION:
            raise ConfigurationContractError("configuration fallback requires the exact approved stable producer")
        if producer.get("wheel") != {
            "filename": _STABLE_FALLBACK_WHEEL,
            "sha256": _STABLE_FALLBACK_WHEEL_SHA256,
        }:
            raise ConfigurationContractError("configuration fallback requires the exact approved stable producer")
        schema = json.loads(stable_2_0_0.schema_bytes())
        schema_sha256 = hashlib.sha256(producer_canonical_json_bytes(schema)).hexdigest()
        fallback_payload = {
            "schema": CONTRACT_RESPONSE_SCHEMA,
            "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
            "workflow_schema_sha256": schema_sha256,
            "workflow_schema": schema,
            "producer": {
                "package": producer.get("package"),
                "version": producer.get("version"),
                "commit": expected_build["commit"],
                "dirty": expected_build["dirty"],
            },
            "validation_response_schema": VALIDATE_RESPONSE_SCHEMA,
        }
        return parse_contract_response(
            json.dumps(fallback_payload, sort_keys=True, separators=(",", ":")),
            server_id=server_id,
            configured_executable=configured_executable,
            capabilities=capabilities,
            source="stable-fallback",
        )


__all__ = [
    "SSHConfigurationContractClient",
    "build_config_contract_command",
    "build_config_validate_command",
]
