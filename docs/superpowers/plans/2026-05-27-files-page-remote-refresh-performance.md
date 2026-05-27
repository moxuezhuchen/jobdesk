# Files Page Remote Refresh Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce repeated remote Files-page refresh latency by reusing one live SFTP session for interactive browsing while retaining safe one-shot service behavior elsewhere.

**Architecture:** `FileTransferService` gains opt-in persistent session ownership guarded by a lock; its existing default still creates and closes a session per operation. `FileTransferPage` opts into persistence, closes superseded or shutdown sessions, and preserves its existing request-id stale-result protection.

**Tech Stack:** Python 3.11+, threading locks, PySide6 background workers, pytest.

---

### Task 1: Add Opt-In Persistent SFTP Session Ownership

**Files:**
- Modify: `src/jobdesk_app/services/file_transfer_service.py`
- Test: `tests/test_file_transfer_service.py`

- [ ] **Step 1: Write failing service lifetime tests**

Extend `FakeSFTP` with `list_dir_info()`. Add tests that the default service closes a newly-created SFTP after each list, that `persistent_session=True` reuses one object across two lists until `close()`, and that an exception closes/discards the cached session so a later list obtains a replacement.

- [ ] **Step 2: Run the lifetime tests and observe missing opt-in mode**

Run: `pytest tests/test_file_transfer_service.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_session_red`

Expected: FAIL because `persistent_session` and `close()` are not implemented.

- [ ] **Step 3: Implement locked persistent contexts**

Add `persistent_session: bool = False`, an `RLock`, and a cached SFTP member. For default mode return the existing close-on-exit context. For persistent mode return a context which holds the lock through the operation, lazily creates the cached client, closes and clears it on operation exception, and leaves it open on success. Add `close()` to release the cached client explicitly.

- [ ] **Step 4: Verify service lifetime tests pass**

Run: `pytest tests/test_file_transfer_service.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_session_green`

Expected: PASS.

### Task 2: Opt The Files Page Into Reuse And Release Sessions

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing page wiring tests**

Add tests that `_connect()` creates `FileTransferService` with persistent-session behavior, reconnecting closes the old service, `shutdown()` closes its active service after workers are stopped, and a late request id still cannot update the visible remote list.

- [ ] **Step 2: Run page tests and observe missing lifecycle management**

Run: `pytest tests/test_gui_behavior.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_session_page_red`

Expected: FAIL because page-created services remain one-shot and are not closed.

- [ ] **Step 3: Wire lifecycle management**

Construct the page service with `persistent_session=True`; before replacing an existing service, close it; after worker teardown in `shutdown()`, close the current service and set it to `None`. Keep `_remote_list_request_id` validation unchanged.

- [ ] **Step 4: Verify page and service tests pass**

Run: `pytest tests/test_file_transfer_service.py tests/test_gui_behavior.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_session_page_green`

Expected: PASS.

### Task 3: Validate Regression And Interactive Latency

**Files:**
- Test: `tests/test_file_transfer_service.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Run the targeted local regression suite**

Run: `pytest tests/test_gui_settings.py tests/test_settings_servers_page.py tests/test_file_transfer_service.py tests/test_file_transfer_page_helpers.py tests/test_gui_behavior.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_files_remaining_targeted`

Expected: PASS.

- [ ] **Step 2: Run quality gates and full regression**

Run: `ruff check --no-cache src tests`

Run: `mypy src --cache-dir C:\tmp\jobdesk_mypy_files_remaining`

Run: `pytest -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_files_remaining_full`

Expected: all commands exit with code 0.

- [ ] **Step 3: Measure confirmed configured remote target**

Identify whether the configured target referenced by the report is `814n` or the previously exercised `814new`. With that target confirmed, time initial list and two repeated lists using a persistent service against the same directory; record the elapsed values and verify repeated refreshes reuse one connection.
