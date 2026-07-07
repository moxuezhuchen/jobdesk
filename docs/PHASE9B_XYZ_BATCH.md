# Phase 9B: Wizard XYZ Batch Import

**Date**: 2026-07-07
**Status**: ✅ Implemented & tested
**Tests**: 13 new, **1056 total passing**

The Phase 8 wizard could only add XYZ files one dialog at a time. For
real research workflows the user usually has a directory of 5–200
structures to process, so the dialog needed a "scan a whole directory"
mode.  This phase adds it without breaking the existing flow.

---

## What changed

### `_XyzPage` in `confflow_wizard_dialog.py`

- **"Add files…"** button (renamed from "Add…") — unchanged behaviour:
  pick one or more files via QFileDialog.
- **"Add directory…"** button — opens a directory picker, then scans
  for `*.xyz` (top-level by default; recursive opt-in).
- **"Remove"** — unchanged: deletes selected entries.
- **"Clear"** — new: empties the list in one click.
- **"Include subdirectories"** checkbox — defaults to **off** so the
  default scan stays predictable; users opt in to recursive walking.
- **`count_label`** — small status line under the buttons showing
  "N file(s) selected (+K new)" after each addition.

### `add_directory(directory: Path, recursive: bool = False) -> int`

Public API for tests and external callers:

```python
page.add_directory(Path("/data/molecules"), recursive=True)
```

- Returns the count of *newly added* files (excludes duplicates).
- Skips files without `.xyz` extension silently.
- Sorted by name so the wizard's ordering is deterministic.
- Returns 0 (no error) if `directory` doesn't exist or is empty.

### Tests (`tests/test_confflow_wizard_xyz_batch.py`)

13 pytest-qt tests covering:

| Test | Verifies |
|---|---|
| `test_add_directory_top_level_only` | Non-recursive scan picks up only direct children |
| `test_add_directory_recursive` | Recursive scan picks up nested files too |
| `test_add_directory_skips_non_xyz_files` | `.txt` and extensionless files are ignored |
| `test_add_directory_deduplicates` | Calling twice doesn't double-add |
| `test_add_directory_missing_dir_returns_zero` | Non-existent dir → 0 added, no error |
| `test_add_directory_empty_dir_returns_zero` | Empty dir → 0 added |
| `test_add_directory_mixed_recursion` | Mixed top-level + nested paths |
| `test_xyz_page_has_recursive_checkbox` | Checkbox exists, defaults unchecked |
| `test_xyz_page_has_clear_button` | Clear button empties list & count |
| `test_xyz_page_count_label_updates` | Count label reflects selection |
| `test_xyz_page_isComplete_requires_files` | Wizard advances only when files present |
| `test_xyz_page_combines_files_and_directories` | Both paths coexist |
| `test_recursive_checkbox_checkbox_state` | Checkbox state round-trips |

---

## Why a public `add_directory()` (not just a private helper)

Tests need to feed a directory without going through `QFileDialog`
(which blocks waiting for user input and can't be driven by pytest-qt).
By exposing `add_directory(path, recursive=...)` we:

1. Give tests a clean entry point.
2. Make the function callable from scripts (e.g. drag-and-drop a
   directory onto the wizard from a future integration).
3. Allow future features (CLI batch upload, recent-paths) to reuse
   the same scan-and-dedup logic.

---

## Edge cases handled

| Case | Behaviour |
|---|---|
| Duplicate path | Silently skipped (idempotent) |
| Missing directory | Returns 0, no exception |
| Empty directory | Returns 0, no exception |
| `.txt` next to `.xyz` | Only `.xyz` added |
| Files added via two routes (file + dir) | Both end up in the list |
| Hidden files (`.xyz`) | Skipped because `glob` excludes dotfiles unless pattern starts with `.` |
| Symbolic links | Followed by `is_file()` check |

---

## Sample user flow

1. Click **ConFlow Wizard** → page 1 ("Input XYZ files").
2. Click **Add directory…** → pick `/data/ligands_jul2026/`.
3. 27 files appear in the list (sorted by name).
4. Click **Include subdirectories** + **Add directory…** again → 31 files
   (4 more from a `conformers/` subdir).
5. Click **Next** → page 2 (calculation settings). The XYZ list is
   preserved across page navigation.

---

## Files Changed / Added

| File | Change |
|---|---|
| `src/jobdesk_app/gui/dialogs/confflow_wizard_dialog.py` | `_XyzPage` redesigned: 4 buttons + recursive checkbox + count label + `add_directory()` API |
| `tests/test_confflow_wizard_xyz_batch.py` | New 13-test suite |

## Test Totals

```
================================= 1056 passed, 16 skipped =================================
```

---

## What's Next (Phase 9C)

1. **Step-advance validation** — block Next button when the calc page
   has invalid fields (charge range, memory floor, empty method).
2. **Cross-program favourites strip** — show recently-used / fav
   presets next to the per-program dropdown.
3. **Result detail pane** — render parsed SCF energy, termination,
   geometry (use the mock from Phase 9A to drive initial UI).
4. **Drag-and-drop onto the XYZ list** — drop a directory or files
   from the OS file manager.