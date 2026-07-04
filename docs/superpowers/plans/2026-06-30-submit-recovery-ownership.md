# Submit Recovery Ownership and GUI Read Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent live submission recovery races, propagate persistence failures, and keep normal GUI reads off SQLite write locks.

**Architecture:** Move exception recovery into `RunService.submit_run`, scoped to operation IDs created by that call. Run GUI startup recovery once and add a repository schema-ready fast path.

**Tech Stack:** Python, SQLite, PySide6, pytest

---

### Task 1: Exact submission ownership

**Files:**
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `src/jobdesk_app/services/run_coordinator.py`
- Test: `tests/test_run_service.py`
- Test: `tests/test_run_coordinator.py`

- [ ] Add failing tests proving failures recover only operation IDs created by the current submit call.
- [ ] Run the focused tests and confirm the ownership assertions fail.
- [ ] Recover owned operations inside `RunService.submit_run` and remove coordinator run-wide recovery.
- [ ] Run focused tests and confirm they pass.

### Task 2: Persistence callback propagation

**Files:**
- Modify: `src/jobdesk_app/remote/submitter.py`
- Test: `tests/test_submitter.py`
- Test: `tests/test_run_service.py`

- [ ] Add failing tests where task checkpoint callbacks raise after remote start.
- [ ] Confirm both nohup and scheduler paths currently return instead of raising.
- [ ] Re-raise checkpoint callback failures while preserving ordinary remote submission errors.
- [ ] Run focused tests and confirm they pass.

### Task 3: One-time GUI recovery

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Test: `tests/test_gui_behavior.py`

- [ ] Add a failing test that activates the page twice and expects one recovery.
- [ ] Add a page-lifetime recovery-complete guard; later activations only refresh/start monitoring.
- [ ] Run the GUI behavior slice.

### Task 4: Read-only repository fast path

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Test: `tests/test_run_repository.py`

- [ ] Add a failing test that reopens a fully initialized repository while rejecting `BEGIN IMMEDIATE`.
- [ ] Detect schema v2 plus completed legacy import before entering initialization transaction.
- [ ] Run repository tests.

### Task 5: Verification

- [ ] Run targeted service, coordinator, submitter, GUI, and repository tests.
- [ ] Run full pytest.
- [ ] Run Ruff, mypy, and `git diff --check`.

### Follow-up Task 6: Make submission cleanup lossless

**Files:**
- Modify: `src/jobdesk_app/services/run_service.py`
- Test: `tests/test_run_service.py`

- [ ] Add failing tests for post-claim validation, false recovery results, and release exceptions masking the primary error.
- [ ] Move all post-claim work into one protected scope and accumulate cleanup diagnostics.
- [ ] Run the focused service tests.

### Follow-up Task 7: Report scheduler partial success

**Files:**
- Modify: `src/jobdesk_app/remote/submitter.py`
- Test: `tests/test_submitter.py`

- [ ] Add a failing two-task scheduler test where the second upload fails after the first checkpoint succeeds.
- [ ] Finalize `SubmitResult` from accepted task outcomes even on later failure.
- [ ] Run submitter tests.

### Follow-up Task 8: Complete repository readiness

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Test: `tests/test_run_repository.py`

- [ ] Add failing tests for reopening a v2 database in DELETE mode and opening with migration diagnostics without write initialization.
- [ ] Require WAL for the ready fast path and separate explicit legacy retry from normal open.
- [ ] Run repository tests.

### Follow-up Task 9: Move recovery to application startup

**Files:**
- Modify: `src/jobdesk_app/gui/main_window.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Test: `tests/test_gui_behavior.py`

- [ ] Add failing tests proving startup recovery begins while Files is the default page and page activation does not replay operations.
- [ ] Add MainWindow-owned startup recovery and gate run-producing UI until it finishes.
- [ ] Run GUI tests.

### Follow-up Task 10: Verify all changes

- [ ] Run all affected test modules.
- [ ] Run full pytest, Ruff, mypy, and `git diff --check`.

