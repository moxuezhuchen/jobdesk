# Changelog

All notable changes to JobDesk will be documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

- Schema v4 is current: v2 introduced the replayable submit/delete operation journal, v3 added trusted-workspace registry and delete-operation bindings, and v4 added UTC submit ownership leases so recovery cannot take over a live submission. Completed operations are retained for seven days, and legacy orphan `submitting` tasks are recovered into `uncertain`.
- Added explicit recovery commands for confirming or abandoning uncertain submissions and a shared, GUI-independent SSH/SFTP `SessionPool` ownership model.
- Replaced writable per-run JSON/TSV manifests with transactional SQLite persistence in the JobDesk runs directory, including one-time legacy import and migration diagnostics.
- Added a shared run coordinator for CLI and GUI lifecycle operations and separated the run monitor service from its Qt adapter.
- Added architecture-boundary, concurrent persistence, migration, coordinator, and GUI delegation regression coverage.
- Prepared the repository for public source preview under Apache License 2.0.
- Consolidated the GUI around single-task execution and ConfFlow batch submission.
- Added guarded remote cancellation, explicit SSH host-key trust configuration, and restricted recursive remote deletion.
- Hardened run persistence, result download diagnostics, task identity generation, scheduler validation, and XYZ validation.
- Added GUI button feedback polish and strengthened GUI behavior coverage.
- Included GUI resource assets in distributable packages and strengthened CI quality gates.
- Added the ConfFlow three-step wizard (`_XyzPage` / `_CalcPage` / `_WorkflowPage`) with method/basis preset dropdown, validation hints on every page, drag-and-drop onto the XYZ list, and an in-memory MRU recent-presets strip.
- Added a `ResultDetailPane` to the Runs page that renders parsed Gaussian / ORCA output on double-click (SCF energy, ZPE, Gibbs, imaginary-freq count, termination, geometry preview).
- Wired `scripts/check_public_tree.ps1` into the CI workflow so future PRs cannot leak private/internal patterns.

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
- Submit / delete operation journal (v2) with seven-day retention of completed entries.
- Trusted-workspace registry (v3) with delete-operation-to-workspace bindings.
- UTC submit-ownership leases (v4): recovery takes over only ownerless legacy submissions or expired leases.
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

[Unreleased]: https://github.com/moxuezhuchen/jobdesk/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.3.0
[0.2.x]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.2.0
[0.1.x]: https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.1.0