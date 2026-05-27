# Files Page Follow-Up Issues

**Recorded:** 2026-05-26

**Context:** Issues observed in the Files page while browsing remote calculation
outputs and using the task-submission controls.

## Issue 1: Transfer Progress Bar Layout

**Observation:** The download progress bar currently stretches across the
entire bottom width of the page below the task action row. In the observed
state it displays `Download: 0%` as a visually dominant full-width control.

**Requested behavior:** Make the transfer progress indicator shorter and place
it in the unused space to the right of the `Create tasks only` action, within
the same action area.

**Acceptance notes:**

- The progress indicator no longer consumes the full bottom width of the Files
  page.
- Task action controls remain readable and usable when progress is visible.
- Upload and download progress/status remain visible without shifting the file
  panes unnecessarily.

## Issue 2: Drag-to-Download Is Not Working

**Observation:** Dragging selected remote output files from the remote pane to
the local pane does not initiate a download.

**Requested behavior:** Support dragging a selected remote file or directory
onto the local pane to download it to the displayed local destination.

**Acceptance notes:**

- Dragging remote-to-local starts the same guarded download workflow as an
  explicit download action.
- Default drag behavior is non-destructive copy/download.
- Ordinary drag download retains the non-destructive `skip_same_size` policy.
- If move semantics remain supported through `Shift`-drag, remote deletion is
  attempted only after a successful download and continues to use existing
  remote deletion safety guards.
- Add GUI regression coverage for remote-to-local drop handling and for any
  progress-layout change.

## Issue 3: External Local Drag-to-Upload Is Not Working

**Observation:** Dragging a local file or directory from a different local
folder, such as from the system file manager, onto the Files page remote pane
does not upload it to the displayed remote destination.

**Requested behavior:** Support dragging external local files or directories
onto the remote pane to upload them to the currently displayed remote
directory.

**Acceptance notes:**

- The target is the currently displayed remote directory.
- Default external drag behavior uploads/copies the source and does not delete
  the local original.
- Ordinary external drag upload retains the non-destructive `skip_same_size`
  policy rather than silently overwriting an existing remote destination.
- Existing remote-to-local drag/download handling remains available.
- Add GUI regression coverage for external URL/file drops onto the remote
  pane, separately from remote-to-local download drops.

## Issue 4: File and Directory Rename Is Incomplete

**Observation:** The Files page does not provide a local-pane rename action.
The current source exposes a remote-pane `Rename` context-menu action through
`_rename_remote()`, but that path still needs user-visible verification because
rename was reported as unavailable in the workflow.

**Requested behavior:** Support renaming selected files and directories from
both local and remote panes through the Files page.

**Acceptance notes:**

- The local pane exposes rename for a selected file or directory and refreshes
  the listing after a successful rename.
- The remote pane rename action is visible and successfully renames a selected
  remote file or directory through `FileTransferService.rename_remote()`.
- Parent navigation rows cannot be renamed.
- Invalid, empty, or path-separator-containing new names are rejected before
  any filesystem or SFTP operation.
- Add GUI regression coverage for local rename, remote rename, and invalid
  rename rejection.

## Scope

This record captures the requested follow-up only. No Files-page behavior is
changed by this document.
