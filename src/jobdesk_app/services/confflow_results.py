from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Status values that indicate the run has reached a terminal state
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "skipped"})


@dataclass(frozen=True)
class ConfFlowSummary:
    initial_conformers: int
    final_conformers: int
    total_duration_seconds: float
    step_status_counts: dict[str, int] = field(default_factory=dict)
    lowest_conformer: dict[str, Any] | None = None


@dataclass(frozen=True)
class ConfFlowStepProgress:
    """Per-step progress snapshot for a single molecule.

    ``completed`` is the set of step names that finished according to the
    latest workflow stats or state file. ``current`` is the step that is
    running right now, if any.
    ``step_statuses`` maps step names to their current status strings.
    Both come from the workflow-stats tracker or state file; when the
    file is missing or unparsable, ``completed`` is empty.
    """

    completed: tuple[str, ...] = ()
    current: str = ""
    last_updated: str = ""
    step_statuses: dict[str, str] = field(default_factory=dict)
    final_status: str = ""


def load_summary(path: Path) -> ConfFlowSummary:
    if not path.exists():
        return ConfFlowSummary(
            initial_conformers=0,
            final_conformers=0,
            total_duration_seconds=0.0,
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ConfFlowSummary(
            initial_conformers=0,
            final_conformers=0,
            total_duration_seconds=0.0,
        )
    return ConfFlowSummary(
        initial_conformers=int(raw.get("initial_conformers", 0) or 0),
        final_conformers=int(raw.get("final_conformers", 0) or 0),
        total_duration_seconds=float(raw.get("total_duration_seconds", 0) or 0),
        step_status_counts=dict(raw.get("step_status_counts", {}) or {}),
        lowest_conformer=raw.get("lowest_conformer"),
    )


def load_step_progress(path: Path) -> ConfFlowStepProgress:
    """Parse ConfFlow's workflow stats file for per-step completion.

    ConfFlow writes one workflow stats file per molecule under the
    consumer-owned work directory.  The shape (v1.0.10) is::

        {
          "steps": [
            {"name": "confgen", "status": "completed", ...},
            {"name": "opt",      "status": "running",   ...}
          ],
          "last_updated": "2026-07-06T..."
        }

    Missing or malformed files yield an empty progress snapshot — callers
    decide whether to render that as "no progress yet" or to flag a parse
    error. We never raise.
    """
    if not path.exists():
        return ConfFlowStepProgress()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ConfFlowStepProgress()
    steps = raw.get("steps") if isinstance(raw, dict) else None
    if not isinstance(steps, list):
        return ConfFlowStepProgress(last_updated=str(raw.get("last_updated", "")) if isinstance(raw, dict) else "")
    completed: list[str] = []
    current = ""
    step_statuses: dict[str, str] = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name", "")).strip()
        status = str(step.get("status", "")).strip().lower()
        if not name:
            continue
        step_statuses[name] = status
        if status == "completed":
            completed.append(name)
        elif status == "running" and not current:
            current = name
    return ConfFlowStepProgress(
        completed=tuple(completed),
        current=current,
        last_updated=str(raw.get("last_updated", "")) if isinstance(raw, dict) else "",
        step_statuses=step_statuses,
    )


def load_workflow_state_progress(state_path: Path) -> ConfFlowStepProgress:
    """Parse ConfFlow v1.3.0's workflow state file for per-step progress.

    The v1.3.0 state file format is::

        {
          "run_id": "...",
          "work_dir": "...",
          "input_files": [...],
          "original_inputs": [...],
          "config_file": "...",
          "steps": {
            "step_01_confgen": {
              "name": "confgen",
              "type": "confgen",
              "status": "completed",
              "submitted_at": 1234567890.123,
              "completed_at": 1234567891.456,
              "output_xyz": "...",
              "error": null,
              "executor_handle_data": {...},
              "fail_count": 0
            },
            ...
          },
          "wavefront_index": 2,
          "started_at": 1234567890.0,
          "last_updated_at": 1234567900.0,
          "final_status": ""
        }

    This function extracts step statuses from the steps dict, identifying
    which step is currently running (status="submitted") and which have
    completed (status="completed"). The function gracefully handles:
    - Missing files
    - Malformed JSON
    - Incomplete/half-written files (partial steps dict)
    - Old state files that may be missing some fields

    Callers decide whether to render empty progress as "no progress yet"
    or to flag a parse error. We never raise.
    """
    if not state_path.exists():
        return ConfFlowStepProgress()

    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ConfFlowStepProgress()

    if not isinstance(raw, dict):
        return ConfFlowStepProgress()

    completed: list[str] = []
    current = ""
    step_statuses: dict[str, str] = {}
    final_status = str(raw.get("final_status", "") or "")

    steps_raw = raw.get("steps")
    if isinstance(steps_raw, dict):
        for step_name, step_data in steps_raw.items():
            if not isinstance(step_data, dict):
                continue
            # Use the "name" field if available, otherwise derive from dict key
            name = str(step_data.get("name", step_name)).strip()
            status = str(step_data.get("status", "pending")).strip().lower()
            if not name:
                continue
            step_statuses[name] = status
            if status == "completed":
                completed.append(name)
            elif status == "submitted" and not current:
                # A "submitted" step is currently running
                current = name

    # Format last_updated from timestamp if available
    last_updated = ""
    last_updated_at = raw.get("last_updated_at")
    if last_updated_at is not None:
        try:
            last_updated = datetime.fromtimestamp(float(last_updated_at)).isoformat()
        except (ValueError, OSError):
            pass

    return ConfFlowStepProgress(
        completed=tuple(completed),
        current=current,
        last_updated=last_updated,
        step_statuses=step_statuses,
        final_status=final_status,
    )


def format_summary(summary: ConfFlowSummary) -> str:
    lines = [
        "ConfFlow summary",
        f"Initial conformers: {summary.initial_conformers}",
        f"Final conformers: {summary.final_conformers}",
        f"Duration: {summary.total_duration_seconds:.1f} s",
    ]
    if summary.step_status_counts:
        status = ", ".join(f"{key}={value}" for key, value in summary.step_status_counts.items())
        lines.append(f"Steps: {status}")
    lowest = summary.lowest_conformer or {}
    cid = lowest.get("cid")
    energy = lowest.get("energy")
    if cid or energy is not None:
        lines.append(f"Lowest conformer: {cid or '-'}; energy={energy}")
    return "\n".join(lines)


def format_step_progress(progress: ConfFlowStepProgress) -> str:
    """One-line rendering suitable for the Runs page status column.

    Shows completed steps, current step, and terminal status when available.
    """
    if not progress.completed and not progress.current and not progress.final_status:
        return ""

    parts = []
    if progress.completed:
        done = ", ".join(progress.completed) if progress.completed else "(none)"
        parts.append(f"done: {done}")
    if progress.current:
        parts.append(f"current: {progress.current}")
    if progress.final_status:
        parts.append(f"status: {progress.final_status}")
    return "; ".join(parts)
