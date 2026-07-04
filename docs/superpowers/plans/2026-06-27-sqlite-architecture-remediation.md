# SQLite Architecture Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SQLite the single writable source of JobDesk run state, centralize lifecycle orchestration, and enforce the intended dependency direction without changing public CLI or GUI behavior.

**Architecture:** Add a transactional `RunRepository` backed by one `jobdesk.db` per runs directory and import existing JSON/TSV runs once. Refactor remote submit/refresh code to operate on task objects rather than persistence paths, retain `RunService` as a compatibility facade, and route duplicated GUI lifecycle flows through a typed `RunCoordinator`. Split the Qt monitor adapter from the pure watcher service and enforce package import boundaries in tests.

**Tech Stack:** Python 3.11+, stdlib `sqlite3`, Pydantic v2, PySide6, pytest/pytest-qt, Ruff, mypy.

---

### Task 1: Enforce dependency boundaries and split the monitor

**Files:**
- Create: `src/jobdesk_app/services/protocols.py`
- Create: `src/jobdesk_app/gui/run_monitor_qt.py`
- Modify: `src/jobdesk_app/services/run_monitor.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py:182-184`
- Create: `tests/test_architecture_boundaries.py`
- Modify: `tests/test_run_monitor.py`

- [ ] **Step 1: Write failing import-boundary and pure-monitor tests**

```python
def test_services_do_not_import_gui_or_qt():
    violations = scan_imports(Path("src/jobdesk_app/services"), forbidden=("jobdesk_app.gui", "PySide6"))
    assert violations == []


def test_watcher_uses_injected_ssh_factory():
    ssh = FakeSSH(events=[b"DONE task-1 0\n"])
    watcher = _Watcher("run-1", "server", "/remote/run", object(), events.append, lambda _cfg: ssh)
    watcher._run_once()
    assert events[0].task_id == "task-1"
```

- [ ] **Step 2: Run the tests and verify the current reverse dependency fails**

Run: `python -m pytest tests/test_architecture_boundaries.py tests/test_run_monitor.py -q --basetemp .pytest_tmp_sqlite_t1_red -p no:cacheprovider`

Expected: failure naming `services/run_monitor.py` imports of `PySide6` and `gui.session`.

- [ ] **Step 3: Add typed ports and make monitoring framework-neutral**

```python
class SSHClientProtocol(Protocol):
    def connect(self) -> None:
        raise NotImplementedError
    def close(self) -> None:
        raise NotImplementedError
    def run(self, command: str, timeout: int | None = None, check: bool = False) -> SSHResultProtocol:
        raise NotImplementedError
    def open_session(self) -> SSHChannelProtocol:
        raise NotImplementedError


class RunMonitor:
    def __init__(self, ssh_factory: Callable[[ServerConfig], SSHClientProtocol], callback: Callable[[DoneEvent], None]):
        self._ssh_factory = ssh_factory
        self._callback = callback
```

Move `QObject` and `Signal` into `gui/run_monitor_qt.py`; inject `services.ssh_session.create_ssh_client` from that adapter. Preserve watcher reconnect/backoff semantics.

- [ ] **Step 4: Run focused tests**

Run: `python -m pytest tests/test_architecture_boundaries.py tests/test_run_monitor.py tests/test_gui_behavior.py -q --basetemp .pytest_tmp_sqlite_t1_green -p no:cacheprovider`

Expected: all selected tests pass and the architecture scan reports no violations.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/protocols.py src/jobdesk_app/services/run_monitor.py src/jobdesk_app/gui/run_monitor_qt.py src/jobdesk_app/gui/pages/runs_results_page.py tests/test_architecture_boundaries.py tests/test_run_monitor.py
git commit -m "Decouple run monitoring from Qt"
```

### Task 2: Implement the transactional SQLite repository

**Files:**
- Create: `src/jobdesk_app/services/run_repository.py`
- Create: `tests/test_run_repository.py`
- Modify: `src/jobdesk_app/services/run_service.py` to import shared `RunRecord`

- [ ] **Step 1: Write failing schema, CRUD, summary, rollback, and concurrent-writer tests**

```python
def test_status_summary_is_derived_from_tasks(tmp_path):
    repo = RunRepository(tmp_path / "runs")
    repo.create_run(sample_record("r1"), [sample_task("a"), sample_task("b")])
    repo.update_tasks("r1", lambda tasks: [task.model_copy(update={"status": TaskStatus.running}) for task in tasks])
    assert repo.load_run("r1").status_summary == {"running": 2}


def test_transaction_rolls_back_run_and_tasks(tmp_path):
    repo = RunRepository(tmp_path / "runs")
    repo.create_run(sample_record("r1"), [sample_task("a")])
    with pytest.raises(RuntimeError):
        repo.mutate_run("r1", mutation_that_raises_after_task_update)
    assert repo.load_tasks("r1")[0].status == TaskStatus.local_ready


def test_separate_processes_do_not_lose_updates(tmp_path):
    runs_dir = tmp_path / "runs"
    repo = RunRepository(runs_dir)
    repo.create_run(sample_record("r1"), [sample_task("a"), sample_task("b")])
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=update_task_in_process, args=(runs_dir, "r1", task_id, status))
        for task_id, status in (("a", "running"), ("b", "failed"))
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert repo.load_run("r1").status_summary == {"failed": 1, "running": 1}
```

- [ ] **Step 2: Run repository tests and verify missing module failure**

Run: `python -m pytest tests/test_run_repository.py -q --basetemp .pytest_tmp_sqlite_t2_red -p no:cacheprovider`

Expected: collection failure because `jobdesk_app.services.run_repository` does not exist.

- [ ] **Step 3: Implement schema and repository**

```python
SCHEMA_VERSION = 1

class RunRepository:
    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)
        self.database_path = self.runs_dir / "jobdesk.db"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def mutate_tasks(self, run_id: str, mutation: Callable[[list[TaskRecord]], list[TaskRecord]]) -> list[TaskRecord]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            tasks = self._load_tasks(connection, run_id)
            updated = mutation(tasks)
            self._replace_tasks(connection, run_id, updated)
            return updated
```

Store task list/dict fields as JSON columns, datetimes as ISO strings, and validate all loaded rows through `TaskRecord`. Derive summaries with SQL `GROUP BY status`.

- [ ] **Step 4: Run repository tests**

Run: `python -m pytest tests/test_run_repository.py -q --basetemp .pytest_tmp_sqlite_t2_green -p no:cacheprovider`

Expected: all repository tests pass, including the two-process writer test.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_repository.py src/jobdesk_app/services/run_service.py tests/test_run_repository.py
git commit -m "Add transactional SQLite run repository"
```

### Task 3: Import legacy JSON/TSV runs once and expose failures

**Files:**
- Modify: `src/jobdesk_app/services/run_repository.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `src/jobdesk_app/cli.py`
- Modify: `tests/test_run_repository.py`
- Modify: `tests/test_run_service.py`

- [ ] **Step 1: Write failing migration tests**

```python
def test_imports_legacy_run_and_keeps_files_unchanged(tmp_path):
    run_dir = write_legacy_run(tmp_path, "240101-001")
    before = snapshot_files(run_dir)
    repo = RunRepository(tmp_path)
    assert repo.load_run("240101-001").status_summary == {"submitted": 1}
    assert snapshot_files(run_dir) == before


def test_malformed_legacy_run_is_reported_and_valid_runs_continue(tmp_path):
    write_invalid_run_json(tmp_path / "bad")
    write_legacy_run(tmp_path, "good")
    repo = RunRepository(tmp_path)
    assert [record.run_id for record in repo.list_runs()] == ["good"]
    assert repo.list_migration_errors()[0].legacy_path.endswith("bad")


def test_legacy_import_is_idempotent(tmp_path):
    write_legacy_run(tmp_path, "r1")
    RunRepository(tmp_path)
    RunRepository(tmp_path)
    assert count_rows(tmp_path / "jobdesk.db", "runs") == 1
```

- [ ] **Step 2: Run migration tests and verify failure**

Run: `python -m pytest tests/test_run_repository.py -k legacy -q --basetemp .pytest_tmp_sqlite_t3_red -p no:cacheprovider`

Expected: failures because legacy import and migration diagnostics are absent.

- [ ] **Step 3: Implement transactional importer and schema marker**

```python
def _import_legacy_runs(self, connection: sqlite3.Connection) -> None:
    if self._metadata(connection, "legacy_import_complete") == "1":
        return
    for run_dir in sorted(self.runs_dir.iterdir()):
        try:
            record = load_legacy_record(run_dir / "run.json")
            tasks = Manifest.read(run_dir / "manifest.tsv")
            self._insert_run(connection, record, tasks, ignore_existing=True)
        except Exception as exc:
            self._record_migration_error(connection, run_dir, exc)
    self._set_metadata(connection, "legacy_import_complete", "1")
```

Make `RunService.list_runs()` read repository records and provide `migration_errors()`; add a CLI warning to stderr when diagnostics exist.

- [ ] **Step 4: Run migration and compatibility tests**

Run: `python -m pytest tests/test_run_repository.py tests/test_run_service.py tests/test_cli.py -q --basetemp .pytest_tmp_sqlite_t3_green -p no:cacheprovider`

Expected: selected suites pass; malformed runs are reported rather than silently omitted.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_repository.py src/jobdesk_app/services/run_service.py src/jobdesk_app/cli.py tests/test_run_repository.py tests/test_run_service.py tests/test_cli.py
git commit -m "Migrate legacy runs into SQLite"
```

### Task 4: Remove manifest persistence from submit and status refresh paths

**Files:**
- Modify: `src/jobdesk_app/remote/submitter.py`
- Modify: `src/jobdesk_app/remote/status_refresh.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `tests/test_submitter.py`
- Modify: `tests/test_status_refresh.py`
- Modify: `tests/test_run_service.py`

- [ ] **Step 1: Write failing object-based API tests**

```python
def test_submitter_returns_updated_tasks_without_writing_manifest(tmp_path):
    tasks = [uploaded_task("a")]
    submitter = JobSubmitter(tasks=tasks, ssh=FakeSSH(), sftp=FakeSFTP(), max_parallel=1,
                             remote_batch_dir="/runs/r1", batch_id="r1")
    result = submitter.submit_batch()
    assert result.updated_tasks[0].status == TaskStatus.submitted
    assert list(tmp_path.iterdir()) == []


def test_refresh_tasks_returns_new_records_without_file_write():
    result, updated = refresh_task_statuses(fake_ssh, [submitted_task("a")], "/runs/r1", "r1")
    assert updated[0].status == TaskStatus.remote_completed
```

- [ ] **Step 2: Run focused tests and verify the APIs are missing**

Run: `python -m pytest tests/test_submitter.py tests/test_status_refresh.py -q --basetemp .pytest_tmp_sqlite_t4_red -p no:cacheprovider`

Expected: constructor/signature failures for object-based persistence-free APIs.

- [ ] **Step 3: Implement object-based submit and refresh**

```python
def refresh_task_statuses(
    ssh,
    tasks: list[TaskRecord],
    remote_batch_dir: str,
    batch_id: str,
    log_tail_lines: int = 50,
    control_subdir: str = "_batch",
    stale_timeout_seconds: int | None = DEFAULT_STALE_TIMEOUT_SECONDS,
) -> tuple[StatusRefreshResult, list[TaskRecord]]:
    updated = [task.model_copy(deep=True) for task in tasks]
    result = _refresh_tasks(
        ssh,
        updated,
        remote_batch_dir,
        batch_id,
        log_tail_lines,
        control_subdir,
        stale_timeout_seconds,
    )
    return result, updated
```

Allow `JobSubmitter` to accept tasks directly and return updated task records in `SubmitResult`. Keep legacy path wrappers only for direct backward-compatible unit tests; production `RunService` must use repository transactions and object APIs.

- [ ] **Step 4: Run remote and service tests**

Run: `python -m pytest tests/test_submitter.py tests/test_status_refresh.py tests/test_run_service.py tests/test_lifecycle.py -q --basetemp .pytest_tmp_sqlite_t4_green -p no:cacheprovider`

Expected: all selected tests pass and new `RunService` operations persist only through SQLite.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/remote/submitter.py src/jobdesk_app/remote/status_refresh.py src/jobdesk_app/services/run_service.py tests/test_submitter.py tests/test_status_refresh.py tests/test_run_service.py
git commit -m "Persist run lifecycle through SQLite"
```

### Task 5: Centralize lifecycle workflows in RunCoordinator

**Files:**
- Create: `src/jobdesk_app/services/run_coordinator.py`
- Create: `tests/test_run_coordinator.py`
- Modify: `src/jobdesk_app/cli.py`

- [ ] **Step 1: Write failing coordinator tests for partial success and cleanup**

```python
def test_refresh_and_download_is_one_durable_use_case(repository, fake_sessions):
    coordinator = RunCoordinator(repository, fake_sessions)
    outcome = coordinator.refresh_and_download("r1", ["*.out"])
    assert outcome.record.status_summary == {"downloaded": 1}
    assert outcome.errors == []


def test_submit_preserves_created_record_when_remote_submit_fails(repository, failing_sessions):
    coordinator = RunCoordinator(repository, failing_sessions)
    outcome = coordinator.create_and_submit(sample_spec())
    assert outcome.records[0].run_id in {r.run_id for r in repository.list_runs()}
    assert outcome.errors
```

- [ ] **Step 2: Run tests and verify missing coordinator failure**

Run: `python -m pytest tests/test_run_coordinator.py -q --basetemp .pytest_tmp_sqlite_t5_red -p no:cacheprovider`

Expected: collection failure because `RunCoordinator` is absent.

- [ ] **Step 3: Implement typed coordinator outcomes**

```python
@dataclass(frozen=True)
class RunOperationOutcome:
    records: list[RunRecord]
    transfer_records: list[TransferRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class RunCoordinator:
    def refresh_and_download(self, run_id: str, patterns: list[str]) -> RunOperationOutcome:
        record = self._service.load_run(run_id)
        server = self._servers_loader().servers[record.server_id]
        with self._session_factory(server) as (ssh, sftp):
            self._service.refresh_run(run_id, ssh)
            transfers, failures = self._service.download_completed(run_id, sftp, patterns)
        return RunOperationOutcome(
            records=[self._service.load_run(run_id)],
            transfer_records=transfers,
            errors=[f"{task_id}: {message}" for task_id, message in failures],
        )

    def create_and_submit(self, spec: RunSpec, *, local_dir: str = "") -> RunOperationOutcome:
        record = self._service.create_run(spec, local_dir=local_dir)
        outcome = self.submit(record.run_id)
        return RunOperationOutcome(records=[self._service.load_run(record.run_id)], errors=outcome.errors)

    def submit(self, run_id: str) -> RunOperationOutcome:
        record = self._service.load_run(run_id)
        server = self._servers_loader().servers[record.server_id]
        with self._session_factory(server) as (ssh, sftp):
            result = self._service.submit_run(run_id, ssh, sftp)
        return RunOperationOutcome(records=[self._service.load_run(run_id)], errors=list(result.errors))

    def cancel(self, run_id: str) -> RunOperationOutcome:
        record = self._service.load_run(run_id)
        server = self._servers_loader().servers[record.server_id]
        with self._ssh_session_factory(server) as ssh:
            _changed, errors = self._service.cancel_run(run_id, ssh)
        return RunOperationOutcome(records=[self._service.load_run(run_id)], errors=errors)
```

Inject server loading and SSH/SFTP session factories. The coordinator owns connection cleanup and scheduler/resource selection; it never imports Qt.

- [ ] **Step 4: Route CLI lifecycle commands through the coordinator and run tests**

Run: `python -m pytest tests/test_run_coordinator.py tests/test_cli.py tests/test_integration_safety.py -q --basetemp .pytest_tmp_sqlite_t5_green -p no:cacheprovider`

Expected: all selected tests pass with unchanged CLI exit codes and output contracts.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_coordinator.py src/jobdesk_app/cli.py tests/test_run_coordinator.py tests/test_cli.py
git commit -m "Centralize run lifecycle coordination"
```

### Task 6: Route GUI workflows through RunCoordinator

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/gui/main_window.py`
- Modify: `tests/test_gui_behavior.py`
- Modify: `tests/test_file_transfer_page_helpers.py`

- [ ] **Step 1: Write failing GUI delegation tests**

```python
def test_monitor_manual_and_timer_refresh_use_same_coordinator(runs_page):
    coordinator = MagicMock()
    runs_page._coordinator = coordinator
    runs_page._run_refresh_use_case("r1", download=True)
    coordinator.refresh_and_download.assert_called_once_with("r1", runs_page._get_download_patterns(ANY))


def test_file_page_create_submit_delegates_to_coordinator(files_page):
    files_page._coordinator = MagicMock()
    files_page._dispatch_run_specs([sample_spec()], submit=True)
    files_page._coordinator.create_and_submit.assert_called_once()
```

- [ ] **Step 2: Run GUI tests and verify missing delegation helpers**

Run: `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests/test_gui_behavior.py tests/test_file_transfer_page_helpers.py -q --basetemp .pytest_tmp_sqlite_t6_red -p no:cacheprovider`

Expected: failures because coordinator injection and shared use-case helpers do not exist.

- [ ] **Step 3: Inject coordinator and replace duplicated workflows**

```python
class MainWindow(QMainWindow):
    def __init__(self):
        self.run_repository = RunRepository(default_runs_dir())
        self.run_coordinator = RunCoordinator.from_default_config(self.run_repository)
        self.files_page = FileTransferPage(
            self.state, self._log, self._update_status, self.show_error,
            coordinator=self.run_coordinator,
        )
        self.runs_page = RunsResultsPage(
            self.state, self._log, self._update_status,
            coordinator=self.run_coordinator,
        )
```

Use one background-worker target for monitor, timer, and manual refresh. Keep UI mutation in result callbacks. Replace direct `RunService` construction in touched run lifecycle paths while preserving test injection points.

- [ ] **Step 4: Run GUI and worker tests**

Run: `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests/test_gui_behavior.py tests/test_file_transfer_page_helpers.py tests/test_gui_worker_utils.py tests/test_run_monitor.py -q --basetemp .pytest_tmp_sqlite_t6_green -p no:cacheprovider`

Expected: all selected GUI tests pass with no worker-thread widget mutation.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/gui/pages/file_transfer_page.py src/jobdesk_app/gui/pages/runs_results_page.py src/jobdesk_app/gui/main_window.py tests/test_gui_behavior.py tests/test_file_transfer_page_helpers.py
git commit -m "Delegate GUI run workflows to coordinator"
```

### Task 7: Tighten typing, document migration, and run release gates

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/TROUBLESHOOTING.md`
- Modify: `tests/test_architecture_boundaries.py`
- Modify: `tests/test_packaging_config.py`

- [ ] **Step 1: Extend boundary and configuration tests**

```python
def test_sqlite_and_coordinator_modules_check_untyped_bodies():
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    modules = flatten_mypy_checked_modules(config)
    assert "jobdesk_app.services.run_repository" in modules
    assert "jobdesk_app.services.run_coordinator" in modules
```

- [ ] **Step 2: Run tests and verify the current mypy configuration fails the new assertion**

Run: `python -m pytest tests/test_architecture_boundaries.py tests/test_packaging_config.py -q --basetemp .pytest_tmp_sqlite_t7_red -p no:cacheprovider`

Expected: failure naming missing checked modules.

- [ ] **Step 3: Update mypy scope and user documentation**

```toml
[[tool.mypy.overrides]]
module = [
    "jobdesk_app.services.run_repository",
    "jobdesk_app.services.run_coordinator",
    "jobdesk_app.services.run_monitor",
    "jobdesk_app.gui.run_monitor_qt",
]
check_untyped_defs = true
```

Document `jobdesk.db`, automatic legacy import, retained recovery files, migration diagnostics, and database backup/recovery commands. Do not claim legacy files remain live.

- [ ] **Step 4: Run focused configuration tests**

Run: `python -m pytest tests/test_architecture_boundaries.py tests/test_packaging_config.py -q --basetemp .pytest_tmp_sqlite_t7_green -p no:cacheprovider`

Expected: selected tests pass.

- [ ] **Step 5: Run full verification**

Run:

```powershell
python -m ruff check .
python -m mypy src
$env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests -q --basetemp .pytest_tmp_sqlite_full -p no:cacheprovider
$env:PYTHONUTF8='1'; python -m build --outdir .build_sqlite_review
git diff --check
```

Expected: Ruff and mypy exit 0; all non-environment-gated tests pass; build produces sdist and wheel; diff check is clean.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml README.md CHANGELOG.md docs/TROUBLESHOOTING.md tests/test_architecture_boundaries.py tests/test_packaging_config.py
git commit -m "Document and verify SQLite run storage"
```

## Completion Criteria

- [ ] `jobdesk.db` is the only writable runtime state store after migration.
- [ ] CLI and GUI concurrent writes are protected by SQLite transactions.
- [ ] Legacy JSON/TSV files are imported once, retained unchanged, and malformed runs are reported.
- [ ] Production submit, refresh, download, retry, cancel, and delete paths use repository transactions.
- [ ] GUI run workflows delegate to `RunCoordinator`.
- [ ] `services` has no Qt or GUI imports.
- [ ] Architecture boundary tests, Ruff, mypy, full pytest, and package build pass.
