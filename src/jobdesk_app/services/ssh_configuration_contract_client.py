"""SSH adapter for the producer-owned ConfFlow configuration contract."""

from __future__ import annotations

import base64
import json
import shlex
from collections.abc import Iterable

from jobdesk_app.application.configuration_contract import (
    ConfigurationValidationResult,
    VerifiedConfigurationContract,
)
from jobdesk_app.core.confflow_contract import REFERENCE_VERSION
from jobdesk_app.core.confflow_preflight import (
    ConfFlowCapabilities,
    validate_confflow_production_capability,
)
from jobdesk_app.remote.confflow_config_contract import (
    CONTRACT_ID,
    CONTRACT_RESPONSE_SCHEMA,
    CONTRACT_VERSION,
    ConfigurationContractError,
    parse_contract_response,
    parse_validation_response,
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


_STABLE_VALIDATOR_SCRIPT = r"""
import json
import os
import sys
import tempfile
import yaml

from confflow.config.models import load_workflow_model
from confflow.shared.config_validation import validate_yaml_config

CONTRACT = json.loads(__CONTRACT_JSON__)

def _document(valid, diagnostics):
    return {
        "content_schema": "confflow.config.validate-response.v1",
        "contract": CONTRACT,
        "valid": valid,
        "diagnostics": diagnostics,
    }

def _diagnostic(code, path="$"):
    messages = {
        "config.invalid_yaml": "configuration input is not valid YAML",
        "config.schema_invalid": "configuration does not satisfy the workflow schema",
        "config.invalid": "configuration is invalid",
        "config.semantic_invalid": "configuration violates a required semantic rule",
        "config.internal_error": "configuration command failed internally",
    }
    return {"code": code, "message": messages[code], "path": path}

path = None
diagnostics = []
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
        diagnostics = [_diagnostic("config.invalid_yaml")]
    else:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            diagnostics = [_diagnostic("config.schema_invalid")]
        else:
            try:
                load_workflow_model(path)
            except Exception:
                diagnostics = [_diagnostic("config.invalid")]
            else:
                try:
                    errors = validate_yaml_config(raw)
                except Exception:
                    diagnostics = [_diagnostic("config.internal_error")]
                    exit_code = 2
                else:
                    diagnostics = [_diagnostic("config.semantic_invalid") for _ in errors]
    if diagnostics and exit_code == 0:
        exit_code = 1
except Exception:
    diagnostics = [_diagnostic("config.internal_error")]
    exit_code = 2
finally:
    if path is not None:
        try:
            os.unlink(path)
        except Exception:
            diagnostics = [_diagnostic("config.internal_error")]
            exit_code = 2

sys.stdout.write(json.dumps(_document(not diagnostics, diagnostics), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
raise SystemExit(exit_code)
"""


def build_stable_config_validate_command(contract: VerifiedConfigurationContract) -> str:
    """Build the exact-2.0 remote validator command without a shell temp file."""

    try:
        identity = json.loads(contract.executable_identity)
        python_executable = identity["python"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationContractError("stable producer identity has no validated Python executable") from exc
    if not isinstance(python_executable, str) or not python_executable:
        raise ConfigurationContractError("stable producer identity has no validated Python executable")
    binding = json.dumps(
        {
            "id": contract.contract_id,
            "version": contract.contract_version,
            "schema_sha256": contract.schema_sha256,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    source = _STABLE_VALIDATOR_SCRIPT.replace("__CONTRACT_JSON__", repr(binding))
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
        else:
            command = _shell(build_config_validate_command(contract.configured_executable), env_init_scripts)
        try:
            response = ssh.run(command, timeout=30, stdin_data=configuration)
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
        if capabilities.version != REFERENCE_VERSION:
            raise ConfigurationContractError("configuration contract command is unsupported")
        try:
            validate_confflow_production_capability(
                capabilities,
                expected_executable=configured_executable or None,
            )
        except ValueError as exc:
            raise ConfigurationContractError(
                "configuration fallback requires the exact approved stable producer"
            ) from exc
        schema = json.loads(stable_2_0_0.schema_bytes())
        producer = capabilities.producer or {}
        fallback_payload = {
            "content_schema": CONTRACT_RESPONSE_SCHEMA,
            "contract": {
                "fixture_set": {
                    "id": stable_2_0_0.FIXTURE_SET_ID,
                    "manifest_sha256": stable_2_0_0.FIXTURE_MANIFEST_SHA256,
                },
                "id": CONTRACT_ID,
                "schema_id": stable_2_0_0.SCHEMA_ID,
                "schema_sha256": stable_2_0_0.SCHEMA_SHA256,
                "version": CONTRACT_VERSION,
            },
            "producer": {
                "build": producer.get("build"),
                "package": producer.get("package"),
                "version": producer.get("version"),
            },
            "workflow_schema": schema,
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
