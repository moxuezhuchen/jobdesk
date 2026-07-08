# Phase 9G: Real Gaussian 16 End-to-End Smoke

**Date**: 2026-07-08
**Status**: ✅ Real g16 smoke green; 6/6 JobDesk parser tests pass
**Tests**: 6 new (real g16, no mock)

Phase 9E deferred any plan that needed real Gaussian access. Phase 9G
unblocks the real-g16 path: it stands up an end-to-end pipeline against
the actual `/opt/g16/g16` install on the developer's WSL Ubuntu distro,
and verifies that JobDesk's existing parsers consume the real
artifacts correctly.

This is **not** a CI-friendly test — it requires a working Gaussian 16 +
license at `/opt/g16/g16`. The harness auto-skips when artifacts are
missing, so it stays in CI as a no-op on machines that have never run
the smoke.

---

## What Was Tested

End-to-end smoke of the JobDesk / ConfFlow / **real Gaussian 16**
integration on methane. The full chain:

```
methane.xyz (5 atoms)
   ↓  confflow
g16_opt step (iprog: g16, itask: opt, keyword: "opt b3lyp/6-31g(d)")
   ↓  /opt/g16/g16
A000001.log + A000001.chk + A000001.gjf + A000001.err
   ↓  confflow result assembly
g16_opt/output.xyz + g16_opt/result.xyz
   ↓  JobDesk parse_gaussian_log
GaussianResult dataclass (energy, geometry, normal_termination, ...)
   ↓  JobDesk load_summary / load_step_progress
ConfFlowSummary + ConfFlowStepProgress dataclasses
```

---

## Harness

**`scripts/smoke_confflow_real_g16_wsl.py`**

Mirrors the Phase 6 ORCA harness shape (`scripts/smoke_confflow_wsl.py`)
but targets real g16. Stamps a bash harness into WSL via base64, runs
it, and pulls artifacts back into `tmp60f7j8ix/phase9g_real_g16/`.

Run:

```bash
python scripts/smoke_confflow_real_g16_wsl.py
```

The harness:

1. Writes `methane.xyz` (5-atom Cartesian) into a unique `/tmp/confflow_phase9g_*` dir
2. Writes a ConFlow YAML targeting **real `/opt/g16/g16`** with `iprog: g16`, `itask: opt`, `keyword: "opt b3lyp/6-31g(d)"`
3. Exports `GAUSS_EXEDIR=/opt/g16/bsd:/opt/g16`, `PATH=/opt/gauopen:/opt/g16/bsd:/opt/g16:$PATH`, `GAUSS_SCRDIR=/opt/g16/scratch`
4. Runs `confflow methane.xyz -c confflow.yaml -w methane_confflow_work --resume --verbose`
5. Prints the result tree + `run_summary.json` + `workflow_stats.json` + `SCF Done` lines + `Normal termination` line
6. Pulls `methane_confflow_work/` back to `tmp60f7j8ix/phase9g_real_g16/` on Windows

**Note on `g16.profile`**: The harness intentionally does **not** `source
/opt/g16/bsd/g16.profile`. That script triggers `set -u` failures
because it dereferences `PERLLIB` without a default, which aborts under
the harness's `set -euo pipefail`. Setting the four env vars by hand
reproduces the same final state without that pitfall.

---

## YAML Used (Real g16)

```yaml
global:
  gaussian_path: /opt/g16/g16
  cores_per_task: 1
  total_memory: "1GB"
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: g16_opt
    type: calc
    params:
      iprog: g16
      itask: opt
      keyword: "opt b3lyp/6-31g(d)"
      cores_per_task: 1
      total_memory: "1GB"
      max_parallel_jobs: 1
```

The example YAML shipped with ConfFlow (`/opt/ConfFlow/confflow.example.yaml`)
documents two ways to spell Gaussian: `"gaussian"` or `"g16"`. Both work.
We use `"g16"`. g16 opt requires the geometry keyword (`opt` here) — confflow
otherwise emits an `.gjf` without `opt` and the resulting `.log` won't
have a `Standard orientation` block (no final geometry for `result.xyz`).

---

## Real Run Output

```
[smoke] g16 location:
/opt/g16/g16
-rwxr-x--- 1 ubuntu ubuntu 113454768 Feb 23  2022 /opt/g16/g16
-rwxr-x--- 1 ubuntu ubuntu  31221864 Feb 23  2022 /opt/g16/l1.exe

[smoke] starting confflow (real g16)
[smoke] confflow rc=0

[smoke] result tree:
    methane_confflow_work:
    .checkpoint
    confflow.log
    failed/                         # confflow --resume rotated an aborted yaml here, then succeeded
       confflow.yaml
    g16_opt/
       calc.log
       manifest.json
       output.xyz                   # last g16 geometry (confflow reads this)
       result.xyz                   # assembled per-conformer XYZ (from output.xyz)
       results.db
       backups/
          A000001.chk               # 749 KB Gaussian checkpoint
          A000001.err               # empty
          A000001.gjf               # 210 B Gaussian input
          A000001.log               # 37 KB Gaussian output
    run_summary.json
    workflow_stats.json

[smoke] g16 .log key lines:
 SCF Done:  E(RB3LYP) =  -40.5183502091     A.U. after    8 cycles
 SCF Done:  E(RB3LYP) =  -40.5183833088     A.U. after    6 cycles
 Optimization completed.
    -- Stationary point found.
 Normal termination of Gaussian 16 at Wed Jul  8 20:37:54 2026.
```

**Wall time**: ~4.4 s for methane opt (1 conformer, 1 step).

The `failed/` directory is a ConfFlow `--resume` artefact — when a
step aborts and then succeeds on resume, confflow leaves the failed
run's input YAML behind for forensics. It's harmless and intentional.

---

## JobDesk Parsers Verified

```
$ python -m pytest tests/test_confflow_real_g16_smoke.py -v
============================= test session starts =============================
collected 6 items

tests/test_confflow_real_g16_smoke.py::test_g16_log_is_parseable_by_parse_gaussian_log PASSED
tests/test_confflow_real_g16_smoke.py::test_g16_log_contains_optimization_completed_marker PASSED
tests/test_confflow_real_g16_smoke.py::test_g16_backups_directory_contains_expected_artifacts PASSED
tests/test_confflow_real_g16_smoke.py::test_run_summary_loads_with_completed_step PASSED
tests/test_confflow_real_g16_smoke.py::test_workflow_stats_records_completed_step_name PASSED
tests/test_confflow_real_g16_smoke.py::test_run_summary_lowest_conformer_xyz_path_resolves_locally PASSED

============================== 6 passed in 0.26s ==============================
```

What each test asserts:

| Test | Asserts |
|---|---|
| `test_g16_log_is_parseable_by_parse_gaussian_log` | `parse_gaussian_log` returns `normal_termination=True`, `final_energy_au ≈ -40.51838331` (matches confflow's `lowest_conformer.energy` to 1e-5), atoms `[C,H,H,H,H]`, geometry has 5 lines starting with C |
| `test_g16_log_contains_optimization_completed_marker` | `.log` contains both `Optimization completed` and `Stationary point found` |
| `test_g16_backups_directory_contains_expected_artifacts` | `backups/` has `.log`, `.gjf`, `.chk`, `.err` |
| `test_run_summary_loads_with_completed_step` | `load_summary` returns 1 conformer initial/final, lowest conformer `cid='A000001'` and energy matches |
| `test_workflow_stats_records_completed_step_name` | `load_step_progress` returns `completed=('g16_opt',)` |
| `test_run_summary_lowest_conformer_xyz_path_resolves_locally` | The `g16_opt/output.xyz` analogue that the GUI's task-dir heuristic consumes exists |

The smoke-run script (`scripts/smoke_confflow_real_g16_wsl.py`) and
the pytest suite are coupled by **path only**: the smoke writes to
`tmp60f7j8ix/phase9g_real_g16/...`, and the test re-discovers the same
tree. If the smoke has not been run on a checkout, every test
`pytest.skip()`s with a clear "run the smoke first" message — no CI
regression on machines without g16.

---

## Files Added / Changed

| File | Change |
|---|---|
| `scripts/smoke_confflow_real_g16_wsl.py` | New ~150-line harness; bash payload is stamped into WSL via base64 |
| `tests/test_confflow_real_g16_smoke.py` | New 6-test suite, all auto-skipped without artifacts |

No production code changes — this phase validates **existing** parser
behaviour against **real** Gaussian output. The parser classes
(`parse_gaussian_log`, `load_summary`, `load_step_progress`) all
behave identically against mock and real output.

---

## Cross-Cutting Notes

### Smoke harness layering

The smoke is structurally identical to `scripts/smoke_confflow_wsl.py`
(the Phase 6 ORCA version). They share:

- The base64-stamp-deployer dance to move a bash payload into WSL
- The `_b64` helper for binary-safe payload encoding
- The `parse_result_dir` / `pull_artifacts` tail of the workflow

The only substantive differences are:

1. YAML's `gaussian_path` + `iprog: g16` instead of `orca_path` + `iprog: orca`
2. Manual env-var export instead of `source /opt/g16/bsd/g16.profile` (see harness note above)
3. Inner timeout raised to 600 s (real g16 is slower than real ORCA's 5 s wallclock)

### Why this phase is not CI-blocking

A `pytest.skip` does not count as a passed test in `--strict` mode but
also doesn't fail. The suite ships as **documentation of what the real
pipeline looks like**, runnable on demand. When the developer has a
licensed g16 + has run the smoke once, the tests become load-bearing
guards against regressions in the parser contract.

### Why `g16_opt` not `g16_sp`

ConfFlow requires geometry output for `result.xyz` assembly. SP-only
g16 runs write the `Standard orientation` block to `.log`, but the
`opt` keyword makes g16 emit a final geometry block **and** a final
`Input orientation` block, which gives both the raw log and the
optimised geometry to the consumer. The smoke intentionally uses
`opt` so the parser sees both pre- and post-optimisation geometry.

### What's Next (Phase 9H candidates)

1. **TS smoke** — run the `itask: ts` path documented in
   `confflow.example.yaml` (line 149-164). Different parser surface
   (freq → imag-count, IRC follow-up).
2. **`chk_from_step` smoke** — two-step workflow where step 7 reads
   step 6's `.chk`. Tests confflow's chk-passing glue; does not
   exercise a new JobDesk parser.
3. **Reduce-orca-mock surface** — now that the g16 path is real,
   the only remaining mock in the wizard→run→parse loop is ORCA's
   back-end. Phase 9G proves the g16 half. Closing ORCA would
   require ORCA 6 install on the same WSL distro.
4. **Move smoke into `tests/integration/`** — formal pytest
   fixture that runs the smoke in `setup` and tears down in
   `teardown`, so the smoke is part of `pytest -m integration`.

Recommendation: **(4)** first — it closes the gap between
`scripts/` and `tests/integration/`, and is the only one of the four
that does not require a new feature surface to be useful.