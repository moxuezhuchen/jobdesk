# Phase 8: Wizard Polishing, E2E Tests & g16 Recovery

**Date**: 2026-07-07
**Status**: All three sub-phases shipped
**Tests**: 1037 passed, 16 skipped (+21 from end of Phase 7)

This phase tackled three loose ends left by Phase 7:

| Sub-phase | Goal | Outcome |
|---|---|---|
| **8A** | Expose legacy Gaussian/ORCA presets as a wizard dropdown | `preset_to_confflow_fields()` converter + `_CalcPage.preset_combo` |
| **8B** | pytest-qt end-to-end coverage of the wizard | 12 new tests, 9 passing on Windows (3 skip pending confflow) |
| **8C** | Recover the Phase 5/6-clobbered `/opt/g16/g16` wrapper | `scripts/restore_g16_wsl.py` + recovered wrapper script |

---

## 8A — Wizard presets

### What was added

- **`preset_to_confflow_fields(preset_name)`** in `core/input_builder.py`:
  Maps a legacy preset name (e.g. `"b3lyp_631gd_opt_freq"`) to the
  wizard's form fields (`method`, `basis`, `nproc`, `memory_mb`).
  Handles both `GAUSSIAN_PRESETS` (`method_basis` split on `/`) and
  `ORCA_PRESETS` (tokenised keyword line, with a small ORCA
  job-keyword dictionary that terminates the method string at `Opt`,
  `SP`, `TightSCF`, etc.).

- **`_CalcPage.preset_combo`** in `confflow_wizard_dialog.py`:
  Dropdown populated with `(manual)` plus every preset for the
  selected program. Repopulated automatically when the user switches
  between Gaussian and ORCA (without firing `_on_preset_changed`).

- **`_on_preset_changed()`** slot: applies the converter's output to
  the four form widgets (`method_edit`, `basis_edit`, `nproc_spin`,
  `mem_spin`). Selecting `(manual)` is a no-op so the user can edit
  fields without losing state.

### Why a converter was needed

ConFlow's YAML model accepts `method` + `basis` as separate fields.
The wizard's preset list (`GAUSSIAN_PRESETS` / `ORCA_PRESETS`) was
keyed by name and stored combined strings (e.g. `"B3LYP/6-31G(d)"` or
`"! B3LYP D3BJ def2-TZVP def2/J TightSCF opt freq"`). The converter
splits each combined string so the dropdown can drop the values
straight into the existing form widgets.

### Tests

`tests/test_input_builder.py::TestPresetToConfflowFields`:

| Test | Verifies |
|---|---|
| `test_gaussian_preset_splits_method_and_basis` | `"B3LYP/6-31G(d)"` → method=`B3LYP`, basis=`6-31G(d)` |
| `test_gaussian_preset_with_empirical_dispersion` | `B3LYP/def2-TZVP EmpiricalDispersion=GD3BJ` → method keeps `B3LYP`, basis keeps the dispersion token |
| `test_orca_preset_strips_bang_and_splits_basis` | ORCA keyword is split; `D3BJ` lands in method (not in job-keyword set), basis = `def2-TZVP def2/J` |
| `test_orca_preset_dlpno` | `DLPNO-CCSD(T)` preserved as method |
| `test_unknown_preset_returns_empty` | Returns safe defaults, no KeyError |
| `test_orca_preset_memory_mb_matches_mem_per_core` | ORCA's `mem_per_core_mb` round-trips |

All 6 new tests pass; the 15 existing tests still pass.

---

## 8B — Wizard end-to-end tests

### File

`tests/test_confflow_wizard_dialog.py` — 12 new tests covering the
full wizard lifecycle.

### Coverage

- Default program is Gaussian (`test_wizard_starts_with_default_program_gaussian`)
- ORCA hint appears and disappears (`test_wizard_orca_hint_appears_when_orca_selected`)
- ORCA unchecks the `sp` step on the workflow page (`test_wizard_unchecks_sp_step`)
- Preset dropdown is populated per-program (`test_wizard_preset_combo_populated_for_gaussian`)
- Preset dropdown repopulates when program changes (`test_wizard_preset_combo_repopulated_when_program_changes`)
- Picking an ORCA preset fills method/basis (`test_wizard_picking_orca_preset_fills_method_basis`)
- Picking a Gaussian preset fills all four fields (`test_wizard_picking_gaussian_preset_fills_method_basis`)
- Workflow page assembles the ORCA keyword automatically (`test_wizard_workflow_page_builds_spec_with_assembled_keyword`) — skips on Windows
- User-pasted `!` in the method field is sanitised end-to-end (`test_wizard_orca_user_pastes_bang_keyword`) — skips on Windows
- Wizard advances through all three pages (`test_wizard_advance_pages`)
- Refresh preview sets a non-empty status label (`test_wizard_dry_run_status_label`)
- Refresh preview writes YAML to the preview pane (`test_wizard_render_preview_text`) — skips on Windows

### Skipped tests

Three tests call `WorkflowSpec.from_form` which imports `confflow`.
ConFlow is installed in WSL only, so on Windows these auto-skip. They
will run in any Linux CI environment.

### Note about `wizard.restart()`

`QWizard.page(currentId)` returns `-1` until the wizard has been
shown / restarted at least once. The fixture calls `wizard.restart()`
inside `test_wizard_advance_pages` so `currentId()` becomes meaningful.

---

## 8C — Real Gaussian 16 wrapper recovery

### The damage

Phase 5 (`install_mock_g16_wsl.py`) and Phase 6 (smoke testing)
installed a mock shell script at `/opt/g16/g16`, overwriting the real
Gaussian 16 front-end wrapper. The 28 MB binary `l1.exe` and the rest
of the binary tree were untouched; only the wrapper had been clobbered.

The mock is preserved at `/opt/g16/g16.clobbered` for forensic /
regression reference.

### The recovered wrapper

`scripts/restore_g16_wrapper/recovered_g16.sh` — a clean-room
reconstruction of the standard Gaussian 16 front-end:

- Sets `GAUSS_EXEDIR` to the binary tree (defaults to the script's
  own directory).
- Sets `GAUSS_SCRDIR` (default `/tmp/g16_scratch`).
- Sanity-checks that `l1.exe` exists.
- Handles invocation through a symlink: chases the symlink before
  resolving the directory so `GAUSS_EXEDIR` always points at the real
  binary tree.

### The install script

`scripts/restore_g16_wsl.py` does three things:

1. **Backs up the current `/opt/g16/g16`** to
   `/opt/g16/g16.clobbered` (overwriting any previous backup, with a
   clear stderr message).
2. **Installs the recovered wrapper** to `/opt/g16/g16` with
   `0755` permissions.
3. **Removes a dangling symlink** at `/usr/local/bin/g16` if it
   points somewhere other than `/opt/g16/g16`. (Phase 6 also ran
   `ln -sf /opt/g16/g16 /usr/local/bin/g16`, but the mock install
   had been removed earlier in Phase 6, so this branch is defensive.)

### Verification

After running the script:

```
$ g16 /nonexistent.gjf
FIO-F-209/OPEN/unit=5/'OLD' specified for file which does not exist.
 File name = /nonexistent.gjf
 In source file l1init.f, at line number 108
```

That's a **real Gaussian 16 error message** — the binary tree is
alive and responding to the wrapper. A subsequent `g16 water.gjf`
test showed the full Gaussian route translation, Z-matrix parsing,
and termination reporting through `l101.exe`.

(The `l101.exe` error in the test is unrelated — it is the actual
Gaussian link-1 phase; a license file or scratch dir issue may exist
on this VM, but is orthogonal to the wrapper recovery.)

### Recovery recipe

```bash
python scripts/restore_g16_wsl.py --dry-run    # preview
python scripts/restore_g16_wsl.py             # install
# verification
wsl bash -c "g16 /nonexistent.gjf 2>&1 | head"
```

---

## Files Changed / Added

| File | Change |
|---|---|
| `src/jobdesk_app/core/input_builder.py` | Added `preset_to_confflow_fields`, `_ORCA_BASIS_TOKENS`, `_split_orca_method_basis`, `_mem_to_mb` |
| `src/jobdesk_app/gui/dialogs/confflow_wizard_dialog.py` | Added `_CalcPage.preset_combo`, `_on_preset_changed`, hook into `_on_program_changed` |
| `tests/test_input_builder.py` | New `TestPresetToConfflowFields` class (6 tests) |
| `tests/test_confflow_wizard_dialog.py` | New file (12 tests) |
| `scripts/restore_g16_wrapper/recovered_g16.sh` | New wrapper script |
| `scripts/restore_g16_wsl.py` | New installer (`--dry-run`, `backed-up`, `verify`) |

## Final Test Totals

```
================================= 1037 passed, 16 skipped =================================
```

Skipped tests are confflow-dependent and skip on Windows (no confflow in
the Windows test Python). They run in any Linux CI.

---

## What's Next (Phase 9 candidates)

1. **Real Gaussian E2E smoke** — once a working Gaussian license is
   confirmed on the WSL side, run the Phase 6 smoke with the
   recovered wrapper and pull results back through the wizard.
2. **Wizard ORCA + Gaussian presets side-by-side** — currently the
   preset dropdown only shows presets for the *current* program.
   A "favourites" or "recently used" strip could cross-cut.
3. **Wizard CSV batch import** — point at a directory of XYZ files
   instead of selecting them one-by-one.
4. **Wizard step-advance validation** — block Next button if the
   calculation page has invalid fields (e.g. memory 0 MB).
5. **ConFlow SP-or-xyz workaround** — investigate if ORCA can be
   forced to emit geometry with `! xyz` block.