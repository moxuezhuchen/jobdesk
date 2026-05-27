# Files Page Transfer Interactions Design

**Date:** 2026-05-27

## Scope

This change addresses three observed Files-page problems:

- The transfer progress bar occupies the full width below the task controls.
- Dragging from the remote pane to the local pane does not reliably start a
  download.
- Dragging local files from outside JobDesk onto the remote pane does not
  reliably start an upload.

The current pending Files-page patch also includes local/remote rename,
local-pane external copy, remote-delete validation, public transfer progress
callbacks, and teardown callback invalidation. These related edits are covered
by their existing tests and must retain the safety constraints below.

## Design

The existing `FileTransferPage` transfer methods remain the behavior boundary.
The page routes drag payloads according to the destination table:

- A remote-path payload dropped on the local table invokes the drop-download
  path and writes into the current local directory.
- Local `file://` URLs dropped on the remote table invoke the drop-upload path
  and write into the current remote directory.
- Ordinary upload and download drops use the existing non-destructive
  `skip_same_size` policy. This work does not add destructive external drag
  behavior or silent overwrite-on-drop.
- Only local `file://` URL payloads are accepted; web URLs are rejected during
  drag acceptance instead of appearing to succeed with no transfer.

The progress bar remains the single transfer-status control, but it moves into
the task action row after `Create tasks only`. It has a compact bounded width
so progress remains visible without dominating the full bottom panel.

## Error Handling

Drag operations continue to use the existing upload/download services and
worker callbacks, so connection errors and transfer failures follow the
existing status/error paths. No source file is removed as part of the external
local drag-to-upload behavior.

Remote deletion continues to require configured `allowed_delete_roots` in
`FileTransferService`. The currently displayed remote directory is not an
authorization root; the GUI may filter obviously invalid selections before
calling the service, but it must not relax service authorization.

Tests construct the Files page with a temporary `GuiSettingsStore`, so teardown
does not persist state into the user's application-data directory.

## Tests

GUI tests cover:

- The progress bar is in the same horizontal row as `Create tasks only` and
  uses a bounded width.
- The remote table accepts external local URL payloads and routes them to the
  upload handler.
- The local table accepts remote-path payloads and routes them to the download
  handler.
- Non-local URL payloads are rejected for both panes.
- Ordinary drag upload retains the non-destructive policy.
- Remote deletion cannot gain permission from the currently viewed directory.
- Files-page teardown persists only to an isolated test settings location.
- Existing transfer service calls continue to target the currently displayed
  local or remote directory.
