"""Fail-closed application boundary for the ConfFlow config contract.

The config contract is deliberately separate from ``confflow.control.v1``.
The control protocol describes a run; this contract describes the producer
configuration semantics JobDesk is about to submit to.  A producer either
returns the exact JSON contract or the released v2.0.0 rollback identity is
accepted through its explicit versioned compatibility exception.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jobdesk_app.core.confflow_contract import (
    ROLLBACK_REFERENCE_BUILD_COMMIT,
    ROLLBACK_REFERENCE_VERSION,
    ROLLBACK_REFERENCE_WHEEL_FILENAME,
    ROLLBACK_REFERENCE_WHEEL_SHA256,
)
from jobdesk_app.core.confflow_executable import ConfFlowExecutableIdentity
from jobdesk_app.core.confflow_preflight import ConfFlowCapabilities

CONFIG_CONTRACT_SCHEMA = "confflow.config.contract.v1"
CONFIG_CONTRACT_VERSION = 1
WORKFLOW_SCHEMA_VERSION = "v1"
SEMANTIC_CONTRACT_VERSION = "1.0"
CONFIG_CONTRACT_COMMAND = "config contract --json"
CONFIG_CONTRACT_COMMAND_NAME = "config_contract"
CONFIG_CONTRACT_MODE = Literal["contract", "approved-identity-compatibility"]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_CONTRACT_VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SCHEMA_FILE = "workflow_config_v1.schema.json"


class ConfigContractResolutionError(RuntimeError):
    """The remote producer config contract could not be accepted."""


@dataclass(frozen=True, slots=True)
class VendoredSchemaBundle:
    """Canonical bytes and digest for JobDesk's vendored producer bundle."""

    bytes: bytes
    digest: str
    files: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"digest": self.digest, "files": list(self.files), "size": len(self.bytes)}

    @property
    def workflow_schema_sha256(self) -> str:
        """Return the digest used by the producer's workflow schema contract."""
        return self.digest


@dataclass(frozen=True, slots=True)
class RemoteIdentityCacheKey:
    """Stable cache/persistence identity for one selected remote executable."""

    server_id: str
    executable_identity: ConfFlowExecutableIdentity
    producer_identity: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.server_id, str) or not self.server_id.strip():
            raise ValueError("remote identity cache key requires a non-empty server id")

    @property
    def value(self) -> str:
        encoded = json.dumps(
            {
                "server_id": self.server_id,
                "executable_identity": self.executable_identity.as_dict(),
                "producer_identity": self.producer_identity,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "server_id": self.server_id,
            "executable_identity": self.executable_identity.as_dict(),
            "producer_identity": list(self.producer_identity),
            "cache_key": self.value,
        }


@dataclass(frozen=True, slots=True)
class ConfigContractResult:
    """Typed result retained by submit and run provenance boundaries."""

    accepted: bool
    mode: CONFIG_CONTRACT_MODE
    response_schema: str | None
    response_version: int | None
    workflow_schema_version: str | None
    producer_package: str
    producer_version: str
    semantic_contract_version: str
    workflow_schema_sha256: str | None
    schema_bundle_sha256: str
    remote_identity: RemoteIdentityCacheKey
    raw_contract: dict[str, object] | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "mode": self.mode,
            "response_schema": self.response_schema,
            "response_version": self.response_version,
            "workflow_schema_version": self.workflow_schema_version,
            "producer": {
                "package": self.producer_package,
                "version": self.producer_version,
            },
            "semantic_contract_version": self.semantic_contract_version,
            "workflow_schema_sha256": self.workflow_schema_sha256,
            "schema_bundle_sha256": self.schema_bundle_sha256,
            "remote_identity": self.remote_identity.as_dict(),
            "contract": self.raw_contract,
            "reason": self.reason,
        }


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _repo_schema_root() -> Path:
    # Resolve through the installed JobDesk package as well as the source
    # checkout.  The resource is a producer-owned workflow schema, not the
    # frozen control.v1 schema bundle.
    return Path(__file__).resolve().parents[1] / "resources" / "workflow_config" / "v1"


def vendored_schema_bundle_bytes(schema_root: Path | str | None = None) -> bytes:
    """Return canonical bytes for the local producer-owned workflow schema."""
    root = Path(schema_root) if schema_root is not None else _repo_schema_root()
    path = root / _SCHEMA_FILE
    try:
        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ConfigContractResolutionError(f"vendored workflow schema is unreadable: {path}") from exc
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def vendored_schema_bundle(schema_root: Path | str | None = None) -> VendoredSchemaBundle:
    data = vendored_schema_bundle_bytes(schema_root)
    return VendoredSchemaBundle(
        bytes=data,
        digest=hashlib.sha256(data).hexdigest(),
        files=(_SCHEMA_FILE,),
    )


def validate_vendored_schema_bundle_bytes(bundle_bytes: bytes, expected_digest: str) -> bytes:
    """Validate exact local bundle bytes before they become accepted provenance."""
    if not isinstance(bundle_bytes, bytes) or not bundle_bytes:
        raise ValueError("vendored schema bundle bytes must be non-empty bytes")
    if not isinstance(expected_digest, str) or _SHA256_RE.fullmatch(expected_digest.lower()) is None:
        raise ValueError("vendored schema bundle digest is malformed")
    actual = hashlib.sha256(bundle_bytes).hexdigest()
    if actual != expected_digest.lower():
        raise ValueError("vendored schema bundle digest mismatch")
    return bundle_bytes


def build_config_contract_command(executable: str | None = None) -> str:
    token = (executable or "").strip() or "confflow"
    if any(char in token for char in "\x00\r\n"):
        raise ValueError("ConfFlow executable must not contain NUL or newlines")
    return f"{shlex.quote(token)} {CONFIG_CONTRACT_COMMAND}"


def _build_config_contract_shell(command: str, env_init_scripts: Iterable[str] = ()) -> str:
    """Run the contract command in the same initialized shell as preflight."""
    lines = [
        "set +u",
        "[ -f /etc/profile ] && . /etc/profile >/dev/null 2>&1 || true",
        '[ -f "$HOME/.bash_profile" ] && . "$HOME/.bash_profile" >/dev/null 2>&1 || true',
        '[ -f "$HOME/.profile" ] && . "$HOME/.profile" >/dev/null 2>&1 || true',
        '[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc" >/dev/null 2>&1 || true',
    ]
    lines.extend(
        f"[ -f {shlex.quote(script)} ] && . {shlex.quote(script)} >/dev/null 2>&1 || true"
        for script in env_init_scripts
        if script
    )
    lines.append(command)
    return "\n".join(lines)


def _command_unavailable(exit_code: int, stdout: str, stderr: str) -> bool:
    if exit_code == 127:
        return True
    text = f"{stdout}\n{stderr}".lower()
    return any(marker in text for marker in ("unknown command", "unrecognized arguments", "no such command"))


def _mapping(value: object) -> dict[str, object]:
    """Narrow optional capability blocks without trusting remote JSON types."""
    return value if isinstance(value, dict) else {}


def _producer_identity(capabilities: ConfFlowCapabilities) -> tuple[str, ...]:
    """Return immutable producer facts that participate in the cache key."""
    producer = _mapping(capabilities.producer)
    build = _mapping(capabilities.build)
    producer_build = _mapping(producer.get("build"))
    wheel = _mapping(producer.get("wheel"))
    return (
        str(capabilities.version),
        str(build.get("commit") or ""),
        str(producer_build.get("commit") or ""),
        str(wheel.get("sha256") or ""),
    )


def _is_approved_stable_identity(capabilities: ConfFlowCapabilities) -> bool:
    """Return whether capability v4 is the exact released v2.0.0 rollback identity."""
    producer = _mapping(capabilities.producer)
    build = _mapping(capabilities.build)
    producer_build = _mapping(producer.get("build"))
    wheel = _mapping(producer.get("wheel"))
    return (
        capabilities.version == ROLLBACK_REFERENCE_VERSION
        and build.get("commit") == ROLLBACK_REFERENCE_BUILD_COMMIT
        and build.get("dirty") is False
        and producer.get("package") == "confflow"
        and producer.get("version") == ROLLBACK_REFERENCE_VERSION
        and producer_build.get("commit") == ROLLBACK_REFERENCE_BUILD_COMMIT
        and producer_build.get("dirty") is False
        and wheel.get("filename") == ROLLBACK_REFERENCE_WHEEL_FILENAME
        and wheel.get("sha256") == ROLLBACK_REFERENCE_WHEEL_SHA256
    )


class ConfigContractResolver:
    """Resolve and validate one producer config contract."""

    def __init__(
        self,
        *,
        schema_bundle: VendoredSchemaBundle | None = None,
        workflow_schema_sha256: str | None = None,
    ) -> None:
        self._schema_bundle = schema_bundle or vendored_schema_bundle()
        validate_vendored_schema_bundle_bytes(self._schema_bundle.bytes, self._schema_bundle.digest)
        self._workflow_schema_sha256 = workflow_schema_sha256 or self._schema_bundle.digest
        if _SHA256_RE.fullmatch(self._workflow_schema_sha256.lower()) is None:
            raise ValueError("workflow schema digest is malformed")
        self._cache: dict[str, ConfigContractResult] = {}

    @property
    def schema_bundle(self) -> VendoredSchemaBundle:
        return self._schema_bundle

    def resolve(
        self,
        ssh: Any,
        *,
        server_id: str,
        executable: str | None,
        capabilities: ConfFlowCapabilities,
        executable_identity: ConfFlowExecutableIdentity | None,
        env_init_scripts: Iterable[str] = (),
    ) -> ConfigContractResult:
        """Resolve a contract, or return the explicit approved compatibility result."""
        if executable_identity is None:
            raise ConfigContractResolutionError("ConfFlow config contract requires immutable executable identity")
        try:
            identity_key = RemoteIdentityCacheKey(
                server_id=server_id,
                executable_identity=executable_identity,
                producer_identity=_producer_identity(capabilities),
            )
        except ValueError as exc:
            raise ConfigContractResolutionError(str(exc)) from exc
        cached = self._cache.get(identity_key.value)
        if cached is not None:
            return cached

        commands = capabilities.commands or {}
        # Old v2.0.0 capability payloads have no additive command map entry.
        # A non-empty v4 command map from a candidate must still be probed.
        if commands.get(CONFIG_CONTRACT_COMMAND_NAME) is False or (
            CONFIG_CONTRACT_COMMAND_NAME not in commands and _is_approved_stable_identity(capabilities)
        ):
            result = self._approved_identity_compatibility(capabilities, identity_key)
            self._cache[identity_key.value] = result
            return result

        command = _build_config_contract_shell(
            build_config_contract_command(executable), env_init_scripts
        )
        try:
            response = ssh.run(command, timeout=30)
        except Exception as exc:
            raise ConfigContractResolutionError(f"ConfFlow config contract command failed: {exc}") from exc
        if response.exit_code != 0:
            if _command_unavailable(response.exit_code, response.stdout, response.stderr):
                result = self._approved_identity_compatibility(capabilities, identity_key)
                self._cache[identity_key.value] = result
                return result
            detail = response.stderr.strip() or response.stdout.strip() or f"exit {response.exit_code}"
            raise ConfigContractResolutionError(f"ConfFlow config contract command failed: {detail}")
        try:
            payload = parse_config_contract(response.stdout)
            result = self._validate_payload(payload, capabilities, identity_key)
            self._cache[identity_key.value] = result
            return result
        except ValueError as exc:
            raise ConfigContractResolutionError(f"ConfFlow config contract rejected: {exc}") from exc

    def _approved_identity_compatibility(
        self,
        capabilities: ConfFlowCapabilities,
        identity_key: RemoteIdentityCacheKey,
    ) -> ConfigContractResult:
        if capabilities.version != ROLLBACK_REFERENCE_VERSION:
            raise ConfigContractResolutionError(
                "ConfFlow config contract command is unavailable and producer version is not the approved rollback v2.0.0"
            )
        producer = capabilities.producer or {}
        build = capabilities.build or {}
        producer_build = producer.get("build")
        if not isinstance(producer_build, dict):
            raise ConfigContractResolutionError("approved compatibility requires producer build provenance")
        wheel = producer.get("wheel")
        if not isinstance(wheel, dict):
            raise ConfigContractResolutionError("approved compatibility requires producer wheel provenance")
        if (
            build.get("commit") != ROLLBACK_REFERENCE_BUILD_COMMIT
            or build.get("dirty") is not False
            or producer.get("package") != "confflow"
            or producer.get("version") != ROLLBACK_REFERENCE_VERSION
            or producer_build.get("commit") != ROLLBACK_REFERENCE_BUILD_COMMIT
            or producer_build.get("dirty") is not False
            or wheel.get("filename") != ROLLBACK_REFERENCE_WHEEL_FILENAME
            or wheel.get("sha256") != ROLLBACK_REFERENCE_WHEEL_SHA256
        ):
            raise ConfigContractResolutionError("unknown producer identity cannot use config contract compatibility")
        declared_sha = (capabilities.executable or {}).get("sha256")
        if not isinstance(declared_sha, str) or declared_sha.lower() != identity_key.executable_identity.sha256.lower():
            raise ConfigContractResolutionError("unknown executable identity cannot use config contract compatibility")
        return ConfigContractResult(
            accepted=True,
            mode="approved-identity-compatibility",
            response_schema=CONFIG_CONTRACT_SCHEMA,
            response_version=None,
            workflow_schema_version=WORKFLOW_SCHEMA_VERSION,
            producer_package="confflow",
            producer_version=ROLLBACK_REFERENCE_VERSION,
            semantic_contract_version=SEMANTIC_CONTRACT_VERSION,
            workflow_schema_sha256=self._workflow_schema_sha256,
            schema_bundle_sha256=self._schema_bundle.digest,
            remote_identity=identity_key,
            reason="approved rollback v2.0.0 producer has no config contract command",
        )

    def _validate_payload(
        self,
        payload: dict[str, object],
        capabilities: ConfFlowCapabilities,
        identity_key: RemoteIdentityCacheKey,
    ) -> ConfigContractResult:
        expected_keys = {"response_schema", "workflow_schema", "semantic_contract_version", "producer"}
        if set(payload) != expected_keys:
            raise ValueError("response schema contains unknown or missing fields")
        if payload.get("response_schema") != CONFIG_CONTRACT_SCHEMA:
            raise ValueError("response schema mismatch")
        workflow_schema = payload.get("workflow_schema")
        if not isinstance(workflow_schema, dict) or set(workflow_schema) != {"version", "sha256", "resource"}:
            raise ValueError("workflow schema binding is malformed")
        workflow_version = workflow_schema.get("version")
        workflow_hash = workflow_schema.get("sha256")
        workflow_resource = workflow_schema.get("resource")
        if workflow_version != WORKFLOW_SCHEMA_VERSION:
            raise ValueError("workflow schema version mismatch")
        if workflow_resource != _SCHEMA_FILE:
            raise ValueError("workflow schema resource mismatch")
        if not isinstance(workflow_hash, str) or workflow_hash.lower() != self._workflow_schema_sha256.lower():
            raise ValueError("workflow schema hash mismatch")

        semantic_version = payload.get("semantic_contract_version")
        if not isinstance(semantic_version, str) or _CONTRACT_VERSION_RE.fullmatch(semantic_version) is None:
            raise ValueError("semantic contract version is malformed")
        if semantic_version != SEMANTIC_CONTRACT_VERSION:
            raise ValueError("semantic contract version mismatch")

        producer = payload.get("producer")
        if not isinstance(producer, dict) or set(producer) != {
            "distribution",
            "version",
            "configuration_contract",
        }:
            raise ValueError("producer binding is malformed")
        if producer.get("distribution") != "confflow":
            raise ValueError("producer binding package mismatch")
        producer_version = producer.get("version")
        if not isinstance(producer_version, str) or producer_version != capabilities.version:
            raise ValueError("producer version does not match capability version")
        if producer.get("configuration_contract") != semantic_version:
            raise ValueError("producer configuration contract binding mismatch")

        # The additive config response intentionally does not duplicate the
        # executable/wheel fields already frozen by capability v4. Bind those
        # facts here so accepting the config contract cannot detach it from the
        # exact producer identity admitted before upload.
        capability_producer = capabilities.producer or {}
        capability_build = capabilities.build or {}
        capability_producer_build = capability_producer.get("build")
        capability_wheel = capability_producer.get("wheel")
        if not isinstance(capability_producer_build, dict) or not isinstance(capability_wheel, dict):
            raise ValueError("capability producer binding is incomplete")
        if capability_producer.get("package") != "confflow":
            raise ValueError("capability producer package mismatch")
        if capability_producer.get("version") != capabilities.version:
            raise ValueError("capability producer version mismatch")
        if capability_build.get("commit") != capability_producer_build.get("commit"):
            raise ValueError("producer build binding mismatch")
        wheel_sha256 = capability_wheel.get("sha256")
        if not isinstance(wheel_sha256, str) or _SHA256_RE.fullmatch(wheel_sha256.lower()) is None:
            raise ValueError("producer wheel binding mismatch")
        executable_sha256 = (capabilities.executable or {}).get("sha256")
        if not isinstance(executable_sha256, str) or executable_sha256.lower() != identity_key.executable_identity.sha256.lower():
            raise ValueError("producer executable binding mismatch")
        return ConfigContractResult(
            accepted=True,
            mode="contract",
            response_schema=CONFIG_CONTRACT_SCHEMA,
            response_version=None,
            workflow_schema_version=workflow_version,
            producer_package="confflow",
            producer_version=producer_version,
            semantic_contract_version=semantic_version,
            workflow_schema_sha256=workflow_hash,
            schema_bundle_sha256=self._schema_bundle.digest,
            remote_identity=identity_key,
            raw_contract=payload,
        )


def parse_config_contract(stdout: str) -> dict[str, object]:
    """Parse one pure JSON stdout document; logs before/after JSON are rejected."""
    if not isinstance(stdout, str) or not stdout.strip():
        raise ValueError("config contract output is empty")
    try:
        payload = json.loads(stdout, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"malformed config contract JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("config contract JSON must be an object")
    return payload


__all__ = [
    "CONFIG_CONTRACT_COMMAND",
    "CONFIG_CONTRACT_COMMAND_NAME",
    "CONFIG_CONTRACT_SCHEMA",
    "CONFIG_CONTRACT_VERSION",
    "SEMANTIC_CONTRACT_VERSION",
    "WORKFLOW_SCHEMA_VERSION",
    "ConfigContractResolutionError",
    "ConfigContractResolver",
    "ConfigContractResult",
    "RemoteIdentityCacheKey",
    "VendoredSchemaBundle",
    "build_config_contract_command",
    "parse_config_contract",
    "validate_vendored_schema_bundle_bytes",
    "vendored_schema_bundle",
    "vendored_schema_bundle_bytes",
]
