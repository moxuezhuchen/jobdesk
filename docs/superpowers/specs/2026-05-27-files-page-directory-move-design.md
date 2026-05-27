# Files Page Directory Move Design

**Date:** 2026-05-27

## Scope

This change adds same-pane drag-to-directory move behavior in the Files page:

- Dragging selected local file or directory rows onto a local directory row
  moves each selected path into that directory.
- Dragging selected remote file or directory rows onto a remote directory row
  moves each selected path into that directory.

Existing cross-pane drops remain transfer operations:

- Local to remote remains upload/copy behavior.
- Remote to local remains download/copy behavior.

The separately recorded configurable text editor and remote refresh
performance work are not part of this change.

## Interaction Design

`_FileTable` continues to own drag payload decoding and now also resolves the
row under the drop position. When a same-pane drag payload lands on a real
directory row, it emits a move request containing the source paths and target
directory path. Dropping on blank table space or onto the other pane keeps
the existing copy/upload/download routing.

The parent navigation row (`..`) is navigation only and is not a move target.
Ordinary same-pane drops onto non-directory rows do not perform a move.

## Local Move Behavior

`FileTransferPage` handles a local move request synchronously with local
filesystem rename/move operations:

- The destination for each source is `target_directory / source.name`.
- Existing destinations are rejected; drag move never overwrites.
- A source cannot be dropped onto itself.
- A directory cannot be moved into itself or one of its descendants.
- Successful moves refresh the local listing and report status.
- Validation or filesystem failures are reported without replacing existing
  destination content.

## Remote Move Behavior

`FileTransferPage` handles a remote move request through the existing
`FileTransferService.rename_remote(old_path, new_path)` boundary:

- The destination for each source is `remote_child_path(target_dir, basename)`.
- A source cannot be dropped onto itself.
- A remote directory cannot be moved into itself or one of its descendants.
- `..` cannot be used as a drop target.
- Successful moves refresh the remote listing and report status.
- Service failures surface through the existing Files-page error callback.

This change does not add remote overwrite behavior. The service/server is
allowed to reject an existing remote destination, and the page reports that
failure.

## Safety And Compatibility

Directory moves do not reuse transfer completion or delete-after-copy
semantics. A same-pane move is one rename/move operation at the source
filesystem boundary. Cross-pane upload and download retain the existing
non-destructive `skip_same_size` behavior and do not implicitly delete source
paths.

## Tests

GUI regression tests cover:

- A same-pane local drag onto a directory row emits and performs a local move.
- A same-pane remote drag onto a directory row emits and performs a
  `rename_remote()` move.
- Local and remote directory sources cannot be moved into descendants.
- Existing local destinations are not overwritten.
- The parent navigation row is not accepted as a directory move target.
- Existing local-to-remote upload and remote-to-local download drag tests
  remain green.

