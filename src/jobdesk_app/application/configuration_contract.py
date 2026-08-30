"""Application boundary for producer-owned workflow configuration contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

ContractSource = Literal["remote", "stable-fallback"]
AdmissionStage = Literal["connect", "capability_probe", "contract_resolve", "identity_compare"]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SAFE_PATH_RE = re.compile(r"""^\$(?:[A-Za-z0-9_.\[\]"'-]+)?$""")


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _canonical_object_from_wire_bytes(name: str, value: object) -> str:
    """Validate frozen canonical JSON wire bytes and return local canonical text.

    Producer contract bindings use ``ensure_ascii=True`` and exactly one final
    newline. Persistence uses a canonical JSON *object* value without that
    transport terminator. The representation change occurs only after exact
    wire validation, so malformed or merely equivalent producer bytes are
    never silently admitted.
    """

    if not isinstance(value, bytes):
        raise ValueError(f"{name} must be UTF-8 JSON bytes")
    try:
        text = value.decode("utf-8", "strict")
        decoded = json.loads(text, parse_constant=_reject_non_finite_json_constant)
    except (UnicodeDecodeError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be UTF-8 JSON bytes") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must encode a JSON object")
    wire = json.dumps(decoded, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    if text != wire:
        raise ValueError(f"{name} must be canonical JSON wire bytes")
    return json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ConfigurationDiagnostic:
    """One privacy-safe diagnostic returned by the producer."""

    code: str
    message: str
    path: str


@dataclass(frozen=True)
class VerifiedConfigurationContract:
    """An immutable, provenance-bound producer contract document."""

    server_id: str
    configured_executable: str
    resolved_executable: str
    executable_identity: bytes
    producer_provenance: bytes
    content_schema: str
    contract_id: str
    contract_version: int
    schema_id: str
    schema_sha256: str
    fixture_set_id: str
    fixture_manifest_sha256: str
    workflow_schema_bytes: bytes
    source: ContractSource

    @property
    def cache_key(self) -> tuple[object, ...]:
        """Return every dimension that makes this verified document reusable."""

        return (
            self.server_id,
            self.configured_executable,
            self.resolved_executable,
            self.executable_identity,
            self.producer_provenance,
            self.content_schema,
            self.contract_id,
            self.contract_version,
            self.schema_id,
            self.schema_sha256,
            self.fixture_set_id,
            self.fixture_manifest_sha256,
            self.source,
        )


@dataclass(frozen=True)
class ConfigurationValidationResult:
    """Typed result from the remote semantic-validation boundary."""

    content_schema: str
    contract_id: str
    contract_version: int
    schema_sha256: str
    valid: bool
    diagnostics: tuple[ConfigurationDiagnostic, ...]
    source: ContractSource


class ConfigurationAdmissionError(RuntimeError):
    """Privacy-safe, typed failure of the configuration admission boundary.

    The producer response, YAML bytes, and transport exception are deliberately
    not retained in the public message.  ``path`` is accepted only for a
    bounded JSON-path-like location emitted by the frozen validation ABI.
    Callers can therefore classify a failure without accidentally displaying
    remote stderr or user configuration content.
    """

    def __init__(
        self,
        code: str,
        path: str | None = None,
        *,
        stage: AdmissionStage | None = None,
        cause_code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        safe_code = code if _SAFE_CODE_RE.fullmatch(code) else "admission_failed"
        safe_path = path if _is_safe_path(path) else None
        safe_stage = stage if stage in {"connect", "capability_probe", "contract_resolve", "identity_compare"} else None
        safe_cause_code = cause_code if isinstance(cause_code, str) and _SAFE_CODE_RE.fullmatch(cause_code) else None
        self.code = safe_code
        self.path = safe_path
        self.stage = safe_stage
        self.cause_code = safe_cause_code
        self.retryable = (
            safe_code == "configuration_admission_unavailable" if retryable is None else bool(retryable)
        )
        suffix = f" at {safe_path}" if safe_path is not None else ""
        super().__init__(f"configuration admission failed [{safe_code}]{suffix}")


def _is_safe_path(path: object) -> bool:
    """Return whether a producer diagnostic path is safe to display."""

    return isinstance(path, str) and len(path) <= 512 and _SAFE_PATH_RE.fullmatch(path) is not None


@dataclass(frozen=True)
class ConfigurationAdmission:
    """Immutable result of remote configuration admission.

    Admission is intentionally a value object rather than a persistence or
    submission handle.  The coordinator returns it only after the producer
    reports ``valid: true`` for the exact bytes and after the producer
    identity has been re-verified on the same SSH session.
    """

    contract: VerifiedConfigurationContract
    content_sha256: str
    validated_at: str
    validation_result: ConfigurationValidationResult | None = None

    def __post_init__(self) -> None:
        if _SHA256_RE.fullmatch(self.content_sha256) is None:
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        if not self.validated_at:
            raise ValueError("validated_at must be a non-empty timestamp")

    @property
    def verified_contract(self) -> VerifiedConfigurationContract:
        """Compatibility name for callers that use the domain terminology."""

        return self.contract

    @property
    def validation(self) -> ConfigurationValidationResult | None:
        """Short alias for the canonical producer validation result."""

        return self.validation_result

    @property
    def canonical_validation_result(self) -> ConfigurationValidationResult | None:
        """Explicit alias used by persistence/admission consumers."""

        return self.validation_result

    def to_configuration_binding(self):
        """Return the immutable persistence record for this accepted admission.

        The producer identity documents arrive as already-verified response
        bytes.  They are decoded strictly here and then checked again by the
        binding value object for canonical-object form; this boundary never
        normalizes malformed producer provenance into something persistable.
        """

        from jobdesk_app.core.configuration_binding import ConfigurationBinding

        return ConfigurationBinding(
            server_id=self.contract.server_id,
            content_sha256=self.content_sha256,
            content_schema=self.contract.content_schema,
            contract_id=self.contract.contract_id,
            contract_version=str(self.contract.contract_version),
            schema_id=self.contract.schema_id,
            schema_sha256=self.contract.schema_sha256,
            fixture_set=self.contract.fixture_set_id,
            fixture_sha256=self.contract.fixture_manifest_sha256,
            source=self.contract.source,
            configured_executable=self.contract.configured_executable,
            resolved_executable=self.contract.resolved_executable,
            canonical_executable_identity_json=_canonical_object_from_wire_bytes(
                "executable_identity", self.contract.executable_identity
            ),
            canonical_producer_provenance_json=_canonical_object_from_wire_bytes(
                "producer_provenance", self.contract.producer_provenance
            ),
            validated_at=self.validated_at,
        )

    @staticmethod
    def utc_now() -> str:
        """Return the canonical UTC timestamp used for a successful admission."""

        return datetime.now(timezone.utc).isoformat()


class ConfigurationContractClient(Protocol):
    """Port used by application orchestration to resolve and validate contracts."""

    def resolve(
        self,
        *,
        server_id: str,
        configured_executable: str,
        env_init_scripts: tuple[str, ...],
        ssh: Any,
        capabilities: Any,
    ) -> VerifiedConfigurationContract: ...

    def validate(
        self,
        contract: VerifiedConfigurationContract,
        configuration: bytes,
        *,
        env_init_scripts: tuple[str, ...],
        ssh: Any,
    ) -> ConfigurationValidationResult: ...


__all__ = [
    "Admission",
    "AdmissionStage",
    "ConfigurationAdmission",
    "ConfigurationAdmissionError",
    "ConfigurationContractClient",
    "ConfigurationDiagnostic",
    "ConfigurationValidationResult",
    "ContractSource",
    "VerifiedConfigurationContract",
]


# Short domain alias retained for callers that refer to this value simply as
# an ``Admission`` while the explicit class name remains discoverable.
Admission = ConfigurationAdmission
