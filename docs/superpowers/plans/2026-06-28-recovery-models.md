# JobDesk Recovery Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make remote submission, cross-resource deletion, status refresh, and GUI session shutdown recoverable through explicit durable models.

**Architecture:** Add `uncertain` to the task state machine, upgrade SQLite to schema v2 with a general operation journal, and move shared GUI connections into a framework-neutral lease-based `SessionPool`. Repository methods return accepted update IDs so application results describe only durable state.

**Tech Stack:** Python 3.11+, sqlite3, Pydantic v2, Paramiko wrappers, PySide6, pytest/pytest-qt, Ruff, mypy.

---

### Task 1: Upgrade SQLite and add the operation journal

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `tests/test_run_repository.py`

- [ ] **Step 1: Write failing schema and phase-CAS tests**

```python
def test_schema_v1_upgrades_to_v2_with_operations(tmp_path):
    create_v1_database(tmp_path / "jobdesk.db")
    repo = RunRepository(tmp_path)
    assert repo.schema_version() == 2
    assert repo.list_operations() == []


def test_operation_phase_transition_is_compare_and_swap(tmp_path):
    repo = RunRepository(tmp_path)
    op = repo.create_operation("run-1", "submit", "claimed", {"task_ids": ["a"]})
    assert repo.advance_operation(op.operation_id, "claimed", "remote_started") is True
    assert repo.advance_operation(op.operation_id, "claimed", "confirmed") is False
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_repository.py -q --basetemp .pytest_tmp_recovery_t1_red`

Expected: failures for schema version 2 and missing operation APIs.

- [ ] **Step 3: Implement schema v2 and typed operation APIs**

Add `OperationRecord`, set `SCHEMA_VERSION = 2`, create the `operations` table/index, and implement:

```python
def create_operation(self, run_id: str, kind: str, phase: str, payload: dict[str, object]) -> OperationRecord:
    raise NotImplementedError
def advance_operation(self, operation_id: str, expected_phase: str, phase: str,
                      *, payload: dict[str, object] | None = None,
                      last_error: str | None = None,
                      complete: bool = False) -> bool:
    raise NotImplementedError
def list_operations(self, *, incomplete_only: bool = False) -> list[OperationRecord]:
    raise NotImplementedError
def prune_completed_operations(self, older_than: datetime) -> int:
    raise NotImplementedError
```

The v1-to-v2 migration must run in one transaction and perform no network access or task-state changes.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_run_repository.py -q --basetemp .pytest_tmp_recovery_t1_green`

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_repository.py tests/test_run_repository.py
git commit -m "Add durable operation journal"
```

### Task 2: Add `uncertain` and explicit recovery transitions

**Files:**
- Modify: `src/jobdesk_app/core/lifecycle.py`
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `tests/test_lifecycle.py`
- Modify: `tests/test_run_repository.py`
- Modify: `tests/test_run_service.py`

- [ ] **Step 1: Write failing state and recovery tests**

```python
def test_abandon_uncertain_returns_only_selected_tasks_to_uploaded(service):
    seed_tasks(service, uncertain=["a", "b"])
    assert service.abandon_submit("run-1", ["a"]) == ["a"]
    assert statuses(service) == {"a": "uploaded", "b": "uncertain"}


def test_confirm_uncertain_records_optional_job_id(service):
    seed_tasks(service, uncertain=["a"])
    assert service.confirm_submitted("run-1", ["a"], {"a": "123"}) == ["a"]
    task = service.repository.load_tasks("run-1")[0]
    assert (task.status, task.remote_job_id) == (TaskStatus.submitted, "123")
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_lifecycle.py tests/test_run_repository.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t2_red`

- [ ] **Step 3: Implement `TaskStatus.uncertain` and CAS recovery methods**

`abandon_submit` clears submitted/start/completion timestamps, scheduler job ID, and error text. Both operations accept only rows currently in `uncertain` and return the accepted task IDs.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_lifecycle.py tests/test_run_repository.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t2_green`

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/core/lifecycle.py src/jobdesk_app/services/run_repository.py src/jobdesk_app/services/run_service.py tests/test_lifecycle.py tests/test_run_repository.py tests/test_run_service.py
git commit -m "Add recoverable uncertain submission state"
```

### Task 3: Journal remote submission phases

**Files:**
- Modify: `src/jobdesk_app/remote/submitter.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `tests/test_submitter.py`
- Modify: `tests/test_run_service.py`

- [ ] **Step 1: Write failing phase-boundary tests**

```python
def test_crash_before_remote_started_rolls_claim_back(service):
    operation_id = seed_submit_operation(service, phase="claimed")
    service.recover_operations()
    assert task_status(service, "a") == TaskStatus.uploaded
    assert operation(service, operation_id).completed_at is not None

def test_crash_after_remote_started_recovers_as_uncertain(service):
    operation_id = seed_submit_operation(service, phase="remote_started")
    service.recover_operations()
    assert task_status(service, "a") == TaskStatus.uncertain
    assert operation(service, operation_id).completed_at is not None

def test_empty_pid_persists_uncertain_and_completes_operation(service):
    result = submit_with_nohup_stdout(service, stdout="")
    assert result.errors
    assert task_status(service, "a") == TaskStatus.uncertain
    assert service.repository.list_operations(incomplete_only=True) == []

def test_scheduler_job_id_confirms_task_and_operation_atomically(service):
    submit_with_scheduler_result(service, job_id="123")
    task = service.repository.load_tasks("run-1")[0]
    assert (task.status, task.remote_job_id) == (TaskStatus.submitted, "123")
    assert service.repository.list_operations(incomplete_only=True) == []
```

Each test injects failure at one boundary and reopens `RunRepository` before asserting durable state.

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_submitter.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t3_red`

- [ ] **Step 3: Implement journal-driven submission**

Replace the callback-only claim flow with repository transactions that create `claimed`, write `remote_started` immediately before the remote command, and commit `confirmed` or `uncertain` with task updates. Add `recover_submit_operations()`:

```python
if op.phase == "claimed":
    repository.release_claimed_tasks(op)
elif op.phase == "remote_started":
    repository.mark_operation_tasks_uncertain(op)
elif op.phase == "confirmed":
    repository.complete_confirmed_submit(op)
```

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_submitter.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t3_green`

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/remote/submitter.py src/jobdesk_app/services/run_service.py tests/test_submitter.py tests/test_run_service.py
git commit -m "Journal remote submission phases"
```

### Task 4: Make delete replayable instead of compensating

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `tests/test_run_repository.py`
- Modify: `tests/test_run_service.py`

- [ ] **Step 1: Write failing crash-recovery tests**

```python
@pytest.mark.parametrize("phase", ["prepared", "metadata_deleted", "files_deleted"])
def test_delete_recovery_resumes_each_phase(tmp_path, phase):
    service, operation_id = seed_interrupted_delete(tmp_path, phase)
    service.recover_operations()
    assert service.repository.operation(operation_id).completed_at is not None
    assert not seeded_run_path(tmp_path).exists()

def test_two_recovery_processes_do_not_advance_same_delete(tmp_path):
    first, operation_id = seed_interrupted_delete(tmp_path, "metadata_deleted")
    second = RunService(tmp_path, runs_dir=first.runs_dir)
    run_recovery_concurrently(first, second)
    assert first.repository.operation(operation_id).phase == "completed"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_repository.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t4_red`

- [ ] **Step 3: Implement delete phases and idempotent recovery**

Create `prepared` with serialized run/tasks and validated paths; delete metadata and advance to `metadata_deleted` in one transaction; delete missing-or-present paths idempotently; finish the operation. Remove exception-time `create_run` compensation. Preserve filesystem errors in `last_error`.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_run_repository.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t4_green`

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_repository.py src/jobdesk_app/services/run_service.py tests/test_run_repository.py tests/test_run_service.py
git commit -m "Make run deletion replayable"
```

### Task 5: Return only durable refresh transitions

**Files:**
- Modify: `src/jobdesk_app/remote/status_refresh.py`
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `tests/test_status_refresh.py`
- Modify: `tests/test_run_service.py`

- [ ] **Step 1: Write failing marker and partial-CAS tests**

```python
@pytest.mark.parametrize("marker, expected", [("running", TaskStatus.running),
                                                ("completed", TaskStatus.remote_completed),
                                                ("failed", TaskStatus.failed)])
def test_uncertain_applies_authoritative_marker_immediately(marker, expected):
    task = make_task(TaskStatus.uncertain)
    snapshot = remote_snapshot(marker, exit_code=0 if marker == "completed" else None)
    new_status, _ = _recover_status(TaskStatus.uncertain, snapshot, task, 86_400)
    assert new_status == expected

def test_refresh_filters_rejected_snapshots_failures_and_task_warnings(service):
    result = refresh_during_concurrent_cancel(service)
    assert result.changed_count == 0
    assert result.snapshots == []
    assert result.failures == []
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_status_refresh.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t5_red`

- [ ] **Step 3: Implement immediate markers and accepted-ID filtering**

Make repository merge return `MergeResult(tasks, accepted_task_ids)`. Rebuild task snapshots, failures, warnings, and `changed_count` from accepted IDs while preserving batch warnings.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_status_refresh.py tests/test_run_service.py -q --basetemp .pytest_tmp_recovery_t5_green`

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/remote/status_refresh.py src/jobdesk_app/services/run_repository.py src/jobdesk_app/services/run_service.py tests/test_status_refresh.py tests/test_run_service.py
git commit -m "Return only durable refresh transitions"
```

### Task 6: Add lease-based `SessionPool`

**Files:**
- Create: `src/jobdesk_app/services/session_pool.py`
- Create: `tests/test_session_pool.py`
- Modify: `src/jobdesk_app/services/protocols.py`

- [ ] **Step 1: Write failing ownership and concurrency tests**

```python
def test_same_server_leases_serialize():
    pool, probe = make_pool_with_concurrency_probe()
    run_two_leases(pool, "server-a", "server-a")
    assert probe.maximum_concurrent_use("server-a") == 1

def test_different_server_leases_overlap():
    pool, probe = make_pool_with_concurrency_probe()
    run_two_leases(pool, "server-a", "server-b")
    assert probe.maximum_total_concurrency >= 2

def test_close_returns_immediately_with_active_lease_and_release_closes_clients():
    pool, clients = make_pool()
    lease = pool.lease("server-a", server_config()).__enter__()
    assert call_with_timeout(pool.close, 0.2)
    assert not clients.ssh.close.called
    lease.release()
    clients.ssh.close.assert_called_once_with()

def test_new_lease_is_rejected_after_close():
    pool, _clients = make_pool()
    pool.close()
    with pytest.raises(RuntimeError, match="closing"):
        with pool.lease("server-a", server_config()):
            pass

def test_dead_session_is_replaced_before_yield():
    pool, clients = make_pool(first_session_alive=False)
    with pool.lease("server-a", server_config()) as lease:
        assert lease.ssh is clients.second_ssh
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_session_pool.py -q --basetemp .pytest_tmp_recovery_t6_red`

- [ ] **Step 3: Implement `SessionPool` and `SessionLease`**

Use a pool metadata lock plus one mutex per server. `close()` only marks closing and closes idle entries; lease release closes active entries after decrementing their count. Client close runs outside the metadata lock.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_session_pool.py -q --basetemp .pytest_tmp_recovery_t6_green`

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/session_pool.py src/jobdesk_app/services/protocols.py tests/test_session_pool.py
git commit -m "Add lease based session pool"
```

### Task 7: Integrate coordinator, GUI, and CLI recovery actions

**Files:**
- Modify: `src/jobdesk_app/services/run_coordinator.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/cli.py`
- Modify: `tests/test_run_coordinator.py`
- Modify: `tests/test_gui_behavior.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing interface tests**

```python
def test_cli_confirm_submitted_updates_selected_uncertain_tasks(cli_env):
    rc = main(["run", "confirm-submitted", cli_env.workspace, "run-1", "--tasks", "a"])
    assert rc == 0
    assert cli_env.task("a").status == TaskStatus.submitted

def test_cli_abandon_submit_requires_task_ids(cli_env):
    with pytest.raises(SystemExit):
        main(["run", "abandon-submit", cli_env.workspace, "run-1"])

def test_gui_exposes_recovery_only_for_uncertain_tasks(runs_page):
    runs_page.show_tasks([make_task(TaskStatus.uncertain)])
    assert runs_page.confirm_submitted_button.isVisible()
    assert runs_page.abandon_submit_button.isVisible()

def test_gui_shutdown_delegates_to_nonblocking_pool_close(runs_page):
    runs_page.shutdown()
    runs_page._session_pool.close.assert_called_once_with()

def test_coordinator_recover_replays_incomplete_operations(coordinator):
    operation_id = seed_submit_operation(coordinator.service, phase="remote_started")
    outcome = coordinator.recover_operations()
    assert outcome.errors == []
    assert operation(coordinator.service, operation_id).completed_at is not None
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_coordinator.py tests/test_cli.py tests/test_gui_behavior.py -q --basetemp .pytest_tmp_recovery_t7_red`

- [ ] **Step 3: Implement coordinator and user interfaces**

Add `confirm_submitted`, `abandon_submit`, and `recover_operations`; add the three CLI commands from the design; replace `_refresh_sessions` and `_refresh_lock` with `SessionPool` leases; add status labels and guarded GUI recovery actions.

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_run_coordinator.py tests/test_cli.py tests/test_gui_behavior.py -q --basetemp .pytest_tmp_recovery_t7_green`

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_coordinator.py src/jobdesk_app/gui/pages/runs_results_page.py src/jobdesk_app/cli.py tests/test_run_coordinator.py tests/test_gui_behavior.py tests/test_cli.py
git commit -m "Expose recovery workflows"
```

### Task 8: Migration, documentation, and full verification

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `README.md`
- Modify: `docs/TROUBLESHOOTING.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_run_repository.py`
- Modify: `tests/test_architecture_boundaries.py`

- [ ] **Step 1: Write failing legacy-state and retention tests**

```python
def test_orphan_v1_submitting_row_becomes_uncertain_during_recovery(service):
    seed_orphan_submitting_task(service, "a")
    service.recover_operations()
    assert task_status(service, "a") == TaskStatus.uncertain

def test_completed_operations_older_than_seven_days_are_pruned(repository):
    seed_completed_operation(repository, age_days=8)
    repository.prune_completed_operations(datetime.now() - timedelta(days=7))
    assert repository.list_operations() == []

def test_session_pool_has_no_gui_or_qt_dependency():
    imports = imports_in(Path("src/jobdesk_app/services/session_pool.py"))
    assert "PySide6" not in imports
    assert not any(name.startswith("jobdesk_app.gui") for name in imports)
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_repository.py tests/test_architecture_boundaries.py -q --basetemp .pytest_tmp_recovery_t8_red`

- [ ] **Step 3: Implement migration recovery and update documentation**

Document `uncertain`, manual recovery commands, operation replay, schema v2 backup requirements, and session ownership. Recovery of orphan v1 `submitting` rows occurs in `recover_operations()`, not schema initialization.

- [ ] **Step 4: Run complete verification**

```powershell
pytest -q --basetemp .pytest_tmp_recovery_full
ruff check .
mypy src
git diff --check
```

Expected: all tests and static checks pass; only the existing CRLF warning may appear.

- [ ] **Step 5: Commit**

```powershell
git add README.md CHANGELOG.md docs/TROUBLESHOOTING.md src/jobdesk_app/services/run_repository.py tests/test_run_repository.py tests/test_architecture_boundaries.py
git commit -m "Document recoverable operation models"
```

### Task 9: Scope delete recovery and report confirmed submissions

**Files:**
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `src/jobdesk_app/remote/submitter.py`
- Modify: `tests/test_run_service.py`
- Modify: `tests/test_submitter.py`

- [ ] **Step 1: Write failing regressions**

```python
def test_delete_recovery_skips_other_workspace_without_recording_error(tmp_path, runs_dir):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    owner = RunService(workspace_a, runs_dir=runs_dir)
    record = owner.create_run(RunSpec(
        server_id="s1", remote_dir="/remote/jobs", command_template="bash {name}",
        max_parallel=1, mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    ), run_id="cross_workspace")
    operation = owner.repository.prepare_delete_run(
        record.run_id, run_dir=record.run_dir,
        results_root=workspace_a / "results",
        results_dir=workspace_a / "results" / record.run_id,
    )
    outsider = RunService(workspace_b, runs_dir=runs_dir)

    assert outsider.recover_delete_operations() == 0
    stored = next(
        item for item in outsider.repository.list_operations()
        if item.operation_id == operation.operation_id
    )
    assert stored.phase == "prepared"
    assert stored.last_error is None

# Add to TestSubmit.test_nohup_without_pid_marks_tasks_uncertain:
assert result.submitted_task_count == 0
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_run_service.py::test_delete_recovery_skips_other_workspace_without_recording_error tests/test_submitter.py::TestSubmit::test_nohup_without_pid_marks_tasks_uncertain -q`

Expected: both assertions fail against the current implementation.

- [ ] **Step 3: Implement the minimal fixes**

In `RunService.recover_delete_operations`, compare the normalized operation
`results_root` with `self.workspace_dir / "results"` and skip non-matching
operations before calling `_recover_delete_operation`. In `JobSubmitter`,
initialize `submitted_task_count` to zero and set it only in confirmed success
paths.

- [ ] **Step 4: Verify GREEN and the full gate**

```powershell
pytest tests/test_run_service.py tests/test_submitter.py -q --basetemp .pytest_tmp_recovery_t9
pytest tests -q --basetemp .pytest_tmp_recovery_full
ruff check .
mypy src
git diff --check
```
