# GUI Single-Run Automation and ConfFlow Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make JobDesk execute, monitor, download, and analyze one submitted computation automatically, including a ConfFlow run treated as one externally managed task.

**Architecture:** Keep JobDesk responsible for remote execution lifecycle and result presentation, not scientific workflow orchestration. Extend the existing run contract with supporting input files and declared result artifacts, add a ConfFlow adapter that produces one task from one XYZ plus one YAML configuration, and display ConfFlow's `run_summary.json` read-only in the existing Runs/Results GUI.

**Tech Stack:** Python, PySide6, existing `RunService`/SSH/SFTP layer, pytest/pytest-qt, ConfFlow JSON output contract, optional real WSL validation.

---

## Product Boundary

JobDesk owns:

- Uploading inputs required by one executable invocation.
- Submitting and monitoring that invocation on a configured server.
- Automatically downloading declared output artifacts after completion.
- Displaying status, failure details, logs, and parsed results.

ConfFlow owns:

- Its YAML-defined internal steps (`confgen`, `calc`, `refine`).
- Gaussian/ORCA calls made inside those steps.
- Resume semantics, failed-step reruns, checkpointing, and workflow statistics.

JobDesk must not create or advance ConfFlow's internal steps as JobDesk runs.

## Verified Existing Context

- The visible GUI mounts `FileTransferPage`, `RunsResultsPage`, and `SettingsServersPage` in `src/jobdesk_app/gui/main_window.py`.
- The old `New Workflow` control exists only in `src/jobdesk_app/gui/pages/runs_page.py`, which is no longer mounted by `MainWindow`.
- `RunsResultsPage` already refreshes active runs and downloads completed Gaussian/ORCA outputs, but it lacks a generic artifact contract and ConfFlow summary rendering.
- `RunSpec` currently turns each selected source into an independent task; this cannot represent one ConfFlow task that needs both `molecule.xyz` and `confflow.yaml`.
- ConfFlow's public invocation is `confflow <input.xyz> -c <config.yaml> [-w <work_dir>]`.
- ConfFlow writes `<input_stem>.txt`, `<input_stem>min.xyz`, `<work_dir>/run_summary.json`, and `<work_dir>/workflow_stats.json`; `run_summary.json` is the stable first integration surface.

## User Flow

For Gaussian and ORCA, preserve the existing submit path and make completion handling automatic.

For ConfFlow:

1. User selects one local or remote `.xyz` input in Files.
2. User selects `Run ConfFlow`, chooses one YAML configuration, server destination, and optional `Resume`.
3. JobDesk uploads the XYZ and YAML when local files are used and submits one command:

```bash
confflow molecule.xyz -c confflow.yaml -w molecule_confflow_work
```

4. Runs automatically monitors the JobDesk task.
5. On task completion, JobDesk downloads ConfFlow report and JSON/XYZ artifacts.
6. Results shows overall status, conformer counts, duration, per-step summary, lowest-energy conformer, and paths to detailed outputs.

## Files and Responsibilities

New modules:

- `src/jobdesk_app/services/program_adapters.py`: constructs program-specific commands and result artifact declarations; initially contains `ConfFlowAdapter`.
- `src/jobdesk_app/services/confflow_results.py`: parses only ConfFlow's downloaded `run_summary.json` into GUI-facing values.
- `src/jobdesk_app/gui/dialogs/confflow_run_dialog.py`: collects ConfFlow-specific input/config/resume fields without adding workflow semantics.

Existing modules to change:

- `src/jobdesk_app/core/run.py`: add supporting files and declared artifact templates to the single-task run contract.
- `src/jobdesk_app/core/manifest.py`: persist rendered remote artifact paths per task.
- `src/jobdesk_app/services/run_service.py`: download declared artifact paths while preserving current extension-based Gaussian/ORCA behavior.
- `src/jobdesk_app/services/gui_settings.py`: expose a built-in ConfFlow program profile.
- `src/jobdesk_app/gui/pages/file_transfer_page.py`: launch ConfFlow as one task and emit submitted run ids.
- `src/jobdesk_app/gui/pages/runs_results_page.py`: automatically select/monitor new runs and render ConfFlow summaries.
- `src/jobdesk_app/gui/pages/runs_page.py`: remove the dormant `New Workflow` UI path.
- `src/jobdesk_app/gui/dialogs/workflow_dialog.py`: delete after the last GUI reference is removed.
- `docs/WSL_WORKFLOW_SMOKE.md`: mark JobDesk-owned workflow testing as legacy scope.
- `docs/superpowers/plans/2026-05-23-workflow-auto-progress.md`: already marked superseded by this plan.

The existing CLI workflow service and its tests are not removed in this change. They remain hidden compatibility code until a separate deprecation decision is made.

### Task 1: Remove Dormant Workflow GUI Exposure

**Files:**

- Modify: `src/jobdesk_app/gui/pages/runs_page.py`
- Delete: `src/jobdesk_app/gui/dialogs/workflow_dialog.py`
- Modify: `tests/test_gui_imports.py`
- Modify: `docs/WSL_WORKFLOW_SMOKE.md`

- [ ] **Step 1: Write a failing regression test for the legacy page**

Add a construction assertion that the legacy page cannot expose a workflow launch control:

```python
def test_legacy_runs_page_has_no_workflow_launch_action(qtbot, app_state):
    from jobdesk_app.gui.pages.runs_page import RunsPage

    page = RunsPage(app_state, log_cb=lambda msg: None, status_cb=lambda msg: None)
    qtbot.addWidget(page)

    assert not hasattr(page, "new_workflow_btn")
    assert not hasattr(page, "_start_workflow")
```

- [ ] **Step 2: Run the test and observe the intended failure**

Run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/test_gui_imports.py -q --basetemp .pytest_tmp_remove_workflow_ui_red
```

Expected: FAIL because `RunsPage` currently owns `new_workflow_btn` and `_start_workflow`.

- [ ] **Step 3: Remove only the dormant GUI workflow path**

In `src/jobdesk_app/gui/pages/runs_page.py`, remove the workflow toolbar construction, its translation assignment, and `_start_workflow()`. Delete `src/jobdesk_app/gui/dialogs/workflow_dialog.py` after verifying no remaining references:

```powershell
rg -n "WorkflowDialog|new_workflow_btn|_start_workflow" src tests
```

Expected after the edit: no reference under `src/jobdesk_app/gui/`.

- [ ] **Step 4: Mark old workflow documentation as non-product scope**

At the top of `docs/WSL_WORKFLOW_SMOKE.md`, add:

```markdown
> Legacy validation note: this document covers previously implemented JobDesk
> workflow experiments. The GUI product path now focuses on single-run
> execution and result analysis; multi-step orchestration belongs to ConfFlow.
```

- [ ] **Step 5: Run focused GUI validation**

Run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/test_gui_imports.py tests/test_gui_behavior.py -q --basetemp .pytest_tmp_remove_workflow_ui
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/gui/pages/runs_page.py tests/test_gui_imports.py docs/WSL_WORKFLOW_SMOKE.md
git add -u src/jobdesk_app/gui/dialogs/workflow_dialog.py
git commit -m "fix(gui): remove dormant workflow launch path"
```

### Task 2: Represent One Task with Supporting Inputs and Declared Artifacts

**Files:**

- Modify: `src/jobdesk_app/core/run.py`
- Modify: `src/jobdesk_app/core/manifest.py`
- Modify: `src/jobdesk_app/services/run_service.py`
- Modify: `tests/test_run_core.py`
- Modify: `tests/test_manifest.py`
- Modify: `tests/test_run_service.py`

- [ ] **Step 1: Write failing core-model tests**

Add tests that define the required contract:

```python
def test_build_run_plan_tracks_supporting_files_and_artifacts():
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/tmp/conf",
        command_template="confflow {name} -c confflow.yaml -w {basename}_confflow_work",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/tmp/conf/water.xyz")],
        supporting_sources=[RunSource("/tmp/conf/confflow.yaml")],
        result_templates=[
            "{basename}.txt",
            "{basename}min.xyz",
            "{basename}_confflow_work/run_summary.json",
            "{basename}_confflow_work/workflow_stats.json",
        ],
    )

    plan = build_run_plan(spec, run_id="run001")

    assert plan.tasks[0].supporting_paths == ["/tmp/conf/confflow.yaml"]
    assert plan.tasks[0].remote_result_files == [
        "water.txt",
        "watermin.xyz",
        "water_confflow_work/run_summary.json",
        "water_confflow_work/workflow_stats.json",
    ]
```

Add a manifest round-trip test:

```python
def test_manifest_round_trip_preserves_remote_result_files(tmp_path):
    task = TaskRecord(
        task_id="water",
        batch_id="run001",
        remote_job_dir="/tmp/conf/.jobdesk_runs/run001/water",
        remote_result_files=["water.txt", "water_confflow_work/run_summary.json"],
    )
    path = tmp_path / "manifest.tsv"

    Manifest.write(path, [task])

    assert Manifest.read(path)[0].remote_result_files == task.remote_result_files
```

- [ ] **Step 2: Write a failing path-safety test for declared artifacts**

Declared output paths become local download destinations, so a relative-path guard is required:

```python
def test_download_completed_rejects_declared_artifact_parent_escape(tmp_path):
    service, record = _create_completed_run_with_artifacts(
        tmp_path,
        result_templates=["../outside.json"],
    )

    records, failures = service.download_completed("run001", FakeSFTP(), [])

    assert records == []
    assert failures == [("water", "unsafe declared result path: ../outside.json")]
    assert not (tmp_path / "results" / "run001" / "outside.json").exists()
```

- [ ] **Step 3: Run the tests and observe missing fields**

Run:

```powershell
pytest tests/test_run_core.py tests/test_manifest.py -q --basetemp .pytest_tmp_task_artifacts_red
```

Expected: FAIL because `RunSpec`, `RunTaskPlan`, and `TaskRecord` do not yet expose these fields.

- [ ] **Step 4: Add the smallest backwards-compatible data contract**

Implement in `src/jobdesk_app/core/run.py`:

```python
@dataclass(frozen=True)
class RunSpec:
    server_id: str
    remote_dir: str
    command_template: str
    max_parallel: int
    mode: RunMode
    sources: list[RunSource] = field(default_factory=list)
    supporting_sources: list[RunSource] = field(default_factory=list)
    result_templates: list[str] = field(default_factory=list)
    batch_size: int | None = None


@dataclass(frozen=True)
class RunTaskPlan:
    task_id: str
    source_path: str
    source_name: str
    remote_job_dir: str
    command: str
    supporting_paths: list[str] = field(default_factory=list)
    remote_result_files: list[str] = field(default_factory=list)
```

Render `result_templates` with the same placeholder values used by `_render_command()`, without invoking a shell:

```python
def _render_text_template(template: str, source: RunSource) -> str:
    values = {
        "name": source.name,
        "stem": source.stem,
        "basename": source.stem,
    }
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result
```

Populate each task with:

```python
supporting_paths=[item.path for item in spec.supporting_sources],
remote_result_files=[_render_text_template(item, source) for item in spec.result_templates],
```

In `src/jobdesk_app/core/manifest.py`, add `remote_result_files` to `_MANIFEST_COLUMNS`, `TaskRecord`, `_task_to_row()`, and `_row_to_task()` with an empty-list default so old manifest files still load.

- [ ] **Step 5: Extend run persistence and artifact download tests**

Add:

```python
def test_download_completed_downloads_declared_nested_artifacts(tmp_path):
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/tmp/conf",
        command_template="confflow {name} -c confflow.yaml -w {basename}_confflow_work",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/tmp/conf/water.xyz")],
        result_templates=["{basename}.txt", "{basename}_confflow_work/run_summary.json"],
    )
    record = service.create_run(spec, run_id="run001")
    tasks = Manifest.read(record.manifest_path)
    tasks[0].status = TaskStatus.remote_completed
    Manifest.write(record.manifest_path, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)
            Path(local_path).write_text(remote_path, encoding="utf-8")
            return TransferRecord(
                TransferDirection.download,
                str(local_path),
                remote_path,
                status=TransferStatus.transferred,
            )

    records, failures = service.download_completed("run001", FakeSFTP(), [])

    assert failures == []
    assert len(records) == 2
    assert (tmp_path / "results" / "run001" / "water" / "water_confflow_work" / "run_summary.json").exists()
```

- [ ] **Step 6: Implement declared-artifact downloads without changing Gaussian/ORCA behavior**

In `RunService.download_completed()`, choose declared paths when present, otherwise retain the current extension-pattern loop:

```python
from pathlib import PurePosixPath


def _safe_declared_result_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe declared result path: {value}")
    return path


if task.remote_result_files:
    result_files = list(task.remote_result_files)
else:
    input_name = task.remote_task_files[0] if task.remote_task_files else task.task_id
    stem = input_name.rsplit(".", 1)[0] if "." in input_name else input_name
    result_files = [f"{stem}{pat if pat.startswith('.') else pat.lstrip('*')}" for pat in patterns]

for relative_path in result_files:
    safe_relative = _safe_declared_result_path(relative_path)
    remote_file = f"{work_dir.rstrip('/')}/{relative_path}"
    local_file = task_dir.joinpath(*safe_relative.parts)
    local_file.parent.mkdir(parents=True, exist_ok=True)
    rec = sftp.download_file(remote_file, local_file, overwrite=True, skip_if_same_size=False)
```

Catch the `ValueError` per task and append `(task.task_id, str(exc))` to `failures`; do not mark that task as downloaded.

When creating `TaskRecord` from a plan, persist:

```python
remote_task_files=[task.source_name, *[Path(path).name for path in task.supporting_paths]],
remote_result_files=list(task.remote_result_files),
```

- [ ] **Step 7: Run the contract suite**

Run:

```powershell
pytest tests/test_run_core.py tests/test_manifest.py tests/test_run_service.py -q --basetemp .pytest_tmp_task_artifacts
```

Expected: PASS, including existing Gaussian/ORCA download tests.

- [ ] **Step 8: Commit**

```powershell
git add src/jobdesk_app/core/run.py src/jobdesk_app/core/manifest.py src/jobdesk_app/services/run_service.py tests/test_run_core.py tests/test_manifest.py tests/test_run_service.py
git commit -m "feat: track supporting inputs and declared result artifacts"
```

### Task 3: Add a ConfFlow Single-Task Adapter

**Files:**

- Create: `src/jobdesk_app/services/program_adapters.py`
- Modify: `src/jobdesk_app/services/gui_settings.py`
- Create: `tests/test_program_adapters.py`
- Modify: `tests/test_gui_settings.py`

- [ ] **Step 1: Write failing adapter tests**

```python
from jobdesk_app.services.program_adapters import ConfFlowAdapter


def test_confflow_adapter_builds_one_single_task_spec():
    spec = ConfFlowAdapter().build_spec(
        server_id="wsl",
        remote_dir="/tmp/conf",
        xyz_path="/tmp/conf/water.xyz",
        config_path="/tmp/conf/confflow.yaml",
        resume=False,
    )

    assert len(spec.sources) == 1
    assert [src.path for src in spec.supporting_sources] == ["/tmp/conf/confflow.yaml"]
    assert spec.command_template == (
        "confflow {name} -c confflow.yaml -w {basename}_confflow_work"
    )
    assert "water_confflow_work/run_summary.json" in build_run_plan(spec, "r1").tasks[0].remote_result_files


def test_confflow_adapter_adds_resume_only_when_selected():
    spec = ConfFlowAdapter().build_spec(
        server_id="wsl",
        remote_dir="/tmp/conf",
        xyz_path="/tmp/conf/water.xyz",
        config_path="/tmp/conf/flow.yaml",
        resume=True,
    )

    assert spec.command_template.endswith(" --resume")
```

- [ ] **Step 2: Run the tests and observe missing adapter**

Run:

```powershell
pytest tests/test_program_adapters.py -q --basetemp .pytest_tmp_confflow_adapter_red
```

Expected: FAIL because `program_adapters.py` does not exist.

- [ ] **Step 3: Implement a narrow adapter**

Create `src/jobdesk_app/services/program_adapters.py`:

```python
from __future__ import annotations

import posixpath

from ..core.run import RunMode, RunSource, RunSpec


class ConfFlowAdapter:
    result_templates = [
        "{basename}.txt",
        "{basename}min.xyz",
        "{basename}_confflow_work/run_summary.json",
        "{basename}_confflow_work/workflow_stats.json",
    ]

    def build_spec(
        self,
        *,
        server_id: str,
        remote_dir: str,
        xyz_path: str,
        config_path: str,
        resume: bool,
    ) -> RunSpec:
        config_name = posixpath.basename(config_path)
        command = f"confflow {{name}} -c {config_name} -w {{basename}}_confflow_work"
        if resume:
            command += " --resume"
        return RunSpec(
            server_id=server_id,
            remote_dir=remote_dir,
            command_template=command,
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource(xyz_path)],
            supporting_sources=[RunSource(config_path)],
            result_templates=list(self.result_templates),
        )
```

Use `shlex.quote(config_name)` in the implementation when rendering a non-simple filename; add a test with `flow config.yaml` to require safe quoting.

- [ ] **Step 4: Expose the program in editable settings**

Add a built-in settings profile:

```python
"ConfFlow": {
    "input_extensions": ".xyz",
    "command_template": "confflow {name} -c confflow.yaml -w {basename}_confflow_work",
    "download_patterns": "run_summary.json,workflow_stats.json,*.txt,*min.xyz",
},
```

This profile is descriptive and selectable; actual ConfFlow submission still uses `ConfFlowAdapter` because it needs the YAML attachment.

- [ ] **Step 5: Run adapter and settings tests**

Run:

```powershell
pytest tests/test_program_adapters.py tests/test_gui_settings.py -q --basetemp .pytest_tmp_confflow_adapter
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/services/program_adapters.py src/jobdesk_app/services/gui_settings.py tests/test_program_adapters.py tests/test_gui_settings.py
git commit -m "feat: add ConfFlow single-task adapter"
```

### Task 4: Add the ConfFlow Submission Dialog to Files

**Files:**

- Create: `src/jobdesk_app/gui/dialogs/confflow_run_dialog.py`
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/gui/i18n.py`
- Create: `tests/test_confflow_run_dialog.py`
- Modify: `tests/test_file_transfer_page_helpers.py`

- [ ] **Step 1: Write dialog validation tests**

The dialog accepts one XYZ and one YAML config, and does not expose ConfFlow internal steps:

```python
def test_confflow_dialog_requires_xyz_and_yaml(qtbot):
    dialog = ConfFlowRunDialog()
    qtbot.addWidget(dialog)

    dialog.xyz_edit.setText("water.xyz")
    dialog.config_edit.setText("")
    dialog._accept_if_valid()

    assert dialog.result() == QDialog.Rejected
    assert "YAML" in dialog.status_label.text()


def test_confflow_dialog_collects_resume_option(qtbot):
    dialog = ConfFlowRunDialog()
    qtbot.addWidget(dialog)
    dialog.xyz_edit.setText("water.xyz")
    dialog.config_edit.setText("confflow.yaml")
    dialog.resume_check.setChecked(True)

    assert dialog.resume_enabled() is True
```

- [ ] **Step 2: Add a helper test for creating exactly one ConfFlow run**

Extract a pure helper in `file_transfer_page.py`:

```python
def build_confflow_spec(server_id, remote_dir, xyz_path, config_path, resume):
    return ConfFlowAdapter().build_spec(
        server_id=server_id,
        remote_dir=remote_dir,
        xyz_path=xyz_path,
        config_path=config_path,
        resume=resume,
    )
```

Test:

```python
def test_build_confflow_spec_never_creates_yaml_as_second_task():
    spec = build_confflow_spec("wsl", "/tmp/conf", "/tmp/conf/water.xyz", "/tmp/conf/flow.yaml", False)

    assert [source.name for source in spec.sources] == ["water.xyz"]
    assert [source.name for source in spec.supporting_sources] == ["flow.yaml"]
```

- [ ] **Step 3: Implement the dialog and Files action**

Add a restrained button labelled `Run ConfFlow` beside the existing task submission controls. Its handler should:

1. Initialize the dialog's XYZ input from exactly one selected `.xyz` file.
2. Require an explicit YAML selection.
3. For local files, upload both files into the chosen remote directory.
4. Use `build_confflow_spec()` and `RunService.create_run()` to create one run.
5. Submit through the same `RunService.submit_run()` background path already used for ordinary tasks.

Add a signal:

```python
class FileTransferPage(QWidget):
    runs_submitted = Signal(list)
```

Emit it in `_on_runs_done()` with successful batch ids so the main window can take the user to live status without another click.

- [ ] **Step 4: Keep the command area generic**

Do not add workflow-step selection, internal ConfFlow step controls, or YAML editing to JobDesk. The dialog only chooses inputs and the `Resume` invocation flag.

- [ ] **Step 5: Run GUI submission tests**

Run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/test_confflow_run_dialog.py tests/test_file_transfer_page_helpers.py -q --basetemp .pytest_tmp_confflow_gui_submit
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/gui/dialogs/confflow_run_dialog.py src/jobdesk_app/gui/pages/file_transfer_page.py src/jobdesk_app/gui/i18n.py tests/test_confflow_run_dialog.py tests/test_file_transfer_page_helpers.py
git commit -m "feat(gui): submit ConfFlow as one managed task"
```

### Task 5: Make Completion Automatic in the Visible GUI

**Files:**

- Modify: `src/jobdesk_app/gui/main_window.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/services/gui_settings.py`
- Modify: `tests/test_gui_behavior.py`
- Modify: `tests/test_gui_settings.py`

- [ ] **Step 1: Write failing navigation and default-lifecycle tests**

```python
def test_submitted_run_switches_to_runs_page(main_window, qtbot):
    main_window.files_page.runs_submitted.emit(["260523-001"])

    assert main_window.shell.pages.currentWidget() is main_window.runs_page


def test_auto_refresh_and_download_are_enabled_by_default():
    settings = GuiSettings()

    assert settings.auto_refresh_enabled is True
    assert settings.auto_download_enabled is True
```

Add a page test that selecting a newly completed run triggers result preview reload after the automatic download worker finishes.

- [ ] **Step 2: Change lifecycle defaults and connect navigation**

In `GuiSettings`, set:

```python
auto_refresh_enabled: bool = True
auto_download_enabled: bool = True
```

In `MainWindow.__init__()`, connect the Files signal:

```python
self.files_page.runs_submitted.connect(self._show_submitted_runs)
```

Implement:

```python
def _show_submitted_runs(self, run_ids: list[str]) -> None:
    if run_ids:
        self.state.current_batch_id = run_ids[-1]
    self.shell.set_current(1)
```

- [ ] **Step 3: Select the submitted run and keep preview current**

In `RunsResultsPage.refresh_run_list()`, select the row matching `self.state.current_batch_id` after table population. In the auto-refresh and monitor completion callbacks, call `_load_result_preview()` for the selected record after downloads finish.

Maintain the existing background worker boundary: all SSH, SFTP, and downloads remain outside the Qt UI thread.

- [ ] **Step 4: Surface download and execution failures**

Replace silent exception swallowing in `_auto_refresh_active()` with a collected status message returned by the worker:

```python
messages: list[str] = []
...
except Exception as exc:
    messages.append(f"{record.run_id}: {exc}")
return messages
```

On completion, display a concise failure status and keep the detailed exception in the application log.

- [ ] **Step 5: Run visible GUI behavior checks**

Run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/test_gui_behavior.py tests/test_gui_settings.py -q --basetemp .pytest_tmp_single_run_auto_gui
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/gui/main_window.py src/jobdesk_app/gui/pages/runs_results_page.py src/jobdesk_app/services/gui_settings.py tests/test_gui_behavior.py tests/test_gui_settings.py
git commit -m "feat(gui): automate single-run monitoring and downloads"
```

### Task 6: Render ConfFlow Structured Results Read-Only

**Files:**

- Create: `src/jobdesk_app/services/confflow_results.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Create: `tests/test_confflow_results.py`
- Modify: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing parser tests from ConfFlow's documented JSON contract**

```python
def test_load_confflow_summary_reads_steps_and_lowest_conformer(tmp_path):
    summary_path = tmp_path / "run_summary.json"
    summary_path.write_text(json.dumps({
        "initial_conformers": 12,
        "final_conformers": 2,
        "total_duration_seconds": 33.2,
        "step_status_counts": {"completed": 2},
        "steps": [{
            "index": 1,
            "name": "opt",
            "type": "calc",
            "status": "completed",
            "input_conformers": 12,
            "output_conformers": 2,
            "failed_conformers": 0,
            "duration_seconds": 30.0,
            "output_xyz": "step_01/output.xyz",
        }],
        "lowest_conformer": {"cid": "A000001", "energy": -40.1, "xyz_path": "watermin.xyz"},
    }), encoding="utf-8")

    summary = load_confflow_summary(summary_path)

    assert summary.final_conformers == 2
    assert summary.steps[0].status == "completed"
    assert summary.lowest_energy == -40.1
```

Add malformed/missing JSON tests that return a visible parse error rather than crashing the page.

- [ ] **Step 2: Implement read-only summary models**

Create `src/jobdesk_app/services/confflow_results.py` with dataclasses:

```python
@dataclass(frozen=True)
class ConfFlowStepSummary:
    name: str
    kind: str
    status: str
    input_conformers: int
    output_conformers: int
    failed_conformers: int
    duration_seconds: float


@dataclass(frozen=True)
class ConfFlowSummary:
    initial_conformers: int
    final_conformers: int
    total_duration_seconds: float
    steps: list[ConfFlowStepSummary]
    lowest_cid: str | None
    lowest_energy: float | None
    lowest_xyz_path: str | None
```

Implement `load_confflow_summary(path: Path) -> ConfFlowSummary` by reading only the keys produced by ConfFlow's `build_run_summary()`.

- [ ] **Step 3: Recognize and display ConfFlow runs**

Add a helper to `RunsResultsPage` that looks for:

```python
result_dir / task_id / f"{task_id}_confflow_work" / "run_summary.json"
```

When found, show:

- A summary row for initial/final conformers, duration, and lowest energy.
- One table row per ConfFlow step.
- A visible parse error message when JSON is invalid.

Do not interpret or control ConfFlow's checkpoints or internal failure recovery.

- [ ] **Step 4: Run parser and GUI result checks**

Run:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/test_confflow_results.py tests/test_gui_behavior.py -q --basetemp .pytest_tmp_confflow_results
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/confflow_results.py src/jobdesk_app/gui/pages/runs_results_page.py tests/test_confflow_results.py tests/test_gui_behavior.py
git commit -m "feat(gui): display ConfFlow run summaries"
```

### Task 7: Document and Validate a Real WSL ConfFlow Task

**Files:**

- Create: `docs/WSL_CONFFLOW_SINGLE_RUN_SMOKE.md`
- Create: `tests/integration/test_real_confflow_wsl.py`
- Create: `tests/test_real_confflow_wsl_helpers.py`

- [ ] **Step 1: Define an opt-in real test gate and safe remote directory**

The integration test must skip unless all of these variables are set:

```powershell
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_confflow_test"
$env:JOBDESK_TEST_REAL_CONFFLOW = "1"
```

Use this path validator:

```python
def _safe_remote_tmp(path: str) -> str:
    if not re.fullmatch(r"/tmp/jobdesk_[A-Za-z0-9._-]+", path):
        raise ValueError("remote test directory must stay under /tmp/jobdesk_*")
    return path
```

- [ ] **Step 2: Use a minimal ConfFlow fixture**

Create these temporary local input files in the test:

```python
(tmp_path / "water.xyz").write_text(
    "3\nwater\nO 0.000000 0.000000 0.000000\n"
    "H 0.000000 0.757000 0.586000\n"
    "H 0.000000 -0.757000 0.586000\n",
    encoding="utf-8",
)
(tmp_path / "confflow.yaml").write_text(
    """
global:
  orca_path: "/opt/orca611/orca"
  cores_per_task: 1
  total_memory: "1GB"
  max_parallel_jobs: 1
  charge: 0
  multiplicity: 1
  sandbox_root: "/tmp/jobdesk_confflow_test"
  allowed_executables: ["/opt/orca611/orca"]
steps:
  - name: "sp"
    type: "calc"
    params:
      iprog: orca
      itask: sp
      keyword: "HF STO-3G"
""".lstrip(),
    encoding="utf-8",
)
```

This fixture executes one inexpensive ORCA single-point step against the verified WSL ORCA installation. The test must invoke `ConfFlowAdapter`, not the removed JobDesk workflow feature.

- [ ] **Step 3: Verify the JobDesk-managed external contract**

The gated integration test should:

1. Upload `water.xyz` and `confflow.yaml`.
2. Create exactly one JobDesk run from `ConfFlowAdapter`.
3. Submit and poll it through existing JobDesk SSH/SFTP/run status services.
4. Download declared artifacts.
5. Assert `run_summary.json` is downloaded and parsed.
6. Assert the summary has at least one completed step and a non-empty final output reference.

- [ ] **Step 4: Write the manual smoke guide**

Document:

- ConfFlow installation and `confflow --help` check in WSL.
- Input/config selection in the JobDesk GUI.
- The expected automatic transition from submitted to downloaded result preview.
- The exact downloaded artifacts and their meanings.
- The fact that JobDesk does not control ConfFlow internal steps.

- [ ] **Step 5: Run default and opt-in integration checks**

Run without real environment variables:

```powershell
pytest tests/integration/test_real_confflow_wsl.py tests/test_real_confflow_wsl_helpers.py -q --basetemp .pytest_tmp_confflow_real_default
```

Expected: helper tests pass and real test is skipped.

Run with WSL configured:

```powershell
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_confflow_test"
$env:JOBDESK_TEST_REAL_CONFFLOW = "1"
pytest tests/integration/test_real_confflow_wsl.py -q --basetemp .pytest_tmp_confflow_real_wsl
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add docs/WSL_CONFFLOW_SINGLE_RUN_SMOKE.md tests/integration/test_real_confflow_wsl.py tests/test_real_confflow_wsl_helpers.py
git commit -m "test: validate ConfFlow single-run execution on WSL"
```

## Final Verification

- [ ] Run focused service and GUI coverage:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/test_run_core.py tests/test_manifest.py tests/test_run_service.py tests/test_program_adapters.py tests/test_confflow_results.py tests/test_file_transfer_page_helpers.py tests/test_gui_behavior.py tests/test_gui_settings.py -q --basetemp .pytest_tmp_confflow_focused
```

- [ ] Run the full local suite:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
pytest tests/ -q --basetemp .pytest_tmp_confflow_full
```

- [ ] Run the repository's CI-parity suite:

```powershell
pytest tests/ -q --ignore=tests/integration --ignore=tests/test_gui_behavior.py --basetemp .pytest_tmp_confflow_ci
```

- [ ] Run real WSL ConfFlow verification when the dependency is installed:

```powershell
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_confflow_test"
$env:JOBDESK_TEST_REAL_CONFFLOW = "1"
pytest tests/integration/test_real_confflow_wsl.py -q --basetemp .pytest_tmp_confflow_real_wsl
```

- [ ] Confirm patch hygiene and no product regression:

```powershell
git diff --check
rg -n "WorkflowDialog|new_workflow_btn|_start_workflow" src/jobdesk_app/gui tests
git status --short --branch
```

Expected: no GUI workflow-launch reference, no whitespace errors, and only the intended commits ahead of the remote branch.

## Explicit Non-Goals

- Do not implement `jobdesk workflow watch`.
- Do not expose JobDesk-owned `opt_freq`, `orca_opt_freq`, or step-advance GUI controls.
- Do not parse or mutate ConfFlow checkpoints, `results.db`, failure rerun internals, or step scheduling.
- Do not add Slurm/PBS behavior beyond existing JobDesk submission support.
- Do not remove legacy CLI workflow services in this implementation; that is a separate deprecation and migration decision.
