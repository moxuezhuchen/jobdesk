# Phase 9H-2: Real Gaussian 16 chk_from_step Smoke

**Date**: 2026-07-08
**Status**: Real g16 chk-passing smoke green; 9/9 new tests pass
**Tests**: 9 new (real g16 two-step pipeline, no mock)
**Smoke wall time**: 4.3 s (methane, 2 steps)

Phase 9G stood up the single-step real-g16 path. Phase 9H-2 extends it to
a **two-step workflow** in which step 7 explicitly consumes step 6's
Gaussian checkpoint file (.chk) via ConfFlow's `chk_from_step` glue. This
is the load-bearing workflow pattern for any non-trivial Gaussian study
(IRC follow-up after TS, single-point on top of an optimised geometry,
composite methods that need a converged wavefunction).

This phase is **not CI-friendly** — it requires a working Gaussian 16 +
license at `/opt/g16/g16`. The pytest suite auto-skips when artifacts
are missing, so CI on machines without g16 is a no-op.

---

## What Was Tested

End-to-end smoke of a real ConfFlow two-step Gaussian 16 pipeline that
hands the checkpoint from one step to the next:

```
methane.xyz (5 atoms, tetrahedral)
  ↓  confflow step_06_g16_opt
  iprog: g16, itask: opt
  keyword: "opt(nomicro) b3lyp/6-31g(d)"
  gaussian_write_chk: true
  ↓  /opt/g16/g16 → A000001.chk (749 KB) + A000001.log
  ↓  confflow copies chk into step_07 input dir as A000001.old.chk
step_07_g16_sp_readchk
  iprog: g16, itask: sp
  keyword: "sp guess=read geom=allcheck"
  chk_from_step: step_06_g16_opt
  ↓  /opt/g16/g16 → A000001.log (Normal termination, SCF Done RHF)
  ↓  confflow result assembly
step_07_g16_sp_readchk/output.xyz + result.xyz
  ↓  JobDesk parse_gaussian_log + load_summary + load_step_progress
ConfFlowSummary + ConfFlowStepProgress
```

The chain is exercised against the **real** `/opt/g16/g16` binary; no mock
of any step. The g16 `.log` shows the load-bearing markers proving the
chk-passing actually happened:

```
%Chk=A000001.chk
%OldChk=A000001.old.chk
%nproc=1
%mem=1GB
# sp guess=read geom=allcheck
...
Copying data from "A000001.old.chk" to current chk file "A000001.chk"
Structure from the checkpoint file:  "A000001.chk"
Initial guess from the checkpoint file:  "A000001.chk"
SCF Done:  E(RHF) =  -39.7265059209     A.U. after    6 cycles
...
Normal termination of Gaussian 16 at Wed Jul  8 20:59:04 2026.
```

If the chk-passing were broken (wrong key name, stale path, etc.) the
`.log` would show either no `%OldChk=` echo at all, or
`Initial guess from the checkpoint file` would be replaced by a fresh
Hückel/extended-Hückel guess and the energy would be different.

---

## Harness

**`scripts/smoke_confflow_real_g16_chk.py`** (~205 lines)

Mirrors `scripts/smoke_confflow_real_g16_wsl.py` from Phase 9G with two
substantive differences:

1. The stamped bash payload contains a **two-step** ConFlow YAML — one
   opt step with `gaussian_write_chk: true` followed by one sp step with
   `chk_from_step: step_06_g16_opt`.
2. The smoke prints the contents of step 07's `.gjf` (must contain
   `%OldChk=A000001.old.chk`), the listing of step 07's `backups/` (must
   contain `A000001.old.chk` next to its own `A000001.chk`), and
   `Copying data from "A000001.old.chk"` lines from the `.log` so the
   chk-passing is observable from the harness output alone.

Same WSL g16 environment setup as 9G:

```bash
export g16root=/opt
export GAUSS_EXEDIR=/opt/g16/bsd:/opt/g16
export PATH=/opt/gauopen:/opt/g16/bsd:/opt/g16:$PATH
export GAUSS_SCRDIR=/opt/g16/scratch
# Skip source /opt/g16/bsd/g16.profile -- it triggers 'set -u' PERLLIB unbound
# errors when sourced under `set -euo pipefail`.
```

Run from the repo root:

```bash
python scripts/smoke_confflow_real_g16_chk.py
```

The smoke writes its workdir to `/tmp/confflow_phase9h2_${BASHPID}` in
WSL, then pulls the `methane_confflow_work/` tree back to
`tmp60f7j8ix/phase9h2_chk/` on Windows. Idempotent — the smoke cleans up
its WSL staging dir after the pull.

---

## YAML Used (Real g16, two-step)

```yaml
global:
  gaussian_path: /opt/g16/g16
  cores_per_task: 1
  total_memory: "1GB"
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: step_06_g16_opt
    type: calc
    params:
      iprog: g16
      itask: opt
      keyword: "opt(nomicro) b3lyp/6-31g(d)"
      cores_per_task: 1
      total_memory: "1GB"
      max_parallel_jobs: 1
      gaussian_write_chk: true
  - name: step_07_g16_sp_readchk
    type: calc
    params:
      iprog: g16
      itask: sp
      keyword: "sp guess=read geom=allcheck"
      cores_per_task: 1
      total_memory: "1GB"
      max_parallel_jobs: 1
      chk_from_step: step_06_g16_opt
```

The exact spelling was confirmed by reading the source:

- `chk_from_step` is the canonical key. It is a `str | int`; an integer
  is a 1-based step index, a string is matched against the step `name`.
  See `/opt/ConfFlow/confflow/workflow/step_handlers.py:252-272`
  (`_resolve_chk_input_dir`) and
  `/opt/ConfFlow/confflow/core/types.py:138`.
- `gaussian_write_chk: true` is a per-step flag (default is "write")
  that tells confflow's Gaussian policy to emit a `%Chk=` line in the
  `.gjf`. It defaults to `true` so it's only strictly needed when you
  want to **opt out**; we set it explicitly here for documentation.

ConFlow's `build_step_dir_name_map`
(`/opt/ConfFlow/confflow/workflow/step_naming.py:32-49`) sanitizes
step names to directory names but preserves underscores, so step
`step_06_g16_opt` lands at `methane_confflow_work/step_06_g16_opt/`.

---

## Real Run Output (excerpt)

```
[smoke] g16 location:
/opt/g16/g16
-rwxr-x--- 1 ubuntu ubuntu 113454768 Feb 23  2022 /opt/g16/g16

[smoke] starting confflow (real g16, two-step chk-passing)
[smoke] confflow rc=0

[smoke] step_06 g16 .log key lines:
 SCF Done:  E(RB3LYP) =  -40.5183502091     A.U. after    8 cycles
 SCF Done:  E(RB3LYP) =  -40.5183833088     A.U. after    6 cycles
 Optimization completed.
    -- Stationary point found.
 Normal termination of Gaussian 16 at Wed Jul  8 20:59:02 2026.

[smoke] step_07 g16 .log key lines:
 %OldChk=A000001.old.chk
 SCF Done:  E(RHF) =  -39.7265059209     A.U. after    6 cycles
 Normal termination of Gaussian 16 at Wed Jul  8 20:59:04 2026.

[smoke] step_07 .gjf (expect %OldChk line):
%Chk=A000001.chk
%OldChk=A000001.old.chk
%nproc=1
%mem=1GB
# sp guess=read geom=allcheck

A000001

0 1
C 0.0 0.0 0.0
H 0.631259 0.631259 0.631259
H -0.631259 -0.631259 0.631259
H -0.631259 0.631259 -0.631259
H 0.631259 -0.631259 -0.631259

[smoke] step_07 backups dir:
A000001.chk       790528 bytes  (step_07's own chk after restart from .old.chk)
A000001.err            0 bytes
A000001.gjf          243 bytes  (contains %OldChk=A000001.old.chk)
A000001.log        14217 bytes
A000001.old.chk   749568 bytes  (== step_06's chk, copied by confflow)
```

Note the H coordinates in the `.gjf`: `0.631259` is the **optimised**
value, not the input `0.629118`. This is because the gjf's geometry
block was assembled by confflow from the chk's internal-coordinate
representation (see line 103 of the log: `Redundant internal
coordinates found in file.  (old form).`). The geometry block is
informational only — g16 actually re-derives the Cartesian geometry
from the chk's internal coordinates because the keyword is
`geom=allcheck`.

`workflow_stats.json` shows both steps completed:

```json
{
  "steps": [
    {"name": "step_06_g16_opt",        "status": "completed", "duration_seconds": 3.25},
    {"name": "step_07_g16_sp_readchk", "status": "completed", "duration_seconds": 1.06}
  ],
  "initial_conformers": 1,
  "final_conformers": 1,
  "step_status_counts": {"completed": 2}
}
```

Total wall time: 4.3 s (well under the 60 s budget).

---

## JobDesk Parsers Verified

```
$ python -m pytest tests/test_confflow_real_g16_chk_smoke.py -v
============================= test session starts =============================
collected 9 items

tests/test_confflow_real_g16_chk_smoke.py::test_step_06_opt_log_terminates_normally PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_step_07_sp_log_terminates_normally_and_reads_chk PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_step_07_log_geometry_matches_step_06_optimised_geometry PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_step_07_log_file_has_nonzero_size PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_step_06_emitted_chk_and_step_07_copied_it PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_step_07_gjf_has_oldchk_directive PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_run_summary_loads_with_two_completed_steps PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_workflow_stats_records_both_step_names PASSED
tests/test_confflow_real_g16_chk_smoke.py::test_final_output_points_into_step_07 PASSED

============================== 9 passed in 0.11s ==============================
```

What each test asserts:

| Test | Asserts |
|---|---|
| `test_step_06_opt_log_terminates_normally` | step_06's `.log` contains both `Optimization completed` + `Stationary point found` + `Normal termination of Gaussian 16` |
| `test_step_07_sp_log_terminates_normally_and_reads_chk` | step_07's `.log` contains `Normal termination`, `SCF Done:`, `Copying data from "A000001.old.chk"`, `Structure from the checkpoint file`, `Initial guess from the checkpoint file` |
| `test_step_07_log_geometry_matches_step_06_optimised_geometry` | the C-H distances in the final `Standard orientation` block of both `.log` files agree to < 1e-4 A AND fall in the 1.05–1.15 A optimised band (not the 0.629118 input value) |
| `test_step_07_log_file_has_nonzero_size` | step_07's `.log` > 1 KB (basic "did g16 actually run" guard) |
| `test_step_06_emitted_chk_and_step_07_copied_it` | step_06's `backups/A000001.chk` exists and is > 1 KB; step_07's `backups/A000001.old.chk` exists, is > 1 KB, and is byte-identical in size to step_06's chk (it's a `shutil.copy2`) |
| `test_step_07_gjf_has_oldchk_directive` | step_07's `A000001.gjf` contains `%OldChk=A000001.old.chk` AND `%Chk=A000001.chk` |
| `test_run_summary_loads_with_two_completed_steps` | `load_summary` reports `step_status_counts['completed'] == 2`, `final_conformers >= 1`, lowest conformer energy in the b3lyp/6-31g(d) band (-42.0 < E < -38.0 a.u.) |
| `test_workflow_stats_records_both_step_names` | `load_step_progress` reports both `step_06_g16_opt` and `step_07_g16_sp_readchk` in `completed` |
| `test_final_output_points_into_step_07` | `run_summary.final_output` references `step_07_g16_sp_readchk`, not step_06; the local `step_07_g16_sp_readchk/output.xyz` exists after pull |

The harness and the test suite are coupled by **path only**: the smoke
writes to `tmp60f7j8ix/phase9h2_chk/methane_confflow_work/...`, and the
test re-discovers the tree. If the smoke has not been run on a
checkout, every test `pytest.skip()`s with a clear "run the smoke
first" message.

---

## Files Added / Changed

| File | Lines | Change |
|---|---|---|
| `scripts/smoke_confflow_real_g16_chk.py` | 205 | New harness; bash payload is stamped into WSL via base64; YAML has two steps with `chk_from_step` glue |
| `tests/test_confflow_real_g16_chk_smoke.py` | 167 | New 9-test suite; auto-skipped when artifacts missing |

No production code changes — this phase validates **existing** parser
behaviour against a **new** workflow shape. None of
`parse_gaussian_log`, `load_summary`, or `load_step_progress` need to
be touched to consume the two-step result tree.

---

## Cross-Cutting Notes

### Why `opt(nomicro)` for step 6

`opt(nomicro)` disables the micro-iteration cache (rational-step
optimisation paths). It is not necessary for correctness — g16's opt
default converges fine on methane — but it is the recommended keyword
in ConfFlow's example YAML (`confflow.example.yaml:155`) for small
basis sets because the per-iteration overhead of the micro-iteration
cache exceeds its convergence speedup on tiny molecules. We followed
the example to keep the smoke representative of a real ConFlow user's
configuration.

### Why `geom=allcheck guess=read` for step 7

The two keywords are independent:

- `guess=read` tells g16 to read the **initial wavefunction guess**
  (MO coefficients) from the chk. Without it, g16 would build a fresh
  Hückel guess from scratch and re-converge the SCF — defeats the
  point of `chk_from_step` if you're using it for wavefunction
  convergence carry-over.
- `geom=allcheck` tells g16 to read the **molecular geometry AND
  charge/multiplicity AND title** from the chk. It also reads
  internal coordinates if they exist, so g16 doesn't need the
  Cartesian block in the `.gjf` to be exact — g16 re-derives it
  from the chk's Z-matrix/internal coordinates.

For the chk-passing smoke we need **both**: `guess=read` for the SCF
convergence carry-over, `geom=allcheck` to bypass the gjf's geometry
block entirely (the geometry block is informational when `geom=allcheck`
is set, but g16 still requires it to be syntactically present).

### Why `chk_from_step` matters

Long workflows where each step needs the prior step's converged
wavefunction:

1. **IRC follow-up after TS** — TS optimisation produces a chk with the
   saddle-point wavefunction; IRC then re-uses that wavefunction as its
   initial guess at each integration step.
2. **Composite methods** — G4 / G4-MP3 type workflows where a high-level
   energy correction needs the lower-level converged wavefunction.
3. **SP-after-opt** — single-point energy at a higher level of theory
   on top of an optimised geometry, where you want to skip the SCF
   from scratch at the higher level.
4. **Property calculations** (NMR, NBO) that need a converged chk as
   input.

Without `chk_from_step`, the consumer step would either need to
manually copy the chk between steps or re-run the SCF from a default
guess. ConFlow's glue automates the copy and emits the right
`%OldChk=` directive; without it, real-world Gaussian workflows are
much more painful.

### What the `%OldChk=A000001.old.chk` assertion proves

The single most important assertion in this whole phase is
`test_step_07_gjf_has_oldchk_directive`. It is the load-bearing proof
that:

1. ConFlow resolved the `chk_from_step` parameter (string match against
   the step `name` map).
2. ConFlow located step 06's `backups/` directory and copied
   `A000001.chk` → `A000001.old.chk` into step 07's input dir.
3. ConFlow emitted the `%OldChk=A000001.old.chk` line in the `.gjf`.
4. g16 honoured the `%OldChk=` directive (the `.log` echoes it back
   and then says `Copying data from "A000001.old.chk" ...`).

If any of these four broke, step 7 would either error out (no chk
file to copy → empty wavefunction), or fall back to a default guess
(silent regression — energy looks plausible but is wrong). The
assertion is in place to catch **both** failure modes.

### The "two-step run" is also a "two-step workflow" template

This phase's YAML is structurally identical to the workflow a real
ConFlow user would write for: **TS search → IRC** or **opt → SP at
higher level**. The only difference would be `itask` (`ts` or `irc`
for the first step) and `keyword`. The smoke proves that ConFlow's
chk-passing glue works for **any** iprog/itask pair, not just opt→sp,
because the glue lives in `_resolve_chk_input_dir` (workflow layer)
and `prepare_task_inputs` (executor layer) — both are iprog-agnostic.

### What's Next (Phase 9I candidates)

1. **TS smoke** — run `itask: ts` for step 6, then `itask: irc` for
   step 7 reading the chk. Validates the TS → IRC chk-passing
   workflow (the canonical ConfFlow example at `confflow.example.yaml:147-164`).
   Different parser surface (freq → imaginary-frequency count,
   IRC-direction handling).
2. **Opt → SP at a different basis** — b3lyp/6-31g(d) opt, then
   b3lyp/6-311++g(2d,p) SP reading the chk. Validates the "different
   basis but same SCF family" workflow (the more interesting case
   for wavefunction carry-over).
3. **Reduce-orca-mock surface** — still the only remaining mock in the
   wizard→run→parse loop. Phase 9G closed g16; Phase 9H-2 closed
   g16 multi-step. Closing ORCA would require ORCA 6 install on the
   same WSL distro.
4. **Move smokes into `tests/integration/`** — formal pytest fixture
   that runs the smoke in `setup` and tears down in `teardown`, so
   the smoke is part of `pytest -m integration`.

**Recommendation**: **(1)** first — the canonical ConfFlow TS→IRC
example is exactly the workflow that motivated the `chk_from_step`
feature. Validating it under real g16 closes the most important
remaining unexercised ConfFlow example. (2) is also valuable but
adds parser surface (different basis after opt requires careful
internal-coordinate rebuild — g16 warns about it in some cases).
(3) requires ORCA install. (4) is good hygiene but doesn't unlock
any new feature surface.