# SQLite Architecture Remediation Design

## Goal

Replace JobDesk's split `run.json` and `manifest.tsv` runtime state with SQLite as the single writable source of truth, then move lifecycle orchestration out of Qt widgets and restore the intended dependency direction.

## Scope

This change addresses the five architecture findings from the 2026-06-27 project review:

1. CLI/GUI cross-process lost updates.
2. Duplicated and silently corrupted run metadata.
3. Run lifecycle orchestration embedded in large GUI pages.
4. `services` depending on Qt and `gui.session`.
5. Weak type and dependency-boundary enforcement around the highest-risk modules.

The public CLI commands, GUI behavior, run identifiers, remote directory layout, and existing scheduler/SSH adapters remain compatible.

## Storage Design

Each JobDesk runs directory contains one `jobdesk.db`. The database uses SQLite WAL mode, foreign keys, a five-second busy timeout, and explicit transactions for every state transition.

### Tables

- `schema_metadata(key, value)` stores the schema version and legacy-import completion marker.
- `runs` stores one row per `RunRecord`, including scheduler resources and the local workspace path.
- `tasks` stores the former `TaskRecord` fields, with `(run_id, task_id)` as the primary key and a foreign key to `runs`.
- `migration_errors` stores legacy directories that could not be imported, including the path and error text. Invalid legacy runs remain visible through repository diagnostics instead of disappearing.

Status summaries are derived from `tasks` with `GROUP BY status`; they are not persisted in `runs`.

### Transaction Boundary

`RunRepository` owns the connection factory, schema initialization, CRUD operations, and task-state transactions. Application code never writes runtime JSON or TSV directly. A write that changes tasks and run metadata commits both together or rolls back both.

SQLite serializes writers across CLI and GUI processes. WAL allows readers to continue while a writer commits. Retry behavior is delegated to SQLite's busy timeout rather than application-level spin loops.

## Legacy Migration

On first repository initialization:

1. Create and version the database in a transaction.
2. Scan run directories containing `run.json` and `manifest.tsv`.
3. Parse each complete legacy run and import its run/task rows atomically.
4. Record malformed or incomplete runs in `migration_errors`; continue importing other runs.
5. Mark the import complete only after the scan transaction commits.

Legacy files are retained unchanged as read-only recovery artifacts. JobDesk no longer updates them after successful import. Reopening the application is idempotent: an imported run is not duplicated, and the completed marker prevents repeated full scans.

## Application Layer

Introduce `RunCoordinator` as the UI/CLI-facing use-case service. It composes `RunRepository`, SSH/SFTP factories, status refresh, scheduler selection, and result download.

Initial operations:

- create one or more runs;
- submit a run;
- refresh and optionally download completed outputs;
- cancel, retry, rerun, and delete;
- list/load runs and migration diagnostics.

Every operation returns a typed result object containing durable state, partial-success information, and user-displayable errors. The coordinator contains no Qt imports.

`RunService` remains as a compatibility facade during this change, delegating persistence and lifecycle operations to the repository/coordinator where practical. Existing callers and tests can migrate incrementally without a flag day.

## GUI Decomposition

The first extraction targets duplicated lifecycle flows rather than mechanically splitting widgets:

- `FileTransferPage` delegates run creation/submission to `RunCoordinator`.
- `RunsResultsPage` delegates monitor refresh, timer refresh, manual refresh, download, retry, cancel, and submit to the same coordinator operations.
- Widgets continue to own interaction state, dialogs, table rendering, button feedback, and worker dispatch.
- Worker functions return typed payloads; all widget mutation stays in UI-thread callbacks.

This removes duplicated business workflows while keeping the visual structure stable.

## Run Monitor Boundary

Split monitoring into:

- a pure Python watcher service that accepts an SSH-client factory and emits `DoneEvent` through a callback;
- a small Qt adapter under `gui` that converts callbacks to a Qt signal.

No module under `services` imports `PySide6` or `jobdesk_app.gui`.

## Types and Architecture Enforcement

Add protocols for SSH clients, SFTP clients, scheduler adapters, and repository operations used by application services. Enable `check_untyped_defs` for the extracted coordinator and monitor modules immediately, then for touched GUI helpers.

Add an architecture test that parses imports and rejects:

- `core -> services/gui`;
- `remote -> services/gui`;
- `services -> gui`;
- PySide6 imports outside `gui`.

## Error Handling and Recovery

- Database initialization and migration errors include the database or legacy path.
- A malformed legacy run is recorded, not silently skipped.
- A transaction failure leaves the prior durable state intact.
- Partial remote success is committed only when the corresponding local state transition is known; error payloads preserve created/submitted records.
- Database corruption is surfaced as a repository error with recovery guidance. Legacy files remain available for manual recovery.

## Testing Strategy

All production changes follow red-green-refactor:

1. Repository schema and CRUD tests.
2. Cross-process concurrent update test using separate Python processes.
3. Legacy migration, idempotency, malformed-record, and rollback tests.
4. Coordinator tests for create/submit/refresh/download partial-success behavior.
5. Pure monitor tests plus Qt adapter tests.
6. Import-boundary tests and expanded mypy coverage.
7. Existing focused GUI tests, then the full test, Ruff, mypy, and package-build gates.

## Rollout and Compatibility

The migration runs automatically without deleting legacy files. There is no dual-write phase. If migration cannot initialize the database, JobDesk fails visibly before mutating legacy data. The implementation remains a single local-user application and does not introduce a daemon or network database.

## Non-goals

- Changing remote job directory formats or shell scripts.
- Redesigning the GUI.
- Adding multi-user or network-database support.
- Replacing Paramiko, scheduler adapters, parsers, or analysis formats.
