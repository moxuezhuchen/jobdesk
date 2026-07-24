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
must be a *mirror* of these tuples — never a free-floating literal.
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
    """JobDesk's expected shape of the ``artifacts`` block in the v2 payload.

    The three fields must round-trip exactly to the producer-side
    constants in ``confflow.contract``. Comparison is field-by-field
    structural equality, not name-only.
    """

    run_summary: str
    workflow_stats: str
    workflow_state: str


CAPABILITY_SCHEMA_VERSION: int = 2

# The three producer-side artifact names are mirrored here as module
# constants so JobDesk code can reference them by name without going
# through ``EXPECTED_ARTIFACTS.*``. The string values are the cross-
# repository contract.
RUN_SUMMARY_FILE: str = "run_summary.json"
WORKFLOW_STATS_FILE: str = "workflow_stats.json"
WORKFLOW_STATE_FILE: str = ".workflow_state.json"

EXPECTED_ARTIFACTS: ConfFlowArtifactContract = ConfFlowArtifactContract(
    run_summary=RUN_SUMMARY_FILE,
    workflow_stats=WORKFLOW_STATS_FILE,
    workflow_state=WORKFLOW_STATE_FILE,
)


# Structured version source of truth. Any change here must be mirrored
# into pyproject.toml's confflow pin, CI's checkout ref + wheel glob,
# docs, and the package's expected reference build.
MIN_VERSION: tuple[int, int, int] = (1, 4, 2)
MAX_EXCLUSIVE: tuple[int, int, int] = (2, 0, 0)


def _format_version_tuple(version: tuple[int, int, int]) -> str:
    """Render a 3-tuple as a PEP 440 short version.

    Trailing ``.0`` segments are stripped *except* the trailing one,
    so ``(2, 0, 0)`` renders as ``2.0`` (PEP 440 normal form) and
    ``(1, 4, 2)`` renders as ``1.4.2``. We never render a single
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

    Example: ``version_spec() == ">=1.4.2,<2.0"``.
    """
    return f">={_format_version_tuple(MIN_VERSION)},<{_format_version_tuple(MAX_EXCLUSIVE)}"
