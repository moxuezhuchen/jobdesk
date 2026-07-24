"""Pure parsing and compatibility checks for remote ConfFlow capabilities."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_MINIMUM_VERSION = (1, 4, 1)
_MAXIMUM_MAJOR = 2
_CAPABILITY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ConfFlowCapabilities:
    schema_version: int
    version: str
    workflow_state: bool
    resume: bool
    dag: bool


def parse_confflow_capabilities(stdout: str) -> ConfFlowCapabilities:
    """Parse the exact JSON document emitted by ``--capabilities --json``."""
    if not stdout or not stdout.strip():
        raise ValueError("ConfFlow capability output is empty")
    try:
        payload = json.loads(stdout, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"malformed ConfFlow capability JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("malformed ConfFlow capability JSON: expected an object")

    schema_version = payload.get("schema_version")
    if type(schema_version) is not int:
        raise ValueError("ConfFlow capability schema_version must be an integer")
    version = payload.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("ConfFlow capability version must be a non-empty string")
    capability_values = payload.get("capabilities")
    if not isinstance(capability_values, dict):
        raise ValueError("ConfFlow capabilities must be an object")

    parsed: dict[str, bool] = {}
    for name in ("workflow_state", "resume", "dag"):
        value = capability_values.get(name)
        if type(value) is not bool:
            raise ValueError(f"ConfFlow capability {name} must be boolean")
        parsed[name] = value

    return ConfFlowCapabilities(
        schema_version=schema_version,
        version=version,
        workflow_state=parsed["workflow_state"],
        resume=parsed["resume"],
        dag=parsed["dag"],
    )


def validate_confflow_capabilities(capabilities: ConfFlowCapabilities, *, require_dag: bool) -> None:
    """Fail closed unless the remote supports JobDesk's workflow contract."""
    if capabilities.schema_version != _CAPABILITY_SCHEMA_VERSION:
        raise ValueError(
            "unsupported ConfFlow capability schema: "
            f"expected {_CAPABILITY_SCHEMA_VERSION}, got {capabilities.schema_version}"
        )
    version = _parse_semver(capabilities.version)
    core = version[:3]
    prerelease = version[3]
    if core < _MINIMUM_VERSION or (core == _MINIMUM_VERSION and prerelease is not None):
        raise ValueError(f"incompatible ConfFlow version {capabilities.version}: require >=1.4.1,<2.0")
    if core[0] >= _MAXIMUM_MAJOR:
        raise ValueError(f"incompatible ConfFlow version {capabilities.version}: require >=1.4.1,<2.0")
    if not capabilities.workflow_state:
        raise ValueError("remote ConfFlow lacks required workflow_state capability")
    if not capabilities.resume:
        raise ValueError("remote ConfFlow lacks required resume capability")
    if require_dag and not capabilities.dag:
        raise ValueError("remote ConfFlow lacks required dag capability")


def _parse_semver(value: str) -> tuple[int, int, int, str | None]:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid ConfFlow semantic version: {value}")
    prerelease = match.group(4)
    if prerelease is not None:
        for identifier in prerelease.split("."):
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError(f"invalid ConfFlow semantic version: {value}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value
