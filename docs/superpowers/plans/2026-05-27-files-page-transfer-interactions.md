# Files Page Transfer Interactions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Files-page transfer progress compact and make both user-reported drag transfer directions operate through safe existing upload/download workflows.

**Architecture:** Keep `FileTransferPage` responsible for transfer actions and `_FileTable` responsible only for destination-based drag payload routing. Reuse `_upload_dropped_local_paths()` and `_download_dropped_remote_paths()` without string-sniffing fallbacks, keep ordinary drops non-destructive, and retain configured-root authorization for remote deletion.

**Tech Stack:** Python, PySide6, pytest, pytest-qt

---

### Task 1: Lock Down Progress Placement and Drag Routing

**Files:**
- Modify: `tests/test_gui_behavior.py`
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`

- [x] **Step 1: Write failing GUI tests**

Add tests that assert:

```python
assert file_page.run_options_row.indexOf(file_page.progress_bar) == (
    file_page.run_options_row.indexOf(file_page.create_only_btn) + 1
)
assert file_page.progress_bar.maximumWidth() <= 360
assert file_page.remote_table._accepts_mime(local_url_mime)
assert upload_paths_emitted == [[str(source)]]
assert download_paths_emitted == [["/remote/result.log"]]
```

Use `QMimeData` with `QUrl.fromLocalFile(...)` for an external upload payload
and `application/x-jobdesk-remote-paths` for the download payload.

- [x] **Step 2: Run tests to verify the new expectations fail**

Run:

```powershell
pytest tests\test_gui_behavior.py -q
```

Expected: failure for compact progress placement before implementation; drag
routing failures identify any destination/payload mismatch.

- [x] **Step 3: Implement compact progress placement**

Store the row as `self.run_options_row`, move `self.progress_bar` into that row
immediately after
`self.create_only_btn`, set a bounded width such as `setMaximumWidth(320)`,
and remove the separate `run_layout.addWidget(self.progress_bar)` placement.

- [x] **Step 4: Implement destination-based drag routing**

Ensure `_FileTable` routes:

```python
if self.role == "remote" and mime.hasUrls():
    self.drop_files.emit(local_paths)
if self.role == "local" and mime.hasFormat("application/x-jobdesk-remote-paths"):
    self.drop_files.emit(remote_paths)
```

Route the events into the existing upload/download handlers, retaining any
already-working local-pane copy behavior needed by current uncommitted
Files-page work. Reject non-local URLs and do not add deletion behavior for
external URL drops.

- [x] **Step 5: Run focused tests to verify green**

Run:

```powershell
pytest tests\test_gui_behavior.py tests\test_file_transfer_page_helpers.py -q
```

Expected: PASS.

### Task 2: Verify the Integrated Patch

**Files:**
- Verify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Verify: `tests/test_gui_behavior.py`

- [x] **Step 1: Run transfer-focused verification**

```powershell
pytest tests\test_gui_behavior.py tests\test_file_transfer_page_helpers.py tests\test_file_transfer_service.py -q
```

Expected: PASS.

- [x] **Step 2: Check patch formatting**

```powershell
git diff --check
```

Expected: no output and exit code `0`.

### Task 3: Address Integrated Review Safety Findings

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/services/file_transfer_service.py`
- Modify: `tests/test_gui_behavior.py`
- Modify: `tests/test_file_transfer_service.py`

- [x] **Step 1: Add regression tests for reviewed failure modes**

Cover non-local URL rejection, POSIX local drag routing, non-destructive drop
policy, settings-store isolation in the Files-page fixture, and the absence of
an API that authorizes deletion from the current browsing directory.

- [x] **Step 2: Run the regression tests red**

Run:

```powershell
pytest tests\test_file_transfer_service.py tests\test_gui_behavior.py -q
```

Expected: failures for the reviewed unsafe behavior before correction.

- [x] **Step 3: Correct the integrated behavior**

Connect remote/local tables directly to upload/download handlers, remove path
sniffing fallbacks, accept only local URL payloads, restore
`OverwritePolicy.skip_same_size` for ordinary drag upload, remove
`extra_allowed_roots`, and isolate GUI settings in tests.

- [x] **Step 4: Run final verification**

Run:

```powershell
pytest tests\test_gui_behavior.py tests\test_file_transfer_page_helpers.py tests\test_file_transfer_service.py -q
pytest -q
ruff check src tests
mypy src
python -m compileall -q src tests
git diff --check
```
