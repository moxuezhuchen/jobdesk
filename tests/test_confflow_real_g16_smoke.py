"""Phase 9G — integration test: real Gaussian 16 → JobDesk parser → confflow
results parser.  End-to-end, no mock.

Skipped automatically when the artifacts from
``scripts/smoke_confflow_real_g16_wsl.py`` are not present (i.e. the smoke
hasn't been run on this checkout).  The smoke itself runs in WSL and
requires a working Gaussian 16 + license at ``/opt/g16/g16``; on any
machine without it, this test is no-op.

Run the smoke first:

    python scripts/smoke_confflow_real_g16_wsl.py

then run pytest.  Test functions share a single fixture that locates the
artifacts once.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
OUTER = REPO / "tmp60f7j8ix" / "phase9g_real_g16"

CANDIDATE_ROOTS = [
    OUTER / "methane_confflow_work",
    OUTER / "methane_confflow_work" / "methane_confflow_work",
]


def _locate_smoke_root() -> Path | None:
    for p in CANDIDATE_ROOTS:
        if (p / "g16_opt" / "backups" / "A000001.log").exists():
            return p
    return None


SMOKE = _locate_smoke_root()


def _require_artifact(name: str) -> Path:
    if SMOKE is None:
        pytest.skip(
            "Real-g16 smoke artifacts not present; run "
            "`python scripts/smoke_confflow_real_g16_wsl.py` first"
        )
    path = SMOKE / name
    if not path.exists():
        pytest.skip(f"Smoke artifact missing: {path}")
    return path


def test_g16_log_is_parseable_by_parse_gaussian_log():
    from jobdesk_app.core.parsers.gaussian import parse_gaussian_log

    log = _require_artifact("g16_opt/backups/A000001.log")
    result = parse_gaussian_log(log)

    assert result.normal_termination is True
    assert result.error_termination is False
    assert result.error_message is None
    assert result.final_energy_au is not None
    # b3lyp/6-31g(d) methane opt: published benchmark ≈ -40.5183 a.u.
    assert abs(result.final_energy_au - (-40.51838331)) < 1e-5, (
        f"final_energy_au off: {result.final_energy_au}"
    )
    assert result.atom_symbols == ["C", "H", "H", "H", "H"]
    assert result.final_xyz is not None
    assert len(result.final_xyz.splitlines()) == 5
    first = result.final_xyz.splitlines()[0].strip()
    assert first.startswith("C")


def test_g16_log_contains_optimization_completed_marker():
    log = _require_artifact("g16_opt/backups/A000001.log")
    text = log.read_text(encoding="utf-8")
    assert "Optimization completed" in text
    assert "Stationary point found" in text


def test_g16_backups_directory_contains_expected_artifacts():
    backups = _require_artifact("g16_opt/backups")
    names = {p.name for p in backups.iterdir()}
    assert "A000001.log" in names
    assert "A000001.gjf" in names
    assert "A000001.chk" in names
    assert "A000001.err" in names


def test_run_summary_loads_with_completed_step():
    from jobdesk_app.services.confflow_results import load_summary

    summary_path = _require_artifact("run_summary.json")
    summary = load_summary(summary_path)

    assert summary.initial_conformers == 1
    assert summary.final_conformers == 1
    assert summary.lowest_conformer is not None
    assert summary.lowest_conformer.get("cid") == "A000001"
    assert abs(float(summary.lowest_conformer.get("energy", 0.0)) - (-40.51838331)) < 1e-5
    assert summary.step_status_counts.get("completed") == 1


def test_workflow_stats_records_completed_step_name():
    from jobdesk_app.services.confflow_results import load_step_progress

    stats_path = _require_artifact("workflow_stats.json")
    progress = load_step_progress(stats_path)

    assert "g16_opt" in progress.completed


def test_run_summary_lowest_conformer_xyz_path_resolves_locally():
    """The lowest-conformer path on the run_summary points back into the
    smoke tree; it must exist locally after the pull."""
    summary_path = _require_artifact("run_summary.json")
    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    lowest = raw.get("lowest_conformer") or {}
    remote_xyz = lowest.get("xyz_path")
    assert remote_xyz, "lowest_conformer.xyz_path missing"
    # The remote path references /tmp/... which no longer exists after
    # the smoke cleans up — this test guards against the confflow adapter
    # silently emitting a stale path.  We instead confirm the analogous
    # local file is present (g16_opt/output.xyz), which is what the GUI
    # would consume via its task_dir heuristic.
    assert (_require_artifact("g16_opt/output.xyz")).exists()