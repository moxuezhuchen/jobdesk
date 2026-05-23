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


def load_summary(path: Path) -> ConfFlowSummary:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ConfFlowSummary(
        initial_conformers=int(raw.get("initial_conformers", 0) or 0),
        final_conformers=int(raw.get("final_conformers", 0) or 0),
        total_duration_seconds=float(raw.get("total_duration_seconds", 0) or 0),
        step_status_counts=dict(raw.get("step_status_counts", {}) or {}),
        lowest_conformer=raw.get("lowest_conformer"),
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
