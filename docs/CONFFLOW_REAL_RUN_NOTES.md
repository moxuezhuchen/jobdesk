# ConfFlow Real-Run Notes (Phase 6 Smoke Test)

**Date**: 2026-07-07
**WSL Distro**: Ubuntu-24.04
**ConFlow Version**: 1.0.10 (installed at `/opt/ConfFlow/.venv`)
**Chemistry Backend**: ORCA 6.1.1 (`/opt/orca611/orca`)
**Molecule**: Methane (CH₄, 5 atoms)

---

## What Was Tested

End-to-end smoke of the JobDesk / ConfFlow integration using a **real ORCA
calculation** (geometry optimization, B3LYP/def2-SVP), not a mock.  The harness
runs entirely in WSL and pulls results back to Windows so the same parsers the
GUI uses can be validated.

**File**: `scripts/smoke_confflow_wsl.py`

---

## Pre-Requisites

1. `wsl` server entry in `%APPDATA%\JobDesk\servers.yaml`
2. ORCA 6.x installed at `/opt/orca611/orca` (or adjust `orca_path` in the
   harness YAML)
3. ConFlow 1.0.x installed in WSL (pip install or `git+...`)

---

## Smoke Harness

```bash
python scripts/smoke_confflow_wsl.py
```

The harness:
1. Writes `methane.xyz` (5-atom Cartesian) into a unique WSL `/tmp/` dir
2. Writes a minimal ConFlow YAML targeting real ORCA
3. Runs `confflow methane.xyz -c confflow.yaml -w methane_confflow_work --resume --verbose`
4. Prints the result tree and `RESULT_DIR`
5. Pulls `methane_confflow_work/` back to `tmp60f7j8ix/phase6_smoke/` on Windows

---

## YAML Used (Real ORCA)

```yaml
global:
  orca_path: /opt/orca611/orca
  cores_per_task: 1
  total_memory: 512MB
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
steps:
  - name: quick_opt
    type: calc
    params:
      iprog: orca
      itask: opt
      keyword: "b3lyp def2-svp Opt MiniPrint"
      cores_per_task: 1
      total_memory: 512MB
      max_parallel_jobs: 1
```

### Key notes on the YAML

- `keyword` must **not** include the `!` prefix — the ORCA policy template
  (`BUILTIN_TEMPLATES["orca"]`) already provides `!`.
- `itask: opt` (not `sp`) is required — ORCA SP does not emit a companion `.xyz`
  file; confflow's `result.xyz` assembly requires geometry output.
- `Opt` in the keyword enables trajectory printing (`.xyz` frames) which confflow
  reads via `parse_last_geometry` → `A000001.xyz` → `result.xyz`.
- `MiniPrint` keeps ORCA output minimal.

---

## Result Tree

```
methane_confflow_work/
├── .checkpoint              # confflow resume checkpoint
├── confflow.log
├── quick_opt/
│   ├── manifest.json        # confflow calc step manifest
│   ├── calc.log            # confflow calc runner log
│   ├── results.db          # SQLite — per-task status
│   ├── output.xyz          # last ORCA geometry (confflow reads this)
│   ├── result.xyz          # assembled per-conformer XYZ (from output.xyz)
│   └── backups/
│       ├── A000001.inp    # ORCA input
│       ├── A000001.out    # ORCA output (83 kB)
│       ├── A000001.gbw    # ORCA wavefunction
│       ├── A000001.xyz    # ORCA trajectory frame (confflow reads here)
│       ├── A000001.property.txt
│       └── A000001_trj.xyz
├── run_summary.json        # ← what JobDesk GUI displays
└── workflow_stats.json     # ← step progress (completed count)
```

---

## Parsers Verified

Both parsers in `jobdesk_app.services.confflow_results` successfully consumed
the real ORCA output:

```
run_summary.json  →  ConfFlowSummary(
                       initial_conformers=1,
                       final_conformers=1,
                       total_duration_seconds=5.31,
                       lowest_conformer=ConfFlowConformer(
                         cid='A000001',
                         energy=-40.45193711,   # Hartree
                         xyz_path='...',
                         source_outputs=[...]
                       )
                     )

workflow_stats.json  →  ConfFlowStepProgress(
                           completed=('quick_opt',),
                           current='', last_updated=''
                         )
```

**Wall time**: ~5 seconds for methane opt (1 conformer, 1 step).

---

## Issues Encountered

### 1. ORCA SP produces no geometry output
- **Symptom**: `ConfFlowError: Calculation step did not produce an output XYZ file`
- **Cause**: `itask: sp` → ORCA does not emit a `.xyz` companion file; confflow's
  assembly relies on `parse_last_geometry(log_file, prog_id=2)` which first tries
  to read `basename.xyz`.  If absent it falls back to parsing the `.out` directly,
  but confflow's `CalcStepRunner` still requires `result.xyz` to be written.
- **Fix**: Use `itask: opt` instead of `sp`.  ORCA optimization writes `A*.xyz`
  trajectory files that confflow reads.

### 2. Double `!` in keyword
- **Symptom**: ORCA fails with "unknown keyword" or empty output.
- **Cause**: YAML `keyword: "! b3lyp def2-svp"` + template's own `!` → `!! b3lyp ...`
- **Fix**: Remove `!` from YAML, let the policy template add it.

### 3. `/opt/g16/g16` was overwritten by mock
- **Context**: The Phase 5 mock g16 (shell script, 1.6 kB) overwrote the real
  Gaussian 16 installation at `/opt/g16/g16`.
- **Recovery**: Real g16 binary is intact in `/opt/g16/*.exe` files; only the
  wrapper script needs restoring.
- **Lesson**: Never overwrite real software paths without a backup strategy.
  The Phase 6 plan should use a **writable staging path** (e.g.
  `$HOME/.local/bin/`) instead of overwriting system binaries.

### 4. PowerShell `$` variable expansion in subprocess strings
- **Symptom**: `$$` and `$BASHPID` get intercepted by PowerShell before reaching
  WSL bash, resulting in empty strings.
- **Fix**: Use `BASHPID` inside bash scripts (not Python subprocess), or use
  Python's `os.getpid()` and pass the literal integer to bash.

---

## Gaussian Notes (for future use)

If g16 is available:

- `gaussian_path: /opt/g16/g16` in YAML (or symlink into PATH)
- `iprog: g16`
- `itask: opt` (Gaussian geometry optimization produces `.log` with
  Standard Orientation block — `parse_last_geometry` reads this directly)
- No companion `.xyz` needed; Gaussian writes geometry into the `.log`.

Gaussian SP also works: `itask: sp` reads SCF energy + final geometry from
Standard Orientation in `.log`.

---

## Next Steps (Phase 7)

Based on this smoke:

1. **Template keyword sanitisation**: Warn if user types `!` in the wizard
   keyword field when ORCA is selected.
2. **ORCA-only wizard**: Show ORCA-optimisation hints in the wizard (not SP)
   since ORCA SP is incompatible with confflow's geometry requirement.
3. **ORCA SP workaround**: Investigate if a `! xyz` ORCA block could force
   `.xyz` output for SP tasks, or add a confflow config flag to skip geometry
   assembly for SP tasks.
4. **Gaussian recovery**: Restore the g16 wrapper script from a known backup
   location to enable Gaussian testing.
5. **Batch test**: Run the integration test with two molecules
   (`tests/integration/test_real_confflow_wsl.py`) once g16 is restored.
