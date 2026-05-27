# Files Page Directory Move Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Files-page users move local or remote paths by dragging them onto a directory row in the same pane.

**Architecture:** Keep `_FileTable` responsible for resolving the drop target row and emitting same-pane move requests. Keep `FileTransferPage` responsible for validating and executing moves: local paths use filesystem move operations, and remote paths use the existing `FileTransferService.rename_remote()` API. Existing cross-pane transfer drops remain unchanged.

**Tech Stack:** Python, PySide6, pytest, pytest-qt

---

### Task 1: Route Same-Pane Directory Drops

**Files:**
- Modify: `tests/test_gui_behavior.py`
- Modify: `tests/test_file_transfer_service.py`
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/services/file_transfer_service.py`

- [x] **Step 1: Write failing route tests**

Add tests which create directory rows and verify:

```python
file_page.local_table.move_local_files.connect(
    lambda paths, target: moves.append((paths, target))
)
file_page.remote_table.move_remote_files.connect(
    lambda paths, target: moves.append((paths, target))
)
assert moves == [([str(source)], str(target_dir))]
assert remote_moves == [(["/remote/source.log"], "/remote/archive")]
```

Use a mocked drop event whose `position().toPoint()` resolves to the inserted
directory item. Add a parent-row case asserting no move signal is emitted.

- [x] **Step 2: Verify route tests fail**

Run:

```powershell
pytest tests\test_gui_behavior.py::TestFileTransferPage::test_local_table_routes_drop_on_directory_for_move tests\test_gui_behavior.py::TestFileTransferPage::test_remote_table_routes_drop_on_directory_for_move tests\test_gui_behavior.py::TestFileTransferPage::test_parent_row_is_not_a_move_drop_target -q -p no:cacheprovider --basetemp C:\dft\tool\jobdesk\.pytest_tmp_directory_move_route
```

Expected: failures because `_FileTable` does not expose same-pane move
signals or directory-target routing.

- [x] **Step 3: Implement route signals and directory target resolution**

Add:

```python
move_local_files = Signal(list, str)
move_remote_files = Signal(list, str)
```

Implement `_drop_directory_path(event)` using `itemAt(event.position().toPoint())`,
the role-specific kind/path columns, and rejection of `..`. In `dropEvent`,
emit `move_local_files` when local URLs land on a local directory row and
`move_remote_files` when remote payloads land on a remote directory row,
before the existing copy/upload/download fallbacks.

- [x] **Step 4: Verify route tests pass**

Run the command from Step 2. Expected: PASS.

### Task 2: Execute Local And Remote Moves Safely

**Files:**
- Modify: `tests/test_gui_behavior.py`
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`

- [x] **Step 1: Write failing move-handler tests**

Add page tests that call the new handlers and assert:

```python
file_page._move_local_paths_into_directory([str(source)], str(target_dir))
assert not source.exists()
assert (target_dir / source.name).read_text(encoding="utf-8") == "contents"

file_page._move_remote_paths_into_directory(
    ["/remote/source.log"], "/remote/archive"
)
service.rename_remote.assert_called_once_with(
    "/remote/source.log", "/remote/archive/source.log"
)
```

Add rejection tests for an existing local destination and local/remote
directory moves into a descendant path. Add a service regression asserting
that `rename_remote("/remote/a.txt", "/remote/b.txt")` raises
`RemotePathError` when `/remote/b.txt` already exists.

- [x] **Step 2: Verify handler tests fail**

Run:

```powershell
pytest tests\test_gui_behavior.py::TestFileTransferPage::test_move_local_path_into_directory tests\test_gui_behavior.py::TestFileTransferPage::test_move_remote_path_into_directory_uses_rename tests\test_gui_behavior.py::TestFileTransferPage::test_move_local_does_not_overwrite_existing_destination tests\test_gui_behavior.py::TestFileTransferPage::test_move_local_directory_rejects_descendant_target tests\test_gui_behavior.py::TestFileTransferPage::test_move_remote_directory_rejects_descendant_target -q -p no:cacheprovider --basetemp C:\dft\tool\jobdesk\.pytest_tmp_directory_move_handlers
pytest tests\test_file_transfer_service.py::test_rename_remote_rejects_existing_destination -q -p no:cacheprovider --basetemp C:\dft\tool\jobdesk\.pytest_tmp_directory_move_service
```

Expected: failures because the handlers do not exist.

- [x] **Step 3: Implement page handlers and wiring**

Connect the new table signals in `FileTransferPage.__init__()`:

```python
self.local_table.move_local_files.connect(self._move_local_paths_into_directory)
self.remote_table.move_remote_files.connect(self._move_remote_paths_into_directory)
```

Implement local moves using `shutil.move()` after rejecting missing sources,
existing destinations, the same path, and directory-to-descendant moves.
Implement remote moves using `remote_child_path()` and
`self._service.rename_remote()` after rejecting same-path and
directory-to-descendant targets. Refresh only the affected pane after at least
one successful move and report validation/service failures through
`self._error_cb("Move Error", ...)`.

Update `FileTransferService.rename_remote()` to call `sftp.exists(new_path)`
and raise `RemotePathError(f"Destination already exists: {new_path}")` before
invoking `sftp.rename(old_path, new_path)`.

- [x] **Step 4: Verify handler tests pass**

Run the command from Step 2. Expected: PASS.

### Task 3: Verify Compatibility And Commit

**Files:**
- Verify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Verify: `src/jobdesk_app/services/file_transfer_service.py`
- Verify: `tests/test_gui_behavior.py`
- Verify: `tests/test_file_transfer_service.py`
- Verify: `docs/superpowers/specs/2026-05-27-files-page-directory-move-design.md`
- Verify: `docs/superpowers/plans/2026-05-27-files-page-directory-move.md`

- [x] **Step 1: Run Files-page regression tests**

```powershell
pytest tests\test_gui_behavior.py tests\test_file_transfer_page_helpers.py tests\test_file_transfer_service.py -q -p no:cacheprovider --basetemp C:\dft\tool\jobdesk\.pytest_tmp_directory_move_regression
```

Expected: PASS, including existing upload and download drag-drop tests.

- [x] **Step 2: Run code-quality checks**

```powershell
ruff check --no-cache src tests
$env:PYTHONPYCACHEPREFIX='C:\dft\tool\jobdesk\.pycache_directory_move_verify'; python -m compileall -q src tests
git diff --check
```

Expected: all commands exit `0`.

- [x] **Step 3: Commit implemented work**

```powershell
git add docs/superpowers/plans/2026-05-27-files-page-directory-move.md src/jobdesk_app/gui/pages/file_transfer_page.py src/jobdesk_app/services/file_transfer_service.py tests/test_gui_behavior.py tests/test_file_transfer_service.py
git commit -m "feat: move Files paths by dropping onto directories"
```
