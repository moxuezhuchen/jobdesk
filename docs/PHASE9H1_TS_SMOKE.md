# Phase 9H-1: Real Gaussian 16 TS Smoke (HCN → HNC Saddle)

**Date**: 2026-07-08
**Status**: ✅ Real g16 TS smoke green; 7/7 new tests pass; 1 imaginary frequency recovered
**Tests**: 7 new (real g16 transition-state, no mock, imag-freq parser surface)
**Smoke wall time**: 16.4 s (HCN, 7 opt steps + 1 freq restart)

Phase 9G stood up the single-step real-g16 path. Phase 9H-2 extended it to
`chk_from_step` glue for two-step pipelines. **This phase — 9H-1 — extends
it to `itask: ts`**, the transition-state search. The load-bearing
assertion is that the `.log` contains exactly one imaginary frequency (the
TS marker), and that the `parse_gaussian_log` parser correctly recovers
the imag-freq count from the asterisks-line format that this g16 build
emits (`******  1 imaginary frequencies (negative Signs) ******`).

This phase is **not CI-friendly** — it requires a working Gaussian 16 +
license at `/opt/g16/g16`. The pytest suite auto-skips when artifacts are
missing, so CI on machines without g16 is a no-op.

---

## What Was Tested

End-to-end smoke of a real ConfFlow single-step Gaussian 16 TS pipeline,
exercising the imaginary-frequency parser surface (a different code path
from the opt+sp smokes of 9G/9H-2):

```
hcn.xyz (3 atoms, bent H-C-N at 70°, H between C and N)
  ↓  confflow step g16_ts
  iprog: g16, itask: ts
  keyword: "opt=(ts,calcfc,noeigen,maxcycles=50) b3lyp/6-31g(d) freq"
  ts_bond_atoms: [1, 3]
  ts_rescue_scan: false
  ↓  /opt/g16/g16 → A000001.chk (749 KB) + A000001.log
  ↓  7 opt steps → SCF converges at -93.3429 a.u.
  ↓  1 freq restart (Geom=AllCheck Guess=TCheck) → 1 imaginary freq at -1145.6 cm-1
  ↓  confflow result assembly
g16_ts/output.xyz + result.xyz
  ↓  JobDesk parse_gaussian_log + load_summary + load_step_progress
ConfFlowSummary + ConfFlowStepProgress
```

The chain is exercised against the **real** `/opt/g16/g16` binary; no
mock of any step. The g16 `.log` shows the load-bearing markers proving
the TS convergence actually happened:

```
SCF Done:  E(RB3LYP) =  -93.3428982973     A.U. after    8 cycles
...
Optimization completed.
   -- Stationary point found.
Normal termination of Gaussian 16 at Wed Jul  8 21:57:02 2026.
Link1:  Proceeding to internal job step number  2.
...
Harmonic frequencies (cm**-1) ...
Frequencies --  -1145.6022              2066.8696              2596.2721
******    1 imaginary frequencies (negative Signs) ******
   1 imaginary frequencies ignored.
Sum of electronic and thermal Free Energies=          -93.353280
...
Normal termination of Gaussian 16 at Wed Jul  8 21:57:05 2026.
```

If the TS search had wandered into a minimum (e.g. linear HCN with H at
one end, a common starting-geometry trap), the `.log` would either
report `0 imaginary frequencies` (clean minimum) or the TS optimizer
would hit `Maximum step size exceeded` and bail out. The single
negative eigenvalue at -1145.6 cm⁻¹ (the H-C-N bending mode) is the
load-bearing marker of a true first-order saddle.

---

## The Starting-Geometry Issue (and Why It Matters)

The HCN → HNC transition state at b3lyp/6-31g(d) is **bent, not
colinear**: the migrating H sits off the C-N axis, with R(H-C) ≈ 1.20
Å, R(C-N) ≈ 1.19 Å, and the H-C-N angle near 70°. A colinear H-C-N
starting guess (H at one end, C in the middle, N at the other end) is a
**minimum** in the redundant internal coordinates, so the TS optimizer
walks H *away* from C in the wrong direction for all 20 default
maxcycles and never reaches the saddle. Starting with a bent H-C-N
geometry breaks the linear symmetry and lets the optimizer find the
true saddle in ≤10 steps.

The smoke's starting XYZ is therefore:

```
H   0.000000   0.000000   0.000000
C   1.200000   0.000000   0.000000
N   1.610414   1.127446   0.000000
```

which has H-C = 1.20 Å, C-N = 1.20 Å, and H-C-N = 70°. The optimizer
takes 7 steps to converge (R1, R2, A1 all reach their saddle-point
values within 1e-3 of the converged answer) and then the freq step
restarts from the chk to compute the Hessian. The total wall time is
16.4 s for the entire job.

The keyword set is `opt=(ts,calcfc,noeigen,maxcycles=50) b3lyp/6-31g(d)
freq`:
- `opt=ts` — saddle-point search (Berny)
- `calcfc` — compute the analytic Hessian at every step (no read-in
  from chk; cheaper than `calcall` for 3-atom systems)
- `noeigen` — print only the lowest eigenvalue of the Hessian (skip the
  full eigenvalue spectrum dump)
- `maxcycles=50` — generous cap (the smoke converges in 7; the cap
  exists so that a future starting-geometry regression can't silently
  exhaust the default 20)
- `freq` — analytical frequency calculation, restarted from the
  converged chk (Gaussian's internal Link1→Link2 transition)

`ts_bond_atoms: [1, 3]` and `ts_rescue_scan: false` are the
ConFlow-side glue. `ts_bond_atoms` tells ConFlow which atom pair to
build the "reactant / product" bond table around; we point at H-N
(atom 1 and 3 in the input order, since H is index 1 in the input
XYZ), and ConFlow uses this to populate the `%OldChk=` directive (none
in this case — single-step smoke) and to label the TS in the result
manifest.

---

## Harness

**`scripts/smoke_confflow_real_g16_ts_wsl.py`** (222 lines)

Mirrors `scripts/smoke_confflow_real_g16_wsl.py` from Phase 9G with two
substantive differences:

1. The stamped bash payload contains a **single-step** ConFlow YAML
   with `itask: ts` and the `opt=(ts,calcfc,noeigen,maxcycles=50)
   b3lyp/6-31g(d) freq` keyword set.
2. The smoke prints the contents of the `g16_ts` step's `backups/`
   (must contain `A000001.chk` and `A000001.log`), the
   `Optimization completed.` and `Stationary point found.` lines from
   the `.log`, and the `Number of Imaginary Frequencies` / `Low
   frequencies` lines from the freq restart so the TS convergence is
   observable from the harness output alone.

Same WSL g16 environment setup as 9G:

```bash
export g16root=/opt
export GAUSS_EXEDIR=/opt/g16/bsd:/opt/g16
export PATH=/opt/g16/bsd:/opt/g16:$PATH    # NO /opt/gauopen -- that path does not exist
export GAUSS_SCRDIR=/opt/g16/scratch
# Skip source /opt/g16/bsd/g16.profile -- it triggers 'set -u' PERLLIB unbound
# errors when sourced under `set -euo pipefail`.
```

Run from the repo root:

```bash
python scripts/smoke_confflow_real_g16_ts_wsl.py
```

The smoke writes its workdir to `/tmp/confflow_phase9h1_${BASHPID}` in
WSL, then pulls the `hcn_confflow_work/` tree back to
`tmp60f7j8ix/phase9h_ts/` on Windows. Idempotent — the smoke cleans up
its WSL staging dir after the pull.

The `wslpath -w` translation sometimes double-nests the artifact tree
on Windows (the smoke ends up writing to
`tmp60f7j8ix/phase9h_ts/hcn_confflow_work/hcn_confflow_work/...` rather
than the single-level form), so the test file's `_locate_smoke_root()`
helper enumerates both candidate roots. This is the same
double-nesting quirk that 9G documented in 9H-4.

---

## YAML Used (Real g16, single-step TS)

```yaml
global:
  gaussian_path: /opt/g16/g16
  cores_per_task: 1
  total_memory: "1GB"
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: g16_ts
    type: calc
    params:
      iprog: g16
      itask: ts
      keyword: "opt=(ts,calcfc,noeigen,maxcycles=50) b3lyp/6-31g(d) freq"
      ts_bond_atoms: [1, 3]
      ts_rescue_scan: false
      cores_per_task: 1
      total_memory: "1GB"
      max_parallel_jobs: 1
```

The exact spelling was confirmed by reading the ConFlow source:

- `itask: ts` is the canonical task name. ConFlow maps it to a TS-specific
  policy that emits the right `%OldChk=` / `%Chk=` lines and builds the
  TS result manifest. See
  `/opt/ConfFlow/confflow/workflow/step_handlers.py` and
  `/opt/ConfFlow/confflow/core/types.py`.
- `ts_bond_atoms` is a 1-based atom-index pair that ConFlow uses to
  label the bond table for the TS. The smoke uses `[1, 3]` (H–N, the
  forming bond) — this is conventional for the HCN→HNC migration.
- `ts_rescue_scan: false` opts out of ConFlow's automatic PES-scan
  fallback if the TS search fails; the smoke relies on the bent
  starting geometry to converge in <10 steps, so the rescue scan is
  unnecessary noise.

---

## Real Run Output (excerpt)

```
[smoke] g16 location:
/opt/g16/g16
-rwxr-x--- 1 ubuntu ubuntu 113454768 Feb 23  2022 /opt/g16/g16
-rwxr-x--- 1 ubuntu ubuntu  31221864 Feb 23  2022 /opt/g16/l1.exe

[smoke] starting confflow (real g16, HCN TS)
[smoke] confflow rc=0

[smoke] g16 backups:
total 832
-rw-r--r-- 1 root root 749568  A000001.chk
-rw-r--r-- 1 root root      0  A000001.err
-rw-r--r-- 1 root root    164  A000001.gjf
-rw-r--r-- 1 root root 88812  A000001.log

[smoke] g16 .log key lines (TS markers):
 SCF Done:  E(RB3LYP) =  -93.3703344213     A.U. after   12 cycles
 SCF Done:  E(RB3LYP) =  -93.3612235664     A.U. after   11 cycles
 SCF Done:  E(RB3LYP) =  -93.3457180549     A.U. after   13 cycles
 SCF Done:  E(RB3LYP) =  -93.3436500672     A.U. after   13 cycles
 SCF Done:  E(RB3LYP) =  -93.3428139018     A.U. after   12 cycles
 SCF Done:  E(RB3LYP) =  -93.3428978346     A.U. after    9 cycles
 SCF Done:  E(RB3LYP) =  -93.3428982973     A.U. after    8 cycles
 Optimization completed.
    -- Stationary point found.
 Normal termination of Gaussian 16 at Wed Jul  8 21:57:02 2026.
 SCF Done:  E(RB3LYP) =  -93.3428982973     A.U. after    1 cycles
 Optimization completed.
    -- Stationary point found.
 Normal termination of Gaussian 16 at Wed Jul  8 21:57:05 2026.
```

The 7 SCF entries are the **opt** loop, with the energy monotonic from
-93.37 → -93.343 a.u. (the saddle-point E is roughly 0.027 a.u. = 17
kcal/mol above the linear HCN minimum at this level of theory). The
final SCF Done + Normal termination at 21:57:05 is the **freq** restart
(Geom=AllCheck, Guess=TCheck) which produced the imaginary-frequency
line.

`run_summary.json` shows the step completed:

```json
{
  "initial_conformers": 1,
  "final_conformers": 1,
  "total_duration_seconds": 16.39,
  "step_status_counts": {"completed": 1},
  "lowest_conformer": {"cid": "A000001", "energy": -93.35328, ...}
}
```

Total wall time: **16.4 s** (well under the 60 s budget that the 9G
smoke established for a single-step real-g16 job).

---

## JobDesk Parsers Verified

```
$ python -m pytest tests/test_confflow_real_g16_ts_smoke.py -v
============================= test session starts =============================
collected 7 items

tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_log_is_parseable_by_parse_gaussian_log PASSED [ 14%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_log_contains_optimization_completed_marker PASSED [ 28%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_log_contains_exactly_one_imaginary_frequency PASSED [ 42%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_imaginary_freq_count_is_one_via_parser PASSED [ 57%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_backups_directory_contains_expected_artifacts PASSED [ 71%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_run_summary_loads_with_completed_step PASSED [ 85%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_workflow_stats_records_completed_step_name PASSED [100%]

============================== 7 passed in 0.12s ==============================
```

What each test asserts:

| Test | Asserts |
|---|---|
| `test_g16_ts_log_is_parseable_by_parse_gaussian_log` | `parse_gaussian_log` returns `normal_termination=True`, `error_termination=False`, `error_message=None`, `final_energy_au` is not None, atom symbols = `[H, C, N]`, and `final_xyz` parses back to 3 lines starting with H |
| `test_g16_ts_log_contains_optimization_completed_marker` | the `.log` contains `Optimization completed` (the TS opt marker; `Stationary point found.` is also present but not asserted since g16.RevC.02 prints it as a sub-line of `Optimization completed.`) |
| `test_g16_ts_log_contains_exactly_one_imaginary_frequency` | **load-bearing**. The `.log` contains the exact string `1 imaginary frequencies (negative Signs)` — g16's `opt=ts freq` consolidated-summary line. Cross-checks via the parser in the next test. |
| `test_g16_ts_imaginary_freq_count_is_one_via_parser` | **load-bearing cross-check**. `parse_gaussian_log(...).imaginary_freq_count == 1`, and the lowest frequency (the only imag one) is more negative than -100 cm⁻¹ (a real saddle, not a near-minimum the optimizer mislabeled) |
| `test_g16_ts_backups_directory_contains_expected_artifacts` | the `g16_ts/backups/` directory contains `A000001.log`, `A000001.gjf`, `A000001.chk`, and `A000001.err` |
| `test_g16_ts_run_summary_loads_with_completed_step` | `load_summary` reports `step_status_counts['completed'] == 1`, `initial_conformers >= 1`, `final_conformers >= 0` |
| `test_g16_ts_workflow_stats_records_completed_step_name` | `load_step_progress` reports `g16_ts` in `completed` |

The harness and the test suite are coupled by **path only**: the smoke
writes to `tmp60f7j8ix/phase9h_ts/hcn_confflow_work/...`, and the test
re-discovers the tree (with double-nesting tolerance). If the smoke
has not been run on a checkout, every test `pytest.skip()`s with a clear
"run the smoke first" message.

---

## Files Added / Changed

| File | Lines | Change |
|---|---|---|
| `scripts/smoke_confflow_real_g16_ts_wsl.py` | 222 | New harness; bash payload is stamped into WSL via base64; YAML is single-step `itask: ts` with the `opt=(ts,calcfc,noeigen,maxcycles=50) b3lyp/6-31g(d) freq` keyword set and a bent H-C-N starting geometry |
| `tests/test_confflow_real_g16_ts_smoke.py` | 158 | New 7-test suite; auto-skipped when artifacts missing |

No production code changes — this phase validates **existing** parser
behaviour against a **new** workflow shape. None of
`parse_gaussian_log`, `load_summary`, or `load_step_progress` need to
be touched to consume the TS result tree.

The `scripts/_debug_phase9h1.py` file that an earlier subagent's
quota-killed run left on disk (a one-off debug helper with the wrong
linear HCN starting geometry) was removed during the smoke bring-up;
it is not part of the deliverables.

---

## Cross-Cutting Notes

### Why the load-bearing assertion is on the asterisks-line format

g16.RevC.02 (the build on this WSL install) emits a single consolidated
imag-freq line near the end of a `opt=ts freq` job:

```
 ******    1 imaginary frequencies (negative Signs) ******
```

…**not** the older `Number of Imaginary Frequencies: 1` summary that
some Gaussian versions print at the very end of a standalone `freq`
job. The g16.RevC.02 behaviour is to consolidate the summary into the
asterisks line because `opt=ts freq` runs as a single keyword set
(Link1 + Link2). The test asserts on the asterisks-line format, which
is what this g16 build actually emits. The cross-check via
`parse_gaussian_log(...).imaginary_freq_count` validates that the
parser is reading the same line the test reads.

This is the kind of assertion that catches silent regressions: if a
future g16 build moves the summary to a different line format, the test
will fail and the maintainer will see the exact `grep` output of
imag-related lines in the error message. Without this assertion, a TS
calc that "converged" to a minimum (zero imag freqs) would silently
look like a successful TS to the user.

### Why the parser-based cross-check matters

`test_g16_ts_imaginary_freq_count_is_one_via_parser` reads the same
`.log` but goes through the structured `GaussianResult` object rather
than the literal-string match. It asserts two things:

1. `result.imaginary_freq_count == 1` — the parser's field-level
   abstraction agrees with the literal-string check. If the parser
   ever drifts from the literal format (e.g. if someone "improves" the
   regex to be smarter and breaks it), this test fails.
2. The lowest frequency is **meaningfully** negative (`< -100 cm⁻¹`).
   g16 prints > 100 cm⁻¹ for a real saddle; a value closer to zero
   (say, -30 cm⁻¹) means the optimizer converged to a near-minimum
   (i.e. it's a spurious saddle that g16 still labels `Stationary
   point found.` because the gradient is below threshold). The
   -100 cm⁻¹ bound is a **safety floor** that catches this silent
   regression class.

### Why the bent starting geometry is the load-bearing input

A colinear H-C-N starting guess (H at one end, C in the middle, N at
the other end) is a **minimum** in the redundant internal coordinates
that g16 uses for the TS search. The optimizer then walks H *away* from
C in the wrong direction for all 20 default maxcycles and never reaches
the saddle. The bent starting geometry (H at one end, C 1.20 Å away,
N 1.20 Å away at a 70° H-C-N angle) breaks the linear symmetry and
puts the optimizer in the correct basin of attraction.

This is a real-world foot-gun: a ConfFlow user who writes the obvious
HCN XYZ in the smoke gets a `Stationary point found.` at the linear
minimum, which is **not** the TS they wanted. The smoke encodes the
bent starting geometry as the right answer for this canonical test
case, so the assertion that the `.log` shows one imag freq at
-1145.6 cm⁻¹ is end-to-end validated against a real g16 convergence.

### What's Next (Phase 9I candidates)

1. **TS → IRC chk-passing smoke** — combine 9H-1 (TS smoke) + 9H-2
   (`chk_from_step` smoke) into a single two-step workflow: TS first,
   then IRC reading the chk. This is the canonical ConfFlow example at
   `confflow.example.yaml:147-164` and was the original motivation for
   the `chk_from_step` feature. Validates the imag-direction handling
   (g16 writes an `irc=` keyword that ConFlow translates to a special
   step manifest).
2. **Move TS smoke into `tests/integration/`** — same migration as
   9H-4 did for 9G. The Phase 9H-4 infrastructure (the importlib
   wrapper, the conftest fixture, the prereq probe) is reusable; a TS
   smoke would add `_real_g16_ts_smoke.py` + a session-scoped fixture
   + 2-3 assertions.
3. **TS at a different basis** — b3lyp/6-31g(d) is the easy case (the
   Hessian is well-behaved at this level). B3LYP/6-311++G(2d,p) would
   exercise the parser against a larger imag-freq count for the
   numerical noise sensitivity (smaller imag freqs are closer to the
   -100 cm⁻¹ floor and may not converge cleanly).
4. **QST2/QST3 TS** — these keywords take two or three starting
   geometries and the optimizer interpolates between them. Different
   parser surface (multi-geometry .gjf, different output blocks). Lower
   priority because QST is rarely used in production workflows.

**Recommendation**: **(1) first** — the TS→IRC chk-passing workflow is
exactly the example ConFlow ships in `confflow.example.yaml:147-164`
and is the highest-value unexercised workflow after this phase. The
infrastructure from 9H-2 (`chk_from_step` assertions) and 9H-1 (TS
parser surface) is now in place; combining them is a 1-day exercise.
**(2) second** — same pattern as 9H-4, pure migration. **(3)** and
**(4)** are interesting but lower priority.

---

## Final Verification

### 9H-1 pytest (real g16 TS, no mock)

```
$ python -m pytest tests/test_confflow_real_g16_ts_smoke.py -v
============================= test session starts =============================
collected 7 items

tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_log_is_parseable_by_parse_gaussian_log PASSED [ 14%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_log_contains_optimization_completed_marker PASSED [ 28%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_log_contains_exactly_one_imaginary_frequency PASSED [ 42%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_imaginary_freq_count_is_one_via_parser PASSED [ 57%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_backups_directory_contains_expected_artifacts PASSED [ 71%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_run_summary_loads_with_completed_step PASSED [ 85%]
tests/test_confflow_real_g16_ts_smoke.py::test_g16_ts_workflow_stats_records_completed_step_name PASSED [100%]

============================== 7 passed in 0.12s ==============================
```

### Full non-integration regression (default-excludes integration)

```
$ python -m pytest tests/ --tb=no -q -m 'not integration'
...
1246 passed, 18 skipped, 6 deselected in 66.02s (0:01:06)
```

The 1246 = 1239 baseline (post-9H-2 and 9H-3) + 7 new from 9H-1. The 18
skipped are the existing opt-in SSH/SFTP/submitter integration tests.
The 6 deselected are the integration-marker tests. No regressions.
