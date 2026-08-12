"""JobDesk's consumer-side view of the ConfFlow handshake contract.

This module is the **single owner** of the consumer-side constants JobDesk
uses to talk to ConfFlow. There are exactly two owners across the two
repositories:

* ConfFlow owns the producer-side artifact names and capability schema
  version (see ``confflow/contract.py`` inside the ConfFlow repo).
* JobDesk owns the **working-directory naming** it passes to ``-w`` and
  the **structured version window** it accepts from the producer.

The two owners are brought together through the CLI ``--capabilities
--json`` probe. JobDesk never Python-imports ConfFlow's contract module.

Structured version source of truth
----------------------------------
``MIN_VERSION`` and ``MAX_EXCLUSIVE`` are the structured tuple that
``version_spec()`` derives the human-readable spec from. Every other
surface (pyproject pin, CI wheel pin, README, validator error messages)
must be a *mirror* of these tuples; never a free-floating literal.
The historical v1.5.3 and v1.4.6 release records remain archived separately;
the Phase F owner exception removed the legacy backend from the production
path. Current control submission requires an exact approved release identity;
the current producer is v2.1.1 and the v2.0.0 identity remains available only
as the explicit rollback pairing.

Reference build artefact
------------------------
The current ConfFlow v2.1.1 wheel released from the clean tagged producer
commit has the following SHA-256::

    confflow-2.1.1-py3-none-any.whl
    sha256: 3425d97246ee6d37369ecce672dfa154643179cc3ee744eb332aee4b94dbc5f3
    commit: 338b53b3a34593271b926fc9e96010186141a386

The v2.0.0 release remains an explicit rollback identity::

    confflow-2.0.0-py3-none-any.whl
    sha256: 04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f
    commit: 69819350d340a6aeccf95aa175edfd1c3f63404b

The provenance is enforced by ``tests/test_confflow_wheel_build.py``
in CI, which asserts both the COMMIT and the DIRTY flag captured at
build time. If the wheel fingerprint changes, update the SHA above and
bump the reference commit.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "WORK_DIR_SUFFIX",
    "work_dir_name",
    "ConfFlowArtifactContract",
    "EXPECTED_ARTIFACTS",
    "RUN_SUMMARY_FILE",
    "WORKFLOW_STATS_FILE",
    "WORKFLOW_STATE_FILE",
    "OUTPUT_MANIFEST_FILE",
    "RUN_SUMMARY_SCHEMA",
    "WORKFLOW_STATS_SCHEMA",
    "WORKFLOW_STATE_SCHEMA",
    "OUTPUT_MANIFEST_SCHEMA",
    "CAPABILITY_SCHEMA_VERSION",
    "MIN_VERSION",
    "MAX_EXCLUSIVE",
    "REFERENCE_VERSION",
    "REFERENCE_BUILD_COMMIT",
    "REFERENCE_WHEEL_FILENAME",
    "REFERENCE_WHEEL_SHA256",
    "ROLLBACK_REFERENCE_VERSION",
    "ROLLBACK_REFERENCE_BUILD_COMMIT",
    "ROLLBACK_REFERENCE_WHEEL_FILENAME",
    "ROLLBACK_REFERENCE_WHEEL_SHA256",
    "LEGACY_REFERENCE_VERSION",
    "LEGACY_REFERENCE_BUILD_COMMIT",
    "LEGACY_REFERENCE_WHEEL_FILENAME",
    "LEGACY_REFERENCE_WHEEL_SHA256",
    "version_spec",
    "RUN_REPORT_FILE",
    "RUN_MIN_XYZ_TEMPLATE",
    "REQUIRED_COMMANDS",
]


WORK_DIR_SUFFIX: str = "_confflow_work"


def work_dir_name(stem: str) -> str:
    """Return the canonical ConfFlow working-directory name for ``stem``.

    The producer picks its own on-disk layout for the *contents* of the
    work directory; the consumer (JobDesk) owns the *name* of the
    directory it passes to ``-w``. Keeping this single-source prevents
    filename drift between the two repositories.
    """
    return f"{stem}{WORK_DIR_SUFFIX}"


@dataclass(frozen=True)
class ConfFlowArtifactContract:
    """JobDesk's expected shape of the ``artifacts`` block in the v4 payload.

    The six fields must round-trip exactly to the producer-side
    constants in ``confflow.contract``. Comparison is field-by-field
    structural equality, not name-only.
    """

    run_summary: str
    workflow_stats: str
    workflow_state: str
    output_manifest: str
    run_report: str | None = None
    min_xyz: str | None = None


CAPABILITY_SCHEMA_VERSION: int = 4

# The three producer-side artifact names are mirrored here as module
# constants so JobDesk code can reference them by name without going
# through ``EXPECTED_ARTIFACTS.*``. The string values are the cross-
# repository contract.
RUN_SUMMARY_FILE: str = "run_summary.json"
WORKFLOW_STATS_FILE: str = "workflow_stats.json"
WORKFLOW_STATE_FILE: str = ".workflow_state.json"
OUTPUT_MANIFEST_FILE: str = "output_manifest.json"
RUN_REPORT_FILE: str = "{basename}.txt"
RUN_MIN_XYZ_TEMPLATE: str = "{basename}min.xyz"
RUN_SUMMARY_SCHEMA: str = "confflow.run_summary.v1"
WORKFLOW_STATS_SCHEMA: str = "confflow.workflow_stats.v1"
WORKFLOW_STATE_SCHEMA: str = "confflow.workflow_state.v1"
OUTPUT_MANIFEST_SCHEMA: str = "confflow.output_manifest.v1"
REQUIRED_COMMANDS: tuple[str, ...] = ("bash", "nohup", "setsid", "xargs", "sha256sum", "mktemp", "base64")

EXPECTED_ARTIFACTS: ConfFlowArtifactContract = ConfFlowArtifactContract(
    run_summary=RUN_SUMMARY_FILE,
    workflow_stats=WORKFLOW_STATS_FILE,
    workflow_state=WORKFLOW_STATE_FILE,
    output_manifest=OUTPUT_MANIFEST_FILE,
    run_report=RUN_REPORT_FILE,
    min_xyz=RUN_MIN_XYZ_TEMPLATE,
)


# Structured version source of truth. Any change here must be mirrored
# into pyproject.toml's confflow pin, CI's checkout ref + wheel glob,
# docs, and the package's expected reference build.
MIN_VERSION: tuple[int, int, int] = (2, 0, 0)
MAX_EXCLUSIVE: tuple[int, int, int] = (3, 0, 0)
REFERENCE_VERSION: str = "2.1.1"
REFERENCE_BUILD_COMMIT: str = "338b53b3a34593271b926fc9e96010186141a386"
REFERENCE_WHEEL_FILENAME: str = "confflow-2.1.1-py3-none-any.whl"
REFERENCE_WHEEL_SHA256: str = "3425d97246ee6d37369ecce672dfa154643179cc3ee744eb332aee4b94dbc5f3"

# Keep the previously accepted producer pairing available for explicit
# rollback and stable-side compatibility checks. It is not the current
# reference used by release provenance tests.
ROLLBACK_REFERENCE_VERSION: str = "2.0.0"
ROLLBACK_REFERENCE_BUILD_COMMIT: str = "69819350d340a6aeccf95aa175edfd1c3f63404b"
ROLLBACK_REFERENCE_WHEEL_FILENAME: str = "confflow-2.0.0-py3-none-any.whl"
ROLLBACK_REFERENCE_WHEEL_SHA256: str = "04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f"

# Preserve the last legacy release metadata for historical/rollback evidence;
# it is not a current production compatibility path after Phase F.
LEGACY_REFERENCE_VERSION: str = "1.4.6"
LEGACY_REFERENCE_BUILD_COMMIT: str = "4e9e74a8991338aec0f393182073c8c087b4fa63"
LEGACY_REFERENCE_WHEEL_FILENAME: str = "confflow-1.4.6-py3-none-any.whl"
LEGACY_REFERENCE_WHEEL_SHA256: str = "7d036a44784d581b5b2fec2443f9cac7a0b2257d08b85c1a1b797bae565f75f5"


def _format_version_tuple(version: tuple[int, int, int]) -> str:
    """Render a 3-tuple as a PEP 440 short version.

    Trailing ``.0`` segments are stripped *except* the trailing one,
    so ``(2, 0, 0)`` renders as ``2.0`` (PEP 440 normal form) and
    ``(1, 5, 0)`` renders as ``1.5``. We never render a single
    major-only version because it would collapse e.g. ``(1, 4, 0)``
    into ``1`` which PEP 440 parses as ``1.0.0`` and round-trips
    silently.
    """
    major, minor, patch = version
    if patch == 0:
        return f"{major}.{minor}"
    return f"{major}.{minor}.{patch}"


def version_spec() -> str:
    """Return the human-readable PEP 440 spec derived from MIN/MAX.

    Example: ``version_spec() == ">=2.0,<3.0"``.
    """
    return f">={_format_version_tuple(MIN_VERSION)},<{_format_version_tuple(MAX_EXCLUSIVE)}"
