# CatGo-Inspired GUI Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor JobDesk's GUI into a run/task-centered workbench that borrows CatGo's high-leverage ideas: job context, task details, logs, remote files, parsed results, diagnostics, and submission presets.

**Architecture:** Keep the current PySide6 `AppShell` and three top-level pages. Refactor `RunsResultsPage` by extracting data aggregation services and smaller UI components, then replace the vertical run/result split with a horizontal workbench: run list, task table, and task inspector. Avoid a full GUI rewrite, embedded terminal, visual DAG editor, or technology-stack change.

**Tech Stack:** Python 3.11, PySide6, pytest-qt, Pydantic/dataclasses, existing `RunService`, `Manifest`, SSH/SFTP wrappers, existing Gaussian/ORCA/ConfFlow parsers.

---

## Product Scope

This plan is for a second-stage GUI refactor, separate from the smaller SSH/terminal/startup plan.

Borrowed from CatGo:

- Context-centered HPC job view: one selected run exposes tasks, logs, files, results, and execution metadata.
- Quick-build style presets: common Gaussian, ORCA, and ConfFlow submit profiles become easier to select and inspect.
- Remote result browsing inside the job context: a run/task view can show declared outputs and remote paths without switching to Files.
- Diagnostics-first failure review: show missing outputs, scheduler state, parser errors, and last refresh/download errors in one place.

Kept from JobDesk:

- PySide6 desktop app.
- Current Files, Runs, Settings navigation.
- Existing SSH/SFTP services and safety checks.
- Existing manifest/run storage model.
- ConfFlow remains externally owned; JobDesk does not model ConfFlow's internal DAG steps.

Out of scope:

- Tauri/Svelte/FastAPI migration.
- Embedded SSH terminal.
- Full visual workflow/DAG editor.
- Scientific structure builder or material-database features.
- Password/OTP automation.

## Target UI Shape

Runs page becomes a dense workbench:

```text
+----------------------------------------------------------------------+
| Toolbar: Refresh | Download | Retry | Cancel | Open Terminal | Preset |
+---------------+-----------------------------+------------------------+
| Run List      | Task Table                  | Task Inspector         |
| run id        | task id | status | job id   | Overview | Logs | Files|
| server        | program | energy | diagnosis| Results | Script | Diag|
| status        |                            |                       |
+---------------+-----------------------------+------------------------+
```

First implementation target:

- Left pane: existing run rows, narrower.
- Middle pane: manifest task rows for the selected run.
- Right pane: selected task/run detail tabs.
- If no task is selected, inspector shows run-level overview.
- Existing result preview behavior remains available through the `Results` inspector tab.

## File Structure

- Create: `src/jobdesk_app/services/run_details.py`
  - Read-only aggregation model for runs, tasks, local result directories, declared outputs, parsed summaries, and diagnostics.
  - No PySide6 dependency.

- Create: `src/jobdesk_app/gui/pages/runs_workbench.py`
  - New component module containing `RunListPanel`, `TaskTablePanel`, and `TaskInspectorPanel`.
  - Reuses existing `StyledTableWidget`.

- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
  - Keep public page name and existing navigation contract.
  - Gradually delegate data loading and rendering to extracted components.
  - Preserve existing refresh/download/retry/cancel behavior.

- Modify: `src/jobdesk_app/services/gui_settings.py`
  - Add lightweight submit preset metadata if needed after task detail extraction.
  - Keep existing `software_profiles` as the source of command/download defaults.

- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
  - Add a small preset selector only after the workbench can show the selected run cleanly.

- Modify: `src/jobdesk_app/gui/i18n.py`
  - Add labels for workbench panes, task inspector tabs, diagnostics, and presets.

- Test: `tests/test_run_details.py`
  - Pure service tests for run/task aggregation and diagnostics.

- Test: `tests/test_gui_behavior.py`
  - Workbench construction, selection flow, and extracted component behavior.

- Test: `tests/test_file_transfer_page_helpers.py`
  - Preset selection behavior, only after preset task is implemented.

---

### Task 1: Add Read-Only Run Detail Aggregation Service

**Files:**
- Create: `src/jobdesk_app/services/run_details.py`
- Test: `tests/test_run_details.py`

- [ ] **Step 1: Write failing tests for run/task detail aggregation**

Create `tests/test_run_details.py`:

```python
from pathlib import Path

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.services.run_details import build_run_detail
from jobdesk_app.services.run_service import RunRecord


def _record(tmp_path: Path) -> RunRecord:
    run_dir = tmp_path / "runs" / "260607-001"
    run_dir.mkdir(parents=True)
    return RunRecord(
        run_id="260607-001",
        server_id="hpc",
        remote_dir="/scratch/jobs",
        command_template="orca {name}",
        max_parallel=2,
        mode="selected_files",
        created_at="2026-06-07T10:00:00",
        run_dir=run_dir,
        manifest_path=run_dir / "manifest.tsv",
        batch_path=run_dir / "batch.json",
    )


def test_build_run_detail_reads_manifest_tasks(tmp_path):
    record = _record(tmp_path)
    Manifest.write(record.manifest_path, [
        TaskRecord(
            task_id="water",
            batch_id=record.run_id,
            remote_job_dir="/scratch/jobs/.jobdesk_runs/260607-001/water",
            remote_work_dir="/scratch/jobs",
            rendered_command="cd /scratch/jobs && orca water.inp",
            remote_result_files=["water.out"],
            server_id="hpc",
            status=TaskStatus.running,
            remote_job_id="12345",
        )
    ])

    detail = build_run_detail(record, workspace_dir=tmp_path)

    assert detail.run_id == "260607-001"
    assert detail.remote_run_dir == "/scratch/jobs/.jobdesk_runs/260607-001"
    assert len(detail.tasks) == 1
    assert detail.tasks[0].task_id == "water"
    assert detail.tasks[0].remote_job_id == "12345"
    assert detail.tasks[0].declared_outputs == ["water.out"]


def test_build_run_detail_marks_missing_declared_local_outputs(tmp_path):
    record = _record(tmp_path)
    Manifest.write(record.manifest_path, [
        TaskRecord(
            task_id="water",
            batch_id=record.run_id,
            remote_job_dir="/scratch/jobs/.jobdesk_runs/260607-001/water",
            remote_result_files=["water.out", "water.gbw"],
            server_id="hpc",
            status=TaskStatus.downloaded,
        )
    ])
    result_dir = tmp_path / "results" / record.run_id / "water"
    result_dir.mkdir(parents=True)
    (result_dir / "water.out").write_text("ok", encoding="utf-8")

    detail = build_run_detail(record, workspace_dir=tmp_path)

    assert detail.tasks[0].local_result_dir == result_dir
    assert detail.tasks[0].missing_outputs == ["water.gbw"]
    assert "Missing declared output: water.gbw" in detail.tasks[0].diagnostics
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
python -m pytest tests/test_run_details.py -q --basetemp .pytest_tmp_gui_detail
```

Expected: fail because `jobdesk_app.services.run_details` does not exist.

- [ ] **Step 3: Implement read-only detail models**

Create `src/jobdesk_app/services/run_details.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..core.manifest import Manifest
from ..core.run import remote_run_dir
from .run_service import RunRecord


@dataclass(frozen=True)
class TaskDetail:
    task_id: str
    status: str
    remote_job_id: str
    remote_job_dir: str
    remote_work_dir: str
    rendered_command: str
    declared_outputs: list[str]
    local_result_dir: Path
    missing_outputs: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunDetail:
    run_id: str
    server_id: str
    remote_dir: str
    remote_run_dir: str
    command_template: str
    status_summary: dict[str, int]
    local_results_dir: Path
    tasks: list[TaskDetail]


def build_run_detail(record: RunRecord, workspace_dir: str | Path) -> RunDetail:
    workspace = Path(workspace_dir)
    local_results_dir = workspace / "results" / record.run_id
    tasks = []
    for task in Manifest.read(record.manifest_path):
        task_result_dir = local_results_dir / task.task_id
        declared = list(task.remote_result_files)
        missing = [
            output for output in declared
            if not (task_result_dir / output).exists()
        ]
        diagnostics = [f"Missing declared output: {output}" for output in missing]
        if task.error_message:
            diagnostics.append(task.error_message)
        tasks.append(TaskDetail(
            task_id=task.task_id,
            status=task.status.value,
            remote_job_id=task.remote_job_id or "",
            remote_job_dir=task.remote_job_dir,
            remote_work_dir=task.remote_work_dir,
            rendered_command=task.rendered_command,
            declared_outputs=declared,
            local_result_dir=task_result_dir,
            missing_outputs=missing,
            diagnostics=diagnostics,
        ))
    return RunDetail(
        run_id=record.run_id,
        server_id=record.server_id,
        remote_dir=record.remote_dir,
        remote_run_dir=remote_run_dir(record.remote_dir, record.run_id),
        command_template=record.command_template,
        status_summary=dict(record.status_summary),
        local_results_dir=local_results_dir,
        tasks=tasks,
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```powershell
python -m pytest tests/test_run_details.py -q --basetemp .pytest_tmp_gui_detail
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_details.py tests/test_run_details.py
git commit -m "Add run detail aggregation service"
```

---

### Task 2: Extract Workbench UI Components Without Changing Layout

**Files:**
- Create: `src/jobdesk_app/gui/pages/runs_workbench.py`
- Modify: `src/jobdesk_app/gui/i18n.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing component construction tests**

Add to `tests/test_gui_behavior.py`:

```python
def test_runs_workbench_components_construct(qtbot):
    from jobdesk_app.gui.pages.runs_workbench import (
        RunListPanel,
        TaskInspectorPanel,
        TaskTablePanel,
    )

    run_list = RunListPanel(language="en")
    task_table = TaskTablePanel(language="en")
    inspector = TaskInspectorPanel(language="en")

    qtbot.addWidget(run_list)
    qtbot.addWidget(task_table)
    qtbot.addWidget(inspector)

    assert run_list.table.columnCount() >= 4
    assert task_table.table.columnCount() >= 5
    assert inspector.tabs.count() >= 5
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::test_runs_workbench_components_construct -q --basetemp .pytest_tmp_gui_workbench
```

Expected: fail because `runs_workbench.py` does not exist.

- [ ] **Step 3: Implement component shells**

Create `src/jobdesk_app/gui/pages/runs_workbench.py`:

```python
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTabWidget, QTextEdit, QVBoxLayout, QWidget

from ..design.components import StyledTableWidget
from ..i18n import tr


class RunListPanel(QWidget):
    def __init__(self, language: str = "en"):
        super().__init__()
        self.language = language
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = StyledTableWidget()
        self.table.setColumnCount(4)
        self.apply_language(language)
        layout.addWidget(self.table)

    def apply_language(self, language: str) -> None:
        self.language = language
        self.table.setHorizontalHeaderLabels([
            tr("Run ID", language),
            tr("Server", language),
            tr("Status", language),
            tr("Created At", language),
        ])


class TaskTablePanel(QWidget):
    def __init__(self, language: str = "en"):
        super().__init__()
        self.language = language
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = StyledTableWidget()
        self.table.setColumnCount(6)
        self.apply_language(language)
        layout.addWidget(self.table)

    def apply_language(self, language: str) -> None:
        self.language = language
        self.table.setHorizontalHeaderLabels([
            tr("Task", language),
            tr("Status", language),
            tr("Job ID", language),
            tr("Program", language),
            tr("Energy(Hartree)", language),
            tr("Diagnosis", language),
        ])


class TaskInspectorPanel(QWidget):
    def __init__(self, language: str = "en"):
        super().__init__()
        self.language = language
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.overview = QLabel()
        self.logs = QTextEdit()
        self.files = QTextEdit()
        self.results = QTextEdit()
        self.script = QTextEdit()
        self.diagnostics = QTextEdit()
        for widget in (self.logs, self.files, self.results, self.script, self.diagnostics):
            widget.setReadOnly(True)
        self.tabs.addTab(self.overview, tr("Overview", language))
        self.tabs.addTab(self.logs, tr("Logs", language))
        self.tabs.addTab(self.files, tr("Files", language))
        self.tabs.addTab(self.results, tr("Results", language))
        self.tabs.addTab(self.script, tr("Submit Script", language))
        self.tabs.addTab(self.diagnostics, tr("Diagnostics", language))
        layout.addWidget(self.tabs)

    def apply_language(self, language: str) -> None:
        self.language = language
        labels = ["Overview", "Logs", "Files", "Results", "Submit Script", "Diagnostics"]
        for index, label in enumerate(labels):
            self.tabs.setTabText(index, tr(label, language))
```

- [ ] **Step 4: Add translations**

Add to `src/jobdesk_app/gui/i18n.py`:

```python
    "Job ID": "\u4f5c\u4e1aID",
    "Overview": "\u6982\u89c8",
    "Logs": "\u65e5\u5fd7",
    "Submit Script": "\u63d0\u4ea4\u811a\u672c",
    "Diagnostics": "\u8bca\u65ad",
```

- [ ] **Step 5: Run test to verify pass**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::test_runs_workbench_components_construct -q --basetemp .pytest_tmp_gui_workbench
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/gui/pages/runs_workbench.py src/jobdesk_app/gui/i18n.py tests/test_gui_behavior.py
git commit -m "Add runs workbench components"
```

---

### Task 3: Replace Runs Page Layout With Run List, Task Table, Inspector

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing selection-flow test**

Add under `TestRunsPage`:

```python
    def test_selecting_run_populates_task_table_and_inspector(self, runs_page, tmp_path):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest, TaskRecord
        from jobdesk_app.services.run_service import RunRecord

        run_dir = tmp_path / "runs" / "260607-001"
        run_dir.mkdir(parents=True)
        manifest_path = run_dir / "manifest.tsv"
        Manifest.write(manifest_path, [
            TaskRecord(
                task_id="water",
                batch_id="260607-001",
                remote_job_dir="/scratch/.jobdesk_runs/260607-001/water",
                rendered_command="orca water.inp",
                server_id="hpc",
                status=TaskStatus.running,
                remote_job_id="123",
            )
        ])
        record = RunRecord(
            run_id="260607-001",
            server_id="hpc",
            remote_dir="/scratch",
            command_template="orca {name}",
            max_parallel=1,
            mode="selected_files",
            created_at="now",
            run_dir=run_dir,
            manifest_path=manifest_path,
            batch_path=run_dir / "batch.json",
            local_dir=str(tmp_path),
        )
        runs_page.state.current_project_root = tmp_path
        runs_page.table.setRowCount(1)
        item = QTableWidgetItem(record.run_id)
        item.setData(Qt.UserRole, record)
        runs_page.table.setItem(0, 0, item)
        runs_page.table.selectRow(0)

        runs_page._on_run_selected(0, 0, -1, -1)

        assert runs_page.task_table.table.rowCount() == 1
        assert runs_page.task_table.table.item(0, 0).text() == "water"
        assert "260607-001" in runs_page.inspector.overview.text()
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage::test_selecting_run_populates_task_table_and_inspector -q --basetemp .pytest_tmp_gui_layout
```

Expected: fail because `RunsResultsPage` has no `task_table` or `inspector`.

- [ ] **Step 3: Replace only the visual layout, not behavior methods**

In `runs_results_page.py`, import:

```python
from ...services.run_details import build_run_detail
from .runs_workbench import RunListPanel, TaskInspectorPanel, TaskTablePanel
```

In `__init__()`, keep `self.table` as the run table for compatibility, but create it through `RunListPanel`:

```python
        self.run_list = RunListPanel(self._language)
        self.table = self.run_list.table
        self.task_table = TaskTablePanel(self._language)
        self.inspector = TaskInspectorPanel(self._language)
```

Replace the old vertical splitter content with a horizontal splitter:

```python
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.run_list)
        splitter.addWidget(self.task_table)
        splitter.addWidget(self.inspector)
        splitter.setSizes([280, 560, 420])
        layout.addWidget(splitter, 1)
```

Keep existing action buttons in a top toolbar above the splitter.

- [ ] **Step 4: Add task rendering helpers**

Add:

```python
    def _load_selected_run_detail(self):
        record = self._selected_record()
        if record is None:
            return None
        workspace = self._result_workspace(record)
        return build_run_detail(record, workspace)

    def _render_task_table(self, detail):
        self.task_table.table.setRowCount(len(detail.tasks))
        for row, task in enumerate(detail.tasks):
            values = [
                task.task_id,
                task.status,
                task.remote_job_id,
                "",
                "",
                "; ".join(task.diagnostics),
            ]
            for col, value in enumerate(values):
                self.task_table.table.setItem(row, col, QTableWidgetItem(value))

    def _render_run_inspector(self, detail):
        self.inspector.overview.setText(
            f"Run: {detail.run_id}\n"
            f"Server: {detail.server_id}\n"
            f"Remote: {detail.remote_run_dir}\n"
            f"Tasks: {len(detail.tasks)}"
        )
        self.inspector.script.setPlainText(detail.command_template)
        self.inspector.diagnostics.setPlainText(
            "\n".join(
                f"{task.task_id}: {diagnostic}"
                for task in detail.tasks
                for diagnostic in task.diagnostics
            )
        )
```

Change `_on_run_selected()` so it calls these helpers before the existing preview path:

```python
        detail = self._load_selected_run_detail()
        if detail is not None:
            self._render_task_table(detail)
            self._render_run_inspector(detail)
```

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage -q --basetemp .pytest_tmp_gui_layout
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/gui/pages/runs_results_page.py tests/test_gui_behavior.py
git commit -m "Refactor Runs page into workbench layout"
```

---

### Task 4: Move Parsed Results Into Inspector Results Tab

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/gui/pages/runs_workbench.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing results-tab test**

Add under `TestRunsPage`:

```python
    def test_result_preview_renders_into_inspector_results_tab(self, runs_page, tmp_path):
        runs_page.state.current_project_root = tmp_path
        result_dir = tmp_path / "results" / "run001" / "water"
        result_dir.mkdir(parents=True)
        (result_dir / "water.out").write_text("FINAL SINGLE POINT ENERGY -76.1", encoding="utf-8")

        record = MagicMock(run_id="run001", command_template="orca {name}", local_dir=str(tmp_path))

        with patch.object(runs_page, "_collect_result_preview", return_value=("text", "parsed result", "Result Preview", False)):
            runs_page._load_result_preview(record)
            qtbot.waitUntil(lambda: "parsed result" in runs_page.inspector.results.toPlainText(), timeout=2000)
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage::test_result_preview_renders_into_inspector_results_tab -q --basetemp .pytest_tmp_gui_results_tab
```

Expected: fail because preview rendering still writes to the old bottom result table/text widgets.

- [ ] **Step 3: Keep old preview widgets as compatibility aliases**

In `TaskInspectorPanel`, add a results table plus text:

```python
from PySide6.QtWidgets import QSplitter
from PySide6.QtCore import Qt
```

Change results tab to contain both:

```python
        self.results_tab = QWidget()
        results_layout = QVBoxLayout(self.results_tab)
        results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_table = StyledTableWidget()
        self.results = QTextEdit()
        self.results.setReadOnly(True)
        results_layout.addWidget(self.results_table)
        results_layout.addWidget(self.results)
        self.tabs.addTab(self.results_tab, tr("Results", language))
```

In `RunsResultsPage.__init__()`, after creating `self.inspector`, set compatibility aliases:

```python
        self.result_table = self.inspector.results_table
        self.result_text = self.inspector.results
```

Keep `self.result_label` only if existing methods still require it; otherwise change label updates to status messages or inspector tab text.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage -q --basetemp .pytest_tmp_gui_results_tab
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/gui/pages/runs_results_page.py src/jobdesk_app/gui/pages/runs_workbench.py tests/test_gui_behavior.py
git commit -m "Move run results preview into inspector"
```

---

### Task 5: Add Logs, Files, Script, and Diagnostics Inspector Content

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/services/run_details.py`
- Test: `tests/test_run_details.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing diagnostics tests**

Add to `tests/test_run_details.py`:

```python
def test_run_detail_collects_submit_log_paths_and_script_text(tmp_path):
    record = _record(tmp_path)
    Manifest.write(record.manifest_path, [
        TaskRecord(
            task_id="water",
            batch_id=record.run_id,
            remote_job_dir="/scratch/jobs/.jobdesk_runs/260607-001/water",
            rendered_command="orca water.inp",
            server_id="hpc",
            status=TaskStatus.failed,
            error_message="scheduler: job disappeared",
        )
    ])

    detail = build_run_detail(record, workspace_dir=tmp_path)

    assert ".jobdesk_submit.log" in detail.remote_log_paths[0]
    assert detail.tasks[0].rendered_command == "orca water.inp"
    assert "scheduler: job disappeared" in detail.tasks[0].diagnostics
```

- [ ] **Step 2: Extend `RunDetail`**

Modify `RunDetail`:

```python
    remote_log_paths: list[str]
```

Set it in `build_run_detail()`:

```python
        remote_log_paths=[
            f"{remote_run_dir(record.remote_dir, record.run_id)}/.jobdesk_submit.log",
            f"{remote_run_dir(record.remote_dir, record.run_id)}/.jobdesk_submit.err",
        ],
```

- [ ] **Step 3: Render inspector tab content**

In `_render_run_inspector()`:

```python
        self.inspector.logs.setPlainText("\n".join(detail.remote_log_paths))
        self.inspector.files.setPlainText(
            "\n".join(
                f"{task.task_id}: {task.remote_job_dir}"
                for task in detail.tasks
            )
        )
        self.inspector.script.setPlainText(
            "\n\n".join(
                f"[{task.task_id}]\n{task.rendered_command}"
                for task in detail.tasks
            )
        )
        self.inspector.diagnostics.setPlainText(
            "\n".join(
                f"{task.task_id}: {diagnostic}"
                for task in detail.tasks
                for diagnostic in task.diagnostics
            ) or tr("OK", self._language)
        )
```

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m pytest tests/test_run_details.py tests/test_gui_behavior.py::TestRunsPage -q --basetemp .pytest_tmp_gui_inspector
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/services/run_details.py src/jobdesk_app/gui/pages/runs_results_page.py tests/test_run_details.py tests/test_gui_behavior.py
git commit -m "Populate run inspector detail tabs"
```

---

### Task 6: Add CatGo-Style Submission Preset Selector

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/services/gui_settings.py`
- Modify: `src/jobdesk_app/gui/i18n.py`
- Test: `tests/test_file_transfer_page_helpers.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing preset tests**

Add to `tests/test_gui_behavior.py` under file page tests:

```python
def test_file_page_preset_selection_updates_command_and_patterns(file_page):
    file_page._gui_settings.software_profiles = {
        "ORCA opt": {
            "input_extensions": ".inp",
            "command_template": "orca {name} > {basename}.out",
            "download_patterns": "*.out,*.gbw",
        }
    }

    file_page._reload_run_presets()
    file_page.run_preset_combo.setCurrentText("ORCA opt")

    assert file_page.command_edit.text() == "orca {name} > {basename}.out"
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::test_file_page_preset_selection_updates_command_and_patterns -q --basetemp .pytest_tmp_gui_presets
```

Expected: fail because `run_preset_combo` and `_reload_run_presets()` do not exist.

- [ ] **Step 3: Add preset combo to FileTransferPage**

In `file_transfer_page.py`, add a `QComboBox` near command/profile controls:

```python
        self.run_preset_combo = QComboBox()
        self.run_preset_combo.currentTextChanged.connect(self._apply_run_preset)
```

Add:

```python
    def _reload_run_presets(self):
        current = self.run_preset_combo.currentText()
        self.run_preset_combo.blockSignals(True)
        self.run_preset_combo.clear()
        for name in sorted((self._gui_settings.software_profiles or {}).keys()):
            self.run_preset_combo.addItem(name)
        index = self.run_preset_combo.findText(current)
        if index >= 0:
            self.run_preset_combo.setCurrentIndex(index)
        self.run_preset_combo.blockSignals(False)

    def _apply_run_preset(self, name: str):
        profile = (self._gui_settings.software_profiles or {}).get(name)
        if not profile:
            return
        self.command_edit.setText(profile.get("command_template", ""))
```

Call `_reload_run_presets()` after loading GUI settings and whenever settings are reloaded.

- [ ] **Step 4: Add translations**

Add:

```python
    "Run Preset": "\u8fd0\u884c\u9884\u8bbe",
```

- [ ] **Step 5: Run targeted tests**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py tests/test_file_transfer_page_helpers.py -q --basetemp .pytest_tmp_gui_presets
```

Expected: pass.

- [ ] **Step 6: Commit**

```powershell
git add src/jobdesk_app/gui/pages/file_transfer_page.py src/jobdesk_app/gui/i18n.py tests/test_gui_behavior.py
git commit -m "Add run preset selector"
```

---

### Task 7: Add Remote File Context Placeholders Without Synchronous SFTP

**Files:**
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/gui/pages/runs_workbench.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing no-sync-SFTP test**

Add under `TestRunsPage`:

```python
    def test_files_tab_shows_remote_paths_without_sftp_on_selection(self, runs_page, tmp_path):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QTableWidgetItem

        record = MagicMock(
            run_id="run1",
            server_id="hpc",
            remote_dir="/scratch",
            command_template="orca {name}",
            status_summary={},
            local_dir=str(tmp_path),
        )
        record.manifest_path = tmp_path / "manifest.tsv"
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest, TaskRecord
        Manifest.write(record.manifest_path, [
            TaskRecord(
                task_id="water",
                batch_id="run1",
                remote_job_dir="/scratch/.jobdesk_runs/run1/water",
                server_id="hpc",
                status=TaskStatus.running,
            )
        ])
        runs_page.table.setRowCount(1)
        item = QTableWidgetItem("run1")
        item.setData(Qt.UserRole, record)
        runs_page.table.setItem(0, 0, item)
        runs_page.table.selectRow(0)

        with patch("jobdesk_app.gui.pages.runs_results_page.create_sftp_client") as make_sftp:
            runs_page._on_run_selected(0, 0, -1, -1)

        make_sftp.assert_not_called()
        assert "/scratch/.jobdesk_runs/run1/water" in runs_page.inspector.files.toPlainText()
```

- [ ] **Step 2: Run test to verify current behavior**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage::test_files_tab_shows_remote_paths_without_sftp_on_selection -q --basetemp .pytest_tmp_gui_files_tab
```

Expected: pass after Task 5. If it fails because selection triggers SFTP, move that work to an explicit `Refresh Files` button in the Files tab.

- [ ] **Step 3: Add explicit future refresh button only if needed**

If the test shows synchronous remote work on selection, add a `Refresh Files` button to `TaskInspectorPanel.files` tab and connect it to a background worker. The first implementation should only list known remote job directories and declared outputs.

- [ ] **Step 4: Run targeted tests**

Run:

```powershell
python -m pytest tests/test_gui_behavior.py::TestRunsPage -q --basetemp .pytest_tmp_gui_files_tab
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add src/jobdesk_app/gui/pages/runs_results_page.py src/jobdesk_app/gui/pages/runs_workbench.py tests/test_gui_behavior.py
git commit -m "Keep run files tab selection side-effect free"
```

---

### Task 8: Documentation, Full Verification, and Cleanup

**Files:**
- Modify: `docs/USER_GUIDE.md`
- Modify: `README.md`

- [ ] **Step 1: Add user-facing workbench documentation**

Add to `docs/USER_GUIDE.md`:

~~~markdown
## Runs Workbench

The Runs page is organized around one selected run. The left pane lists runs,
the middle pane lists manifest tasks, and the right inspector shows details for
the selected run or task.

Inspector tabs:

- Overview: run id, server, remote run directory, and task count.
- Logs: known remote submit log paths.
- Files: known task working directories and declared outputs.
- Results: parsed Gaussian, ORCA, and ConfFlow summaries from downloaded files.
- Submit Script: rendered task commands.
- Diagnostics: missing declared outputs, scheduler/download errors, and parser
  failures.

Submission presets in the Files page come from Software Profiles. They help
build common Gaussian, ORCA, and ConfFlow commands without exposing a visual
workflow editor.
~~~

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests/test_run_details.py tests/test_gui_behavior.py::TestRunsPage tests/test_file_transfer_page_helpers.py -q --basetemp .pytest_tmp_gui_refactor_focused
```

Expected: pass.

- [ ] **Step 3: Run GUI import tests**

Run:

```powershell
python -m pytest tests/test_gui_imports.py -q --basetemp .pytest_tmp_gui_refactor_imports
```

Expected: pass.

- [ ] **Step 4: Run static checks**

Run:

```powershell
python -m ruff check .
python -m mypy src
```

Expected: pass.

- [ ] **Step 5: Run full suite**

Run:

```powershell
python -m pytest tests -q --basetemp .pytest_tmp_gui_refactor_full
```

Expected: pass.

- [ ] **Step 6: Diff hygiene**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files changed.

- [ ] **Step 7: Commit docs and cleanup**

```powershell
git add README.md docs/USER_GUIDE.md
git commit -m "Document run workbench UI"
```

---

## Manual QA Checklist

- Start `jobdesk-gui`.
- Open Files and submit or select an existing run.
- Navigate to Runs.
- Confirm the page shows run list, task table, and inspector panes.
- Select a run with multiple manifest tasks.
- Confirm task table shows every manifest task.
- Confirm inspector Overview shows run id, server, remote run directory, and task count.
- Confirm Results tab still shows existing parsed Gaussian/ORCA/ConfFlow outputs.
- Confirm Logs tab shows `.jobdesk_submit.log` and `.jobdesk_submit.err` paths.
- Confirm Files tab does not block UI while selecting runs.
- Confirm Retry, Cancel, Retry Download, Delete, Compare Selected, and existing context menu actions still work.
- Confirm Files page preset selection updates the command template without changing remote connection behavior.

## Risk Notes

- `runs_results_page.py` is already large. Keep extraction incremental and preserve method names used by tests.
- Do not perform SFTP or SSH operations in table selection handlers.
- Keep `RunService`, `Manifest`, and parser logic outside PySide widgets.
- Keep task-level remote file browsing read-only until a separate design handles editing, deletes, and upload/download authority.
- Avoid replacing existing `StyledTableWidget` and column-width persistence during this refactor.
- Keep ConfFlow internal steps opaque; only show per-molecule JobDesk tasks and downloaded ConfFlow summaries.
