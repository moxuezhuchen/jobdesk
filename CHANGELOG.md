# Changelog

All notable changes to JobDesk will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

The current schema is **schema v5**. Schema v4 databases auto-upgrade on first open.

### Fixed
- `test_architecture_boundaries.py::test_schema_documentation_describes_v2_to_v5_migration_chain`: renamed from the v4-suffix legacy name; the test and all three target documents already referenced v5 correctly.
- `tests/test_workflow_submit_gui_regression.py`: line-ending normalised from CRLF to LF.

### Changed
- **CI**: split the monolithic `test` job into four parallel jobs (`lint`, `type-check`, `build`, `test`) so that a ruff / mypy / build failure is identified without waiting for the full pytest matrix. Added `pytest-cov` with XML/term-missing reporters; coverage is uploaded as an artifact on Python 3.11 only.
- **Dependencies**: removed the vendored ConfFlow project; the `chem` extra now relies on the external `confflow>=1.3.0` package.

### Fixed (Phase 2.0 dual-entry follow-ups)
- `gui/main_window.py::_on_workflow_chosen` now opens the `SubmitDialog` with the picked preset pre-selected instead of only navigating to the Files page. The previous behaviour left the Workflow-page "Use this preset for submit" button as a dead link once the user had already browsed and picked a preset.
- `gui/main_window.py::_on_runs_go_to_submit` opens `SubmitDialog` from the Runs-page empty-state **Go to Submit** button (previously it navigated to the Workflow page and stopped). The dialog renders an amber empty-state hint and locks into Workflow mode while no files are selected, so the user can still browse presets before queuing a submission.
- `gui/dialogs/submit_dialog.py` now tolerates `files=[]` — the empty-state banner replaces the file list, **Submit ▶** is disabled, and `build_payload()` raises a clear `ValueError` if reached with no sources so the bug surfaces loudly instead of silently returning a broken payload. A new `set_files()` API lets tests and future drag-drop paths swap sources at runtime.

### Fixed
- Fix InputSourcePanel directory-drop not honoring the recursive-scan checkbox

### Added (Phase 15C — activity log persistence)
- `services.run_repository.append_activity()` / `list_recent_activity()`: new repository methods backed by the new `submit_activity_log` SQLite table (schema v5).
- `SubmitPage` activity log now persists to the repository on every `_log()` call and reloads the last 50 entries on startup, so activity survives application restarts.

## [0.5.0] — 2026-07-08

The current schema is **schema v4** (introduced in v0.2.x; retained by v0.5.0; superseded by v5 in the unreleased version). The unified Submit page consumes the v4 `RunSpec` / `WorkflowSpec` shape unchanged.

### Added (Phase 14 — unified Submit page)
- `core.submit_payload` value types: `InputSource`, `WorkflowFields`, `SubmitPayload` (frozen dataclasses, no Qt deps). `core.RunSpec.workflow_kind` is the discriminant for the page → use-case → worker boundary.
- `services.SubmitUseCase`: single entry point that turns a `SubmitPayload` into a `PreparedBatch` (local paths, remote targets, `RunSpec` list, optional `workflow.yaml` path). Pure logic — no Qt, no I/O, no network. The page-level worker callback owns uploads and `RunCoordinator.create_and_submit`.
- `gui.widgets.calculation_widget.CalculationWidget`: embedded version of the wizard's calc page (no `QWizardPage` superclass). Same field set + a `CalculationFields` value type. Recent-presets MRU stays in memory.
- `gui.widgets.workflow_widget.WorkflowWidget`: embedded version of the wizard's workflow page. Wires an optional `CalculationWidget` so `build_spec(calc)` can produce a `WorkflowSpec` without re-reading widgets.
- `gui.widgets.input_builder_widget.InputBuilderWidget`: embedded version of the InputBuilder dialog. `build_content()` / `build_content_to()` instead of `accept()` / `reject()`.
- `gui.widgets.input_source_panel.InputSourcePanel`: tabbed local / remote picker. `add_local_paths` / `add_remote_paths` / `set_sources` API; `sources_changed(list[InputSource])` signal; drag-drop accepts `.xyz` / `.gjf` / `.inp` and directories (recursive only via the "Add directory…" button, not via drop).
- `gui.pages.submit_page.SubmitPage`: first-class unified submit UI. Embeds the four widgets above + the `SubmitUseCase`. Exposes `submit_requested(SubmitPayload)`, `create_only_requested(SubmitPayload)`, `use_as_input_received(list)`. Live preview pane, activity log, server pill, max-parallel spinbox.
- Right-click context menu on `FileTransferPage.local_table` / `remote_table`: "Use as input → Submit" + "Send to ConfFlow → Submit". Emits `use_as_input_received(list[InputSource])`; `MainWindow` wires it to `SubmitPage.push_sources()` and `AppShell.set_current(1)`.

### Removed
- `gui.dialogs.confflow_wizard_dialog.ConfFlowWizard` and `_CalcPage` / `_WorkflowPage` / `_XyzPage` (QWizardPage subclasses).
- `gui.dialogs.input_builder_dialog.InputBuilderDialog` (QDialog shell).
- The `gui.dialogs` package (now empty).
- `FileTransferPage._run_selected` / `_run_selected_chunks` / `_run_confflow` / `_open_confflow_wizard` / `_on_confflow_done` / `_create_only` / `_execute_run_use_case` / `_remote_generate_gjf` / `_auto_fill_command` / `_save_command_history` / `_save_command_profile_template` / `_load_command_history` / `_load_remembered_profile` / `_apply_gui_settings_no_folder` and the run-buttons (`confflow_btn`, `run_btn`, `create_only_btn`, `command_edit`, `command_preview`, `max_parallel_spin`, `run_mode_combo`).

### Fixed
- `SubmitUseCase._build_single_specs` now sets `RunSpec.workflow_kind` from the program (`gaussian` / `orca`) instead of always defaulting to `gaussian`.
- `SubmitPage.set_server_status` had a `NameError` on `language` — fixed to use `self._language`. Also simplified the guard and added a recursive-checkbox inheritance so the Remote tab starts in sync with the Local one.

### Tests
- 1200 passed, 35 skipped.
- New: `tests/test_submit_payload.py` (13 tests), `tests/test_submit_use_case.py` (15 tests), `tests/test_input_source_panel.py` (14 tests), `tests/test_submit_page.py` (14 tests).
- Migrated: 8 wizard test files (calc / workflow / i18n / recent-presets / xyz-batch / xyz-drop / wizard-dialog / input-builder-i18n) now drive the embedded widgets directly.
- `TestFileTransferPage` (17 tests for removed run/ConFlow/command-edit paths) `pytest.skip`-ed with a clear "removed in v0.5.0 phase 14C" reason.


## [0.4.0] — 2026-07-07

### Added
- Added the ConfFlow three-step wizard (`_XyzPage` / `_CalcPage` / `_WorkflowPage`) with method/basis preset dropdown, validation hints on every page, drag-and-drop onto the XYZ list, and an in-memory MRU recent-presets strip.
- Added a `ResultDetailPane` to the Runs page that renders parsed Gaussian / ORCA output on double-click (SCF energy, ZPE, Gibbs, imaginary-freq count, termination, geometry preview).
- Added Chinese (zh) translations for the ConfFlow wizard and Input Builder dialogs (titles, subtitles, buttons, form labels, validation messages, ORCA caveat). The `tr(text, language)` helper now covers both dialogs end-to-end.
- Added `README.zh.md`, `CHANGELOG.md` per-version history, `CONTRIBUTING.md`, and `docs/architecture.md` to lower the onboarding cost for new contributors.
- Wired `scripts/check_public_tree.ps1` into the CI workflow so future PRs cannot leak private/internal patterns.

### Fixed
- `.gitignore` now excludes `tmp*/` cwd-pollution directories produced by manual shell pipeline testing.

### Tests
- 1162 passed, 18 skipped (up from 1125 / 18 at the start of Phase 10).

## [0.3.0] — 2026-07-07

### Added (Phase 7 / 8 / 9)
- ConfFlow wizard dialog with three pages (input / calculation / workflow) and live YAML preview (`docs/PHASE7_WIZARD_UX.md`).
- ORCA keyword assembly (`assemble_orca_keyword()`) with explicit "ORCA SP does not emit geometry" hint.
- Method/basis preset dropdown (8A) and pytest-qt wizard behaviour tests (8B).
- `g16` wrapper recovery flow: `scripts/restore_g16_wsl.py` + `restore_g16_wrapper/recovered_g16.sh` (`docs/PHASE8_WIZARD_AND_G16.md`).
- Mock Gaussian pipeline (`scripts/mock-gaussian/mock_l1_exe`) and six integration tests gated on the mock binary (`docs/PHASE9_GAUSSIAN_SMOKE.md`).
- XYZ directory batch import with recursive checkbox and Clear (`docs/PHASE9B_XYZ_BATCH.md`).
- `_CalcPage.isComplete()` validation: hint labels, `_touched` set, re-entry-safe `completeChanged` emit (`docs/PHASE9C_WIZARD_VALIDATION.md`).
- Wizard polish pass: workflow-page validation mirror (9D-1), drag-and-drop onto the XYZ list (9D-2), runs-results detail pane (9D-3), in-memory recent-presets MRU (9D-4) (`docs/PHASE9D_PLAN_RESULTS.md`).

### Infrastructure
- New GitHub Actions workflows: `package-smoke.yml` (PyInstaller bundle smoke), `optional-coverage.yml` (Ubuntu chem + posix tests).
- Pre-commit gate: `scripts/check_public_tree.ps1` greps for private patterns (internal IPs, VPN references, integration-test secret variable names) before publish.

### Fixed
- Pytest `basetemp` redirected to `%TEMP%/jobdesk_pytest_<hex>/` on Windows to keep repo clean.
- Background worker leak fixed by autouse `_drain_background_workers()` fixture.
- Schema v2-to-v3 migration seeded workspace trust only from live run rows; journal payloads are not trust anchors.

## [0.2.x] — 2026-06 / 2026-07

### Added (SQLite architecture remediation)
- Transactional SQLite run repository replacing per-run `run.json` / `manifest.tsv` manifests.
- One-time legacy import with migration diagnostics recorded in the database.
- v2 operation journal (submit + delete history, seven-day retention of completed entries).
- v3 trusted-workspace registry (delete-operation-to-workspace bindings).
- v4 UTC submit ownership leases (ownerless / expired takeover semantics).
- Architecture-boundary, concurrent persistence, migration, and coordinator regression coverage.
- `RunCoordinator` shared between CLI and GUI; monitor service decoupled from its Qt adapter.

### Fixed
- SSH/SFTP leases previously could be silently shared across GUI windows; now owned exclusively by `SessionPool`.
- Remote cancellation guarded against deleting protected roots.
- Manifest writes serialized to avoid concurrent corruption.

## [0.1.x] — 2026-05 / 2026-06

### Added
- Initial public-preview release.
- File transfer (list / upload / download / preview) over SSH/SFTP with external-terminal integration (Windows Terminal / PuTTY).
- Single-task Gaussian / ORCA submission: `jobdesk run create / submit / refresh / download / cancel / retry / delete`.
- Run database stored in `%APPDATA%/JobDesk/runs/jobdesk.db` (SQLite, no separate server process).
- `Run ID` format `YYMMDD-NNN`, daily-reset.
- Status auto-update via SSH `tail -f` of the remote `events.log`.
- Settings → Servers page for YAML-driven server configuration with key-based auth and SSH agent / Pageant support.
- GUI button feedback states (idle / pending / success / error) and the design system (`tokens.py`, `components.py`, `animations.py`).
- Real-Gaussian smoke (Phase 6, real ORCA on methane in WSL); later superseded by the Phase 9 mock pipeline when Gaussian licenses became unavailable.
- Apache License 2.0, `SECURITY.md`, `.gitignore` for Python caches and CI artefacts.
- GitHub Actions CI matrix on Python 3.11 / 3.12 / 3.13 (lint + mypy + build + pytest).

[Unreleased]: https://github.com/moxuezhuchen/jobdesk/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.5.0
[0.4.0]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.4.0
[0.3.0]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.3.0
[0.2.x]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.2.0
[0.1.x]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.1.0