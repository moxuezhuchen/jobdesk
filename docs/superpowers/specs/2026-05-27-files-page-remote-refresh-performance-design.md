# Files Page Remote Refresh Performance Design

**Date:** 2026-05-27

## Scope

This change improves interactive remote-directory browsing on the Files page
by avoiding repeated SSH/SFTP connection establishment during routine remote
operations. It preserves existing transfer and remote-path safety behavior.

The server reported by the user is `814n`; prior repository verification
records refer to `814new`. Live performance verification must first identify
which configured server entry represents the reported target.

## Session Ownership

`FileTransferService` gains an opt-in persistent-session mode. Its default
remains one SFTP context per operation so existing CLI and independent call
sites retain their current resource lifecycle.

When persistent mode is enabled, the service:

- Lazily creates one SFTP session from its existing factory on first use.
- Reuses that session for subsequent service operations.
- Serializes access to the persistent session because Files-page operations
  execute from background workers and SFTP clients must not be used
  concurrently.
- Provides `close()` to release the session when the page switches servers or
  shuts down.
- Discards a failed persistent session so a later operation can establish a
  new connection.

The Files page enables persistent mode for its remote service. Other pages and
the CLI remain unchanged unless separately migrated later.

## Refresh Coordination

The existing background remote listing and request-id checks remain the UI
coordination mechanism:

- Listing stays off the GUI thread.
- A later directory request supersedes earlier slower results.
- Switching server or shutting down invalidates pending results and closes the
  service session after active work has been stopped.

The optimization does not add speculative directory caching. A refresh still
requests current directory contents from the server; it avoids redundant
session establishment rather than displaying stale cached data.

## Failure Handling

If the persistent SFTP session fails during a service operation, that
operation reports its error through the existing page callback path and the
cached session is closed and cleared. A subsequent user operation may reconnect
through the existing factory.

Remote-path normalization, guarded deletion, non-overwrite move protection,
and transfer policies remain unchanged.

## Validation

Deterministic tests cover:

- Default one-shot mode retains current close-after-operation behavior.
- Persistent mode creates one session for sequential remote listings.
- `close()` releases a cached persistent session.
- An operation failure clears the cached session and a later request creates a
  replacement.
- Files-page service setup enables persistent mode and shutdown/switching
  closes it.
- Existing request-id behavior prevents stale results from replacing a newer
  view.

Live validation records elapsed times for:

- Initial connection and first remote directory listing.
- A repeated refresh of the same directory.
- Navigation into a neighboring or child directory.

The before/after comparison is run against the confirmed configured server
identity for the reported `814n` environment.
