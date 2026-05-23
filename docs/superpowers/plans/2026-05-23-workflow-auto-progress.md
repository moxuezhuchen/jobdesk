# Workflow Auto Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a CLI command that refreshes, downloads, and advances a workflow until it completes or reaches a reportable failure.

**Architecture:** Keep `WorkflowRunner` responsible for workflow state and downstream input generation. Add a small orchestration service responsible for one remote progress cycle: refresh step runs, download completed outputs, sync the workflow, upload generated inputs, and submit ready steps. Expose that service through `jobdesk workflow watch`; GUI background execution and new Slurm/PBS behavior remain out of scope.

**Tech Stack:** Python, argparse, existing `RunService` and remote adapters, pytest, optional real WSL Gaussian/ORCA integration tests.

---

## Scope

In scope:

- `jobdesk workflow watch <workspace> <workflow_id>` for automated CLI progression.
- A single-cycle service API usable by tests and future GUI work.
- Gaussian and ORCA output download selection.
- Clear timeout, failure, and completion reporting.
- Optional real WSL verification using the existing `wsl` server entry.

Out of scope:

- GUI background polling, notifications, or tray behavior.
- New Slurm/PBS scheduler semantics.
- Restart, retry, cancellation, or recovery after chemistry-engine failure.
- Workflow graph changes beyond the existing built-in workflows.

## Command Behavior

Proposed command:

```powershell
jobdesk workflow watch . <workflow_id> --interval-seconds 5 --timeout-seconds 3600
```

Options:

- `--once`: execute one progress cycle and return; useful for scripts and unit tests.
- `--interval-seconds N`: wait time between cycles; default `5`.
- `--timeout-seconds N`: fail with exit code `2` if the workflow is not terminal within the limit; default `3600`.

Terminal behavior:

- Exit `0` when all workflow steps are complete.
- Exit `2` on upload, submit, download, remote task failure, missing workflow, or timeout.
- With `--once`, exit `0` when a cycle succeeds even if the workflow is still running, and print the current state.

## Task 1: Add Cycle Result and Engine Output Selection

**Files:**

- Create: `src/jobdesk_app/services/workflow_driver.py`
- Create: `tests/test_workflow_driver.py`

- [ ] Write failing tests for an output-pattern helper:

```python
def test_gaussian_step_download_patterns():
    step = WorkflowStep(name="opt", command_template="g16 {name}")
    assert output_patterns_for_step(step) == ["*.log", "*.out"]


def test_orca_step_download_patterns():
    step = WorkflowStep(
        name="opt",
        command_template="$(type -P orca) {name} > {basename}.out",
    )
    assert output_patterns_for_step(step) == ["*.out"]
```

- [ ] Add a narrow result object for one cycle:

```python
@dataclass
class WorkflowCycleResult:
    completed: bool = False
    running: bool = False
    started: list[str] = field(default_factory=list)
    downloaded_run_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
```

- [ ] Implement `output_patterns_for_step()` using the step command template, consistent with the current ORCA detection logic in `WorkflowRunner`.
- [ ] Run:

```powershell
pytest tests/test_workflow_driver.py -q --basetemp .pytest_tmp_workflow_driver_patterns
```

- [ ] Commit:

```powershell
git add src/jobdesk_app/services/workflow_driver.py tests/test_workflow_driver.py
git commit -m "test: define workflow auto-progress cycle surface"
```

## Task 2: Move Submission Orchestration Out of the CLI

**Files:**

- Modify: `src/jobdesk_app/services/workflow_driver.py`
- Modify: `src/jobdesk_app/cli.py`
- Modify: `tests/test_workflow_driver.py`
- Modify: `tests/test_cli.py`

The current `_submit_workflow_steps()` function in `cli.py` owns business logic required by both `advance` and `watch`. Move this responsibility into the new service rather than having `watch` call CLI internals.

- [ ] Write failing driver tests for:

  - Uploading each `pending_uploads` entry before submitting a newly started step.
  - Returning a useful error without submitting when an upload fails.
  - Submitting existing first-step inputs when no generated upload is pending.

- [ ] Implement a `WorkflowDriver.submit_started_steps(...) -> list[str]` method using the same services and manifest behavior currently exercised by CLI tests.
- [ ] Replace `_cmd_workflow_run()` and `_cmd_workflow_advance()` calls to `_submit_workflow_steps()` with the driver method.
- [ ] Preserve current CLI output and exit codes; the existing workflow CLI tests must continue to pass unchanged except for fixtures needed to construct the driver.
- [ ] Run:

```powershell
pytest tests/test_workflow_driver.py tests/test_cli.py -q --basetemp .pytest_tmp_workflow_submit_refactor
```

- [ ] Commit:

```powershell
git add src/jobdesk_app/services/workflow_driver.py src/jobdesk_app/cli.py tests/test_workflow_driver.py tests/test_cli.py
git commit -m "refactor: centralize workflow step submission"
```

## Task 3: Implement One Automatic Progress Cycle

**Files:**

- Modify: `src/jobdesk_app/services/workflow_driver.py`
- Modify: `src/jobdesk_app/services/workflow_service.py`
- Modify: `tests/test_workflow_driver.py`
- Modify: `tests/test_workflow_service.py`

One cycle should:

1. Refresh each running step's run.
2. Download outputs only after the run is remotely complete.
3. Mark failed runs as workflow-blocking errors.
4. Call `sync_status()` after downloads are recorded.
5. Call `advance()` and submit any newly ready steps.
6. Return whether the workflow is running or complete.

- [ ] Write failing tests for:

  - Running step remains running when refresh has no completion.
  - Completed Gaussian step downloads outputs, then starts its dependent step.
  - Completed ORCA step downloads `.out`, then starts its dependent step.
  - Failed refresh/download/upload/submit yields `errors` and starts no new downstream work.
  - Final completed step produces `completed=True`.

- [ ] Add event records only where they make diagnosis materially better:

  - `step_outputs_downloaded`
  - `workflow_completed`
  - `workflow_failed`

- [ ] Keep cycle execution synchronous. Do not place sleeping or command-line printing in the service.
- [ ] Run:

```powershell
pytest tests/test_workflow_driver.py tests/test_workflow_service.py -q --basetemp .pytest_tmp_workflow_cycle
```

- [ ] Commit:

```powershell
git add src/jobdesk_app/services/workflow_driver.py src/jobdesk_app/services/workflow_service.py tests/test_workflow_driver.py tests/test_workflow_service.py
git commit -m "feat: execute one workflow progress cycle"
```

## Task 4: Add `workflow watch` CLI Loop

**Files:**

- Modify: `src/jobdesk_app/cli.py`
- Modify: `tests/test_cli.py`

- [ ] Write failing CLI tests for:

  - Parser acceptance of `workflow watch . <workflow_id> --once`.
  - A successful `--once` cycle that reports running state and returns `0`.
  - A completed workflow that reports completion and returns `0`.
  - Driver errors and timeout returning `2` with actionable output.

- [ ] Implement argument parsing and `_cmd_workflow_watch(args)`.
- [ ] For normal mode, call one cycle immediately, then sleep only while still running.
- [ ] Use `time.monotonic()` for timeout calculation.
- [ ] Print only state changes, started steps, downloaded outputs, and terminal/error messages so polling does not flood the terminal.
- [ ] Run:

```powershell
pytest tests/test_cli.py tests/test_workflow_driver.py -q --basetemp .pytest_tmp_workflow_watch_cli
```

- [ ] Commit:

```powershell
git add src/jobdesk_app/cli.py tests/test_cli.py
git commit -m "feat: add workflow watch command"
```

## Task 5: Document and Exercise Real WSL Progression

**Files:**

- Modify: `docs/WSL_WORKFLOW_SMOKE.md`
- Modify: `tests/integration/test_real_workflow_wsl.py`
- Modify: `tests/integration/test_real_workflow_orca_wsl.py`

- [ ] Add manual examples for:

```powershell
jobdesk workflow watch . <workflow_id>
jobdesk workflow watch . <workflow_id> --once
```

- [ ] Extend optional Gaussian and ORCA real WSL integration tests to drive progress through the orchestration service or `--once` command, avoiding timing-sensitive assertions on printed polling frequency.
- [ ] Keep the existing environment gates:

```powershell
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
$env:JOBDESK_TEST_REAL_G16 = "1"
$env:JOBDESK_TEST_REAL_ORCA = "1"
```

- [ ] Run real WSL tests separately so a missing local engine produces a clear diagnosis:

```powershell
pytest tests/integration/test_real_workflow_wsl.py -q --basetemp .pytest_tmp_real_g16_watch
pytest tests/integration/test_real_workflow_orca_wsl.py -q --basetemp .pytest_tmp_real_orca_watch
```

- [ ] Commit:

```powershell
git add docs/WSL_WORKFLOW_SMOKE.md tests/integration/test_real_workflow_wsl.py tests/integration/test_real_workflow_orca_wsl.py
git commit -m "test: verify workflow watch on WSL engines"
```

## Final Verification

- [ ] Run the focused workflow and CLI suite:

```powershell
pytest tests/test_workflow_driver.py tests/test_workflow_service.py tests/test_cli.py -q --basetemp .pytest_tmp_workflow_watch_focused
```

- [ ] Run the full local suite:

```powershell
pytest tests/ -q --basetemp .pytest_tmp_workflow_watch_full
```

- [ ] Run CI parity:

```powershell
pytest tests/ -q --ignore=tests/integration --ignore=tests/test_gui_behavior.py --basetemp .pytest_tmp_workflow_watch_ci
```

- [ ] Run both gated real WSL workflows when G16 and ORCA remain available:

```powershell
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
$env:JOBDESK_TEST_REAL_G16 = "1"
pytest tests/integration/test_real_workflow_wsl.py -q --basetemp .pytest_tmp_real_g16_watch

$env:JOBDESK_TEST_REAL_ORCA = "1"
pytest tests/integration/test_real_workflow_orca_wsl.py -q --basetemp .pytest_tmp_real_orca_watch
```

- [ ] Check patch hygiene and repository state:

```powershell
git diff --check
git status --short --branch
```

## Implementation Boundary

Stop after the CLI command and the optional WSL verification pass. A GUI auto-run experience should be a separate change after the polling semantics, error handling, and real engine behavior have stable test coverage.
