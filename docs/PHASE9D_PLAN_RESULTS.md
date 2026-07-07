# Phase 9D: Wizard Polish + Runs-Results Detail Pane

**Date**: 2026-07-07
**Status**: ✅ All four sub-phases implemented & tested
**Tests**: 59 new, **1125 total passing, 18 skipped** (up from 1066 / 16)

Phase 9C plugged the calc-page validation gap. Phase 9D is a four-pronged
polish + feature pass that turned three known rough edges and one
deferred feature into shipped code:

| Sub | Scope | New tests |
|---|---|---|
| 9D-1 | Mirror 9C's validation pattern onto `_WorkflowPage` | 15 |
| 9D-2 | Drag-and-drop onto `_XyzPage` | 10 |
| 9D-3 | `ResultDetailPane` on the runs-results page | 24 |
| 9D-4 | Recent-presets strip in the calc page (in-memory MRU) | 10 |

---

## 9D-1 — `_WorkflowPage.isComplete()` validation

**Mirror of Phase 9C, same template.** Until now, `QWizardPage.isComplete()`
returned `True` by default on the workflow page — meaning the user could
click Finish with no steps selected, an empty work-dir name, or duplicate
advanced options, and only learn about it when the YAML preview failed.
This is a real UX bug, not a nice-to-have.

### What changed (`confflow_wizard_dialog.py`)

- New validation state on `_WorkflowPage`: `_touched: set[str]`, `_errors: dict[str, str]`, `_was_complete: bool | None`
- Three new hint labels: `work_dir_hint`, `steps_hint`, `adv_hint`
- `isComplete()` override with the same `_was_complete`-before-emit
  re-entry safety pattern that 9C established for `_CalcPage`
- Signals: `textChanged` / `editingFinished` on `work_dir_edit`,
  `textChanged` on `adv_edit`, `toggled` on every step `QCheckBox`

### Validation rules

| Field | Rule |
|---|---|
| `work_dir_edit` | `text().strip()` non-empty + no `/` or `\` |
| `_step_checks` | At least one step selected |
| `adv_edit` | No duplicate keys in key=value lines |

The duplicate-key check re-parses `adv_edit.toPlainText()` and reports
the first conflicting key in the hint message.

### Tests (`tests/test_confflow_wizard_workflow_validation.py`)

15 pytest-qt tests covering defaults, empty / whitespace / slash work-dir,
no-steps-selected, duplicate adv keys, hint labels start empty,
hint appears / clears on editingFinished, `_touched` tracking for both
text and step-checkbox fields, and a positive case (unique adv keys stay valid).

### Subtle bug discovered during subagent run

The subagent that implemented this initially added a `_on_step_toggled`
helper rather than reusing `_on_spin_touched` from 9C — the name was
misleading for checkboxes. Behavior matches; naming is clearer.

---

## 9D-2 — Drag-and-drop onto `_XyzPage`

**The data path was already there from 9B** (`add_directory` / `_try_add_path` /
`_refresh_count`). 9D-2 only adds the Qt event hooks to wire OS-level
drag-and-drop into those existing APIs.

### What changed (`confflow_wizard_dialog.py`)

- `self.list.setAcceptDrops(True)` + `setDropIndicatorShown(True)`
- Three event overrides on `_XyzPage`:
  - `dragEnterEvent` — accept iff mime has at least one local `QUrl`
  - `dragMoveEvent` — mirror dragEnterEvent so the drop indicator stays lit
  - `dropEvent` — route each URL: directory → `add_directory(p, recursive=...)`,
    `.xyz` file → `_try_add_path(p)`, anything else → silently skipped
- `setSubTitle(...)` updated to mention drag-and-drop

### Tests (`tests/test_confflow_wizard_xyz_drop.py`)

10 tests using the established `MagicMock + QMimeData` pattern from
`test_gui_behavior.py:2003` (avoids spawning a real `QDragEnterEvent`,
which crashes without a native window). Covers: list accepts drops,
single `.xyz` file drop, `.txt` rejection, directory drop honours
recursive checkbox, dedup, non-local URL rejection, empty-mime rejection,
and dragEnterEvent accept / reject paths.

---

## 9D-3 — `ResultDetailPane` on the runs-results page

**A new widget class** that renders the parsed output of a Gaussian or
ORCA job below the result preview table. The page already had
`_auto_analyze` / `_analyze_workspace_files` parsing `.log` / `.out`
files; 9D-3 puts that parsed data on screen in a per-task detail view.

### What changed (`runs_results_page.py`)

- New class `ResultDetailPane(QWidget)` (≈ 195 lines) with title, status,
  SCF energy, ZPE, Gibbs, imaginary-freq count, walltime / cputime,
  termination message, error message, and a monospace geometry preview
- `_resolve_output_path(task, workspace)` — three-tier heuristic:
  1. First `*.log` in `task.task_dir`
  2. First `*.out` in `task.task_dir`
  3. `<stem>.log` / `<stem>.out` derived from `task.remote_task_files[0].stem`
     under `workspace`
- `_render_detail_for_task(task_id, task, workspace)` — uses cache keyed
  by `(task_id, mtime, size)` to avoid re-parsing
- `_on_result_row_double_clicked` dispatches to:
  - `kind="analysis"` row → render parsed result
  - `kind="uncertain"` row → show error/status with placeholder geometry
  - other → `clear()`
- `_detail_cache` added; `_ckpt_` synthetic checkpoint events clear both
  `_analyze_cache` and `_detail_cache`
- `_render_cached_detail` dispatches to `render_gaussian` / `render_orca`
  by attribute sniffing (works for both real dataclasses and MagicMocks)

### Three subagent bugs fixed during validation pass

1. **`QFont.Monospace` accessed as instance attribute, not class** —
   `font.setStyleHint(font.Monospace)` raised `AttributeError`.
   Fixed: `font.setStyleHint(QFont.Monospace)` + added missing `QFont` import.
2. **`render_geometry` wrote the literal string `"f'{n} atoms'"`** —
   f-string inside a list was a plain string, not evaluated.
   Fixed: `[f"{n} atoms", *lines]`.
3. **`_render_cached_detail` heuristic for ORCA detection was wrong** —
   it special-cased `OrcaResult` but did not handle the mock parser's
   return value shape. Worked around by checking for `total_energy_au`
   attribute (Gaussian does not have this).

### Tests (`tests/test_runs_results_detail_pane.py`)

24 tests covering the widget, the resolver, and the integration:

- Widget: starts empty, renders Gaussian, renders ORCA, imaginary-freq
  count display, error status styling, abnormal termination, clear() resets,
  `_format_seconds` formatter, missing-geometry placeholder
- Resolver: prefers `.log` over `.out`, falls back to `.out`, returns
  `None` on empty/missing dir, derives from remote file stem
- Integration: page has the pane, end-to-end Gaussian render, end-to-end
  ORCA render, cache hit (no second parse), missing output shows
  "Output file not found", parser exception shows "Parse error", cache
  cleared on `_ckpt_`, double-click on analysis row triggers render,
  double-click on uncertain row shows error, double-click on empty row clears

---

## 9D-4 — Recent-presets strip in the calc page

**In-memory MRU strip** next to the preset combo. No persistence —
Phase 9D explicitly chose the lowest-friction option per the work plan
("if doing #1, do in-memory MRU first").

### What changed (`confflow_wizard_dialog.py`)

- New constant `_MAX_RECENT_PRESETS = 5`
- New `_CalcPage` attributes:
  - `recent_presets: OrderedDict[str, None]` — MRU list
  - `recent_strip: QHBoxLayout` and `recent_strip_wrap: QWidget` — the
    container
  - `recent_label: QLabel("Recent:")` — the "Recent:" prefix label
- `_record_recent_preset(name)` — `move_to_end(last=False)` to promote
  to the front; pop the rightmost entry (oldest) when over the cap
- `_refresh_recent_strip()` — clears buttons (keeps label + stretch),
  rebuilds one `QToolButton` per preset in MRU order, shows the wrap
  iff MRU is non-empty
- `_apply_recent_preset(name)` — `findData` then `setCurrentIndex` +
  manual `_on_preset_changed(idx)` call (Qt skips duplicate-index
  signals, so we cannot rely on the combo alone)
- `_on_preset_changed` calls `_record_recent_preset` + `_refresh_recent_strip`
  at the end

### Subtle bug fixed during validation pass

Initial `_record_recent_preset` used `pop(name) + dict[name] = None`,
which on a re-pick removed the entry and re-appended it to the **end**
(rightmost). With our convention "MRU = leftmost", this silently
demoted re-picked presets to the **oldest** position — opposite of intent.
Fixed with explicit `move_to_end(name, last=False)` and matching
`popitem(last=True)` for the trim.

### Tests (`tests/test_confflow_wizard_recent_presets.py`)

10 tests covering: strip starts hidden, record adds to MRU, strip
appears after first pick, button count matches preset count,
dedup-on-repick moves to front, cap at `_MAX_RECENT_PRESETS`, strip
re-renders on re-pick, clicking a recent button re-applies preset fields,
isolation between wizard instances, unknown-id apply is a no-op.

---

## Files Changed / Added

| File | Change |
|---|---|
| `src/jobdesk_app/gui/dialogs/confflow_wizard_dialog.py` | `_WorkflowPage` validation + 3 hint labels; `_XyzPage` drop events; `_CalcPage` recent strip + `QToolButton` + `QWidget` imports |
| `src/jobdesk_app/gui/pages/runs_results_page.py` | New `ResultDetailPane` class (195 lines); `_resolve_output_path`; `_render_detail_for_task`; `_on_result_row_double_clicked`; `_detail_cache`; `QFont` import |
| `tests/test_confflow_wizard_workflow_validation.py` | New 15-test suite (9D-1) |
| `tests/test_confflow_wizard_xyz_drop.py` | New 10-test suite (9D-2) |
| `tests/test_runs_results_detail_pane.py` | New 24-test suite (9D-3) |
| `tests/test_confflow_wizard_recent_presets.py` | New 10-test suite (9D-4) |

## Test Totals

```
================================= 1125 passed, 18 skipped =================================
```

Wizard files combined:
```
test_confflow_wizard_dialog.py               9 passed, 3 skipped
test_confflow_wizard_calc_validation.py      12 passed
test_confflow_wizard_workflow_validation.py  15 passed
test_confflow_wizard_xyz_batch.py            13 passed
test_confflow_wizard_xyz_drop.py             10 passed
test_confflow_wizard_recent_presets.py       10 passed
                                            ─────────────
                                             69 passed, 3 skipped
```

Runs-results:
```
test_runs_results_detail_pane.py             24 passed
test_gui_behavior.py::TestRunsPage           82 passed
```

---

## Cross-cutting notes

### Python import path gotcha

Two `jobdesk_app` source trees exist on disk:
- `C:\dft\tool\jobdesk\src\jobdesk_app\` (stale, `pip install -e` from the old checkout)
- `C:\dft\tool\jobdesk-dev\src\jobdesk_app\` (current)

`pyproject.toml` declares `pythonpath = ["src"]`, which pytest prepends
to `sys.path`. That makes `python -m pytest` resolve to the **dev
workspace**, not the stale tree. Confirmed by `python -m pytest
tests/test_confflow_wizard_workflow_validation.py` succeeding — those
tests reference `_WorkflowPage.isComplete` which only exists in the
dev tree.

### Re-entry safety recap

Both 9C and 9D-1 hit the same QWizard re-entry hazard during
`isComplete()`: emitting `completeChanged` makes QWizard synchronously
re-query `isComplete()`, which would loop forever if `_was_complete`
was updated **after** the emit. Both pages now use the
"update-then-emit" order with an inline comment explaining the trap.

### What's Next (Phase 9E candidates)

1. **Persist recent presets** — promote 9D-4 from in-memory to YAML-on-disk
   via `PresetFavouriteStore` (mirroring `RunProfileStore.save_command_history`).
   Adds cross-session memory; small extra effort.
2. **Extend mock `l1.exe`** to emit thermo / frequencies lines so the
   `ResultDetailPane` ZPE/Gibbs/imag-freq columns can be filled by
   the mock instead of only by fixtures.
3. **Reuse `_auto_analyze` results** for the detail pane (currently
   re-parses). Eliminates double-parse when preview & detail both fire.
4. **Drag-drop onto the local-files table** in `file_transfer_page.py`
   (already supports internal drag, just needs OS drag).
5. **Cross-page wizard "save as draft"** — persist wizard form state so
   the user can quit mid-flow and resume.

The recommendation from the work-plan survey still stands: (1) and
(2) are the highest-value, lowest-risk follow-ups. (3) is a
performance cleanup only. (4) and (5) expand UX surface area without
closing known gaps.