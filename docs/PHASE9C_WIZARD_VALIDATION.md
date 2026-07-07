# Phase 9C: Wizard Calculation-Page Validation

**Date**: 2026-07-07
**Status**: ✅ Implemented & tested
**Tests**: 12 new, **1066 total passing** (1 pre-existing flaky test in `test_run_service.py::test_delete_isolation_transaction_has_one_rename_winner` is timing-sensitive and not related to this phase)

Phase 9B gave the wizard a directory-batch XYZ picker. Phase 9C plugs the
next-most-annoying gap: until now, the **Calculation settings** page would
let the user click Next with garbage values (empty method, 256 MB memory,
charge out of range, etc.) and only fail at workflow-render time — far
from the field that caused the problem. This phase moves validation to
the field level so the wizard blocks Next **as soon as** a field is
invalid, and shows the user a red hint right under the offending input.

---

## What changed

### `_CalcPage` in `confflow_wizard_dialog.py`

The calculation page now validates itself in two complementary ways:

#### 1. `isComplete()` — gates the Next button

```python
def isComplete(self) -> bool:
    errors = self._compute_validation()
    complete = not errors
    prev = self._was_complete
    self._was_complete = complete
    if prev is not None and prev != complete:
        self.completeChanged.emit()
    return complete
```

`_compute_validation()` returns a `field-name → error-message` map; the
page is complete when the map is empty. When validity flips, the page
emits `completeChanged` so QWizard re-evaluates Next-button enablement.

**Validation rules**

| Field | Rule |
|---|---|
| `method_edit` | `text().strip()` must be non-empty |
| `basis_edit` | `text().strip()` must be non-empty |
| `charge_spin` | `-10 ≤ value ≤ 10` (spinbox range already enforces this) |
| `mult_spin` | `value ≥ 1` (spinbox range enforces `≤ 10`) |
| `nproc_spin` | `value ≥ 1` (spinbox range enforces `≤ 256`) |
| `mem_spin` | `value ≥ 1024` MB |

#### 2. Inline hint labels — surface errors right under each field

Six new QLabels, one per editable field, styled red and italic:

```python
_hint_style = "color: #c00; font-style: italic;"
```

- `method_hint`, `basis_hint`, `charge_hint`, `mult_hint`, `nproc_hint`, `mem_hint`
- Added as `form.addRow("", self.<field>_hint)` so they sit right under the
  input, with the label column blank.
- Hint is **only shown when the field is invalid AND the user has touched
  it**. Touching is tracked via `self._touched: set[str]`.

**Touch semantics**

| Field type | "Touched" trigger | Why |
|---|---|---|
| `method_edit`, `basis_edit` | `editingFinished` | Avoid yelling mid-keystroke; the user is still typing |
| `charge_spin`, `mult_spin`, `nproc_spin`, `mem_spin` | first `valueChanged` | Spinboxes don't have meaningful `editingFinished`; any interaction counts |

**Live re-validation on every keystroke / value change** keeps
`isComplete()` in sync so the Next button toggles immediately when the
field becomes valid (or invalid). The inline hint label, however, only
appears once the field is touched, so the user is not bombarded with red
text while still typing.

#### 3. Re-entry safety (subtle but important)

Initial implementation triggered a **stack overflow** during testing.
Root cause: `completeChanged.emit()` makes QWizard re-query `isComplete()`
synchronously. Because `_was_complete` was being updated *after* the
emit, the recursive call saw the stale value and re-emitted, recursing
infinitely. The fix updates `_was_complete` *before* emitting:

```python
prev = self._was_complete
self._was_complete = complete   # update FIRST
if prev is not None and prev != complete:
    self.completeChanged.emit()  # then emit
```

This is documented inline so the next maintainer doesn't trip on it.

### Public-ish helpers exposed for tests

```python
page._compute_validation() -> dict[str, str]   # field-name -> error message
page._touched                                # set of touched field names
page._errors                                 # snapshot of last compute
page._hint_style                             # class constant (styleSheet)
page.method_hint / basis_hint / charge_hint / mult_hint / nproc_hint / mem_hint
```

---

## Tests (`tests/test_confflow_wizard_calc_validation.py`)

12 pytest-qt tests covering:

| Test | Verifies |
|---|---|
| `test_calc_page_complete_with_defaults` | Fresh wizard (B3LYP / 6-31G(d)) is valid by default |
| `test_calc_page_incomplete_when_method_empty` | Empty method blocks Next; "method" in `_errors` |
| `test_calc_page_incomplete_when_method_whitespace_only` | Whitespace-only is treated as empty |
| `test_calc_page_incomplete_when_basis_empty` | Empty basis blocks Next |
| `test_calc_page_memory_below_floor_is_invalid` | 512 MB < 1024 floor → invalid |
| `test_calc_page_nproc_stays_valid_at_range_floor` | nproc=1 stays valid (spinbox enforces ≥1) |
| `test_calc_page_hint_label_starts_empty` | All 6 hint labels exist, start with empty text |
| `test_calc_page_hint_appears_on_invalid_method` | Hint shows after `editingFinished` on bad method |
| `test_calc_page_hint_clears_when_field_fixed` | Hint clears when method becomes valid |
| `test_calc_page_touched_set_tracks_interactions` | `_touched` contains field after `editingFinished` |
| `test_calc_page_charge_spin_touched_on_value_change` | Spinbox `valueChanged` marks touched, hint stays empty (value still valid) |
| `test_calc_page_complete_with_charge_out_of_range_via_helper` | `monkeypatch` injects charge=99 → helper reports invalid |

Tests access the page directly (`wizard.calc_page`) rather than driving
`wizard.next()`, because `_XyzPage.isComplete()` blocks navigation when
no XYZ files are loaded. Direct page access keeps the validation suite
focused on calc-page behaviour without coupling to the XYZ page.

---

## Why "touched" instead of always-show

A naive implementation would show the hint as soon as a field becomes
invalid. That feels hostile when the user has just opened the wizard:
"Memory must be at least 1024 MB." is flashing red on a default value of
4096 MB — wait, that's actually valid. But for fields like `charge` with
a -10..10 range, the moment the user clicks the spinbox and arrows down
to -1, the wizard starts yelling. Worse: as the user types `B3LYP` into
`method_edit`, every intermediate state (`B`, `B3`, `B3L`, …) fires
`textChanged` and would briefly flash "Method is required." on the way
to a valid value.

Gating the hint on `_touched` is the standard UX pattern (mirrors how
browsers only show validation popovers after the user leaves a field):
the page can still flip `isComplete()` immediately so the Next button
is correct, but the red text only appears once the user has actually
finished with that field.

---

## Edge cases handled

| Case | Behaviour |
|---|---|
| Field becomes invalid → valid | Hint clears; Next re-enabled |
| Field becomes valid → invalid | Hint appears (if touched); Next disabled |
| Default values | All valid, all hints empty, Next enabled |
| Spinbox stepped outside int range | Impossible: QSpinBox range constraint blocks it |
| Method edited then erased then re-typed | `_touched` persists once set, so hint shows mid-edits after first commit |
| `completeChanged.emit()` re-entrancy | `_was_complete` updated before emit → no infinite recursion |
| Charge / mult / nproc / mem out of range via spinbox | Range constraint blocks UI input; `monkeypatch` covers the programmatic case in tests |

---

## Sample user flow

1. Wizard opens on **Input XYZ files**. User adds files, clicks Next.
2. **Calculation settings** page — all hints empty (defaults are valid).
3. User clicks into **Method** and accidentally hits Backspace until empty,
   then tabs away. Red hint: "Method is required." Next button greys out.
4. User types `MP2` and tabs away. Hint clears; Next re-enables.
5. User clicks **Memory** spinbox and arrows down to 512 MB. Red hint:
   "Memory must be at least 1024 MB." Next greys out.
6. User arrows up to 1024 MB. Hint clears; Next re-enables.
7. User clicks Next → **Workflow settings** page.

---

## Files Changed / Added

| File | Change |
|---|---|
| `src/jobdesk_app/gui/dialogs/confflow_wizard_dialog.py` | `_CalcPage`: validation + 6 hint labels + `_touched`/`_errors`/`_was_complete` state + re-entry-safe `isComplete()` |
| `tests/test_confflow_wizard_calc_validation.py` | New 12-test suite |

## Test Totals

```
================================= 1066 passed, 18 skipped =================================
```

Wizard files combined:
```
test_confflow_wizard_dialog.py        9 passed, 3 skipped (pre-existing skips)
test_confflow_wizard_xyz_batch.py     13 passed
test_confflow_wizard_calc_validation.py  12 passed
                                    ─────────────────
                                      34 passed, 3 skipped
```

---

## What's Next (Phase 9D candidates)

1. **Cross-program favourites strip** — recently-used / favourite presets
   next to the per-program dropdown (carried over from 9B doc).
2. **Result detail pane** — render parsed SCF energy, termination, geometry
   in the runs-results page (use the Phase 9A mock to drive initial UI).
3. **Drag-and-drop onto the XYZ list** — drop a directory or files from
   the OS file manager.
4. **Workflow-page `isComplete()` mirror** — apply the same
   validation/hint pattern to `_WorkflowPage` (e.g. require at least one
   step selected, validate work_dir name).