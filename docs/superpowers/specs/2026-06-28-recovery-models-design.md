# JobDesk Recovery Models Design

## Goal

Replace ad-hoc failure compensation with three explicit models: a recoverable submission state machine, a durable cross-resource operation journal, and lease-based ownership of shared SSH/SFTP sessions.

The design applies to the SQLite architecture remediation already in progress. It does not redesign the GUI, remote directory format, scheduler adapters, or public run identifiers.

## Design Choice

Three approaches were considered:

1. Continue adding local compensation branches. This has the smallest immediate diff but leaves failure semantics distributed across services and widgets.
2. Add an explicit state machine, a general `operations` journal, and a framework-neutral `SessionPool`. This addresses the current failure classes without introducing a daemon and is the selected approach.
3. Adopt full event sourcing or a background service. This offers stronger replay semantics but is disproportionate for a local single-user desktop application.

## Submission State Machine

### States

The task lifecycle adds `uncertain`:

```text
uploaded -> submitting -> submitted -> running -> remote_completed
                         \-> uncertain
```

`submitting` means JobDesk owns an active local claim and has not completed the remote call. `submitted` means remote acceptance is confirmed. `uncertain` means a remote side effect may have occurred but JobDesk cannot prove success or failure.

Terminal and downstream states remain `downloaded`, `analyzed`, `failed`, and `cancelled`.

### Automatic Transitions

- A successful scheduler response with a job ID moves `submitting` to `submitted` in the same SQLite transaction that advances the submit operation phase.
- A successful nohup response with a PID moves all claimed tasks to `submitted`.
- A lost response, empty PID, or scheduler exception after the submit command begins moves affected tasks to `uncertain`.
- An authoritative remote marker immediately moves either `submitting` or `uncertain` to `running`, `remote_completed`, or `failed`; marker handling never waits for a stale timeout.
- Absence of a marker never converts `uncertain` to `failed` automatically.
- Ordinary retry, rerun, and submit operations reject `uncertain` tasks.

### Manual Recovery

Two explicit operations resolve `uncertain` tasks:

- `confirm-submitted`: the user confirms the remote task exists. The task moves to `submitted`; an optional remote job ID may be supplied when known.
- `abandon-submit`: the user confirms the remote task does not exist. Execution metadata is cleared and the task returns to `uploaded`.

Both operations require selected task IDs and use compare-and-swap against `uncertain`. They are available through `RunCoordinator`, CLI commands, and GUI actions. The GUI labels the state as `Submission Unknown` in English and `提交结果未知` in Chinese.

## Durable Operation Journal

### Schema

Schema version 2 adds an `operations` table:

```text
operation_id TEXT PRIMARY KEY
run_id TEXT NOT NULL
kind TEXT NOT NULL
phase TEXT NOT NULL
payload_json TEXT NOT NULL
last_error TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
completed_at TEXT
```

`run_id` is indexed but is not a foreign key. Delete operations must survive removal of the corresponding run row so recovery can continue.

Completed operations are retained for seven days for diagnostics and then removed during repository initialization. Incomplete operations are never removed automatically.

### Submit Phases

```text
claimed -> remote_started -> confirmed | uncertain -> completed
```

1. `claimed`: SQLite atomically claims uploaded tasks and creates the operation with task IDs and the intended scheduler settings.
2. `remote_started`: written immediately before invoking the remote submit command.
3. `confirmed`: stores returned job IDs and commits the corresponding task states.
4. `uncertain`: records the error or missing response and commits affected tasks to `uncertain`.
5. `completed`: no further recovery work remains.

On startup, an incomplete submit operation at `claimed` is safe to roll back to `uploaded` because no remote command began. An operation at `remote_started` becomes `uncertain`. `confirmed` is completed after verifying the task rows already contain its durable job IDs.

### Delete Phases

```text
prepared -> metadata_deleted -> files_deleted -> completed
```

The `prepared` payload contains the run metadata, task payloads, and exact validated filesystem paths required for recovery.

1. `prepared`: create the operation before any deletion.
2. `metadata_deleted`: delete the run and tasks in the same SQLite transaction that advances the operation.
3. `files_deleted`: remove the validated run and result paths idempotently.
4. `completed`: record successful cleanup.

Startup recovery resumes `metadata_deleted` operations by deleting remaining files. A `prepared` operation is safe to resume from metadata deletion. Missing files count as already deleted. Filesystem errors retain the operation and `last_error` for the next startup or explicit retry.

The journal is the recovery authority; deletion no longer recreates deleted run rows as an exception-time compensation.

### Repository API

`RunRepository` owns operation creation, phase transitions, recovery queries, and cleanup. Every phase transition uses an expected current phase so concurrent GUI and CLI processes cannot both advance the same operation.

Task merge methods return accepted task IDs alongside durable rows. Application services use those IDs to filter snapshots, failures, and changed counts before returning results.

## Session Ownership Model

### SessionPool

Add a framework-neutral `SessionPool` under `services`. The GUI owns one pool for its lifetime. The pool receives SSH and SFTP factories and stores one entry per server.

Each entry contains:

- the SSH/SFTP clients;
- a per-server mutex;
- an active lease count;
- a closing flag;
- connection health state.

### Lease Protocol

Operations use a context-managed lease:

```python
with session_pool.lease(server_id, server_config) as session:
    coordinator.refresh_with_session(run_id, session.ssh, session.sftp)
```

- The first lease creates and connects the session.
- Leases for the same server serialize remote client use.
- Different servers can operate concurrently.
- A dead connection is replaced before the lease is yielded.
- Releasing the final lease closes the session when the pool is closing.

### Shutdown

`SessionPool.close()` is non-blocking:

1. Atomically mark the pool and all entries as closing.
2. Reject new leases.
3. Immediately close entries with no active lease.
4. Let active leases close their entries when released.

The GUI does not acquire a network-operation lock during shutdown and does not close a client owned by an active worker. Worker callbacks remain suppressed after widget shutdown by the existing worker ownership checks.

CLI coordinators continue using operation-scoped sessions and do not require a pool.

## Coordinator and User Interfaces

`RunCoordinator` remains the application boundary and gains:

- `confirm_submitted(run_id, task_ids, remote_job_ids=None)`;
- `abandon_submit(run_id, task_ids)`;
- `recover_operations()` for startup and explicit diagnostics;
- methods accepting an acquired session for GUI pool integration.

CLI additions:

```text
jobdesk run confirm-submitted <workspace> <run_id> --tasks ... [--job-id task=id]
jobdesk run abandon-submit <workspace> <run_id> --tasks ...
jobdesk run recover <workspace>
```

GUI run details expose recovery actions only when selected tasks are `uncertain`. Destructive `abandon-submit` requires confirmation explaining that retry is safe only after verifying no remote job exists.

## Durable Refresh Results

Status refresh first computes candidate transitions, then merges them with compare-and-swap. The repository returns accepted task IDs. Before returning to callers, `RunService` filters:

- snapshots;
- failure records;
- warnings tied to rejected task transitions;
- `changed_count`.

Batch-level transport warnings remain even when no task transition is accepted. Returned task-level results therefore describe only state persisted in SQLite.

## Migration and Compatibility

- Schema version 1 upgrades transactionally to version 2 by creating `operations` and updating `schema_metadata`; the schema migration performs no network access and does not change task states.
- At application recovery, a legacy `submitting` row with no matching operation becomes `uncertain` atomically and a synthesized completed submit operation records that decision. A later status refresh applies any authoritative remote marker immediately.
- Existing `submitted` semantics do not change.
- Legacy JSON/TSV migration remains read-only and independent of the operation journal.
- New enum values are accepted by SQLite payload validation and displayed by GUI and CLI summaries.

## Failure Matrix

Tests inject failure at every phase boundary:

### Submission

- crash after claim but before remote command;
- crash after `remote_started` but before response;
- empty PID or missing scheduler job ID;
- successful remote response followed by SQLite failure;
- immediate running/completed/failed marker for `submitting` and `uncertain`;
- both manual recovery actions and stale compare-and-swap rejection.

### Deletion

- crash at each delete phase;
- missing files during replay;
- locked files followed by successful restart recovery;
- concurrent recovery attempts from separate repository instances;
- completed-operation retention cleanup.

### Sessions

- same-server operations serialize;
- different-server operations overlap;
- shutdown with zero, one, and multiple active leases;
- session creation failure;
- dead connection replacement;
- lease release after widget destruction.

### Returned Results

- CAS accepts all, some, or none of the proposed transitions;
- rejected transitions do not appear in snapshots or failures;
- batch warnings remain visible.

All changes follow red-green-refactor and finish with the complete pytest suite, Ruff, mypy, architecture checks, and `git diff --check`.

## Non-goals

- A background daemon or network database.
- Full event sourcing of every task field change.
- Automatic retry of `uncertain` submissions.
- Automatic scheduler discovery by name, comment, or command history.
- GUI visual redesign beyond recovery controls and status labels.
