"""Strict consumer parsers for ConfFlow's machine configuration boundary."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from jobdesk_app.application.configuration_contract import (
    ConfigurationDiagnostic,
    ConfigurationValidationResult,
    ContractSource,
    VerifiedConfigurationContract,
)
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities

CONTRACT_RESPONSE_SCHEMA = "confflow.configuration-contract.v1"
VALIDATE_RESPONSE_SCHEMA = "confflow.configuration-validation.v1"
WORKFLOW_SCHEMA_VERSION = "confflow.workflow.v2"
CONTRACT_ID = CONTRACT_RESPONSE_SCHEMA
CONTRACT_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ConfigurationContractError(RuntimeError):
    """A remote configuration response failed closed validation."""


def _reject_non_finite_json_constant(value: str) -> None:
    """Reject Python's non-standard ``NaN``/``Infinity`` JSON extensions."""

    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationContractError("configuration contract contains non-JSON values") from exc
    return (encoded + "\n").encode("utf-8")


def producer_canonical_json_bytes(value: object) -> bytes:
    """Return the exact canonical bytes used by ConfFlow's v1 contract hashes."""

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationContractError("configuration contract contains non-JSON values") from exc
    return encoded.encode("utf-8")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _document(stdout: str) -> dict[str, object]:
    if not stdout or not stdout.strip():
        raise ConfigurationContractError("configuration response is empty")
    try:
        value = json.loads(
            stdout,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_json_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ConfigurationContractError("configuration response is not one valid JSON document") from exc
    if not isinstance(value, dict):
        raise ConfigurationContractError("configuration response must be an object")
    return value


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ConfigurationContractError(f"{label} fields do not match the frozen response ABI")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigurationContractError(f"{label} must be an object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationContractError(f"{label} must be a non-empty string")
    return value


def _sha(value: object, label: str) -> str:
    text = _text(value, label)
    if _SHA256.fullmatch(text) is None:
        raise ConfigurationContractError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _capability_binding(
    capabilities: ConfFlowCapabilities,
    *,
    configured_executable: str,
) -> tuple[str, bytes, bytes]:
    payload = capabilities.raw_payload
    if not isinstance(payload, dict):
        raise ConfigurationContractError("verified capabilities must retain raw provenance")
    producer = _object(payload.get("producer"), "capability producer")
    wheel = _object(producer.get("wheel"), "capability producer.wheel")
    build = _object(payload.get("build"), "capability build")
    producer_build = _object(producer.get("build"), "capability producer.build")
    install = payload.get("install_provenance", producer.get("install_provenance"))
    install = _object(install, "capability install_provenance")
    executable = _object(payload.get("executable"), "capability executable")
    path = _text(executable.get("path"), "capability executable.path")
    if configured_executable and path != configured_executable:
        raise ConfigurationContractError("capability executable does not match the configured executable")
    realpath = executable.get("realpath") or path
    realpath = _text(realpath, "capability executable.realpath")
    # Bind all producer/build/wheel/install fields, while requiring the blocks
    # the production handshake promises to expose.
    if not wheel or not build or not producer_build or not install:
        raise ConfigurationContractError("capability provenance is incomplete")
    provenance = {
        "build": build,
        "install_provenance": install,
        "producer": producer,
    }
    return realpath, canonical_json_bytes(executable), canonical_json_bytes(provenance)


def parse_contract_response(
    stdout: str,
    *,
    server_id: str,
    configured_executable: str,
    capabilities: ConfFlowCapabilities,
    source: ContractSource = "remote",
) -> VerifiedConfigurationContract:
    """Parse, hash, and provenance-bind the frozen contract response ABI."""

    payload = _document(stdout)
    _exact_keys(
        payload,
        {
            "schema",
            "workflow_schema_version",
            "workflow_schema_sha256",
            "workflow_schema",
            "producer",
            "validation_response_schema",
        },
        "contract response",
    )
    if payload.get("schema") != CONTRACT_RESPONSE_SCHEMA:
        raise ConfigurationContractError("unsupported configuration contract response schema")
    if payload.get("workflow_schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise ConfigurationContractError("unsupported workflow schema version")
    if payload.get("validation_response_schema") != VALIDATE_RESPONSE_SCHEMA:
        raise ConfigurationContractError("unsupported configuration validation response schema")
    schema_sha256 = _sha(payload.get("workflow_schema_sha256"), "workflow_schema_sha256")

    producer = _object(payload.get("producer"), "producer")
    _exact_keys(producer, {"package", "version", "commit", "dirty"}, "producer")
    if producer.get("package") != "confflow" or producer.get("version") != capabilities.version:
        raise ConfigurationContractError("configuration producer does not match verified capabilities")
    commit = producer.get("commit")
    dirty = producer.get("dirty")
    if commit is not None and not isinstance(commit, str):
        raise ConfigurationContractError("configuration producer build provenance is malformed")
    if dirty is not None and type(dirty) is not bool:
        raise ConfigurationContractError("configuration producer build provenance is malformed")
    capability_producer = capabilities.producer or {}
    if {"commit": commit, "dirty": dirty} != capability_producer.get("build"):
        raise ConfigurationContractError("configuration producer build does not match verified capabilities")

    schema = _object(payload.get("workflow_schema"), "workflow_schema")
    schema_bytes = producer_canonical_json_bytes(schema)
    if hashlib.sha256(schema_bytes).hexdigest() != schema_sha256:
        raise ConfigurationContractError("workflow schema bytes do not match contract digest")
    schema_id = _text(schema.get("$id"), "workflow_schema.$id")

    # The existing durable binding columns predate the producer-owned v1 ABI
    # and name this document digest a fixture manifest.  Preserve the storage
    # shape while binding it to the complete producer contract document rather
    # than inventing a producer field that ConfFlow does not emit.
    contract_document_sha256 = hashlib.sha256(producer_canonical_json_bytes(payload)).hexdigest()

    resolved, executable_identity, provenance = _capability_binding(
        capabilities,
        configured_executable=configured_executable,
    )
    return VerifiedConfigurationContract(
        server_id=server_id,
        configured_executable=configured_executable,
        resolved_executable=resolved,
        executable_identity=executable_identity,
        producer_provenance=provenance,
        content_schema=CONTRACT_RESPONSE_SCHEMA,
        contract_id=CONTRACT_ID,
        contract_version=CONTRACT_VERSION,
        schema_id=schema_id,
        schema_sha256=schema_sha256,
        fixture_set_id=CONTRACT_RESPONSE_SCHEMA,
        fixture_manifest_sha256=contract_document_sha256,
        workflow_schema_bytes=schema_bytes,
        source=source,
    )


def parse_validation_response(
    stdout: str,
    *,
    contract: VerifiedConfigurationContract,
) -> ConfigurationValidationResult:
    payload = _document(stdout)
    _exact_keys(
        payload,
        {"schema", "valid", "workflow_schema_sha256", "issues"},
        "validation response",
    )
    if payload.get("schema") != VALIDATE_RESPONSE_SCHEMA:
        raise ConfigurationContractError("unsupported configuration validation response schema")
    if _sha(payload.get("workflow_schema_sha256"), "workflow_schema_sha256") != contract.schema_sha256:
        raise ConfigurationContractError("validation response contract binding mismatch")
    valid = payload.get("valid")
    if type(valid) is not bool:
        raise ConfigurationContractError("validation response valid must be boolean")
    raw_diagnostics = payload.get("issues")
    if not isinstance(raw_diagnostics, list):
        raise ConfigurationContractError("validation response issues must be an array")
    diagnostics: list[ConfigurationDiagnostic] = []
    for raw in raw_diagnostics:
        diagnostic = _object(raw, "issue")
        _exact_keys(diagnostic, {"message", "path"}, "issue")
        message = _text(diagnostic.get("message"), "issue.message")
        path = diagnostic.get("path")
        if not isinstance(path, str) or len(path) > 512 or any(char in path for char in "\x00\r\n"):
            raise ConfigurationContractError("issue path is malformed")
        if len(message) > 4096 or any(char in message for char in "\x00\r\n"):
            raise ConfigurationContractError("issue message is malformed")
        diagnostics.append(ConfigurationDiagnostic("config.invalid", message, path))
    if valid == bool(diagnostics):
        raise ConfigurationContractError("validation status and diagnostics disagree")
    return ConfigurationValidationResult(
        content_schema=VALIDATE_RESPONSE_SCHEMA,
        contract_id=contract.contract_id,
        contract_version=contract.contract_version,
        schema_sha256=contract.schema_sha256,
        valid=valid,
        diagnostics=tuple(diagnostics),
        source=contract.source,
    )


__all__ = [
    "CONTRACT_ID",
    "CONTRACT_RESPONSE_SCHEMA",
    "CONTRACT_VERSION",
    "ConfigurationContractError",
    "VALIDATE_RESPONSE_SCHEMA",
    "WORKFLOW_SCHEMA_VERSION",
    "canonical_json_bytes",
    "producer_canonical_json_bytes",
    "parse_contract_response",
    "parse_validation_response",
]
