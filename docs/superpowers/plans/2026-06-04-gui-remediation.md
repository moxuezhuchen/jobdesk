# JobDesk GUI Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the remaining GUI and release-readiness issues from the current review: server ID overwrite, duplicated worker orchestration, blocking GUI operations, partial GUI type coverage, optional test blind spots, and missing PyInstaller smoke coverage.

**Architecture:** Keep the existing PySide6 pages and service boundaries; do not rewrite the GUI. Add small helper modules for pure validation and worker orchestration, then migrate the riskiest page actions to those helpers with regression tests. CI changes are split into optional/packaging workflows so normal Windows CI remains stable while the skipped surfaces become executable gates when their prerequisites exist.

**Tech Stack:** Python 3.11+, PySide6, pytest, pytest-qt, ruff, mypy, GitHub Actions, PyInstaller

---

## File Structure

- Create: `src/jobdesk_app/gui/pages/settings_servers_helpers.py`
  - Pure validation for server IDs. No Qt imports.
- Modify: `src/jobdesk_app/gui/pages/settings_servers_page.py`
  - Reject duplicate server IDs in add/edit dialogs.
  - Use context-aware worker logging for connection tests.
- Test: `tests/test_settings_servers_page.py`
  - Pure helper regressions plus GUI dialog regressions for add/edit duplicate rejection.
- Create: `src/jobdesk_app/gui/worker_utils.py`
  - Typed helpers for tracked `BackgroundWorker` startup, signal wiring, result/error cleanup, and context emitters.
- Test: `tests/test_gui_worker_utils.py`
  - Unit tests for registry removal, signal callback wiring, contextual progress/log emitters, and avoiding post-construction `_target_fn` mutation.
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
  - Replace ad hoc worker setup and `_target_fn` mutation.
  - Move local delete, remote delete, transfer progress, and run submission work onto tracked workers.
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
  - Replace repeated worker setup and move run deletion onto a worker.
- Modify: `pyproject.toml`
  - Add packaging extra for PyInstaller.
  - Tighten mypy checking for new GUI helper modules and existing pure GUI helpers.
- Create: `.github/workflows/optional-coverage.yml`
  - Manual/scheduled optional coverage for RDKit/POSIX-shell tests and real remote integration tests when secrets are configured.
- Create: `.github/workflows/package-smoke.yml`
  - Build the Windows PyInstaller app on relevant PRs and manual dispatch.

---

### Task 1: Prevent Server ID Overwrite

**Files:**
- Create: `src/jobdesk_app/gui/pages/settings_servers_helpers.py`
- Modify: `src/jobdesk_app/gui/pages/settings_servers_page.py`
- Test: `tests/test_settings_servers_page.py`

- [ ] **Step 1: Write failing pure validation tests**

Append these tests to `tests/test_settings_servers_page.py`:

```python
from jobdesk_app.gui.pages.settings_servers_helpers import validate_server_id_change


def test_validate_server_id_change_allows_unchanged_id():
    assert validate_server_id_change({"wsl", "hpc"}, old_id="wsl", new_id="wsl") is None


def test_validate_server_id_change_allows_new_unique_id():
    assert validate_server_id_change({"wsl"}, old_id=None, new_id="hpc") is None


def test_validate_server_id_change_rejects_blank_id():
    assert validate_server_id_change({"wsl"}, old_id=None, new_id="   ") == "Server ID is required"


def test_validate_server_id_change_rejects_duplicate_add():
    assert validate_server_id_change({"wsl"}, old_id=None, new_id="wsl") == "Server ID already exists: wsl"


def test_validate_server_id_change_rejects_duplicate_rename():
    assert validate_server_id_change({"wsl", "hpc"}, old_id="wsl", new_id="hpc") == "Server ID already exists: hpc"
```

- [ ] **Step 2: Run the helper tests red**

Run:

```powershell
python -m pytest tests\test_settings_servers_page.py::test_validate_server_id_change_allows_unchanged_id tests\test_settings_servers_page.py::test_validate_server_id_change_rejects_duplicate_add -q --basetemp .pytest_tmp_gui_remediation_t1
```

Expected: FAIL with `ModuleNotFoundError: No module named 'jobdesk_app.gui.pages.settings_servers_helpers'`.

- [ ] **Step 3: Add the validation helper**

Create `src/jobdesk_app/gui/pages/settings_servers_helpers.py`:

```python
from __future__ import annotations

from collections.abc import Iterable


def validate_server_id_change(existing_ids: Iterable[str], old_id: str | None, new_id: str) -> str | None:
    candidate = new_id.strip()
    if not candidate:
        return "Server ID is required"
    normalized_existing = {sid.strip() for sid in existing_ids if sid.strip()}
    if old_id is not None and candidate == old_id:
        return None
    if candidate in normalized_existing:
        return f"Server ID already exists: {candidate}"
    return None
```

- [ ] **Step 4: Run the helper tests green**

Run:

```powershell
python -m pytest tests\test_settings_servers_page.py::test_validate_server_id_change_allows_unchanged_id tests\test_settings_servers_page.py::test_validate_server_id_change_allows_new_unique_id tests\test_settings_servers_page.py::test_validate_server_id_change_rejects_blank_id tests\test_settings_servers_page.py::test_validate_server_id_change_rejects_duplicate_add tests\test_settings_servers_page.py::test_validate_server_id_change_rejects_duplicate_rename -q --basetemp .pytest_tmp_gui_remediation_t1
```

Expected: PASS.

- [ ] **Step 5: Write failing GUI regression tests**

Append these tests to `tests/test_settings_servers_page.py`:

```python
def test_edit_server_rejects_duplicate_server_id(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  wsl:\n"
        "    host: 127.0.0.1\n"
        "    username: root\n"
        "    auth_method: key\n"
        "  hpc:\n"
        "    host: cluster\n"
        "    username: chemist\n"
        "    auth_method: key\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()
    statuses: list[str] = []

    def accept_with_duplicate_id(dialog):
        id_edit = next(edit for edit in dialog.findChildren(QLineEdit) if edit.text() == "wsl")
        id_edit.setText("hpc")
        return QDialog.Accepted

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
        return_value=settings_store,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
        return_value=servers_path,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.load_servers",
        side_effect=lambda: load_servers_from_path(servers_path),
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_duplicate_id), patch(
        "PySide6.QtWidgets.QMessageBox.warning",
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, statuses.append)
        qtbot.addWidget(page)
        page.server_table.selectRow(0)
        page._edit_server()

    saved = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]
    assert set(saved) == {"wsl", "hpc"}
    assert saved["hpc"]["host"] == "cluster"
    assert statuses == ["Server ID already exists: hpc"]


def test_add_server_rejects_duplicate_server_id(qtbot, tmp_path):
    servers_path = tmp_path / "servers.yaml"
    servers_path.write_text(
        "servers:\n"
        "  wsl:\n"
        "    host: 127.0.0.1\n"
        "    username: root\n"
        "    auth_method: key\n",
        encoding="utf-8",
    )
    settings_store = MagicMock()
    settings_store.load.return_value = GuiSettings()
    statuses: list[str] = []

    def accept_with_duplicate_id(dialog):
        edits = dialog.findChildren(QLineEdit)
        id_edit = next(edit for edit in edits if edit.placeholderText().startswith("如: myserver"))
        host_edit = next(edit for edit in edits if edit.placeholderText().startswith("如: 192.168"))
        user_edit = next(edit for edit in edits if edit.placeholderText().startswith("如: root"))
        id_edit.setText("wsl")
        host_edit.setText("cluster")
        user_edit.setText("chemist")
        return QDialog.Accepted

    with patch(
        "jobdesk_app.gui.pages.settings_servers_page.GuiSettingsStore",
        return_value=settings_store,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.get_default_servers_path",
        return_value=servers_path,
    ), patch(
        "jobdesk_app.gui.pages.settings_servers_page.load_servers",
        side_effect=lambda: load_servers_from_path(servers_path),
    ), patch("PySide6.QtWidgets.QDialog.exec", new=accept_with_duplicate_id), patch(
        "PySide6.QtWidgets.QMessageBox.warning",
    ):
        page = SettingsServersPage(MagicMock(), lambda message: None, statuses.append)
        qtbot.addWidget(page)
        page._add_server()

    saved = yaml.safe_load(servers_path.read_text(encoding="utf-8"))["servers"]
    assert set(saved) == {"wsl"}
    assert saved["wsl"]["host"] == "127.0.0.1"
    assert statuses == ["Server ID already exists: wsl"]
```

- [ ] **Step 6: Run the GUI regressions red**

Run:

```powershell
python -m pytest tests\test_settings_servers_page.py::test_edit_server_rejects_duplicate_server_id tests\test_settings_servers_page.py::test_add_server_rejects_duplicate_server_id -q --basetemp .pytest_tmp_gui_remediation_t1
```

Expected: FAIL because the current page overwrites the duplicate IDs.

- [ ] **Step 7: Wire duplicate validation into edit and add dialogs**

In `src/jobdesk_app/gui/pages/settings_servers_page.py`, add imports:

```python
from PySide6.QtWidgets import QMessageBox

from .settings_servers_helpers import validate_server_id_change
```

In `_edit_server()`, replace:

```python
new_sid = id_edit.text().strip()
if not new_sid:
    return
if new_sid != sid:
    data["servers"].pop(sid, None)
```

with:

```python
new_sid = id_edit.text().strip()
server_ids = set(data.get("servers", {}))
server_id_error = validate_server_id_change(server_ids, old_id=sid, new_id=new_sid)
if server_id_error:
    self._status_cb(server_id_error)
    QMessageBox.warning(self, tr("Edit Server:", self._language), server_id_error)
    return
if new_sid != sid:
    data["servers"].pop(sid, None)
```

In `_add_server()`, replace:

```python
servers = data.setdefault("servers", {})
servers[sid] = {
```

with:

```python
servers = data.setdefault("servers", {})
server_id_error = validate_server_id_change(set(servers), old_id=None, new_id=sid)
if server_id_error:
    self._status_cb(server_id_error)
    QMessageBox.warning(self, tr("Add", self._language), server_id_error)
    return
servers[sid] = {
```

- [ ] **Step 8: Run task 1 verification**

Run:

```powershell
python -m pytest tests\test_settings_servers_page.py -q --basetemp .pytest_tmp_gui_remediation_t1
python -m ruff check src\jobdesk_app\gui\pages\settings_servers_page.py src\jobdesk_app\gui\pages\settings_servers_helpers.py tests\test_settings_servers_page.py
```

Expected: PASS and `All checks passed!`.

- [ ] **Step 9: Commit task 1**

Run:

```powershell
git add src\jobdesk_app\gui\pages\settings_servers_helpers.py src\jobdesk_app\gui\pages\settings_servers_page.py tests\test_settings_servers_page.py
git commit -m "fix: reject duplicate server ids"
```

---

### Task 2: Add Typed Worker Orchestration Helpers

**Files:**
- Create: `src/jobdesk_app/gui/worker_utils.py`
- Test: `tests/test_gui_worker_utils.py`

- [ ] **Step 1: Write failing worker utility tests**

Create `tests/test_gui_worker_utils.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock, patch

from jobdesk_app.gui.worker_utils import WorkerContext, start_context_worker, start_tracked_worker


class _Signal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class _FakeWorker:
    def __init__(self, target=None):
        self._target_fn = target
        self.result = _Signal()
        self.error = _Signal()
        self.log = _Signal()
        self.progress = _Signal()
        self.finished = _Signal()
        self.started_count = 0
        self.deleteLater = MagicMock()

    def start(self):
        self.started_count += 1


class _Owner:
    def __init__(self):
        self._workers = []


def test_start_tracked_worker_removes_worker_when_finished():
    owner = _Owner()
    worker = _FakeWorker()

    start_tracked_worker(owner, worker, registry_attr="_workers")

    assert owner._workers == [worker]
    assert worker.started_count == 1
    worker.finished.emit()
    assert owner._workers == []
    worker.deleteLater.assert_called_once_with()


def test_start_tracked_worker_wires_result_error_and_progress_callbacks():
    owner = _Owner()
    worker = _FakeWorker()
    results = []
    errors = []
    progress = []

    start_tracked_worker(
        owner,
        worker,
        registry_attr="_workers",
        on_result=results.append,
        on_error=errors.append,
        on_progress=lambda done, total: progress.append((done, total)),
    )

    worker.result.emit("ok")
    worker.error.emit("bad")
    worker.progress.emit(5, 10)

    assert results == ["ok"]
    assert errors == ["bad"]
    assert progress == [(5, 10)]


def test_start_context_worker_passes_emitters_to_target():
    owner = _Owner()
    captured = []

    def target(ctx: WorkerContext):
        ctx.emit_log("running")
        ctx.emit_progress(1, 3)
        return "done"

    fake_worker = _FakeWorker()

    with patch("jobdesk_app.gui.worker_utils.BackgroundWorker", return_value=fake_worker):
        worker = start_context_worker(
            owner,
            target=target,
            registry_attr="_workers",
            on_result=lambda value: captured.append(("result", value)),
            on_progress=lambda done, total: captured.append(("progress", done, total)),
        )

    assert worker is fake_worker
    assert owner._workers == [fake_worker]
    result = fake_worker._target_fn()
    fake_worker.result.emit(result)
    assert captured == [("progress", 1, 3), ("result", "done")]
```

- [ ] **Step 2: Run worker utility tests red**

Run:

```powershell
python -m pytest tests\test_gui_worker_utils.py -q --basetemp .pytest_tmp_gui_remediation_t2
```

Expected: FAIL with `ModuleNotFoundError: No module named 'jobdesk_app.gui.worker_utils'`.

- [ ] **Step 3: Implement `worker_utils.py`**

Create `src/jobdesk_app/gui/worker_utils.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .workers import BackgroundWorker


@dataclass(frozen=True)
class WorkerContext:
    emit_log: Callable[[str], None]
    emit_progress: Callable[[int, int], None]


def start_tracked_worker(
    owner: object,
    worker: BackgroundWorker,
    *,
    registry_attr: str,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    delete_later: bool = True,
) -> BackgroundWorker:
    registry = getattr(owner, registry_attr, None)
    if registry is None:
        registry = []
        setattr(owner, registry_attr, registry)

    def _remove_worker() -> None:
        current = getattr(owner, registry_attr, [])
        if worker in current:
            current.remove(worker)

    if on_result is not None:
        worker.result.connect(on_result)
    if on_error is not None:
        worker.error.connect(on_error)
    if on_progress is not None:
        worker.progress.connect(on_progress)
    if on_log is not None:
        worker.log.connect(on_log)
    if on_finished is not None:
        worker.finished.connect(on_finished)
    worker.finished.connect(_remove_worker)
    if delete_later and hasattr(worker, "deleteLater"):
        worker.finished.connect(worker.deleteLater)
    registry.append(worker)
    worker.start()
    return worker


def start_context_worker(
    owner: object,
    *,
    target: Callable[[WorkerContext], Any],
    registry_attr: str,
    on_result: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    on_finished: Callable[[], None] | None = None,
    delete_later: bool = True,
) -> BackgroundWorker:
    worker_ref: dict[str, BackgroundWorker] = {}

    def _run() -> Any:
        worker = worker_ref["worker"]
        ctx = WorkerContext(
            emit_log=worker.log.emit,
            emit_progress=worker.progress.emit,
        )
        return target(ctx)

    worker = BackgroundWorker(_run)
    worker_ref["worker"] = worker
    return start_tracked_worker(
        owner,
        worker,
        registry_attr=registry_attr,
        on_result=on_result,
        on_error=on_error,
        on_progress=on_progress,
        on_log=on_log,
        on_finished=on_finished,
        delete_later=delete_later,
    )
```

- [ ] **Step 4: Run worker utility tests green**

Run:

```powershell
python -m pytest tests\test_gui_worker_utils.py -q --basetemp .pytest_tmp_gui_remediation_t2
python -m ruff check src\jobdesk_app\gui\worker_utils.py tests\test_gui_worker_utils.py
```

Expected: PASS and `All checks passed!`.

- [ ] **Step 5: Commit task 2**

Run:

```powershell
git add src\jobdesk_app\gui\worker_utils.py tests\test_gui_worker_utils.py
git commit -m "refactor: add tracked gui worker helpers"
```

---

### Task 3: Migrate Blocking GUI Actions to Tracked Workers

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/gui/pages/runs_results_page.py`
- Modify: `src/jobdesk_app/gui/pages/settings_servers_page.py`
- Modify: `tests/test_gui_behavior.py`
- Modify: `tests/test_settings_servers_page.py`
- Test: `tests/test_gui_worker_utils.py`

- [ ] **Step 1: Add source guard test for `_target_fn` mutation**

Append to `tests/test_gui_worker_utils.py`:

```python
from pathlib import Path


def test_file_transfer_page_does_not_mutate_worker_target_function():
    source = Path("src/jobdesk_app/gui/pages/file_transfer_page.py").read_text(encoding="utf-8")
    assert "worker._target_fn =" not in source
```

- [ ] **Step 2: Add failing tests for backgrounded FileTransferPage deletes**

Append these tests inside the `TestFileTransferPage` class in `tests/test_gui_behavior.py`:

```python
    def test_delete_remote_runs_in_background_worker(self, file_page):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        service = MagicMock()
        file_page._service = service
        file_page.remote_table.setRowCount(1)
        file_page.remote_table.setItem(0, 0, QTableWidgetItem("result.log"))
        file_page.remote_table.setItem(0, 4, QTableWidgetItem("file"))
        file_page.remote_table.setItem(0, 5, QTableWidgetItem("/remote/run/result.log"))
        file_page.remote_table.selectRow(0)

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker") as start_worker:
            file_page._delete_remote()

        service.delete_remote.assert_not_called()
        target = start_worker.call_args.kwargs["target"]
        target(MagicMock())
        service.delete_remote.assert_called_once_with("/remote/run/result.log", recursive=True)


    def test_delete_local_runs_in_background_worker(self, file_page, tmp_path):
        from PySide6.QtWidgets import QMessageBox, QTableWidgetItem

        local_file = tmp_path / "old.log"
        local_file.write_text("old", encoding="utf-8")
        file_page.local_table.setRowCount(1)
        file_page.local_table.setItem(0, 0, QTableWidgetItem("old.log"))
        file_page.local_table.setItem(0, 3, QTableWidgetItem("file"))
        file_page.local_table.setItem(0, 4, QTableWidgetItem(str(local_file)))
        file_page.local_table.selectRow(0)

        with patch(
            "jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker") as start_worker:
            file_page._delete_local()

        assert local_file.exists()
        target = start_worker.call_args.kwargs["target"]
        target(MagicMock())
        assert not local_file.exists()
```

- [ ] **Step 3: Add failing test for remote run creation being backgrounded**

Append inside the same class:

```python
    def test_remote_run_submission_creates_runs_in_background_worker(self, file_page):
        from PySide6.QtWidgets import QMessageBox

        file_page._service = MagicMock()
        file_page._connected_server = MagicMock(env_init_scripts=[])
        file_page._connected_server_id = "wsl"
        file_page.command_edit.setCurrentText("g16 {name}")
        file_page.remote_path.setText("/remote/work")

        with patch.object(file_page, "_selected_remote_entries", return_value=(["/remote/work/a.gjf"], [])), \
             patch.object(file_page, "_selected_local_entries", return_value=([], [])), \
             patch("jobdesk_app.gui.pages.file_transfer_page.QMessageBox.question", return_value=QMessageBox.Yes), \
             patch("jobdesk_app.gui.pages.file_transfer_page.RunService") as run_service_cls, \
             patch("jobdesk_app.gui.pages.file_transfer_page.start_context_worker") as start_worker:
            file_page._run_selected_chunks(submit=True)

        run_service_cls.assert_not_called()
        assert start_worker.call_count == 1
```

- [ ] **Step 4: Run task 3 new tests red**

Run:

```powershell
python -m pytest tests\test_gui_worker_utils.py::test_file_transfer_page_does_not_mutate_worker_target_function tests\test_gui_behavior.py::TestFileTransferPage::test_delete_remote_runs_in_background_worker tests\test_gui_behavior.py::TestFileTransferPage::test_delete_local_runs_in_background_worker tests\test_gui_behavior.py::TestFileTransferPage::test_remote_run_submission_creates_runs_in_background_worker -q --basetemp .pytest_tmp_gui_remediation_t3
```

Expected: FAIL because the page still mutates `_target_fn` and performs several operations synchronously.

- [ ] **Step 5: Import worker helpers**

In `src/jobdesk_app/gui/pages/file_transfer_page.py`, replace:

```python
from ..workers import BackgroundWorker
```

with:

```python
from ..worker_utils import WorkerContext, start_context_worker, start_tracked_worker
from ..workers import BackgroundWorker
```

Keep `BackgroundWorker` only while unmigrated call sites remain. Remove it after all direct constructions in this file are replaced.

In `src/jobdesk_app/gui/pages/runs_results_page.py` and `src/jobdesk_app/gui/pages/settings_servers_page.py`, add:

```python
from ..worker_utils import WorkerContext, start_context_worker, start_tracked_worker
```

Use the relative path appropriate for each file: `from ..worker_utils` for `gui/pages/*`.

- [ ] **Step 6: Replace transfer worker target mutation**

In `FileTransferPage`, replace `_download_selected()`, `_upload_selected()`, and `_start_transfer_worker()` with this shape:

```python
    def _download_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        remote_path = self._selected_remote_path()
        if remote_path is None:
            self._status_cb("Select a remote file or folder")
            return
        local_base = self.state.current_project_root or Path.cwd()
        target = Path(local_base) / Path(remote_path).name
        service = self._service

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            rec = service.download_path(
                remote_path,
                target,
                OverwritePolicy.overwrite,
                progress_callback=_progress,
            )
            return rec if isinstance(rec, list) else [rec]

        self._start_transfer_worker(_run, "Download", self._refresh_local)

    def _upload_selected(self):
        if self._service is None:
            self._status_cb("Connect to a server first")
            return
        local_path = self._selected_local_path()
        if local_path is None:
            self._status_cb("Select a local file or folder")
            return
        remote_target = self._remote_target_for_local(local_path)
        service = self._service

        def _run(ctx: WorkerContext):
            def _progress(done, total):
                ctx.emit_progress(int(done), int(total))

            rec = service.upload_path(
                local_path,
                remote_target,
                OverwritePolicy.overwrite,
                progress_callback=_progress,
            )
            return rec if isinstance(rec, list) else [rec]

        self._start_transfer_worker(_run, "Upload", self._refresh_remote)

    def _start_transfer_worker(self, run_fn, label: str, on_done_refresh):
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setFormat(f"{label}: %p%")
        self.progress_bar.setVisible(True)

        def _on_progress(done, total):
            if total > 0:
                self.progress_bar.setValue(int(done * 100 / total))
                self.progress_bar.setFormat(f"{label}: {done // 1024}K / {total // 1024}K")
            else:
                self.progress_bar.setMaximum(0)

        def _on_done(records):
            self.progress_bar.setVisible(False)
            self.progress_bar.setMaximum(100)
            if not isinstance(records, list):
                records = [records]
            self._status_cb(format_queue_summary([r.status for r in records], self._language))
            on_done_refresh()

        def _on_error(msg):
            self.progress_bar.setVisible(False)
            self.progress_bar.setMaximum(100)
            self._error_cb(f"{label} Error", msg)

        start_context_worker(
            self,
            target=run_fn,
            registry_attr="_background_workers",
            on_progress=_on_progress,
            on_result=_on_done,
            on_error=_on_error,
        )
        self._status_cb(f"{label} started")
```

- [ ] **Step 7: Move local and remote delete to workers**

In `FileTransferPage._delete_local()`, keep selection and confirmation on the GUI thread, then run deletion in a worker:

```python
        paths = self._selected_local_paths()
        if not paths:
            return
        if QMessageBox.question(
            self,
            tr("Delete", self._language),
            tr("Delete {n} local item(s)?", self._language, n=len(paths)),
            QMessageBox.Yes | QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        def _run(_ctx: WorkerContext):
            for path in paths:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            return len(paths)

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=lambda count: (self._status_cb(f"Deleted {count} local item(s)"), self._refresh_local()),
            on_error=lambda error: self._error_cb("Delete Error", error),
        )
```

In `FileTransferPage._delete_remote()`, keep path validation and confirmation on the GUI thread, then run service deletes in a worker:

```python
        service = self._service
        valid_paths = list(valid_paths)

        def _run(_ctx: WorkerContext):
            for remote_path in valid_paths:
                service.delete_remote(remote_path, recursive=True)
            return len(valid_paths)

        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=lambda count: (self._status_cb(f"Deleted {count} remote item(s)"), self._refresh_remote()),
            on_error=lambda error: self._error_cb("Remote Delete Error", error),
        )
```

Use the existing `_selected_remote_entries()` and current rejection logic for `..`, current directory, and top-level paths; only move the actual delete loop into `_run()`.

- [ ] **Step 8: Move remote run creation and submission into workers**

In `FileTransferPage._run_selected_chunks()`, after validation and confirmation, move the remote-source path into the same worker style already used by the local-source path. The GUI thread should build only immutable inputs: `local_base`, `remote_dir`, `command_template`, `max_parallel`, `run_mode`, `server_id`, `connected_server`, and selected `files/dirs`.

Use this worker body for the remote-source path:

```python
        def _run(_ctx: WorkerContext):
            from ...services.scheduler_helpers import resources_from_server, scheduler_from_server

            all_sources = [RunSource(path=p, is_dir=False) for p in files] + [
                RunSource(path=p, is_dir=True) for p in dirs
            ]
            if run_mode == RunMode.current_directory:
                all_sources = []
            chunks = chunk_sources(all_sources, 0)
            if run_mode == RunMode.current_directory:
                chunks = [[]]

            svc = RunService(local_base)
            run_records = []
            for chunk in chunks:
                spec = RunSpec(
                    server_id=server_id,
                    remote_dir=remote_dir,
                    command_template=command_template,
                    max_parallel=max_parallel,
                    mode=run_mode,
                    sources=chunk,
                )
                run_records.append(svc.create_run(spec, local_dir=str(local_base)))

            if not submit:
                return {"records": run_records, "results": None}

            with sftp_session(connected_server) as (ssh, sftp):
                results = [
                    svc.submit_run(
                        record.run_id,
                        ssh,
                        sftp,
                        env_init_scripts=list(getattr(connected_server, "env_init_scripts", []) or []),
                        scheduler=scheduler_from_server(connected_server),
                        resources=resources_from_server(connected_server),
                    )
                    for record in run_records
                ]
            return {"records": run_records, "results": results}
```

Connect the result handler:

```python
        def _on_run_worker_done(payload):
            records = payload["records"]
            if records:
                first = records[0]
                self.state.current_project_root = Path(local_base)
                self.state.current_batch_id = first.run_id
                self.state.current_manifest_path = first.manifest_path
            self._save_remembered_profile()
            self._save_command_history()
            results = payload["results"]
            if results is None:
                self._status_cb(f"Created {len(records)} run(s)")
            else:
                self._on_runs_done(results)
```

Start it with:

```python
        start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_result=_on_run_worker_done,
            on_error=lambda error: self._error_cb("Run Error", error),
        )
        self._status_cb("Submitting...")
```

- [ ] **Step 9: Migrate direct worker setup in RunsResultsPage**

For each `BackgroundWorker(_run)` block in `RunsResultsPage`, replace append/remove/deleteLater wiring with the `start_context_worker()` pattern used in `_delete_run()` below: pass the page object as `owner`, use `registry_attr="_bg_workers"`, pass the existing result/error callbacks through `on_result` and `on_error`, and pass existing cleanup callbacks through `on_finished`.

For `_delete_run()`, move the deletion loop into a worker:

```python
        workspace = self._workspace()

        def _run(_ctx: WorkerContext):
            deleted = 0
            errors: list[str] = []
            for rid in run_ids:
                try:
                    record = RunService(workspace).load_run(rid)
                    record_workspace = self._result_workspace(record)
                    RunService(record_workspace).delete_run(rid)
                    deleted += 1
                except Exception as exc:
                    errors.append(f"{rid}: {exc}")
            return deleted, errors

        def _done(result):
            deleted, errors = result
            self.refresh_run_list()
            if errors:
                self._status_cb(tr("Delete failed", self._language) + f": {'; '.join(errors)}")
            else:
                self._status_cb(tr("Deleted: {n} records", self._language, n=deleted))

        start_context_worker(
            self,
            target=_run,
            registry_attr="_bg_workers",
            on_result=_done,
            on_error=lambda error: self._status_cb(tr("Delete failed", self._language) + f": {error}"),
        )
```

- [ ] **Step 10: Migrate SettingsServersPage connection test worker**

In `_test_connection()`, replace use of `self._worker.log.emit(...)` inside `_run()` with `ctx.emit_log(...)`:

```python
        def _run(ctx: WorkerContext):
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _test_one(sid, srv):
                try:
                    with ssh_session(srv) as ssh:
                        ok = ssh.test_connection()
                    return sid, "connected" if ok else "no-response"
                except Exception as e:
                    return sid, f"{tr('Error:', self._language)} {e}"

            with ThreadPoolExecutor(max_workers=len(servers_list)) as pool:
                futures = {pool.submit(_test_one, sid, srv): sid for sid, srv in servers_list}
                for f in as_completed(futures):
                    sid, status = f.result()
                    ctx.emit_log(f"{sid}\t{status}")
            return {}

        self._worker = start_context_worker(
            self,
            target=_run,
            registry_attr="_background_workers",
            on_log=_on_log,
            on_error=lambda e: self._status_cb(f"{tr('Test failed:', self._language)} {e}"),
        )
```

Initialize `self._background_workers: list = []` in `SettingsServersPage.__init__()` so shutdown can stop both `self._worker` and tracked background workers.

- [ ] **Step 11: Run task 3 focused verification**

Run:

```powershell
python -m pytest tests\test_gui_worker_utils.py tests\test_gui_behavior.py tests\test_settings_servers_page.py -q --basetemp .pytest_tmp_gui_remediation_t3
python -m ruff check src\jobdesk_app\gui tests\test_gui_worker_utils.py tests\test_gui_behavior.py tests\test_settings_servers_page.py
```

Expected: PASS and `All checks passed!`.

- [ ] **Step 12: Commit task 3**

Run:

```powershell
git add src\jobdesk_app\gui\pages\file_transfer_page.py src\jobdesk_app\gui\pages\runs_results_page.py src\jobdesk_app\gui\pages\settings_servers_page.py tests\test_gui_behavior.py tests\test_settings_servers_page.py tests\test_gui_worker_utils.py
git commit -m "refactor: run blocking gui actions on tracked workers"
```

---

### Task 4: Tighten GUI Type Checking

**Files:**
- Modify: `pyproject.toml`
- Modify: files changed by Tasks 1-3 if mypy reports concrete annotation issues

- [ ] **Step 1: Add mypy override for pure GUI helper modules**

In `pyproject.toml`, add this override after the existing non-GUI `check_untyped_defs` override:

```toml
[[tool.mypy.overrides]]
module = [
    "jobdesk_app.gui.worker_utils",
    "jobdesk_app.gui.pages.settings_servers_helpers",
    "jobdesk_app.gui.pages.file_transfer_helpers",
]
check_untyped_defs = true
```

- [ ] **Step 2: Run mypy and fix reported helper typing issues**

Run:

```powershell
python -m mypy src
```

Expected before fixes: either PASS or concrete errors in the helper modules.

If mypy reports `Need type annotation for <name>`, add explicit types. Use these patterns:

```python
registry: list[BackgroundWorker] | None = getattr(owner, registry_attr, None)
worker_ref: dict[str, BackgroundWorker] = {}
```

If mypy reports callback type mismatch for Qt signals, keep the helper API typed with `Callable` and cast only at the Qt boundary:

```python
from typing import cast

worker.result.connect(cast(Callable[[object], None], on_result))
```

- [ ] **Step 3: Run task 4 verification**

Run:

```powershell
python -m mypy src
python -m ruff check pyproject.toml src\jobdesk_app\gui
```

Expected: `Success: no issues found in 71 source files` or the current source-file count after new files; ruff reports `All checks passed!`.

- [ ] **Step 4: Commit task 4**

Run:

```powershell
git add pyproject.toml src\jobdesk_app\gui
git commit -m "test: type-check gui helper modules"
```

---

### Task 5: Add Optional Coverage Workflow for Skipped Surfaces

**Files:**
- Create: `.github/workflows/optional-coverage.yml`

- [ ] **Step 1: Create optional workflow**

Create `.github/workflows/optional-coverage.yml`:

```yaml
name: Optional coverage

on:
  workflow_dispatch:
  schedule:
    - cron: "17 20 * * 0"

jobs:
  chem-and-posix:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev,chem]"

      - name: Run optional local tests
        run: python -m pytest tests/test_viewer.py tests/test_remote_status.py -q --basetemp /tmp/jobdesk_pytest_optional

  real-remote:
    runs-on: windows-latest
    env:
      JOBDESK_TEST_SERVERS_YAML_B64: ${{ secrets.JOBDESK_TEST_SERVERS_YAML_B64 }}
      JOBDESK_TEST_SSH_SERVER_ID: ${{ secrets.JOBDESK_TEST_SSH_SERVER_ID }}
      JOBDESK_TEST_REMOTE_TMP_DIR: ${{ secrets.JOBDESK_TEST_REMOTE_TMP_DIR }}
      JOBDESK_TEST_REAL_CONFFLOW: ${{ secrets.JOBDESK_TEST_REAL_CONFFLOW }}
      QT_QPA_PLATFORM: offscreen
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Skip when remote secrets are unavailable
        if: ${{ env.JOBDESK_TEST_SERVERS_YAML_B64 == '' || env.JOBDESK_TEST_SSH_SERVER_ID == '' || env.JOBDESK_TEST_REMOTE_TMP_DIR == '' }}
        run: echo "Remote integration secrets are not configured; skipping real remote tests."

      - name: Decode servers.yaml
        if: ${{ env.JOBDESK_TEST_SERVERS_YAML_B64 != '' && env.JOBDESK_TEST_SSH_SERVER_ID != '' && env.JOBDESK_TEST_REMOTE_TMP_DIR != '' }}
        shell: pwsh
        run: |
          New-Item -ItemType Directory -Force .ci-secrets | Out-Null
          [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($env:JOBDESK_TEST_SERVERS_YAML_B64)) | Set-Content -LiteralPath .ci-secrets\servers.yaml -Encoding UTF8

      - name: Run real SSH/SFTP/submitter tests
        if: ${{ env.JOBDESK_TEST_SERVERS_YAML_B64 != '' && env.JOBDESK_TEST_SSH_SERVER_ID != '' && env.JOBDESK_TEST_REMOTE_TMP_DIR != '' }}
        shell: pwsh
        run: |
          $env:JOBDESK_TEST_SERVERS_YAML = (Resolve-Path .ci-secrets\servers.yaml).Path
          python -m pytest tests\integration\test_real_ssh.py tests\integration\test_real_sftp.py tests\integration\test_real_submitter.py -q --basetemp "$env:RUNNER_TEMP\jobdesk_pytest_real"

      - name: Run real ConfFlow test when enabled
        if: ${{ env.JOBDESK_TEST_SERVERS_YAML_B64 != '' && env.JOBDESK_TEST_SSH_SERVER_ID != '' && env.JOBDESK_TEST_REMOTE_TMP_DIR != '' && env.JOBDESK_TEST_REAL_CONFFLOW == '1' }}
        shell: pwsh
        run: |
          $env:JOBDESK_TEST_SERVERS_YAML = (Resolve-Path .ci-secrets\servers.yaml).Path
          python -m pytest tests\integration\test_real_confflow_wsl.py -q --basetemp "$env:RUNNER_TEMP\jobdesk_pytest_confflow"
```

- [ ] **Step 2: Validate workflow syntax locally**

Run:

```powershell
python -c "import yaml; yaml.safe_load(open('.github/workflows/optional-coverage.yml', encoding='utf-8')); print('yaml-ok')"
```

Expected: `yaml-ok`.

- [ ] **Step 3: Commit task 5**

Run:

```powershell
git add .github\workflows\optional-coverage.yml
git commit -m "ci: add optional integration coverage workflow"
```

---

### Task 6: Add PyInstaller Package Smoke Workflow

**Files:**
- Modify: `pyproject.toml`
- Create: `.github/workflows/package-smoke.yml`

- [ ] **Step 1: Add packaging extra**

In `pyproject.toml`, add this optional dependency group:

```toml
package = [
    "pyinstaller>=6.0",
]
```

Place it under `[project.optional-dependencies]` next to `chem` and `dev`.

- [ ] **Step 2: Create package smoke workflow**

Create `.github/workflows/package-smoke.yml`:

```yaml
name: Package smoke

on:
  pull_request:
    paths:
      - ".github/workflows/package-smoke.yml"
      - "packaging/**"
      - "pyproject.toml"
      - "src/jobdesk_app/gui/**"
  workflow_dispatch:

jobs:
  pyinstaller:
    runs-on: windows-latest
    env:
      QT_QPA_PLATFORM: offscreen
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev,package]"

      - name: Import GUI entry point
        run: python -c "from jobdesk_app.gui.app import main; print('gui-import-ok')"

      - name: Build PyInstaller bundle
        run: pyinstaller packaging\pyinstaller\jobdesk-gui.spec --noconfirm --clean

      - name: Verify bundle output
        shell: pwsh
        run: |
          $candidates = @(
            "dist\JobDesk\JobDesk.exe",
            "dist\jobdesk-gui\jobdesk-gui.exe"
          )
          $found = $false
          foreach ($candidate in $candidates) {
            if (Test-Path -LiteralPath $candidate) {
              Write-Output "bundle-ok: $candidate"
              $found = $true
            }
          }
          if (-not $found) {
            Get-ChildItem -Recurse dist | Select-Object FullName
            throw "Expected PyInstaller executable was not found"
          }
```

- [ ] **Step 3: Validate packaging metadata and workflow syntax**

Run:

```powershell
python -c "import tomllib; tomllib.loads(open('pyproject.toml', 'rb').read()); print('toml-ok')"
python -c "import yaml; yaml.safe_load(open('.github/workflows/package-smoke.yml', encoding='utf-8')); print('yaml-ok')"
python -m pytest tests\test_packaging_config.py -q --basetemp .pytest_tmp_gui_remediation_t6
```

Expected: `toml-ok`, `yaml-ok`, and packaging tests PASS.

- [ ] **Step 4: Commit task 6**

Run:

```powershell
git add pyproject.toml .github\workflows\package-smoke.yml
git commit -m "ci: add pyinstaller package smoke workflow"
```

---

### Task 7: Final Verification

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused GUI and packaging checks**

Run:

```powershell
python -m pytest tests\test_settings_servers_page.py tests\test_gui_worker_utils.py tests\test_gui_behavior.py tests\test_packaging_config.py -q --basetemp .pytest_tmp_gui_remediation_final_gui
```

Expected: PASS.

- [ ] **Step 2: Run full repo gates**

Run:

```powershell
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_gui_remediation_final -p no:cacheprovider
python -m build --outdir .build_gui_remediation
```

Expected:
- ruff: `All checks passed!`
- mypy: `Success: no issues found in N source files`
- pytest: all non-optional tests pass; skips remain limited to real integration, POSIX shell on Windows, and RDKit when not installed
- build: sdist and wheel created under `.build_gui_remediation`

- [ ] **Step 3: Clean local build output**

Run:

```powershell
$target = Resolve-Path -LiteralPath '.build_gui_remediation'
$expected = Join-Path (Resolve-Path '.').Path '.build_gui_remediation'
if ($target.Path -ne $expected) { throw "Unexpected target $($target.Path)" }
Remove-Item -LiteralPath $target.Path -Recurse -Force
```

Expected: `.build_gui_remediation` removed and `git status --short` shows only intended source/test/workflow changes.

- [ ] **Step 4: Check whitespace and final status**

Run:

```powershell
git diff --check
git status --short --branch
```

Expected: `git diff --check` has no output; status shows the task commits ahead of `origin/main` or a clean working tree after commits.

---

## Self-Review

**Spec coverage:**
- Server ID overwrite: Task 1.
- Worker setup duplication and `_target_fn` mutation: Task 2 and Task 3.
- Blocking GUI operations on UI thread: Task 3.
- Partial GUI type coverage: Task 4.
- Optional skipped surfaces: Task 5.
- Missing PyInstaller smoke: Task 6.
- Full verification and cleanup: Task 7.

**Placeholder scan:**
- No red-flag placeholder phrases are present in action steps.
- Each code-changing task includes exact paths, code snippets, commands, and expected outcomes.

**Type consistency:**
- `WorkerContext`, `start_tracked_worker()`, and `start_context_worker()` are defined before use.
- The same `registry_attr` names are used consistently: `_background_workers` for `FileTransferPage` and `SettingsServersPage`, `_bg_workers` for `RunsResultsPage`.
- `validate_server_id_change()` returns `str | None` and all page call sites treat non-`None` as a rejection message.
