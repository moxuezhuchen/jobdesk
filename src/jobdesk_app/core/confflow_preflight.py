"""Pure parsing and compatibility checks for remote ConfFlow capabilities.

The validator is **fail closed**: every requirement must be satisfied for
the payload to be accepted. The check happens before any upload, dry-run,
or nohup so that an incompatible remote never gets a hand on JobDesk's
workload.

Schema v4 vs v1/v2/v3
---------------
The current contract (see :mod:`.confflow_contract`) requires ConfFlow
to emit a v4 payload including ``artifacts``, ``commands``, ``build``,
``producer``, and ``executable``.
Older payloads whose ``--capabilities --json`` output omits
``schema_version`` 4 are rejected outright — there is no negotiation.
The parser still tolerates missing optional blocks so the validator can
report the schema mismatch precisely instead of a malformed-JSON error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from packaging.version import InvalidVersion, Version

from .confflow_contract import (
    CAPABILITY_SCHEMA_VERSION,
    EXPECTED_ARTIFACTS,
    MAX_EXCLUSIVE,
    MIN_VERSION,
    REFERENCE_BUILD_COMMIT,
    REFERENCE_VERSION,
    REFERENCE_WHEEL_FILENAME,
    REFERENCE_WHEEL_SHA256,
    REQUIRED_COMMANDS,
    ConfFlowArtifactContract,
    version_spec,
)

PRERELEASE_AT_MIN_REJECT = True
PRERELEASE_ABOVE_MIN_ACCEPT = True

_ACCEPTED_VERSION_SPELLING_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:rc\d+|-rc\.\d+)?$"
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
    producer: dict[str, object] | None = field(default=None, compare=False)
    executable: dict[str, object] | None = field(default=None, compare=False)
    raw_payload: dict[str, object] | None = field(default=None, compare=False, repr=False)


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
    producer = _parse_producer(payload.get("producer"))
    executable = _parse_executable(payload.get("executable"))

    return ConfFlowCapabilities(
        schema_version=schema_version,
        version=version,
        workflow_state=parsed["workflow_state"],
        resume=parsed["resume"],
        dag=parsed["dag"],
        artifacts=artifacts,
        commands=commands,
        build=build,
        producer=producer,
        executable=executable,
        raw_payload=payload,
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


def _parse_producer(raw: object) -> dict[str, object] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("ConfFlow capability producer must be an object")
    package = raw.get("package")
    version = raw.get("version")
    build = raw.get("build")
    wheel = raw.get("wheel")
    if not isinstance(package, str) or not package:
        raise ValueError("ConfFlow capability producer.package must be a non-empty string")
    if not isinstance(version, str) or not version:
        raise ValueError("ConfFlow capability producer.version must be a non-empty string")
    if not isinstance(build, dict) or not isinstance(wheel, dict):
        raise ValueError("ConfFlow capability producer build and wheel must be objects")
    commit = build.get("commit")
    dirty = build.get("dirty")
    if commit is not None and not isinstance(commit, str):
        raise ValueError("ConfFlow capability producer.build.commit must be a string or null")
    if dirty is not None and type(dirty) is not bool:
        raise ValueError("ConfFlow capability producer.build.dirty must be boolean or null")
    for name in ("filename", "sha256"):
        value = wheel.get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"ConfFlow capability producer.wheel.{name} must be a string or null")
    parsed: dict[str, object] = {
        "package": package,
        "version": version,
        "build": {"commit": commit, "dirty": dirty},
        "wheel": dict(wheel),
    }
    if "install_provenance" in raw:
        install_provenance = raw.get("install_provenance")
        if not isinstance(install_provenance, dict):
            raise ValueError("ConfFlow capability producer.install_provenance must be an object")
        parsed["install_provenance"] = dict(install_provenance)
    return parsed


def _parse_executable(raw: object) -> dict[str, object] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("ConfFlow capability executable must be an object")
    for name in ("path", "realpath", "sha256", "python"):
        value = raw.get(name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"ConfFlow capability executable.{name} must be a string or null")
    for name in ("size", "mtime_ns", "device", "inode"):
        value = raw.get(name)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"ConfFlow capability executable.{name} must be a non-negative integer or null")
    return dict(raw)


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
    names = ("run_summary", "workflow_stats", "workflow_state", "output_manifest", "run_report", "min_xyz")
    if any(not isinstance(raw.get(name), str) for name in names):
        return None
    try:
        return ConfFlowArtifactContract(
            run_summary=raw["run_summary"],
            workflow_stats=raw["workflow_stats"],
            workflow_state=raw["workflow_state"],
            output_manifest=raw["output_manifest"],
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
    if capabilities.raw_payload is not None:
        if capabilities.producer is None:
            raise ValueError("ConfFlow capability schema requires a producer block")
        if capabilities.executable is None:
            raise ValueError("ConfFlow capability schema requires an executable block")
        if capabilities.producer.get("package") != "confflow":
            raise ValueError("ConfFlow capability producer.package must be 'confflow'")
        producer_version = capabilities.producer.get("version")
        if producer_version != capabilities.version:
            raise ValueError("ConfFlow capability producer.version must match version")
    version = _parse_version(capabilities.version)
    core = version.release
    prerelease = version.is_prerelease
    if core < MIN_VERSION or (PRERELEASE_AT_MIN_REJECT and core == MIN_VERSION and prerelease) or (core > MIN_VERSION and prerelease and not PRERELEASE_ABOVE_MIN_ACCEPT):
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


def validate_confflow_production_capability(
    capabilities: ConfFlowCapabilities,
    *,
    expected_executable: str | None = None,
) -> dict[str, object]:
    """Require the exact clean, attested Gate B producer identity.

    The ordinary validator checks the wire contract. Production submission
    additionally accepts only the exact wheel and clean producer commit that
    Gate B installed. Development/source/editable installs therefore fail
    closed instead of becoming a warning or a best-effort submission.

    Returns the executable identity fields used by the submitter's second
    remote probe and by the immutable runner guard.
    """
    validate_confflow_capabilities(capabilities, require_dag=False)
    payload = capabilities.raw_payload
    if not isinstance(payload, dict):
        raise ValueError("ConfFlow production capability requires raw provenance")
    if capabilities.version != REFERENCE_VERSION:
        raise ValueError("ConfFlow production version does not match the approved release")

    build = capabilities.build or {}
    if build.get("commit") != REFERENCE_BUILD_COMMIT:
        raise ValueError("ConfFlow production build commit does not match the approved release")
    if build.get("dirty") is not False:
        raise ValueError("ConfFlow production build must be clean")

    producer = payload.get("producer")
    if not isinstance(producer, dict):
        raise ValueError("ConfFlow production capability requires producer provenance")
    producer_build = producer.get("build")
    if not isinstance(producer_build, dict):
        raise ValueError("ConfFlow production capability requires producer build provenance")
    if producer_build.get("commit") != REFERENCE_BUILD_COMMIT:
        raise ValueError("ConfFlow producer build commit does not match the approved release")
    if producer_build.get("dirty") is not False:
        raise ValueError("ConfFlow producer build must be clean")

    wheel = producer.get("wheel")
    if not isinstance(wheel, dict):
        raise ValueError("ConfFlow production capability requires wheel provenance")
    if wheel.get("filename") != REFERENCE_WHEEL_FILENAME:
        raise ValueError("ConfFlow producer wheel filename does not match the approved release")
    if wheel.get("sha256") != REFERENCE_WHEEL_SHA256:
        raise ValueError("ConfFlow producer wheel digest does not match the approved release")

    install_provenance = payload.get("install_provenance")
    if not isinstance(install_provenance, dict):
        install_provenance = producer.get("install_provenance")
    if not isinstance(install_provenance, dict) or install_provenance.get("status") != "verified":
        raise ValueError("ConfFlow install provenance is not verified")

    executable = capabilities.executable
    if not isinstance(executable, dict):
        raise ValueError("ConfFlow production capability requires executable provenance")
    path = executable.get("path")
    digest = executable.get("sha256")
    python_executable = executable.get("python")
    if not isinstance(path, str) or not path or not path.startswith("/") or any(char in path for char in "\x00\r\n"):
        raise ValueError("ConfFlow executable path is missing")
    if expected_executable and path != expected_executable:
        raise ValueError("ConfFlow executable does not match the configured production path")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        raise ValueError("ConfFlow executable digest is missing or malformed")
    expected_python = f"{path.rsplit('/', 1)[0]}/python3.12"
    if (
        not isinstance(python_executable, str)
        or not python_executable.startswith("/")
        or any(char in python_executable for char in "\x00\r\n")
        or python_executable != expected_python
    ):
        raise ValueError("ConfFlow executable Python path does not match the controlled Python 3.12 virtual environment")

    identity: dict[str, object] = {
        "path": path,
        "realpath": executable.get("realpath") or path,
        "sha256": digest.lower(),
        "python": python_executable,
    }
    for name in ("size", "mtime_ns", "device", "inode"):
        value = executable.get(name)
        if value is not None:
            if type(value) is not int or value < 0:
                raise ValueError(f"ConfFlow executable.{name} must be a non-negative integer")
            identity[name] = value
    return identity


def _parse_version(value: str) -> Version:
    if not _ACCEPTED_VERSION_SPELLING_RE.fullmatch(value):
        raise ValueError(f"invalid ConfFlow semantic version: {value}")
    try:
        version = Version(value)
    except InvalidVersion as exc:
        raise ValueError(f"invalid ConfFlow semantic version: {value}") from exc
    if version.epoch or version.dev is not None or version.post is not None or version.local is not None:
        raise ValueError(f"invalid ConfFlow semantic version: {value}")
    return version


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value
