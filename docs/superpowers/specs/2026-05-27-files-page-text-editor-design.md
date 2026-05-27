# Files Page Text Editor Design

**Date:** 2026-05-27

## Scope

The Files page uses a configurable text editor as the default opener for
ordinary files in both local and remote panes. This does not replace the
existing explicit `Open in Viewer` actions for chemistry visualization, and
does not add remote edit/save-back behavior.

## Settings

`GuiSettings` gains a persistent `text_editor_path` string. The default on
Windows is `notepad.exe`, so a newly configured application has a predictable
plain-text opening path without relying on file associations.

The Settings page adds a Text Editor setting card with:

- A path/command input displaying the saved value.
- A browse button for selecting an executable.
- Save and discard behavior through the existing `GuiSettingsStore` workflow.

The setting is translated consistently with the current Settings page labels.

## File Opening

Files-page directory double-click behavior is unchanged and continues to
navigate directories.

For ordinary local files, double-click/Enter launches the configured editor
with the local path as one argument.

For ordinary remote files, the existing download-to-temporary-file workflow
remains in place. After download succeeds, the page launches the configured
editor with the temporary local copy as one argument.

The launcher uses an argument list rather than a shell command so paths with
spaces are handled safely. If the editor cannot be started, the page reports
an error instead of silently falling back to the operating-system association.

## Non-Goals

- Automatic upload of a modified temporary remote file.
- File-extension-specific editor selection.
- Replacement or removal of molecular viewer actions.

## Tests

Regression tests cover:

- `GuiSettingsStore` defaults and round-trip persistence of
  `text_editor_path`.
- Settings-page load/save behavior for the editor field.
- Local ordinary-file opening launches the configured editor.
- Remote ordinary-file opening launches the configured editor after
  downloading the temporary copy.
- Directory navigation and explicit viewer behavior are not routed through the
  text editor.
