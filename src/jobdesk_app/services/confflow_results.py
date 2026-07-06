from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    latest ``workflow_stats.json``. ``current`` is the step that is running
    right now, if any. Both come from the workflow-stats tracker; when the
    file is missing or unparsable, ``completed`` is empty.
    """

    completed: tuple[str, ...] = ()
    current: str = ""
    last_updated: str = ""


def load_summary(path: Path) -> ConfFlowSummary:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ConfFlowSummary(
        initial_conformers=int(raw.get("initial_conformers", 0) or 0),
        final_conformers=int(raw.get("final_conformers", 0) or 0),
        total_duration_seconds=float(raw.get("total_duration_seconds", 0) or 0),
        step_status_counts=dict(raw.get("step_status_counts", {}) or {}),
        lowest_conformer=raw.get("lowest_conformer"),
    )


def load_step_progress(path: Path) -> ConfFlowStepProgress:
    """Parse ConfFlow's ``workflow_stats.json`` for per-step completion.

    ConfFlow writes one ``workflow_stats.json`` per molecule under
    ``{stem}_confflow_work/``. The shape (v1.0.10) is::

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
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name", "")).strip()
        status = str(step.get("status", "")).strip().lower()
        if not name:
            continue
        if status == "completed":
            completed.append(name)
        elif status == "running" and not current:
            current = name
    return ConfFlowStepProgress(
        completed=tuple(completed),
        current=current,
        last_updated=str(raw.get("last_updated", "")) if isinstance(raw, dict) else "",
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
    """One-line rendering suitable for the Runs page status column."""
    if not progress.completed and not progress.current:
        return ""
    done = ", ".join(progress.completed) if progress.completed else "(none)"
    if progress.current:
        return f"done: {done}; current: {progress.current}"
    return f"done: {done}"
