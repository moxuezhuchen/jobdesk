# Review Findings Remediation Design

## Scope

Resolve every confirmed finding from the 2026-07-01 full change review:

- prevent workspace-scoped delete recovery from bypassing schema-v3 bindings;
- prevent startup recovery from taking over a live submit operation;
- bring Runs-page submit and cancel workers under guarded lifecycle ownership;
- parse monitor event streams correctly across arbitrary SSH `recv()` boundaries;
- keep legacy import retries out of ordinary GUI startup recovery;
- align schema documentation with the resulting version 4 migration chain; and
- keep required new files while excluding test artifacts from delivery.

## Submit ownership leases

Schema version 4 adds nullable `owner_id` and `lease_expires_at` columns to the
operation journal. New submit claims receive a process-unique owner ID and an
expiry timestamp in the same transaction that changes tasks to `submitting`.
The owning `RunService.submit_run` renews the lease at durable phase boundaries
and runs a lightweight heartbeat while potentially slow remote work is active.
Owner-scoped completion, release, and exception cleanup require the matching
owner ID. If the heartbeat loses ownership, no further remote task is started;
already-started work follows the existing uncertain-state semantics.

Startup recovery may process an incomplete submit operation only when it has no
owner (legacy rows) or its lease is expired. Recovery acquires the expired
operation transactionally before changing task state, so two recovery processes
cannot both take ownership. A live owner therefore cannot be rolled back or
marked `uncertain` by another GUI or CLI process. Lease duration is conservative,
and renewal failures abort before starting new remote work.

Migration from schema v3 adds the two columns without assigning owners to old
rows. Those rows remain recoverable because they represent work created before
lease ownership existed.

## Delete recovery authorization

Every delete recovery path, including `recover_delete_operations()` for one
workspace, validates all three independent facts before filesystem mutation:

1. the current workspace is present in `workspace_roots`;
2. `delete_operation_workspaces` binds the operation to that workspace; and
3. the payload paths and run snapshot agree with that bound workspace.

The validation is centralized and shared by scoped and global recovery. Missing
or mismatched bindings leave the operation incomplete and produce a diagnostic;
they never fall back to trusting operation payloads.

## GUI worker lifecycle

Runs-page submit and cancel operations use the existing guarded worker helper and
`_bg_workers` registry. Result and error callbacks are suppressed after page
shutdown. A shared remote-mutation busy gate prevents submit and cancel from
running concurrently for the same page interaction. The gate is released on
success, error, and worker completion, including shutdown paths.

## Monitor stream framing

The SSH watcher maintains a text buffer across `recv()` calls. It emits only
newline-terminated records and retains the trailing partial line for the next
chunk. Incremental UTF-8 decoding preserves multibyte characters split across
chunks. On reconnect, an unterminated tail is discarded because it is not a
complete durable event; `tail -n 0` continues to avoid replaying old events.

## Legacy migration behavior

GUI startup recovery handles submit/delete journals and legacy orphan
`submitting` rows, but does not call `retry_legacy_imports()`. Retrying failed
JSON/TSV imports remains available through the explicit CLI recovery/diagnostic
path. This preserves the repository ready fast path and prevents routine startup
from taking the legacy-import write lock or scanning every run directory.

## Documentation and delivery hygiene

The changelog and troubleshooting guide describe schema v4 as the current
version, with v2 introducing journals, v3 introducing trusted workspace
bindings, and v4 introducing submit ownership leases. Required untracked source,
test, design, and plan files remain part of the intended change set. Generated
test directories are excluded and removed separately without changing product
behavior.

## Testing

Each behavior is implemented test-first:

- forged scoped delete recovery is rejected before touching another workspace;
- live leases cannot be recovered, expired leases can, and recovery acquisition
  is compare-and-swap safe;
- owner-mismatched submit completion and cleanup are rejected;
- overlapping submit/cancel attempts do not orphan workers or invoke callbacks
  after shutdown;
- event lines and UTF-8 characters split across receive chunks are reconstructed;
- GUI startup does not retry legacy imports while explicit CLI recovery does;
- schema v3-to-v4 migration is atomic and idempotent; and
- user documentation names the correct current schema.

Targeted tests run after each red/green cycle. Completion requires the full test
suite, Ruff, mypy, and `git diff --check` to pass.
