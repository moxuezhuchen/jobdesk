"""Phase 9H-2 — integration test: real Gaussian 16 chk-from-step pipeline.

Validates the two-step ConFlow workflow that passes a Gaussian checkpoint
from step_06_g16_opt (opt) to step_07_g16_sp_readchk (sp, guess=read).
Skipped automatically when the smoke artifacts are not present.

Run the smoke first::

    python scripts/smoke_confflow_real_g16_chk_wsl.py

then run pytest from the repo root::

    python -m pytest tests/test_confflow_real_g16_chk_smoke.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
OUTER = REPO / "tmp60f7j8ix" / "phase9h2_chk"

CANDIDATE_ROOTS = [
    OUTER / "methane_confflow_work",
    OUTER / "methane_confflow_work" / "methane_confflow_work",
]


def _locate_smoke_root() -> Path | None:
    for p in CANDIDATE_ROOTS:
        if (p / "step_06_g16_opt" / "backups" / "A000001.log").exists() and (
            p / "step_07_g16_sp_readchk" / "backups" / "A000001.log"
        ).exists():
            return p
    return None


SMOKE = _locate_smoke_root()


def _require_artifact(name: str) -> Path:
    if SMOKE is None:
        pytest.skip(
            "Real-g16 chk-from-step smoke artifacts not present; run "
            "`python scripts/smoke_confflow_real_g16_chk_wsl.py` first"
        )
    path = SMOKE / name
    if not path.exists():
        pytest.skip(f"Smoke artifact missing: {path}")
    return path


def test_step_06_opt_log_terminates_normally():
    log = _require_artifact("step_06_g16_opt/backups/A000001.log")
    text = log.read_text(encoding="utf-8")
    assert "Normal termination of Gaussian 16" in text, text[:200]
    assert "Optimization completed" in text
    assert "Stationary point found" in text


def test_step_07_sp_log_terminates_normally_and_reads_chk():
    """Step 7 must terminate normally AND show evidence it actually
    consumed step_6's checkpoint file."""
    log = _require_artifact("step_07_g16_sp_readchk/backups/A000001.log")
    text = log.read_text(encoding="utf-8")
    assert "Normal termination of Gaussian 16" in text, text[:200]
    assert "SCF Done:" in text, "no SCF Done line in step_07 .log"
    # Gaussian only emits these when it actually opened A000001.old.chk.
    # They are the load-bearing signals that `chk_from_step` worked.
    assert 'Copying data from "A000001.old.chk"' in text, (
        "step_07 .log does not show 'Copying data from A000001.old.chk'; the OldChk directive was not honoured"
    )
    assert "Structure from the checkpoint file" in text
    assert "Initial guess from the checkpoint file" in text


def test_step_07_log_geometry_matches_step_06_optimised_geometry():
    """The geometry step_07 reads from the chk must be step_06's OPTIMISED
    geometry (not the original input). After opt the C-H distance is
    ~1.0934 A; the input file has 0.629118 raw H positions."""
    log06 = _require_artifact("step_06_g16_opt/backups/A000001.log")
    log07 = _require_artifact("step_07_g16_sp_readchk/backups/A000001.log")
    text06 = log06.read_text(encoding="utf-8")
    text07 = log07.read_text(encoding="utf-8")

    import re

    # Atom row in Gaussian's 'Input orientation' / 'Standard orientation' table:
    #   '     1          6           0        0.000000    0.000000    0.000000'
    # Capture: index, atomic-number, x, y, z (columns 0, 1, 3, 4, 5).
    atom_re = re.compile(
        r"^\s+(\d+)\s+(\d+)\s+\d+\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$",
        re.MULTILINE,
    )

    def _last_block_xyz(text: str) -> list[tuple[int, float, float, float]]:
        # Each Standard/Input orientation block has 5 atom rows for methane.
        # Take the LAST 5 atom rows in the file -- that's the final geometry.
        rows = atom_re.findall(text)
        assert len(rows) >= 5, f"too few atom rows: {len(rows)}"
        last5 = rows[-5:]
        return [(int(z), float(x), float(y), float(z_)) for (idx, z, x, y, z_) in last5]

    atoms06 = _last_block_xyz(text06)
    atoms07 = _last_block_xyz(text07)
    assert len(atoms06) == 5 and len(atoms07) == 5
    # Compute C-H distances sorted (C is atom index 1).
    cx, cy, cz = atoms06[0][1], atoms06[0][2], atoms06[0][3]
    d06 = sorted(((ax - cx) ** 2 + (ay - cy) ** 2 + (az - cz) ** 2) ** 0.5 for (z, ax, ay, az) in atoms06[1:])
    cx, cy, cz = atoms07[0][1], atoms07[0][2], atoms07[0][3]
    d07 = sorted(((ax - cx) ** 2 + (ay - cy) ** 2 + (az - cz) ** 2) ** 0.5 for (z, ax, ay, az) in atoms07[1:])
    # 1e-4 A tolerance covers Gaussian's printf rounding between the two logs.
    for a, b in zip(d06, d07):
        assert abs(a - b) < 1e-4, f"C-H distance mismatch: step_06={d06} step_07={d07}"
    # Sanity: the C-H distance after opt is ~1.0934 A -- confirms we read the
    # optimised geometry, not the input geometry (H pos 0.629118 / sqrt(3)).
    assert all(1.05 < d < 1.15 for d in d06), f"step_06 C-H dist odd: {d06}"
    assert all(1.05 < d < 1.15 for d in d07), f"step_07 C-H dist odd: {d07}"


def test_step_07_log_file_has_nonzero_size():
    """Trivial but load-bearing: step_07 produced a real g16 .log."""
    log = _require_artifact("step_07_g16_sp_readchk/backups/A000001.log")
    assert log.stat().st_size > 1024, f"step_07 .log suspiciously small: {log.stat().st_size} bytes"


def test_step_06_emitted_chk_and_step_07_copied_it():
    """step_06 must have produced A000001.chk AND step_07 must have copied
    it as A000001.old.chk in the backups dir (the file confflow uses to
    satisfy the %OldChk directive)."""
    chk06 = _require_artifact("step_06_g16_opt/backups/A000001.chk")
    old_chk = _require_artifact("step_07_g16_sp_readchk/backups/A000001.old.chk")
    assert chk06.stat().st_size > 1024, "step_06 .chk is empty / truncated"
    assert old_chk.stat().st_size > 1024, "step_07 .old.chk is empty / truncated"
    # Sizes should match (it is a copy of step_06's chk).
    assert chk06.stat().st_size == old_chk.stat().st_size, (
        f"step_07 .old.chk size {old_chk.stat().st_size} != step_06 .chk size {chk06.stat().st_size}"
    )


def test_step_07_gjf_has_oldchk_directive():
    """The single most important assertion of this whole smoke: confflow
    must have written `%OldChk=A000001.old.chk` into step_07's .gjf. If
    this line is missing the sp step will silently start from a fresh
    guess instead of reading the prior wavefunction — defeats the whole
    point of chk_from_step."""
    gjf = _require_artifact("step_07_g16_sp_readchk/backups/A000001.gjf")
    text = gjf.read_text(encoding="utf-8")
    assert "%OldChk=A000001.old.chk" in text, f"step_07 .gjf missing %OldChk directive; contents:\n{text}"
    # And the chk itself must still be requested for this step.
    assert "%Chk=A000001.chk" in text


def test_run_summary_loads_with_two_completed_steps():
    from jobdesk_app.services.confflow_results import load_summary

    summary_path = _require_artifact("run_summary.json")
    summary = load_summary(summary_path)

    assert summary.initial_conformers >= 1
    assert summary.final_conformers >= 1
    counts = summary.step_status_counts
    assert counts.get("completed") == 2, f"expected step_status_counts['completed'] == 2, got {counts}"
    assert summary.lowest_conformer is not None
    # After sp-on-opt geometry, the wavefunction is read from chk so the
    # energy must differ from a fresh guess — assert it is finite/negative
    # (b3lyp/6-31g(d) methane SP-on-opt ≈ -39.7 a.u.).
    energy = float(summary.lowest_conformer.get("energy", 0.0))
    assert -42.0 < energy < -38.0, f"sp energy out of band: {energy}"


def test_workflow_stats_records_both_step_names():
    from jobdesk_app.services.confflow_results import load_step_progress

    stats_path = _require_artifact("workflow_stats.json")
    progress = load_step_progress(stats_path)

    completed = set(progress.completed)
    assert "step_06_g16_opt" in completed, completed
    assert "step_07_g16_sp_readchk" in completed, completed


def test_final_output_points_into_step_07():
    """run_summary.final_output must point at step_07's output.xyz, not
    step_06's — the GUI consumes this for the last-step snapshot."""
    summary_path = _require_artifact("run_summary.json")
    raw = json.loads(summary_path.read_text(encoding="utf-8"))
    final = raw.get("final_output", "")
    assert "step_07_g16_sp_readchk" in final, f"final_output should point at step_07, got: {final}"
    # And the analogous local file must be present after the pull.
    assert (_require_artifact("step_07_g16_sp_readchk/output.xyz")).exists()
