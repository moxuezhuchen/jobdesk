# Review Findings Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the six confirmed recovery, concurrency, stream-framing, lifecycle, and documentation defects while preserving crash recovery and existing behavior.

**Architecture:** Upgrade the operation journal to schema v4 with renewable submit ownership leases, centralize delete authorization, and reuse existing guarded GUI worker infrastructure. Keep explicit migration diagnostics separate from GUI startup and add stream framing at the SSH watcher boundary.

**Tech Stack:** Python 3.13, SQLite/WAL, PySide6, Paramiko-compatible channels, pytest, Ruff, mypy.

---

## File map

- `src/jobdesk_app/services/run_repository.py`: schema v4 migration, lease persistence, owner-aware CAS, delete binding validation.
- `src/jobdesk_app/services/run_service.py`: lease heartbeat/ownership and authorized delete recovery.
- `src/jobdesk_app/services/run_coordinator.py`: separate startup journal recovery from explicit legacy retry.
- `src/jobdesk_app/cli.py`: invoke the explicit legacy retry path.
- `src/jobdesk_app/services/run_monitor.py`: incremental UTF-8/event-line framing.
- `src/jobdesk_app/gui/pages/runs_results_page.py`: guarded submit/cancel workers and busy gate.
- `tests/test_run_repository.py`, `tests/test_run_service.py`, `tests/test_run_coordinator.py`, `tests/test_cli.py`, `tests/test_run_monitor.py`, `tests/test_gui_behavior.py`: regression coverage.
- `CHANGELOG.md`, `docs/TROUBLESHOOTING.md`, `README.md`: schema v4 documentation.

### Task 1: Enforce delete operation workspace bindings

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Test: `tests/test_run_service.py`

- [ ] **Step 1: Write the failing scoped-forgery test**

Create a delete operation bound to workspace A, advance it to `metadata_deleted`, forge its payload paths and snapshot to workspace B, create `RunService(B, runs_dir=shared)`, and assert `recover_delete_operations()` reports no completion and preserves `B/results/<run_id>`.

```python
def test_scoped_delete_recovery_rejects_operation_bound_to_other_workspace(tmp_path):
    # Prepare in A, forge payload to B, recover through B.
    assert service_b.recover_delete_operations() == 0
    assert victim.exists()
    assert repository.delete_operation_workspace(operation.operation_id) == workspace_a
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_service.py::test_scoped_delete_recovery_rejects_operation_bound_to_other_workspace -q`

Expected: FAIL because the victim path is deleted or isolated.

- [ ] **Step 3: Centralize authorization**

Add a repository/service helper that requires registry membership, operation binding equality, absolute payload workspace, and matching `results_root`. Call it before every scoped or global recovery filesystem action.

```python
def _authorized_delete_workspace(self, operation: OperationRecord) -> Path:
    bound = self.repository.delete_operation_workspace(operation.operation_id)
    trusted = set(self.repository.list_workspace_roots())
    if bound is None or lexical(bound) != lexical(self.workspace_dir):
        raise ValueError("delete operation workspace binding mismatch")
    if lexical(bound) not in {lexical(path) for path in trusted}:
        raise ValueError("delete operation workspace is not trusted")
    return lexical(bound)
```

- [ ] **Step 4: Verify GREEN and focused regressions**

Run: `pytest tests/test_run_service.py -q`

Expected: PASS.

### Task 2: Add schema-v4 submit ownership leases

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Test: `tests/test_run_repository.py`
- Test: `tests/test_run_service.py`

- [ ] **Step 1: Write schema migration and lease RED tests**

Cover atomic v3-to-v4 migration, nullable legacy ownership, live lease exclusion, expired lease acquisition, owner-mismatched phase updates, and two concurrent recovery claimers.

```python
def test_v3_migration_adds_submit_lease_columns(tmp_path):
    repository = open_v3_repository_then_upgrade(tmp_path)
    assert repository.schema_version() == 4
    assert {"owner_id", "lease_expires_at"} <= operation_columns(repository)

def test_live_submit_lease_is_not_recovered(repository):
    operation = claim_with_owner(repository, "owner-a", expires_in=timedelta(minutes=2))
    assert repository.acquire_submit_recovery(operation.operation_id, "recovery-b") is False
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_repository.py -k "v3_migration_adds_submit_lease or submit_lease" -q`

Expected: FAIL because schema v4 and lease APIs do not exist.

- [ ] **Step 3: Implement schema and owner-aware CAS**

Set `SCHEMA_VERSION = 4`; migrate with `ALTER TABLE operations ADD COLUMN owner_id TEXT` and `lease_expires_at TEXT`. Extend `OperationRecord`, row conversion, claim creation, phase advancement, completion, release, and recovery APIs so active submit mutations require the matching owner. Recovery acquisition uses one `BEGIN IMMEDIATE` transaction and succeeds only for ownerless or expired leases.

```sql
UPDATE operations
SET owner_id = ?, lease_expires_at = ?, updated_at = ?
WHERE operation_id = ? AND completed_at IS NULL
  AND (owner_id IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)
```

- [ ] **Step 4: Verify repository GREEN**

Run: `pytest tests/test_run_repository.py -q`

Expected: PASS.

- [ ] **Step 5: Write service-level live-owner RED test**

Pause submitter A after `remote_started`, invoke startup recovery from service B, and assert B changes nothing; resume A and assert its job ID is durably confirmed.

```python
def test_startup_recovery_does_not_take_over_live_submit(tmp_path):
    assert recovery_service.recover_submit_operations() == 0
    release_remote_submit.set()
    assert final_task.status == TaskStatus.submitted
```

- [ ] **Step 6: Verify RED**

Run: `pytest tests/test_run_service.py::test_startup_recovery_does_not_take_over_live_submit -q`

Expected: FAIL because recovery currently takes over the operation.

- [ ] **Step 7: Implement heartbeat ownership**

Give each `submit_run` invocation a UUID owner, claim with a lease, run a bounded heartbeat while remote submission is active, stop it in `finally`, and pass owner ID to all phase/checkpoint/release calls. Stop starting new remote tasks when renewal reports lost ownership.

```python
with _SubmitLeaseHeartbeat(repository, owned_operation_ids, owner_id):
    result = submitter.submit_batch(...)
```

- [ ] **Step 8: Verify service GREEN**

Run: `pytest tests/test_run_service.py -q`

Expected: PASS.

### Task 3: Frame SSH monitor events across receive chunks

**Files:**
- Modify: `src/jobdesk_app/services/run_monitor.py`
- Test: `tests/test_run_monitor.py`

- [ ] **Step 1: Write split-line and split-UTF-8 RED tests**

Feed channel chunks `b"DONE task-"`, `b"1 0\n"` and split a multibyte UTF-8 character across chunks. Assert exactly one reconstructed callback line.

```python
assert callbacks == [("run-1", "server-1", "DONE task-1 0")]
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_monitor.py -k "split" -q`

Expected: FAIL with multiple/malformed callback lines.

- [ ] **Step 3: Implement incremental framing**

Use `codecs.getincrementaldecoder("utf-8")(errors="replace")`; append decoded text to a per-connection buffer, emit only records ending in `\n`, normalize a preceding `\r`, and discard an unterminated tail on disconnect.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_run_monitor.py -q`

Expected: PASS.

### Task 4: Guard Runs-page submit and cancel workers

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write lifecycle and overlap RED tests**

Start submit, attempt cancel before it finishes, and assert the second mutation is rejected without replacing worker tracking. Shut down with a worker pending and assert result/error callbacks do not touch feedback or refresh UI afterward.

```python
assert len(page._bg_workers) == 1
page.shutdown()
worker.result.emit(result)
assert refresh_spy.call_count == 0
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_gui_behavior.py -k "submit_cancel_worker or mutation_worker_shutdown" -q`

Expected: FAIL because `self._worker` is overwritten and callbacks remain connected.

- [ ] **Step 3: Implement guarded busy ownership**

Replace direct `BackgroundWorker` construction with `start_context_worker(..., registry_attr="_bg_workers")`. Add `_remote_mutation_running`; acquire before submit/cancel, reject or disable conflicting actions, and release from the helper's finished callback while respecting `_shutting_down`.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_gui_behavior.py -q`

Expected: PASS.

### Task 5: Separate GUI startup recovery from legacy import retry

**Files:**
- Modify: `src/jobdesk_app/services/run_coordinator.py`
- Modify: `src/jobdesk_app/cli.py`
- Test: `tests/test_run_coordinator.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write RED tests for recovery modes**

Assert ordinary `recover_operations()` does not invoke `retry_legacy_imports`; assert the CLI `run recover` explicit path does invoke it and reports migration errors.

```python
service.retry_legacy_imports.assert_not_called()
assert cli_result.exit_code == 1
assert "legacy migration failed" in cli_result.output
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_coordinator.py tests/test_cli.py -k "legacy_import or recover_operations" -q`

Expected: FAIL because the coordinator currently retries imports unconditionally.

- [ ] **Step 3: Implement explicit mode**

Keep `recover_operations()` for startup journal/orphan recovery. Add `recover_operations(include_legacy_imports: bool = False)` or a separate `recover_with_legacy_imports()` entry point, and make only the CLI explicit recovery command select legacy retry.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_run_coordinator.py tests/test_cli.py tests/test_gui_behavior.py -q`

Expected: PASS.

### Task 6: Align documentation and delivery hygiene

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `docs/TROUBLESHOOTING.md`
- Modify: `.gitignore`

- [ ] **Step 1: Add a documentation assertion or repository check**

Extend an existing architecture/documentation test to assert the current schema number and migration-chain wording appear in user documentation.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_architecture_boundaries.py -k schema_documentation -q`

Expected: FAIL because documentation currently says schema v2/v3.

- [ ] **Step 3: Update documentation and ignore rules**

Document v2 journals, v3 workspace trust bindings, and v4 submit ownership leases. Add a narrow ignore rule for repository-root pytest scratch directories without hiding product directories.

- [ ] **Step 4: Verify GREEN and inventory**

Run: `pytest tests/test_architecture_boundaries.py -q`

Run: `git status --short --untracked-files=all`

Expected: tests pass; required source/tests/specs/plans remain visible, scratch directories do not.

### Task 7: Full verification and independent review

**Files:** All changed files.

- [ ] **Step 1: Run focused combined regressions**

Run: `pytest tests/test_run_repository.py tests/test_run_service.py tests/test_run_monitor.py tests/test_run_coordinator.py tests/test_cli.py tests/test_gui_behavior.py -q --basetemp .pytest_review_fixes`

Expected: PASS.

- [ ] **Step 2: Run the complete suite**

Run: `pytest -q --basetemp .pytest_review_fixes_full`

Expected: PASS with only the established environment-dependent skips.

- [ ] **Step 3: Run quality gates**

Run: `ruff check .`

Run: `mypy src`

Run: `git diff --check origin/main`

Expected: all exit 0, aside from any existing line-ending informational warning.

- [ ] **Step 4: Review the full final diff**

Inspect `git diff --stat origin/main`, `git diff origin/main`, and all untracked files. Dispatch independent reviewers for repository/recovery, remote/monitor, and GUI/CLI areas. Resolve every confirmed Critical or Important issue and rerun the affected tests.

- [ ] **Step 5: Report without committing**

Summarize changed behavior, exact verification counts, remaining skips, and any cleanup blocked by filesystem permissions. Do not commit or push unless the user separately requests it.
