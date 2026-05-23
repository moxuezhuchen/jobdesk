# Full Review Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the correctness, remote-safety, recovery, packaging, CI, and documentation defects identified in the 2026-05-23 full review.

**Architecture:** Keep JobDesk focused on single-run remote execution and ConfFlow result presentation. Strengthen the existing run contract with unique task identity, strict input validation, persisted execution/cancellation metadata, guarded remote operations, and atomic state files; then make tests, packaging, CI, and docs enforce that contract.

**Tech Stack:** Python 3.11+, Pydantic, Paramiko, PySide6, pytest, Ruff, setuptools/GitHub Actions.

---

### Task 1: Prevent Scientific Input and Task Identity Corruption

**Files:**
- Modify: `src/jobdesk_app/core/run.py`
- Modify: `src/jobdesk_app/core/input_builder.py`
- Test: `tests/test_run_core.py`
- Test: `tests/test_input_builder.py`

- [ ] Add a regression test proving sanitized source names such as `mol a.xyz` and `mol_a.xyz` receive distinct stable task IDs, remote directories, and declared result identities.
- [ ] Run `python -m pytest tests/test_run_core.py -q --basetemp .pytest_tmp_fix_identity_red` and confirm the collision assertion fails before production edits.
- [ ] Generate deterministic unique task IDs during plan creation and reject any remaining duplicate identity invariant.
- [ ] Add strict XYZ tests for missing rows, malformed coordinates, and extra coordinate rows; confirm they fail before input builder edits.
- [ ] Make XYZ parsing reject any atom-count or coordinate-shape mismatch before an input file is generated.
- [ ] Run `python -m pytest tests/test_run_core.py tests/test_input_builder.py -q --basetemp .pytest_tmp_fix_input_green`.

### Task 2: Make Remote Lifecycle Actions Truthful and Guarded

**Files:**
- Modify: `src/jobdesk_app/core/manifest.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `src/jobdesk_app/remote/submitter.py`
- Modify: `src/jobdesk_app/remote/ssh.py`
- Modify: `src/jobdesk_app/services/file_transfer_service.py`
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/cli.py`
- Test: `tests/test_run_service.py`
- Test: `tests/test_submitter.py`
- Test: `tests/test_ssh.py`
- Test: `tests/test_file_transfer_service.py`
- Test: `tests/test_file_transfer_page_helpers.py`
- Test: `tests/test_gui_behavior.py`
- Test: `tests/test_cli.py`

- [ ] Add tests showing cancel must call a remote cancellation implementation and preserve cancellation failure rather than marking a running task stopped.
- [ ] Persist submission scheduler/job identity and execution settings so retry and cancel use the original strategy.
- [ ] Add tests that retry preserves scheduler/resources/environment scripts and implement that behavior in the results page/service boundary.
- [ ] Add host-key-policy tests requiring explicit known-host acceptance by default; replace silent auto-accept behavior with a safe opt-in policy.
- [ ] Add remote-delete tests that deny recursive deletion without an explicit allowed root and accept only descendants of actual JobDesk run directories; implement the guard and GUI error path.
- [ ] Run focused lifecycle, SSH, transfer, CLI, and GUI tests.

### Task 3: Make Persistence, Diagnostics, and Analysis Recoverable

**Files:**
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `src/jobdesk_app/core/manifest.py`
- Modify: `src/jobdesk_app/services/gui_settings.py`
- Modify: `src/jobdesk_app/services/run_profiles.py`
- Modify: `src/jobdesk_app/gui/pages/settings_servers_page.py`
- Modify: `src/jobdesk_app/core/analyzer.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Test: `tests/test_run_service.py`
- Test: `tests/test_manifest.py`
- Test: `tests/test_gui_settings.py`
- Test: `tests/test_run_profiles.py`
- Test: `tests/test_gui_behavior.py`

- [ ] Add tests for atomic replacement and visible persistence/download errors, then replace direct writes and swallowed errors with atomic state updates and structured failure messages.
- [ ] Add a regression test for result lookup from remote task file names, then use filename-only local fallback resolution.
- [ ] Add UI/result metadata for execution/parse/review boundaries so parsed output is not presented as scientific validation.
- [ ] Keep large-log handling off the GUI thread and add a bounded-result preview regression test where practical.
- [ ] Run focused persistence and results tests.

### Task 4: Close Packaging, CI, Lint, Integration-Test, and Documentation Gaps

**Files:**
- Modify: `pyproject.toml`
- Modify: `packaging/pyinstaller/jobdesk-gui.spec`
- Modify: `.github/workflows/ci.yml`
- Modify: source/test files reported by full Ruff
- Modify: `tests/integration/test_real_sftp.py`
- Modify: `tests/integration/test_real_submitter.py`
- Modify: `tests/integration/test_real_confflow_wsl.py`
- Modify: `README.md`
- Modify: `examples/README.md`
- Modify: relevant `docs/` user-facing pages
- Create: `LICENSE`
- Create: `CHANGELOG.md`

- [ ] Package GUI resource files in wheel and PyInstaller output, and add an installed-artifact/resource assertion.
- [ ] Add `build` and `mypy` to development tooling, turn full Ruff/build/package checks into CI gates, and run the full Ruff remediation.
- [ ] Make opt-in real integration failures fail rather than skip and guard/quote remote cleanup paths.
- [ ] Remove stale user instructions for removed `jobdesk workflow` commands; document ConfFlow boundaries, validation levels, cancellation/delete behavior, and packaging/install steps.
- [ ] Add minimal project licensing and change-log metadata suitable for the current development release.

### Task 5: Full Verification

**Files:**
- Verify all modified files and generated distribution artifacts.

- [ ] Run `python -m pytest -q --basetemp .pytest_tmp_full_review_fix`.
- [ ] Run `python -m ruff check .`.
- [ ] Run `python -m mypy .`.
- [ ] Run `python -m build --outdir .pytest_tmp_full_review_dist`.
- [ ] Install or inspect the wheel in an isolated location and verify GUI SVG resources and CLI entry points.
- [ ] Run CLI help/input smoke and offscreen GUI construction/shutdown smoke.
- [ ] Run opt-in real WSL integration tests only when the configured WSL/G16/ORCA/ConfFlow prerequisites are available; report explicitly otherwise.
- [ ] Run `git diff --check` and review the complete diff before any commit request.
