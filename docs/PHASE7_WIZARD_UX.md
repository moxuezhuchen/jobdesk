# Phase 7: Wizard UX & ORCA Keyword Assembly

**Date**: 2026-07-07
**Status**: ✅ Implemented & tested
**Tests**: 6 new, 1022 total passing (0 failures)

---

## What Changed

Phase 7 closes the gap between the wizard's *form-friendly* field set
(`method`, `basis` as separate text fields) and ConfFlow's ORCA policy,
which only consumes a single `keyword` string.  Without this glue, every
ORCA run from the wizard would execute with `keyword: "#p"` (the ORCA
policy's default fallback) — silently producing garbage.

### 1. `assemble_orca_keyword(method, basis, extra="")` in `workflow_spec.py`

```python
def assemble_orca_keyword(method: str, basis: str, extra: str = "") -> str:
    """Splice wizard fields into a single ORCA keyword line.

    Drops any leading ``!`` because the policy template adds one.  Empty
    components are skipped, so ``("B3LYP", "")`` → ``"B3LYP"``.
    """
    parts = [_strip_bang(p) for p in (method, basis) if p and p.strip()]
    extra = _strip_bang(extra)
    if extra:
        parts.append(extra)
    return " ".join(parts)
```

### 2. `WorkflowSpec.from_form()` now auto-assembles `keyword` for ORCA

```python
if program == "orca" and not calc_payload.get("keyword"):
    assembled = assemble_orca_keyword(method, basis)
    if assembled:
        calc_payload["keyword"] = assembled
```

User-supplied `extra_options["keyword"]` always wins — the assembler only
fills the gap.  Gaussian is untouched (its policy builds the route line
itself from `method/basis`).

### 3. Wizard: ORCA hint + smart step defaults

`_CalcPage._on_program_changed(program)` runs whenever the user changes
the program dropdown:

- **ORCA selected** → greyed italic hint appears:

  > *"ORCA: ConfFlow's policy template already prefixes '!'. Use a
  > geometry optimization step (e.g. 'opt') — ORCA single-point does not
  > emit a geometry file and the run will fail."*

- **ORCA selected** → unchecks the `sp` workflow step (best-effort;
  respects the user's manual check).

- **Gaussian selected** → hint cleared.

### 4. `install_mock_g16_wsl.py` now defaults to safe *staging* mode

Phase 6 lesson: the old installer overwrote the **real** `/opt/g16/g16`
wrapper with a mock. The new installer:

```bash
python scripts/install_mock_g16_wsl.py                       # staging (safe default)
python scripts/install_mock_g16_wsl.py --mode system --backup  # Phase 6 legacy behaviour
```

- **staging** (default): writes the mock to `~/.local/bin/g16` and
  symlinks `/usr/local/bin/g16` → `~/.local/bin/g16`. The real g16 in
  `/opt/g16/g16` is left intact.
- **system** (with `--backup`): copies the existing g16 to `.real`
  before clobbering. Without `--backup` it overwrites — matching Phase 6
  semantics — but a clear warning is emitted.

---

## Test Coverage

New tests in `tests/test_workflow_spec.py`:

| Test | Verifies |
|---|---|
| `test_assemble_orca_keyword_basic` | `method + basis` → `"m b"` |
| `test_assemble_orca_keyword_strips_bang` | Leading `!` (single, double) is dropped from any component |
| `test_assemble_orca_keyword_extra_tokens` | `Opt MiniPrint` appended, `!`-stripped |
| `test_assemble_orca_keyword_skips_empty_components` | Empty `method`/`basis` → still a valid string |
| `test_from_form_passes_keyword_for_orca` | ORCA + form fields → YAML contains `keyword: B3LYP def2-svp` |
| `test_from_form_orca_keyword_keeps_user_override` | `extra_options["keyword"]` wins over the assembler |
| `test_from_form_gaussian_does_not_force_keyword` | Gaussian YAML has *no* synthesised `keyword` |

The confflow-dependent tests skip on Windows (confflow is WSL-only) —
they will run in any Linux dev environment and CI.

---

## Smoke Flow (recap from Phase 6)

After these changes, the wizard's "Refresh preview" button on the
workflow page should produce a YAML whose `calc.keyword` field is
populated automatically:

```yaml
work_dir: methane_work
calc:
  program: orca
  method: b3lyp
  basis: def2-svp
  charge: 0
  multiplicity: 1
  nproc: 1
  memory_mb: 512
  keyword: b3lyp def2-svp          # ← new (assembled)
  steps:
    - opt                          # ← sp unchecked by hint
```

When the user clicks Submit, the wizard's accepted YAML travels through
`ConfFlowAdapter` → SFTP → `confflow methane.xyz -c workflow.yaml -w
methane_confflow_work`. The ORCA template emits `! b3lyp def2-svp` from
`{keyword}`, runs the geometry optimization, and writes
`A000001.xyz` + `result.xyz` + `run_summary.json` exactly as the Phase 6
smoke test demonstrated.

---

## Files Changed

- `src/jobdesk_app/core/workflow_spec.py` — `assemble_orca_keyword()`,
  `_strip_bang()`, integration in `from_form()`, exported in `__all__`.
- `src/jobdesk_app/gui/dialogs/confflow_wizard_dialog.py` —
  `_CalcPage.orca_hint` label + `_on_program_changed()` slot, removed
  SP step when ORCA is picked.
- `tests/test_workflow_spec.py` — 7 new tests.
- `scripts/install_mock_g16_wsl.py` — `--mode {staging,system}`,
  `--backup`, default destination `~/.local/bin/g16`.
- `docs/CONFFLOW_REAL_RUN_NOTES.md` — Phase 6 lessons (cross-referenced).
- `docs/PHASE7_WIZARD_UX.md` — this file.

---

## What's Next (Phase 8 candidates)

1. **Wizard presets dropdown** — surface `GAUSSIAN_PRESETS` /
   `ORCA_PRESETS` from `core/input_builder.py` in the wizard so users
   pick "B3LYP-D3BJ def2-TZVP opt" without typing.
2. **ConFlow keyword validation** — extend the wizard preview to flag
   ORCA keywords containing forbidden tokens (`SP` without preceding
   `Opt`, missing basis, etc.).
3. **ORCA SP workaround** — investigate whether `! xyz` block in ORCA
   forces `.xyz` output, or add a confflow config flag to skip
   geometry assembly for pure SP tasks.
4. **Restore real g16** — recover the Phase-5-overwritten wrapper from
   `/opt/g16/g16.real` (if Phase 6 ran with `--backup`) and add an
   integration test for the g16 path.
5. **End-to-end GUI test** — `pytest-qt` driven wizard test that fills
   `_CalcPage` and asserts the produced YAML is runnable.