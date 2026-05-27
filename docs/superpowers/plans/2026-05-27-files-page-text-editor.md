# Files Page Text Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route ordinary Files-page file opens through a user-configurable text editor, defaulting to `notepad.exe`.

**Architecture:** Persist one editor command in `GuiSettings`, expose it in the existing Settings cards, and centralize editor launching in `FileTransferPage`. Local files launch directly; remote files preserve the current temp-download workflow and only change the launcher.

**Tech Stack:** Python 3.11+, PySide6, YAML settings persistence, pytest/pytest-qt.

---

### Task 1: Persist And Edit The Text Editor Setting

**Files:**
- Modify: `src/jobdesk_app/services/gui_settings.py`
- Modify: `src/jobdesk_app/gui/pages/settings_servers_page.py`
- Modify: `src/jobdesk_app/gui/i18n.py`
- Test: `tests/test_gui_settings.py`
- Test: `tests/test_settings_servers_page.py`

- [ ] **Step 1: Write failing settings persistence tests**

Add assertions that a missing settings file loads `text_editor_path == "notepad.exe"`, and that saving/loading `GuiSettings(text_editor_path="C:/Tools/code.exe")` preserves that value.

- [ ] **Step 2: Run the tests and observe the missing field failure**

Run: `pytest tests/test_gui_settings.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_editor_red`

Expected: FAIL because `GuiSettings` has no `text_editor_path`.

- [ ] **Step 3: Add minimal settings persistence**

Add `text_editor_path: str = "notepad.exe"` to `GuiSettings`, load it with `str(raw.get("text_editor_path", "notepad.exe") or "notepad.exe")`, and write it in `GuiSettingsStore.save()`.

- [ ] **Step 4: Add failing Settings-page tests**

Create a page with a mocked `GuiSettingsStore`, assert an editor line edit is populated from a stored custom path, then edit the value and invoke `_save_settings()` to assert the replacement settings include the edited path.

- [ ] **Step 5: Run the Settings-page tests and observe the missing control failure**

Run: `pytest tests/test_settings_servers_page.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_editor_ui_red`

Expected: FAIL because no editor control is present.

- [ ] **Step 6: Add the editor settings card**

Create `self.text_editor_edit`, a browse button calling `_browse_text_editor()`, and a `SettingCard` placed after local directory. Load and save `text_editor_path`, falling back to `notepad.exe` for blank input. Add translations for `Text Editor`, `Editor used to open files in Files page`, and `Select text editor`.

- [ ] **Step 7: Verify settings tests pass**

Run: `pytest tests/test_gui_settings.py tests/test_settings_servers_page.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_editor_settings_green`

Expected: PASS.

### Task 2: Route Files-Page Default Opens Through The Editor

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Test: `tests/test_gui_behavior.py`

- [ ] **Step 1: Write failing behavior tests**

Add tests which set `file_page._gui_settings = replace(file_page._gui_settings, text_editor_path="C:/Tools/editor.exe")`, open a local file item, and assert `subprocess.Popen(["C:/Tools/editor.exe", local_path])` is called. Add a remote completion-path test asserting the same launcher is used after the downloaded temp path is returned. Keep a directory navigation test asserting launching is not performed.

- [ ] **Step 2: Run the targeted tests and observe OS-association behavior**

Run: `pytest tests/test_gui_behavior.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_editor_open_red`

Expected: FAIL because ordinary files still call `os.startfile`.

- [ ] **Step 3: Implement one explicit editor launcher**

Import `subprocess`, add `_open_in_text_editor(self, path: str | Path)` which calls `subprocess.Popen([self._gui_settings.text_editor_path or "notepad.exe", str(path)])` and reports launch exceptions through `_error_cb`. Use it in `_open_local_item()` and the remote download result callback. Leave `Open in Viewer` actions unchanged.

- [ ] **Step 4: Verify default-open behavior passes**

Run: `pytest tests/test_gui_behavior.py tests/test_gui_settings.py tests/test_settings_servers_page.py -q -p no:cacheprovider --basetemp C:\tmp\jobdesk_pytest_editor_green`

Expected: PASS.
