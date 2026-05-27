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

## Issue 5: Configurable Text Editor for File Opening

**Observation:** Files-page file opening is inconsistent with a WinSCP-like
text-file workflow. Local file double-click currently delegates to the
operating system association, and remote file double-click downloads a
temporary copy before delegating to that association. Users cannot choose the
editor that should be used for routine file inspection.

**Requested behavior:** Add an application setting for a text editor
executable. By default, opening any ordinary file from either Files-page pane
uses the configured text editor, rather than the operating system file
association.

**Acceptance notes:**

- Settings exposes a persistent text-editor executable selection or path.
- Double-click/open actions for local files launch the configured editor with
  the selected local file path.
- Double-click/open actions for remote files continue to download a temporary
  local copy first, then launch that copy in the configured editor.
- Existing explicit `Open in Viewer` actions remain available for molecular
  visualization tools and are not replaced by the text-editor default.
- Directories retain navigation behavior and are not passed to the editor.
- The initial behavior covers opening and inspection only; editing a temporary
  remote copy does not automatically upload changes back to the server.
- Add GUI settings and Files-page regression coverage for configured-editor
  persistence and local/remote file launch routing.

## Issue 6: Remote Files Refresh Performance on `814n`

**Observation:** Refreshing or browsing the remote Files pane against the
reported `814n` server is too slow compared with WinSCP. The current refresh
path schedules a remote listing through `FileTransferService.list_remote()`
and `sftp.list_dir_info()`, but the user-visible experience does not meet an
interactive file-browser expectation.

**Requested behavior:** Optimize remote directory refresh and navigation speed
so routine browsing on `814n` feels comparable to a dedicated SFTP browser
such as WinSCP.

**Acceptance notes:**

- Confirm the configured server identity before measurement, since prior test
  records refer to `814new` while this report names `814n`.
- Establish measured timings for first connection, repeated refresh of the
  same remote directory, and navigation into an adjacent directory on the
  reported server.
- Instrument or otherwise isolate whether latency comes from SSH/SFTP
  connection establishment, directory listing, per-entry metadata retrieval,
  table rendering, or unnecessary repeated refresh operations.
- Reuse an established live connection for routine refresh where valid and
  avoid redundant network work during ordinary browsing.
- Keep the Files page responsive while listing is in progress and prevent stale
  slower results from overwriting a newer requested directory view.
- Preserve directory metadata and existing transfer/delete safety behavior;
  performance work must not bypass path guards or silently remove useful file
  information.
- Add deterministic tests for refresh request coordination and any cache or
  connection-reuse logic, plus a live performance check against the confirmed
  server configuration.

## Scope

This record captures the requested follow-up only. No Files-page behavior is
changed by this document.
