"""Phase 9H-1 -- integration test: real Gaussian 16 TS -> JobDesk parser ->
confflow results parser.  End-to-end, no mock.

Skipped automatically when the artifacts from
``scripts/smoke_confflow_real_g16_ts_wsl.py`` are not present (i.e. the
smoke hasn't been run on this checkout).  The smoke itself runs in WSL
and requires a working Gaussian 16 + license at ``/opt/g16/g16``; on any
machine without it, this test is no-op.

Run the smoke first:

    python scripts/smoke_confflow_real_g16_ts_wsl.py

then run pytest.  Test functions share a single fixture that locates the
artifacts once.

The load-bearing assertion for the TS smoke is
``test_g16_ts_log_contains_exactly_one_imaginary_frequency``: a TS calc
must converge with exactly one imaginary frequency.  All other tests
mirror the Phase 9G methane suite and confirm the parser contract still
holds against a different job-type (itask: ts vs itask: opt).
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
OUTER = REPO / "tmp60f7j8ix" / "phase9h_ts"

CANDIDATE_ROOTS = [
    OUTER / "hcn_confflow_work",
    OUTER / "hcn_confflow_work" / "hcn_confflow_work",
]


def _locate_smoke_root() -> Path | None:
    for p in CANDIDATE_ROOTS:
        if (p / "g16_ts" / "backups" / "A000001.log").exists():
            return p
    return None


SMOKE = _locate_smoke_root()


def _require_artifact(name: str) -> Path:
    if SMOKE is None:
        pytest.skip(
            "Real-g16 TS smoke artifacts not present; run "
            "`python scripts/smoke_confflow_real_g16_ts_wsl.py` first"
        )
    path = SMOKE / name
    if not path.exists():
        pytest.skip(f"Smoke artifact missing: {path}")
    return path


def test_g16_ts_log_is_parseable_by_parse_gaussian_log():
    from jobdesk_app.core.parsers.gaussian import parse_gaussian_log

    log = _require_artifact("g16_ts/backups/A000001.log")
    result = parse_gaussian_log(log)

    assert result.normal_termination is True
    assert result.error_termination is False
    assert result.error_message is None
    assert result.final_energy_au is not None
    # 3 atoms: H, C, N
    assert result.atom_symbols == ["H", "C", "N"]
    assert result.final_xyz is not None
    assert len(result.final_xyz.splitlines()) == 3
    first = result.final_xyz.splitlines()[0].strip()
    assert first.startswith("H")


def test_g16_ts_log_contains_optimization_completed_marker():
    log = _require_artifact("g16_ts/backups/A000001.log")
    text = log.read_text(encoding="utf-8")
    # g16 TS convergence writes "Optimization completed." in the .log;
    # the Stationary point found marker is not used for TS runs, so we
    # only check the completion line and the imaginary-frequency line.
    assert "Optimization completed" in text


def test_g16_ts_log_contains_exactly_one_imaginary_frequency():
    """Load-bearing assertion: a TS calc must end with exactly one imag freq.

    g16's TS+``freq`` output writes the imag-freq count as the line

        ******    1 imaginary frequencies (negative Signs) ******

    near the end of the .log (right after the frequency block). The
    alternate ``Number of Imaginary Frequencies: 1`` summary line that
    some Gaussian versions print at the very end of a *standalone*
    ``freq`` job is NOT printed when ``opt=ts freq`` is run as a single
    keyword set (g16.RevC.02 here consolidates the summary into the
    asterisks line).  We assert on the asterisks-line format, which is
    what this g16 build actually emits.  The parser-based cross-check
    lives in ``test_g16_ts_imaginary_freq_count_is_one_via_parser``.
    """
    log = _require_artifact("g16_ts/backups/A000001.log")
    text = log.read_text(encoding="utf-8")
    assert "1 imaginary frequencies (negative Signs)" in text, (
        "TS .log must report exactly 1 imaginary frequency; got:\n"
        + "\n".join(
            line for line in text.splitlines()
            if "Imaginary Frequencies" in line or "imaginary" in line.lower()
        )
    )


def test_g16_ts_imaginary_freq_count_is_one_via_parser():
    """Cross-check: GaussianResult.imaginary_freq_count must equal 1."""
    from jobdesk_app.core.parsers.gaussian import parse_gaussian_log

    log = _require_artifact("g16_ts/backups/A000001.log")
    result = parse_gaussian_log(log)
    assert result.imaginary_freq_count == 1, (
        f"parser saw {result.imaginary_freq_count} imag freq(s); expected 1"
    )
    # And the lowest frequency (the only imag one) should be meaningfully
    # negative (g16 prints > 100 cm-1 for a real saddle).
    imag = [f for f in result.frequencies_cm1 if f < 0]
    assert imag, "parser found no negative frequencies at all"
    assert min(imag) < -100.0, (
        f"imaginary frequency {min(imag)} cm-1 too small; g16 likely "
        "converged to a minimum, not a saddle"
    )


def test_g16_ts_backups_directory_contains_expected_artifacts():
    backups = _require_artifact("g16_ts/backups")
    names = {p.name for p in backups.iterdir()}
    assert "A000001.log" in names
    assert "A000001.gjf" in names
    assert "A000001.chk" in names
    assert "A000001.err" in names


def test_g16_ts_run_summary_loads_with_completed_step():
    from jobdesk_app.services.confflow_results import load_summary

    summary_path = _require_artifact("run_summary.json")
    summary = load_summary(summary_path)

    assert summary.initial_conformers >= 1
    assert summary.final_conformers >= 0
    assert summary.step_status_counts.get("completed") == 1


def test_g16_ts_workflow_stats_records_completed_step_name():
    from jobdesk_app.services.confflow_results import load_step_progress

    stats_path = _require_artifact("workflow_stats.json")
    progress = load_step_progress(stats_path)

    assert "g16_ts" in progress.completed
