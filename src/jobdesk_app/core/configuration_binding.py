"""Immutable, validated snapshot of the configuration accepted for a run.

This type deliberately stores the two structured identity documents as
canonical JSON text.  It prevents a caller from mutating accepted identity
after persistence and keeps the repository boundary independent of a specific
ConfFlow client implementation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT: Final = 4096
_MAX_IDENTIFIER: Final = 256
_MAX_JSON: Final = 65536


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is not allowed: {value}")


def _required_text(name: str, value: str, *, maximum: int = _MAX_IDENTIFIER) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{name} must be a non-empty string no longer than {maximum} characters")
    return value


def _sha256(name: str, value: str) -> str:
    _required_text(name, value, maximum=64)
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
    return value


def _canonical_json(name: str, value: str) -> str:
    _required_text(name, value, maximum=_MAX_JSON)
    try:
        decoded = json.loads(value, parse_constant=_reject_non_finite_constant)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must encode a JSON object")
    canonical = json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if value != canonical:
        raise ValueError(f"{name} must be canonical JSON")
    return canonical


def is_canonical_json_object(value: object) -> int:
    """Return SQLite-compatible ``1`` only for a canonical JSON object string.

    The integer return is intentional: it is registered as a deterministic
    SQLite function in schema-v7 CHECK constraints and treats every malformed
    value as invalid rather than leaking parser exceptions through SQL.
    """
    if not isinstance(value, str):
        return 0
    try:
        decoded = json.loads(value, parse_constant=_reject_non_finite_constant)
    except (TypeError, ValueError):
        return 0
    if not isinstance(decoded, dict):
        return 0
    return int(json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) == value)


@dataclass(frozen=True)
class ConfigurationBinding:
    """The exact configuration contract and producer identity bound to one run.

    ``canonical_*_json`` fields must be compact, key-sorted JSON objects.  The
    content digest is verified against no payload here because this persistence
    layer intentionally does not own the input document bytes.
    """

    server_id: str
    content_sha256: str
    content_schema: str
    contract_id: str
    contract_version: str
    schema_id: str
    schema_sha256: str
    fixture_set: str
    fixture_sha256: str
    source: str
    configured_executable: str
    resolved_executable: str
    canonical_executable_identity_json: str
    canonical_producer_provenance_json: str
    validated_at: str

    def __post_init__(self) -> None:
        _required_text("server_id", self.server_id)
        _sha256("content_sha256", self.content_sha256)
        _required_text("content_schema", self.content_schema)
        _required_text("contract_id", self.contract_id)
        _required_text("contract_version", self.contract_version)
        _required_text("schema_id", self.schema_id)
        _sha256("schema_sha256", self.schema_sha256)
        _required_text("fixture_set", self.fixture_set)
        _sha256("fixture_sha256", self.fixture_sha256)
        _required_text("source", self.source)
        _required_text("configured_executable", self.configured_executable, maximum=_MAX_TEXT)
        _required_text("resolved_executable", self.resolved_executable, maximum=_MAX_TEXT)
        _canonical_json("canonical_executable_identity_json", self.canonical_executable_identity_json)
        _canonical_json("canonical_producer_provenance_json", self.canonical_producer_provenance_json)
        _required_text("validated_at", self.validated_at)

    @staticmethod
    def canonical_json(document: dict[str, object]) -> str:
        """Return the canonical JSON representation required by this binding."""
        return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def content_digest_matches(self, content: bytes) -> bool:
        """Return whether ``content`` has the persisted SHA-256 digest."""
        return hashlib.sha256(content).hexdigest() == self.content_sha256
