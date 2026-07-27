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

Reference build artefact
------------------------
The ConfFlow v1.4.3 wheel that this revision of JobDesk (commit
``5167477`` on main) was integrated against has the following SHA-256::

    confflow-1.4.3-py3-none-any.whl
    sha256: 415875e294a454ffd6d6a12835f087f33c8b5731cec74cb5e36588036ff7671d
    commit: 7b37c223d2c07a062ab62965911c3cd8d6641591

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
    "CAPABILITY_SCHEMA_VERSION",
    "MIN_VERSION",
    "MAX_EXCLUSIVE",
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
    """JobDesk's expected shape of the ``artifacts`` block in the v3 payload.

    The five fields must round-trip exactly to the producer-side
    constants in ``confflow.contract``. Comparison is field-by-field
    structural equality, not name-only.
    """

    run_summary: str
    workflow_stats: str
    workflow_state: str
    run_report: str | None = None
    min_xyz: str | None = None


CAPABILITY_SCHEMA_VERSION: int = 3

# The three producer-side artifact names are mirrored here as module
# constants so JobDesk code can reference them by name without going
# through ``EXPECTED_ARTIFACTS.*``. The string values are the cross-
# repository contract.
RUN_SUMMARY_FILE: str = "run_summary.json"
WORKFLOW_STATS_FILE: str = "workflow_stats.json"
WORKFLOW_STATE_FILE: str = ".workflow_state.json"
RUN_REPORT_FILE: str = "{basename}.txt"
RUN_MIN_XYZ_TEMPLATE: str = "{basename}min.xyz"
REQUIRED_COMMANDS: tuple[str, ...] = ("bash", "nohup", "setsid", "xargs", "sha256sum", "mktemp", "base64")

EXPECTED_ARTIFACTS: ConfFlowArtifactContract = ConfFlowArtifactContract(
    run_summary=RUN_SUMMARY_FILE,
    workflow_stats=WORKFLOW_STATS_FILE,
    workflow_state=WORKFLOW_STATE_FILE,
    run_report=RUN_REPORT_FILE,
    min_xyz=RUN_MIN_XYZ_TEMPLATE,
)


# Structured version source of truth. Any change here must be mirrored
# into pyproject.toml's confflow pin, CI's checkout ref + wheel glob,
# docs, and the package's expected reference build.
MIN_VERSION: tuple[int, int, int] = (1, 4, 3)
MAX_EXCLUSIVE: tuple[int, int, int] = (2, 0, 0)


def _format_version_tuple(version: tuple[int, int, int]) -> str:
    """Render a 3-tuple as a PEP 440 short version.

    Trailing ``.0`` segments are stripped *except* the trailing one,
    so ``(2, 0, 0)`` renders as ``2.0`` (PEP 440 normal form) and
    ``(1, 4, 3)`` renders as ``1.4.3``. We never render a single
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

    Example: ``version_spec() == ">=1.4.3,<2.0"``.
    """
    return f">={_format_version_tuple(MIN_VERSION)},<{_format_version_tuple(MAX_EXCLUSIVE)}"
