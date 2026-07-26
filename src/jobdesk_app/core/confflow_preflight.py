"""Pure parsing and compatibility checks for remote ConfFlow capabilities.

The validator is **fail closed**: every requirement must be satisfied for
the payload to be accepted. The check happens before any upload, dry-run,
or nohup so that an incompatible remote never gets a hand on JobDesk's
workload.

Schema v3 vs v1/v2
---------------
The current contract (see :mod:`.confflow_contract`) requires ConfFlow
to emit a v3 payload including ``artifacts``, ``commands``, and ``build``.
Older payloads whose ``--capabilities --json`` output omits
``schema_version`` 3 are rejected outright — there is no negotiation.
The parser still tolerates missing optional blocks so the validator can
report the schema mismatch precisely instead of a malformed-JSON error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .confflow_contract import (
    CAPABILITY_SCHEMA_VERSION,
    EXPECTED_ARTIFACTS,
    MAX_EXCLUSIVE,
    MIN_VERSION,
    REQUIRED_COMMANDS,
    ConfFlowArtifactContract,
    version_spec,
)

PRERELEASE_AT_MIN_REJECT = True
PRERELEASE_ABOVE_MIN_ACCEPT = True

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True)
class ConfFlowCapabilities:
    schema_version: int
    version: str
    workflow_state: bool
    resume: bool
    dag: bool
    # `None` is allowed by the parser so v1 payloads can be diagnosed as
    # "unsupported schema" rather than as malformed JSON. The validator
    # demands a not-None value when schema_version == CAPABILITY_SCHEMA_VERSION.
    artifacts: ConfFlowArtifactContract | None = None
    commands: dict[str, bool] | None = None
    build: dict[str, object] | None = None


def parse_confflow_capabilities(stdout: str) -> ConfFlowCapabilities:
    """Parse the exact JSON document emitted by ``--capabilities --json``.

    The parser **tolerates** a missing ``artifacts`` block so the
    validator can identify older v1 payloads and reject them with a
    clear ``unsupported schema`` message rather than a JSON error.
    """
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

    artifacts = _parse_artifacts(payload.get("artifacts"))
    commands = _parse_commands(payload.get("commands"))
    build = _parse_build(payload.get("build"))

    return ConfFlowCapabilities(
        schema_version=schema_version,
        version=version,
        workflow_state=parsed["workflow_state"],
        resume=parsed["resume"],
        dag=parsed["dag"],
        artifacts=artifacts,
        commands=commands,
        build=build,
    )


def _parse_commands(raw: object) -> dict[str, bool] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("ConfFlow capability commands must be an object")
    commands: dict[str, bool] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or type(value) is not bool:
            raise ValueError("ConfFlow capability commands must map names to booleans")
        commands[name] = value
    return commands


def _parse_build(raw: object) -> dict[str, object] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("ConfFlow capability build must be an object")
    commit = raw.get("commit")
    dirty = raw.get("dirty")
    if commit is not None and not isinstance(commit, str):
        raise ValueError("ConfFlow capability build.commit must be a string or null")
    if dirty is not None and type(dirty) is not bool:
        raise ValueError("ConfFlow capability build.dirty must be boolean or null")
    return {"commit": commit, "dirty": dirty}


def _parse_artifacts(raw: object) -> ConfFlowArtifactContract | None:
    """Return the parsed artifacts contract, or None when absent.

    A non-object value is treated as ``None`` so the validator can
    surface the schema mismatch as the root cause rather than masking
    it with a secondary "artifacts malformed" error.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    names = ("run_summary", "workflow_stats", "workflow_state", "run_report", "min_xyz")
    if any(not isinstance(raw.get(name), str) for name in names):
        return None
    try:
        return ConfFlowArtifactContract(
            run_summary=raw["run_summary"],
            workflow_stats=raw["workflow_stats"],
            workflow_state=raw["workflow_state"],
            run_report=raw["run_report"],
            min_xyz=raw["min_xyz"],
        )
    except (KeyError, TypeError):
        return None


def validate_confflow_capabilities(capabilities: ConfFlowCapabilities, *, require_dag: bool) -> None:
    """Fail closed unless the remote supports JobDesk's workflow contract.

    The schema check fires first: v1 payloads are rejected outright,
    even when ``artifacts`` is ``None``, so there is no soft path
    through the validator.
    """
    spec = version_spec()
    if capabilities.schema_version != CAPABILITY_SCHEMA_VERSION:
        raise ValueError(
            "unsupported ConfFlow capability schema: "
            f"expected {CAPABILITY_SCHEMA_VERSION}, got {capabilities.schema_version}"
        )
    version = _parse_semver(capabilities.version)
    core = version[:3]
    prerelease = version[3]
    if core < MIN_VERSION or (PRERELEASE_AT_MIN_REJECT and core == MIN_VERSION and prerelease is not None) or (core > MIN_VERSION and prerelease is not None and not PRERELEASE_ABOVE_MIN_ACCEPT):
        raise ValueError(f"incompatible ConfFlow version {capabilities.version}: require {spec}")
    if core >= MAX_EXCLUSIVE:
        raise ValueError(f"incompatible ConfFlow version {capabilities.version}: require {spec}")
    if capabilities.artifacts is None:
        raise ValueError(
            f"unsupported ConfFlow capability schema: schema {CAPABILITY_SCHEMA_VERSION} requires an artifacts block"
        )
    if capabilities.artifacts != EXPECTED_ARTIFACTS:
        raise ValueError(
            f"ConfFlow artifacts contract mismatch: expected {EXPECTED_ARTIFACTS}, got {capabilities.artifacts}"
        )
    if capabilities.commands is None:
        raise ValueError("ConfFlow capability schema requires a commands block")
    missing_commands = [name for name in REQUIRED_COMMANDS if capabilities.commands.get(name) is not True]
    if missing_commands:
        raise ValueError("ConfFlow missing commands: " + ", ".join(missing_commands))
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
