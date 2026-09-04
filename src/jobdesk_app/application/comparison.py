"""Cross-run comparison and export utilities.

Collects results from multiple runs, computes relative energies,
and exports to CSV/TSV/Markdown.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..core.analysis_schema import ExtractResult
from ..core.manifest import TaskRecord

HARTREE_TO_KCAL = 627.5094740631


@dataclass
class RunComparison:
    """Aggregated comparison data across multiple runs."""

    rows: list[dict] = field(default_factory=list)
    field_names: list[str] = field(default_factory=list)


class AnalysisProfileLike(Protocol):
    """Analysis rules required by comparison, independent of their store."""

    extract_rules: list[ExtractResult]


class RunComparisonRecord(Protocol):
    local_dir: str
    command_template: str


class RunComparisonSource(Protocol):
    """Read-only run/task source supplied by the composition root."""

    def load_run(self, run_id: str) -> RunComparisonRecord: ...

    def load_tasks(self, run_id: str) -> list[TaskRecord]: ...


def compare_runs(
    workspace_dir: Path | str,
    run_ids: list[str],
    energy_field: str = "scf_energy",
    profile_name: str = "gaussian_opt_freq",
    *,
    profile_loader: Callable[[str], AnalysisProfileLike | None],
    run_source_factory: Callable[[Path], RunComparisonSource],
) -> RunComparison:
    """Collect and compare results from multiple runs.

    For each run, loads downloaded results and extracts the named energy field.
    Computes relative energies in kcal/mol (lowest = 0).

    Args:
        workspace_dir: Local workspace directory.
        run_ids: List of run IDs to compare.
        energy_field: Name of the energy field to compare (from ExtractResult).
        profile_name: Analysis profile to use for extraction.

    Returns:
        RunComparison with rows sorted by energy.
    """
    if not run_ids:
        return RunComparison()

    from ..core.analyzer import analyze_tasks
    workspace = Path(workspace_dir)
    source = run_source_factory(workspace)
    profile = profile_loader(profile_name)
    if profile is None:
        return RunComparison()

    all_rows: list[dict] = []
    for run_id in run_ids:
        try:
            record = source.load_run(run_id)
        except Exception:
            continue
        tasks = source.load_tasks(run_id)
        result_workspace = Path(record.local_dir) if record.local_dir else workspace
        results, _ = analyze_tasks(profile.extract_rules, tasks, result_workspace / "results", run_id)
        # Group by task_id, collect all fields
        task_data: dict[str, dict] = {}
        for r in results:
            if r.task_id not in task_data:
                task_data[r.task_id] = {
                    "run_id": run_id,
                    "task_id": r.task_id,
                    "command": record.command_template,
                }
            task_data[r.task_id][r.field_name] = r.value
        all_rows.extend(task_data.values())

    if not all_rows:
        return RunComparison(rows=all_rows)

    # Compute relative energies
    energies = [
        float(value)
        for row in all_rows
        if isinstance((value := row.get(energy_field)), (int, float))
    ]
    if energies:
        min_e = min(energies)
        for row in all_rows:
            e = row.get(energy_field)
            if isinstance(e, (int, float)):
                row[f"{energy_field}_rel_kcal"] = round((float(e) - min_e) * HARTREE_TO_KCAL, 4)

    # Sort by energy
    all_rows.sort(key=lambda r: r.get(energy_field, float("inf")))

    # Collect all field names
    field_names: list[str] = []
    seen: set[str] = set()
    for row in all_rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                field_names.append(k)

    return RunComparison(rows=all_rows, field_names=field_names)


def export_csv(comparison: RunComparison, output_path: Path | str | None = None) -> str:
    """Export comparison to CSV. Returns CSV string; also writes to file if path given."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=comparison.field_names, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(comparison.rows)
    content = buf.getvalue()
    if output_path:
        Path(output_path).write_text(content, encoding="utf-8")
    return content


def export_markdown(comparison: RunComparison) -> str:
    """Export comparison as a Markdown table."""
    if not comparison.rows:
        return "(no data)"
    fields = comparison.field_names
    header = "| " + " | ".join(fields) + " |"
    sep = "| " + " | ".join("---" for _ in fields) + " |"
    rows = []
    for row in comparison.rows:
        cells = [str(row.get(f, "")) for f in fields]
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep] + rows)
