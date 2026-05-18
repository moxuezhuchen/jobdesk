# JobDesk GUI Modernization Design

## Goal

Modernize the JobDesk PySide6 GUI into a consistent, maintainable Windows desktop workbench without risking the existing scientific-computing workflow. The first implementation phase should improve structure, styling, layout, and test coverage while keeping the current business logic and service layer intact.

## Decision

Use a conservative modernization path as the main line:

- Keep native PySide6 widgets for the first phase.
- Extract GUI styling out of `main_window.py` into a focused theme module.
- Standardize navigation, page spacing, typography, buttons, tables, forms, and state messages.
- Clarify how `ProjectsPage` fits into the visible workflow.
- Treat `qfluentwidgets` as a separate spike, not as a required dependency for phase one.

This avoids a high-risk dependency migration while still making the UI feel less like a default Qt utility and more like a polished local workstation app.

## Current Context

The current GUI is organized around `src/jobdesk_app/gui/main_window.py`, which owns the main window structure and a large `_APP_STYLESHEET` string. The main window currently presents a left navigation list and a stacked page area for Files, Runs, Results, Servers, and Settings.

The page modules already separate most behavior by workflow:

- `src/jobdesk_app/gui/pages/file_transfer_page.py`
- `src/jobdesk_app/gui/pages/runs_page.py`
- `src/jobdesk_app/gui/pages/results_page.py`
- `src/jobdesk_app/gui/pages/servers_page.py`
- `src/jobdesk_app/gui/pages/settings_page.py`
- `src/jobdesk_app/gui/pages/projects_page.py`

There are existing tests for GUI imports, state helpers, settings helpers, file-transfer helpers, i18n, run helpers, and result helpers. Some existing tests assert specific stylesheet text in `main_window.py`, which will need to be replaced with less brittle theme API checks.

## Non-Goals

This redesign does not rewrite the core JobDesk workflow, CLI, remote SSH/SFTP layer, run service, project service, or batch lifecycle.

This redesign does not require a new frontend framework.

This redesign does not make `qfluentwidgets` a production dependency until a separate spike proves that it works well with the current Python, PySide6, packaging, and Chinese text requirements.

This redesign does not attempt a complete visual rebuild of every dialog in one pass. Dialog cleanup should happen only when it supports the main workbench consistency.

## User Experience Direction

The GUI should feel like a practical Windows 11 scientific workbench:

- Light theme by default for long working sessions.
- Dense but readable layouts suitable for tables and repeated operations.
- Clear workflow navigation instead of decorative landing-page treatment.
- Restrained accent color, currently aligned with the existing blue selection color.
- Consistent spacing, typography, and control heights.
- Cleaner table headers, selected rows, scrollbars, disabled states, hover states, and form fields.
- Status and error messages that are visible in context and mirrored to logs where appropriate.

Avoid a heavy dark "cyber" look for the primary interface. It can look impressive, but JobDesk is a productivity tool where readability and scanning matter more.

## Architecture

### Theme Module

Create `src/jobdesk_app/gui/theme.py` as the owner of visual tokens and QSS generation.

The module should expose stable helpers such as:

- `APP_FONT_FAMILIES`
- `ThemeColors`
- `ThemeMetrics`
- `build_app_stylesheet()`
- `apply_app_theme(app_or_widget)`

The first implementation can keep these simple. The important boundary is that `main_window.py` should no longer contain the full stylesheet.

### Main Window

Keep `MainWindow` responsible for:

- Window title and size.
- Shared `AppState`.
- File logging setup.
- Navigation labels and language refresh.
- Page creation and page activation.
- Error dialog and status/log callbacks.
- Shutdown coordination.

Move stylesheet construction into `theme.py`.

Keep the existing left-navigation plus stacked-page model for phase one. It is simple, understandable, and already maps to the current pages. Improve its spacing and visual polish before considering a more invasive navigation component.

### Page Layout Helpers

Introduce small layout/style helpers only where they remove real duplication. Good candidates are:

- A helper to create a page title label.
- A helper to create standard horizontal action rows.
- A helper to apply table defaults such as alternating rows, selection behavior, and resize behavior.

Avoid a large custom widget framework. The codebase is still small enough that a few focused helpers are better than a private UI toolkit.

### Project Workflow Decision

Resolve the current `ProjectsPage` ambiguity in phase one. It exists as a page module but is not currently part of the main navigation shown in `MainWindow`.

The preferred decision is to add Projects as the first navigation item if the project lifecycle remains a first-class workflow. This gives users a clear entry point for opening or creating project context before transferring files or managing runs.

If the page is not ready to be visible, document it as internal or legacy and leave it outside the nav intentionally. The implementation plan should not leave the ambiguity unresolved.

## Implementation Phases

### Phase 1: Theme Extraction and Test Baseline

Extract `_APP_STYLESHEET` into `theme.py`, update `MainWindow` to call `build_app_stylesheet()`, and update tests to assert the theme API instead of hard-coded private constants.

This phase should produce no user-visible workflow change. It creates the foundation for safer visual iteration.

### Phase 2: Workbench Layout Polish

Modernize the main window and shared control styling:

- Navigation width, padding, selected item styling, hover styling, and focus styling.
- Page background and content spacing.
- Standard button height and states.
- Table header, grid, row selection, alternate rows, and scrollbars.
- Input fields, combo boxes, spin boxes, and text areas.
- Disabled and read-only states.

This phase should make the app visibly cleaner without changing the underlying page behavior.

### Phase 3: Page Consistency Pass

Apply consistent page structure to Files, Runs, Results, Servers, and Settings:

- Title area.
- Primary action row.
- Secondary controls.
- Main table or content area.
- Empty state or no-selection status where useful.

Keep edits local to each page. Do not change service behavior while improving layout.

### Phase 4: Projects Navigation Decision

Either add Projects to the main navigation as the first page or explicitly document why it remains hidden. If added, make sure language refresh, page activation, and dependent page refresh behavior remain correct.

### Phase 5: Fluent Spike

Create a separate experimental branch or plan to test `PySide6-Fluent-Widgets` / `qfluentwidgets` with this project.

The spike should verify:

- Installation with the current Python version.
- Compatibility with installed PySide6.
- App startup.
- Chinese text rendering.
- Navigation replacement feasibility.
- Packaging impact.
- Whether visual gains justify the dependency.

The spike should not be required for phase-one completion.

## Testing Strategy

Update and expand tests around stable behavior:

- GUI modules import successfully.
- `build_app_stylesheet()` returns expected selectors or token-derived colors.
- `MainWindow` still exposes expected policy helpers such as `main_window_has_status_bar()` and `main_window_shows_log_panel()`.
- Page helper functions still format statuses, selections, runs, and settings text correctly.
- i18n remains correct for English and Chinese labels.

Avoid tests that depend on exact full QSS strings in `main_window.py`. Tests should assert meaningful theme properties and behavior rather than implementation placement.

Manual verification should include launching `jobdesk-gui` and checking:

- App starts without traceback.
- Navigation switches all visible pages.
- Tables render with readable headers and selected rows.
- Buttons and inputs have consistent sizing.
- Chinese and English labels display correctly.
- Existing workflows still reach their previous actions.

## Risks and Mitigations

### Risk: Visual polish breaks tests tied to private style strings

Mitigation: Replace those assertions with tests against `theme.py` public helpers.

### Risk: Page styling becomes inconsistent again

Mitigation: Centralize shared style in `theme.py` and use small helpers for repeated page patterns.

### Risk: `ProjectsPage` changes workflow expectations

Mitigation: Make the Projects decision explicit and test navigation labels/order. If visible, ensure opening a project still refreshes Runs and Results as before.

### Risk: Fluent dependency causes packaging or compatibility issues

Mitigation: Keep Fluent as a spike until installation, startup, text rendering, and packaging are proven locally.

### Risk: Chinese text or source encoding remains fragile

Mitigation: Include i18n tests in the verification path and inspect any mojibake in source files or test output before broader UI work.

## Acceptance Criteria

The phase-one modernization is complete when:

- `main_window.py` no longer owns the full application stylesheet.
- The GUI has a dedicated theme module with tests.
- Existing GUI import/helper tests pass after updates.
- The visible workbench has consistent navigation, buttons, inputs, and table styling.
- The status of `ProjectsPage` is explicitly resolved.
- `jobdesk-gui` starts and each visible page can be opened manually.
- No new production dependency is required for the completed phase.
- Fluent remains documented as an optional follow-up spike.
