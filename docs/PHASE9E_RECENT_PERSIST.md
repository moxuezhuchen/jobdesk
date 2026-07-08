# Phase 9E-1: Persist Recent Presets

**Date**: 2026-07-08
**Status**: ✅ Implemented & tested
**Tests**: 9 new, **1211 total passing, 16 skipped** (up from 1202 / 16)

Phase 9D-4 added an in-memory MRU strip to the calc page — favourite
presets appeared as one-click buttons next to the preset dropdown, but
the list was forgotten at every restart. Phase 9E-1 promotes that strip
to a YAML-on-disk MRU so the user's workflow survives restarts.

This is the **highest-value, lowest-risk** follow-up identified by the
9D-4 post-mortem and listed in `PHASE9D_PLAN_RESULTS.md`'s "What's Next"
section. The work is intentionally minimal: one new service class, one
load-on-construct / save-on-record hook on the existing widget, and a
test suite covering the new persistence contract.

---

## What changed

### 1. New service — `services/recent_presets.py`

`PresetFavouriteStore` mirrors `RunProfileStore.save_command_history`'s
storage shape (a single YAML key holding an ordered list, written through
`atomic_write_text`) but is **safe to fail silently**: a missing file
yields `[]`, a corrupt YAML yields `[]`, a permission error on save logs
a warning and returns. The wizard must never crash because the user's
home directory is misbehaving.

API:

| Method | Behaviour |
|---|---|
| `load() -> list[str]` | Most-recent-first MRU; `[]` on missing / corrupt / non-list file |
| `save(presets)` | Accepts `OrderedDict` (the widget's in-memory shape) or list; writes atomically |
| `clear()` | Unlinks the YAML so the next widget sees an empty MRU |

Default path: `<app-data>/JobDesk/recent_presets.yaml` (same dir as
existing `run_profiles.yaml`).

### 2. Widget wiring — `widgets/calculation_widget.py`

- New optional kwarg on `CalculationWidget.__init__`: `preset_store: PresetFavouriteStore | None`. Default is a store against the real app-data path; tests inject a `tmp_path` store.
- New private method `_hydrate_recent_presets()` called as the final step of the constructor: clears in-memory state, loads from the store, dedupes, caps at `_MAX_RECENT_PRESETS`, then triggers `_refresh_recent_strip()` so the strip is pre-populated on first show.
- `_record_recent_preset()` now calls `self._preset_store.save(self.recent_presets)` after every MRU mutation so each pick is durable.

The store field is therefore the **single source of truth for the MRU**.
The in-memory `OrderedDict` is now a hydrated cache rather than the
authoritative copy.

### 3. Test suite — `tests/test_confflow_wizard_recent_presets.py`

The fixture now injects a `tmp_path`-backed store, so tests no longer
pollute the real `%APPDATA%\JobDesk\recent_presets.yaml`. One existing
test was renamed and re-purposed (`...isolation...` → `...shared_with_same_store...`),
and one net-new isolation test was added (two widgets with **different**
stores do not share state).

Nine new persistence tests:

| Test | Asserts |
|---|---|
| `test_late_widget_hydrates_existing_mru_on_construction` | A widget built against a pre-populated store inherits the saved MRU |
| `test_widget_picks_persist_to_disk` | Each preset selection writes the updated MRU back |
| `test_widget_survives_corrupt_disk_store` | Malformed YAML does not crash widget construction |
| `test_widget_dedupes_and_caps_on_hydration` | A store holding duplicates or more than the cap is sanitised |
| `test_widget_drops_non_string_entries_from_disk` | Non-string entries are filtered out |
| `test_store_round_trip_preserves_mru_order` | Save then load keeps original order |
| `test_store_clear_removes_disk_file` | `clear()` removes the YAML file |
| `test_default_store_path_uses_app_data_dir` | Default path lives in the JobDesk app-data dir |
| `test_recent_presets_shared_between_widget_instances` | Two widgets backed by one store see each other's picks |

---

## Files Changed / Added

| File | Change |
|---|---|
| `src/jobdesk_app/services/recent_presets.py` | New 75-line module with `PresetFavouriteStore` |
| `src/jobdesk_app/gui/widgets/calculation_widget.py` | New `preset_store` kwarg, hydrate on construct, save on record |
| `tests/test_confflow_wizard_recent_presets.py` | 9 net-new tests; fixture rewritten with `tmp_path` |

---

## Cross-cutting notes

### Why a separate store (not piggyback on `RunProfileStore`)?

`run_profiles.yaml` is keyed by `(server_id, remote_dir)` and stores
per-server command history. Recent presets are *per-user*, not
per-server / per-project, so they belong in a separate file with a
separate lifecycle. Splitting also keeps the schema simple — the MRU
file is one short YAML key, easy to inspect / hand-edit / clear.

### Why silent failure on bad disk state?

The wizard is the entry point to a multi-minute calculation pipeline.
A user who just spent ten minutes picking a workflow step should not
have the wizard refuse to open because `%APPDATA%` is read-only or the
profile file got truncated. `PresetFavouriteStore` follows the same
"warn and continue" pattern as `RunProfileStore.save_command_history`,
but adds explicit non-fatal catches at every point because the wizard
runtime is stricter than background utilities.

### Behaviour change for existing users

Old in-memory strip → now persists across restarts. The first run after
this update sees an empty MRU (clean slate); subsequent runs accumulate.
No migration needed — there is no on-disk data to migrate.

### What's Next

`PHASE9D_PLAN_RESULTS.md` listed five candidates. With 9E-1 done, the
remaining ranked options are:

| # | Item | Why |
|---|---|---|
| 9E-2 | Extend mock `l1.exe` to emit thermo / freq lines | Lets `ResultDetailPane` ZPE / Gibbs / imag-freq columns be exercised end-to-end via the mock instead of only by hand-fixtured parsers |
| 9E-3 | Reuse `_auto_analyze` results in detail pane | Performance cleanup only — re-parsing is fast for small files |
| 9E-4 | Drag-drop onto `file_transfer_page` local table | Expands drag-and-drop coverage; low risk, medium value |
| 9E-5 | Cross-page wizard "save as draft" | Resume mid-flow on quit; high UX value but larger scope |

Recommendation: **(9E-2)** — completes the mock-driven CI loop so
Gaussian and ORCA test fixtures exercise the same code paths as real
runs, no license required.
