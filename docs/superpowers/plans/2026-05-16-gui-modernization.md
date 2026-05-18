# JobDesk GUI Modernization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Modernize the JobDesk PySide6 GUI with a maintainable theme layer, cleaner workbench layout, explicit Projects navigation, and stronger GUI tests without adding a new production UI dependency.

**Architecture:** Keep native PySide6 widgets and the current service layer. Move visual policy into `src/jobdesk_app/gui/theme.py`, keep `MainWindow` focused on shell/navigation/page lifecycle, and use small shared helpers for repeated page patterns.

**Tech Stack:** Python 3.13, PySide6, pytest, existing JobDesk GUI services and page modules.

---

## File Structure

- Create `src/jobdesk_app/gui/theme.py`
  - Owns colors, metrics, QSS generation, title-label helper, button-height normalization, and table defaults.
- Modify `src/jobdesk_app/gui/main_window.py`
  - Imports theme helpers, removes `_APP_STYLESHEET`, adds Projects navigation decision, and keeps page lifecycle behavior.
- Modify `src/jobdesk_app/gui/i18n.py`
  - Adds the `Projects` label with an ASCII unicode-escape value for Chinese.
- Modify page modules:
  - `src/jobdesk_app/gui/pages/file_transfer_page.py`
  - `src/jobdesk_app/gui/pages/runs_page.py`
  - `src/jobdesk_app/gui/pages/results_page.py`
  - `src/jobdesk_app/gui/pages/servers_page.py`
  - `src/jobdesk_app/gui/pages/settings_page.py`
  - `src/jobdesk_app/gui/pages/projects_page.py`
- Create `tests/test_gui_theme.py`
  - Verifies the theme API and helper behavior.
- Modify `tests/test_gui_imports.py`
  - Removes brittle assertions against `_APP_STYLESHEET`.
- Modify `tests/test_i18n.py`
  - Verifies the Projects label path in English and Chinese.

---

### Task 1: Extract the Theme Module

**Files:**
- Create: `src/jobdesk_app/gui/theme.py`
- Create: `tests/test_gui_theme.py`
- Modify: `src/jobdesk_app/gui/main_window.py`
- Modify: `tests/test_gui_imports.py`

- [ ] **Step 1: Write the failing theme tests**

Create `tests/test_gui_theme.py`:

```python
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 not installed")


def test_build_app_stylesheet_contains_core_selectors_and_tokens():
    from jobdesk_app.gui.theme import ThemeColors, ThemeMetrics, build_app_stylesheet

    css = build_app_stylesheet()

    assert ThemeColors.ACCENT == "#2563eb"
    assert ThemeMetrics.CONTROL_HEIGHT == 36
    assert "QMainWindow" in css
    assert "QListWidget::item:selected" in css
    assert ThemeColors.ACCENT in css
    assert "selection-color: #ffffff" in css


def test_page_title_helper_sets_object_name_and_text(qt_app):
    from jobdesk_app.gui.theme import page_title_label

    label = page_title_label("Runs")

    assert label.text() == "Runs"
    assert label.objectName() == "PageTitle"


@pytest.fixture
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
```

- [ ] **Step 2: Run the new tests and confirm they fail**

Run:

```powershell
pytest tests\test_gui_theme.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'jobdesk_app.gui.theme'`.

- [ ] **Step 3: Add the initial theme module**

Create `src/jobdesk_app/gui/theme.py`:

```python
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QTableWidget, QSizePolicy


class ThemeColors:
    BACKGROUND = "#f5f7fb"
    SURFACE = "#ffffff"
    SURFACE_ALT = "#f8fafc"
    TEXT = "#20242c"
    MUTED_TEXT = "#475569"
    BORDER = "#cbd5e1"
    BORDER_SUBTLE = "#e2e8f0"
    HEADER = "#e8edf5"
    NAV_BACKGROUND = "#1f2937"
    NAV_TEXT = "#e5e7eb"
    ACCENT = "#2563eb"
    ACCENT_SOFT = "#eef4ff"
    ACCENT_PRESSED = "#dbeafe"
    ACCENT_BORDER = "#93c5fd"
    WHITE = "#ffffff"


class ThemeMetrics:
    CONTROL_HEIGHT = 36
    PAGE_MARGIN = 14
    PAGE_SPACING = 10
    RADIUS = 6
    NAV_MIN_WIDTH = 140
    NAV_MAX_WIDTH = 190


APP_FONT_FAMILIES = '"Microsoft YaHei UI", "Segoe UI", Arial'


def build_app_stylesheet() -> str:
    c = ThemeColors
    m = ThemeMetrics
    return f"""
QMainWindow, QWidget {{
    background: {c.BACKGROUND};
    color: {c.TEXT};
    font-family: {APP_FONT_FAMILIES};
    font-size: 10pt;
    font-weight: 600;
}}
QLabel#PageTitle {{
    color: {c.TEXT};
    font-size: 14pt;
    font-weight: 700;
    padding: 0 0 4px 0;
}}
QListWidget {{
    background: {c.NAV_BACKGROUND};
    color: {c.NAV_TEXT};
    border: 0;
    padding: 10px 6px;
    outline: 0;
}}
QListWidget::item {{
    padding: 10px 12px;
    border-radius: {m.RADIUS}px;
    font-weight: 600;
}}
QListWidget::item:hover {{
    background: #334155;
}}
QListWidget::item:selected {{
    background: {c.ACCENT};
    color: {c.WHITE};
}}
QPushButton {{
    background: {c.SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS}px;
    padding: 0 12px;
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {c.ACCENT_SOFT};
    border-color: {c.ACCENT_BORDER};
}}
QPushButton:pressed {{
    background: {c.ACCENT_PRESSED};
}}
QPushButton:disabled {{
    color: #94a3b8;
    background: #f1f5f9;
    border-color: {c.BORDER_SUBTLE};
}}
QLineEdit, QComboBox, QSpinBox, QTextEdit, QTableWidget, QGroupBox {{
    background: {c.SURFACE};
    border: 1px solid {c.BORDER};
    border-radius: {m.RADIUS}px;
}}
QLineEdit, QComboBox, QSpinBox {{
    min-height: {m.CONTROL_HEIGHT}px;
    max-height: {m.CONTROL_HEIGHT}px;
    padding: 0 8px;
    font-weight: 600;
}}
QGroupBox {{
    margin-top: 12px;
    padding: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {c.MUTED_TEXT};
}}
QHeaderView::section {{
    background: {c.HEADER};
    border: 0;
    border-right: 1px solid {c.BORDER};
    border-bottom: 1px solid {c.BORDER};
    padding: 6px 8px;
    font-weight: 700;
}}
QTableWidget {{
    gridline-color: {c.BORDER_SUBTLE};
    selection-background-color: {c.ACCENT};
    selection-color: {c.WHITE};
    alternate-background-color: {c.SURFACE_ALT};
    font-weight: 600;
}}
QTableWidget::item:selected {{
    background: {c.ACCENT};
    color: {c.WHITE};
}}
QSplitter::handle {{
    background: #d8dee9;
}}
QSplitter::handle:hover {{
    background: {c.ACCENT_BORDER};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: transparent;
    border: 0;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: #cbd5e1;
    border-radius: 4px;
    min-height: 28px;
    min-width: 28px;
}}
QScrollBar::handle:hover {{
    background: #94a3b8;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
"""


def page_title_label(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label


def normalize_control_heights(*widgets) -> None:
    for widget in widgets:
        widget.setMinimumHeight(ThemeMetrics.CONTROL_HEIGHT)
        widget.setMaximumHeight(ThemeMetrics.CONTROL_HEIGHT)
        widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(), QSizePolicy.Fixed)


def configure_standard_table(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)
    table.verticalHeader().setVisible(False)
```

- [ ] **Step 4: Use the theme module from `main_window.py`**

Modify the imports in `src/jobdesk_app/gui/main_window.py`:

```python
from .theme import ThemeMetrics, build_app_stylesheet
```

Change this line:

```python
self.setStyleSheet(_APP_STYLESHEET)
```

to:

```python
self.setStyleSheet(build_app_stylesheet())
```

Change the nav width setup:

```python
self.nav.setMinimumWidth(ThemeMetrics.NAV_MIN_WIDTH)
self.nav.setMaximumWidth(ThemeMetrics.NAV_MAX_WIDTH)
```

Remove the `_APP_STYLESHEET = """..."""` block from the bottom of the file.

- [ ] **Step 5: Update the import test**

In `tests/test_gui_imports.py`, replace `test_main_window_ui_policy_helpers` with:

```python
def test_main_window_ui_policy_helpers():
    from jobdesk_app.gui.main_window import main_window_has_status_bar, main_window_shows_log_panel
    from jobdesk_app.gui.theme import build_app_stylesheet

    css = build_app_stylesheet()

    assert main_window_has_status_bar() is False
    assert main_window_shows_log_panel() is False
    assert "selection-background-color: #2563eb" in css
    assert "selection-color: #ffffff" in css
```

- [ ] **Step 6: Run focused tests**

Run:

```powershell
pytest tests\test_gui_theme.py tests\test_gui_imports.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

Run:

```powershell
git add src\jobdesk_app\gui\theme.py src\jobdesk_app\gui\main_window.py tests\test_gui_theme.py tests\test_gui_imports.py
git commit -m "refactor: extract gui theme"
```

---

### Task 2: Make Projects Navigation Explicit

**Files:**
- Modify: `src/jobdesk_app/gui/main_window.py`
- Modify: `src/jobdesk_app/gui/i18n.py`
- Modify: `tests/test_gui_imports.py`
- Modify: `tests/test_i18n.py`

- [ ] **Step 1: Write failing tests for navigation labels and Projects translation**

Add to `tests/test_gui_imports.py`:

```python
def test_main_navigation_labels_include_projects_first():
    from jobdesk_app.gui.main_window import main_navigation_labels

    assert main_navigation_labels("en") == (
        "Projects",
        "Files",
        "Runs",
        "Results",
        "Servers",
        "Settings",
    )
```

Add to `tests/test_i18n.py`:

```python
def test_translate_projects_label():
    assert tr("Projects", "en") == "Projects"
    assert tr("Projects", "zh") == "\u9879\u76ee"
```

- [ ] **Step 2: Run tests and confirm they fail**

Run:

```powershell
pytest tests\test_gui_imports.py::test_main_navigation_labels_include_projects_first tests\test_i18n.py::test_translate_projects_label -q
```

Expected: FAIL because `main_navigation_labels` is missing and the Chinese `Projects` mapping is missing.

- [ ] **Step 3: Add Projects translation**

In `src/jobdesk_app/gui/i18n.py`, add this entry to `ZH`:

```python
"Projects": "\u9879\u76ee",
```

- [ ] **Step 4: Add the navigation helper and Projects page**

In `src/jobdesk_app/gui/main_window.py`, add the import:

```python
from .pages.projects_page import ProjectsPage
```

Add this helper near the existing policy helpers:

```python
def main_navigation_labels(language: str) -> tuple[str, ...]:
    labels = ("Projects", "Files", "Runs", "Results", "Servers", "Settings")
    return tuple(tr(label, language) for label in labels)
```

In `MainWindow.__init__`, replace the hard-coded labels loop:

```python
for label in ("Files", "Runs", "Results", "Servers", "Settings"):
    self.nav.addItem(tr(label, self.language))
```

with:

```python
for label in main_navigation_labels(self.language):
    self.nav.addItem(label)
```

Create `self.projects_page` before `self.files_page`:

```python
self.projects_page = ProjectsPage(self.state, self._log, self._update_status, self._on_project_opened)
```

Add it first in the stack:

```python
self.pages.addWidget(self.projects_page)
self.pages.addWidget(self.files_page)
self.pages.addWidget(self.runs_page)
self.pages.addWidget(self.results_page)
self.pages.addWidget(self.servers_page)
self.pages.addWidget(self.settings_page)
```

Update `_apply_language` to use the helper:

```python
labels = main_navigation_labels(self.language)
```

Update the page loop in `_apply_language`:

```python
for page in (
    self.projects_page,
    self.files_page,
    self.runs_page,
    self.results_page,
    self.servers_page,
    self.settings_page,
):
    if hasattr(page, "apply_language"):
        page.apply_language(self.language)
```

Update `shutdown()` only if `ProjectsPage` later gains background workers. For the current page, no shutdown call is required.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
pytest tests\test_gui_imports.py tests\test_i18n.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```powershell
git add src\jobdesk_app\gui\main_window.py src\jobdesk_app\gui\i18n.py tests\test_gui_imports.py tests\test_i18n.py
git commit -m "feat: expose projects in gui navigation"
```

---

### Task 3: Add Shared Page Layout Helpers

**Files:**
- Modify: `src/jobdesk_app/gui/theme.py`
- Modify: `tests/test_gui_theme.py`

- [ ] **Step 1: Write failing tests for helper behavior**

Add to `tests/test_gui_theme.py`:

```python
def test_normalize_control_heights_sets_fixed_height(qt_app):
    from PySide6.QtWidgets import QPushButton
    from jobdesk_app.gui.theme import ThemeMetrics, normalize_control_heights

    button = QPushButton("Refresh")
    normalize_control_heights(button)

    assert button.minimumHeight() == ThemeMetrics.CONTROL_HEIGHT
    assert button.maximumHeight() == ThemeMetrics.CONTROL_HEIGHT


def test_configure_standard_table_applies_table_defaults(qt_app):
    from PySide6.QtWidgets import QTableWidget
    from jobdesk_app.gui.theme import configure_standard_table

    table = QTableWidget()
    configure_standard_table(table)

    assert table.alternatingRowColors() is True
    assert table.verticalHeader().isVisible() is False
```

- [ ] **Step 2: Run tests and confirm they fail if helpers are incomplete**

Run:

```powershell
pytest tests\test_gui_theme.py -q
```

Expected: PASS if Task 1 already included the helpers exactly; otherwise FAIL on the missing or incomplete helper and proceed with Step 3.

- [ ] **Step 3: Complete helper implementation**

Ensure `src/jobdesk_app/gui/theme.py` contains:

```python
def normalize_control_heights(*widgets) -> None:
    for widget in widgets:
        widget.setMinimumHeight(ThemeMetrics.CONTROL_HEIGHT)
        widget.setMaximumHeight(ThemeMetrics.CONTROL_HEIGHT)
        widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(), QSizePolicy.Fixed)


def configure_standard_table(table: QTableWidget) -> None:
    table.setAlternatingRowColors(True)
    table.setShowGrid(True)
    table.verticalHeader().setVisible(False)
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
pytest tests\test_gui_theme.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

Run:

```powershell
git add src\jobdesk_app\gui\theme.py tests\test_gui_theme.py
git commit -m "test: cover gui theme helpers"
```

---

### Task 4: Apply Shared Theme Helpers to Pages

**Files:**
- Modify: `src/jobdesk_app/gui/pages/file_transfer_page.py`
- Modify: `src/jobdesk_app/gui/pages/runs_page.py`
- Modify: `src/jobdesk_app/gui/pages/results_page.py`
- Modify: `src/jobdesk_app/gui/pages/servers_page.py`
- Modify: `src/jobdesk_app/gui/pages/settings_page.py`
- Modify: `src/jobdesk_app/gui/pages/projects_page.py`

- [ ] **Step 1: Write import-level guard test**

Add to `tests/test_gui_imports.py`:

```python
def test_page_modules_can_use_theme_helpers():
    from jobdesk_app.gui.theme import configure_standard_table, normalize_control_heights, page_title_label

    assert callable(configure_standard_table)
    assert callable(normalize_control_heights)
    assert callable(page_title_label)
```

- [ ] **Step 2: Run the guard test**

Run:

```powershell
pytest tests\test_gui_imports.py::test_page_modules_can_use_theme_helpers -q
```

Expected: PASS.

- [ ] **Step 3: Replace inline title labels**

In `runs_page.py`, replace:

```python
self.title = QLabel()
self.title.setStyleSheet("font-size: 14pt; font-weight: bold;")
layout.addWidget(self.title)
```

with:

```python
from ..theme import configure_standard_table, page_title_label

self.title = page_title_label()
layout.addWidget(self.title)
```

In `results_page.py`, `servers_page.py`, and `projects_page.py`, make the same title replacement. Keep `SettingsPage` title-less because its group boxes already define the page structure.

- [ ] **Step 4: Apply table defaults**

In `runs_page.py`, after `self.table = QTableWidget()` add:

```python
configure_standard_table(self.table)
```

In `results_page.py`, after `self.data_table = QTableWidget()` add:

```python
configure_standard_table(self.data_table)
```

In `servers_page.py`, after `self.table = QTableWidget()` add:

```python
configure_standard_table(self.table)
```

In `settings_page.py`, after `self.table = QTableWidget()` add:

```python
configure_standard_table(self.table)
```

In `projects_page.py`, after `self.info_table = QTableWidget()` add:

```python
configure_standard_table(self.info_table)
```

In `file_transfer_page.py`, update `_setup_table`:

```python
def _setup_table(table: QTableWidget, headers: list[str], hidden_columns: list[int] | None = None) -> None:
    configure_standard_table(table)
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    table.horizontalHeader().setStretchLastSection(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    for column in hidden_columns or []:
        table.setColumnHidden(column, True)
```

Add this import in `file_transfer_page.py`:

```python
from ..theme import ThemeMetrics, configure_standard_table, normalize_control_heights
```

- [ ] **Step 5: Remove the local file-transfer control-height constant**

In `file_transfer_page.py`, remove:

```python
CONTROL_HEIGHT = 36
```

Change `_normalize_control_heights` to delegate:

```python
def _normalize_control_heights(self, *widgets):
    normalize_control_heights(*widgets)
```

- [ ] **Step 6: Use theme metrics for common page margins**

In simple page constructors that currently use default layout margins, set a consistent margin:

```python
from ..theme import ThemeMetrics

layout.setContentsMargins(
    ThemeMetrics.PAGE_MARGIN,
    ThemeMetrics.PAGE_MARGIN,
    ThemeMetrics.PAGE_MARGIN,
    ThemeMetrics.PAGE_MARGIN,
)
layout.setSpacing(ThemeMetrics.PAGE_SPACING)
```

Apply this in `runs_page.py`, `results_page.py`, `servers_page.py`, and `projects_page.py`. `settings_page.py` already uses `14` and `10`; replace those numeric literals with `ThemeMetrics.PAGE_MARGIN` and `ThemeMetrics.PAGE_SPACING`.

- [ ] **Step 7: Run GUI helper tests**

Run:

```powershell
pytest tests\test_gui_imports.py tests\test_file_transfer_page_helpers.py tests\test_runs_page_helpers.py tests\test_results_page_helpers.py tests\test_settings_page_helpers.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 4**

Run:

```powershell
git add src\jobdesk_app\gui\pages\file_transfer_page.py src\jobdesk_app\gui\pages\runs_page.py src\jobdesk_app\gui\pages\results_page.py src\jobdesk_app\gui\pages\servers_page.py src\jobdesk_app\gui\pages\settings_page.py src\jobdesk_app\gui\pages\projects_page.py tests\test_gui_imports.py
git commit -m "refactor: apply shared gui page styling"
```

---

### Task 5: Add Main Window Smoke Coverage

**Files:**
- Modify: `tests/test_gui_imports.py`

- [ ] **Step 1: Add a smoke test for `MainWindow` construction**

Add this fixture near the top of `tests/test_gui_imports.py`:

```python
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
```

Add this test:

```python
def test_main_window_constructs_with_expected_pages():
    from PySide6.QtWidgets import QApplication
    from jobdesk_app.gui.main_window import MainWindow, main_navigation_labels

    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    try:
        assert window.nav.count() == len(main_navigation_labels("en"))
        assert window.pages.count() == len(main_navigation_labels("en"))
        assert window.nav.item(0).text() == "Projects"
        assert window.pages.currentIndex() == 0
    finally:
        window.shutdown()
        window.close()
```

- [ ] **Step 2: Run the smoke test**

Run:

```powershell
pytest tests\test_gui_imports.py::test_main_window_constructs_with_expected_pages -q
```

Expected: PASS. If Qt cannot initialize in the local environment, keep the import tests and record the failure output before deciding whether to mark the smoke test with a platform-specific skip.

- [ ] **Step 3: Run the full GUI-focused test set**

Run:

```powershell
pytest tests\test_gui_imports.py tests\test_gui_theme.py tests\test_gui_state.py tests\test_gui_settings.py tests\test_i18n.py tests\test_file_transfer_page_helpers.py tests\test_runs_page_helpers.py tests\test_results_page_helpers.py tests\test_settings_page_helpers.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit Task 5**

Run:

```powershell
git add tests\test_gui_imports.py
git commit -m "test: add gui main window smoke coverage"
```

---

### Task 6: Manual Launch Verification

**Files:**
- Modify only if verification exposes a concrete issue in files touched by Tasks 1-5.

- [ ] **Step 1: Start the GUI**

Run:

```powershell
jobdesk-gui
```

Expected: The JobDesk window opens without traceback.

- [ ] **Step 2: Verify visible navigation**

Manual checks:

- Projects is the first nav item.
- Files, Runs, Results, Servers, and Settings are visible.
- Selecting each item switches the stacked page.
- Page titles use the same style where titles are present.
- Tables use alternating rows and no vertical header.

- [ ] **Step 3: Verify language refresh path**

Manual checks:

- Open Settings.
- Change Language to Chinese.
- Save Settings.
- Navigation labels refresh, including Projects.
- Change Language back to English and save.

- [ ] **Step 4: Verify basic workflow surfaces**

Manual checks:

- Projects page opens and its buttons are visible.
- Files page renders local and remote panes.
- Runs page renders the run table and action row.
- Results page renders batch/table/analysis controls.
- Servers page renders table and server action buttons.
- Settings page renders Defaults and Paths groups.

- [ ] **Step 5: Record verification result**

Add a short note to the final implementation summary with:

```text
Manual GUI launch: PASS
Navigation switch check: PASS
Language refresh check: PASS
Workflow surface check: PASS
```

If a check fails, record the exact failure text and fix the smallest related issue before final completion.

- [ ] **Step 6: Run full test suite**

Run:

```powershell
pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit verification fixes if any**

If files changed during manual verification, run:

```powershell
git add src\jobdesk_app\gui\theme.py src\jobdesk_app\gui\main_window.py src\jobdesk_app\gui\i18n.py src\jobdesk_app\gui\pages\file_transfer_page.py src\jobdesk_app\gui\pages\runs_page.py src\jobdesk_app\gui\pages\results_page.py src\jobdesk_app\gui\pages\servers_page.py src\jobdesk_app\gui\pages\settings_page.py src\jobdesk_app\gui\pages\projects_page.py tests\test_gui_theme.py tests\test_gui_imports.py tests\test_i18n.py
git commit -m "fix: polish gui modernization"
```

If no files changed, do not create an empty commit.

---

## Self-Review Notes

- The plan implements the approved design without adding a new production dependency.
- The plan resolves `ProjectsPage` by making it the first visible navigation item.
- The plan replaces private stylesheet assertions with a public theme API.
- The plan keeps business logic and service modules out of scope.
- The Fluent spike remains outside this implementation plan.
