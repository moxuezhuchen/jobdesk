"""Cross-run comparison and export utilities.

Collects results from multiple runs, computes relative energies,
and exports to CSV/TSV/Markdown.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

HARTREE_TO_KCAL = 627.5094740631


@dataclass
class RunComparison:
    """Aggregated comparison data across multiple runs."""
    rows: list[dict] = field(default_factory=list)
    field_names: list[str] = field(default_factory=list)


def compare_runs(
    workspace_dir: Path | str,
    run_ids: list[str],
    energy_field: str = "scf_energy",
    profile_name: str = "gaussian_opt_freq",
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
    from ..core.analyzer import analyze_tasks
    from .analysis_profiles import AnalysisProfileStore
    from .run_service import RunService

    workspace = Path(workspace_dir)
    svc = RunService(workspace)
    profile = AnalysisProfileStore().get(profile_name)
    if profile is None:
        return RunComparison()

    all_rows: list[dict] = []
    for run_id in run_ids:
        try:
            record = svc.load_run(run_id)
        except Exception:
            continue
        from ..core.manifest import Manifest
        tasks = Manifest.read(record.manifest_path)
        results, _ = analyze_tasks(profile.extract_rules, tasks, workspace / "results", run_id)
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
    energies = [row.get(energy_field) for row in all_rows if isinstance(row.get(energy_field), (int, float))]
    if energies:
        min_e = min(energies)
        for row in all_rows:
            e = row.get(energy_field)
            if isinstance(e, (int, float)):
                row[f"{energy_field}_rel_kcal"] = round((e - min_e) * HARTREE_TO_KCAL, 4)

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


def plot_energy_profile(
    comparison: RunComparison,
    energy_field: str = "scf_energy",
    output_path: Path | str | None = None,
) -> bool:
    """Plot a simple energy bar chart using PySide6 QtCharts.

    Returns True if plot was created, False if QtCharts is unavailable.
    """
    try:
        from PySide6.QtCharts import QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView, QValueAxis
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return False

    rel_field = f"{energy_field}_rel_kcal"
    labels = []
    values = []
    for row in comparison.rows:
        if rel_field in row:
            labels.append(f"{row.get('task_id', '')} ({row.get('run_id', '')[:8]})")
            values.append(float(row[rel_field]))

    if not values:
        return False

    app = QApplication.instance()
    if app is None:
        return False

    bar_set = QBarSet("ΔE (kcal/mol)")
    for v in values:
        bar_set.append(v)

    series = QBarSeries()
    series.append(bar_set)

    chart = QChart()
    chart.addSeries(series)
    chart.setTitle("Relative Energies")

    axis_x = QBarCategoryAxis()
    axis_x.append(labels)
    chart.addAxis(axis_x, Qt.AlignBottom)
    series.attachAxis(axis_x)

    axis_y = QValueAxis()
    axis_y.setTitleText("ΔE (kcal/mol)")
    chart.addAxis(axis_y, Qt.AlignLeft)
    series.attachAxis(axis_y)

    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    view.resize(800, 400)

    if output_path:
        pixmap = view.grab()
        pixmap.save(str(output_path))

    return True
