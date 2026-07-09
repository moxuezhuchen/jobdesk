"""Phase 9H-4 — real Gaussian 16 → JobDesk parser → ConFlow parser.

End-to-end, no mock.  Pytest-managed lifecycle: the ``real_g16_smoke_work_dir``
session fixture runs the smoke harness during setup and stores artifacts under
pytest's ``basetemp``.  The 6 assertions below mirror the Phase 9G suite
(``tests/test_confflow_real_g16_smoke.py``) but read from the fixture path so
they are hermetic — no hardcoded ``tmp60f7j8ix/...`` coupling.

Auto-skipped when bash (or WSL on Windows) is missing, or when
``/opt/g16/g16`` is not installed/licensed.

Run with::

    python -m pytest tests/integration/test_real_confflow_real_g16.py -v

The integration marker is opt-in (see ``pyproject.toml``::

    addopts = "-m 'not integration'"

so this suite does not run on a plain ``pytest tests/`` regression.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.integration.conftest import g16_smoke_prerequisites

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not g16_smoke_prerequisites()[0],
        reason=g16_smoke_prerequisites()[1] or "real-g16 smoke prerequisites missing",
    ),
]


def _require(work_dir: Path, name: str) -> Path:
    path = work_dir / name
    if not path.exists():
        pytest.fail(f"smoke artifact missing: {path}")
    return path


def test_g16_log_is_parseable_by_parse_gaussian_log(real_g16_smoke_work_dir):
    from jobdesk_app.core.parsers.gaussian import parse_gaussian_log

    log = _require(real_g16_smoke_work_dir, "g16_opt/backups/A000001.log")
    result = parse_gaussian_log(log)

    assert result.normal_termination is True
    assert result.error_termination is False
    assert result.error_message is None
    assert result.final_energy_au is not None
    # b3lyp/6-31g(d) methane opt: published benchmark ~ -40.5183 a.u.
    assert abs(result.final_energy_au - (-40.51838331)) < 1e-5, (
        f"final_energy_au off: {result.final_energy_au}"
    )
    assert result.atom_symbols == ["C", "H", "H", "H", "H"]
    assert result.final_xyz is not None
    assert len(result.final_xyz.splitlines()) == 5
    first = result.final_xyz.splitlines()[0].strip()
    assert first.startswith("C")


def test_g16_log_contains_optimization_completed_marker(real_g16_smoke_work_dir):
    log = _require(real_g16_smoke_work_dir, "g16_opt/backups/A000001.log")
    text = log.read_text(encoding="utf-8")
    assert "Optimization completed" in text
    assert "Stationary point found" in text


def test_g16_backups_directory_contains_expected_artifacts(real_g16_smoke_work_dir):
    backups = _require(real_g16_smoke_work_dir, "g16_opt/backups")
    names = {p.name for p in backups.iterdir()}
    assert "A000001.log" in names
    assert "A000001.gjf" in names
    assert "A000001.chk" in names
    assert "A000001.err" in names


def test_run_summary_loads_with_completed_step(real_g16_smoke_work_dir):
    from jobdesk_app.services.confflow_results import load_summary

    summary_path = _require(real_g16_smoke_work_dir, "run_summary.json")
    summary = load_summary(summary_path)

    assert summary.initial_conformers == 1
    assert summary.final_conformers == 1
    assert summary.lowest_conformer is not None
    assert summary.lowest_conformer.get("cid") == "A000001"
    assert abs(float(summary.lowest_conformer.get("energy", 0.0)) - (-40.51838331)) < 1e-5
    assert summary.step_status_counts.get("completed") == 1


def test_workflow_stats_records_completed_step_name(real_g16_smoke_work_dir):
    from jobdesk_app.services.confflow_results import load_step_progress

    stats_path = _require(real_g16_smoke_work_dir, "workflow_stats.json")
    progress = load_step_progress(stats_path)

    assert "g16_opt" in progress.completed


def test_run_summary_lowest_conformer_xyz_path_resolves_locally(real_g16_smoke_work_dir):
    """The lowest-conformer ``xyz_path`` on ``run_summary.json`` points back to
    the smoke tree; the analogous local ``g16_opt/output.xyz`` (what the GUI
    consumes via its task-dir heuristic) must exist after the pull."""
    summary_path = _require(real_g16_smoke_work_dir, "run_summary.json")
    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    lowest = raw.get("lowest_conformer") or {}
    remote_xyz = lowest.get("xyz_path")
    assert remote_xyz, "lowest_conformer.xyz_path missing"
    assert (_require(real_g16_smoke_work_dir, "g16_opt/output.xyz")).exists()
