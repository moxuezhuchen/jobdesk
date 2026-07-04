# Submit Recovery Ownership and GUI Read Path Design

## Goal

Fix four confirmed defects without adding a new database schema: live submit operations must never be mistaken for crash leftovers, submit checkpoint failures must propagate, and routine GUI reads must not acquire SQLite write locks.

## Architecture

A submission owns the exact operation IDs returned by `claim_submit_tasks`. Recovery caused by that submission is performed inside `RunService.submit_run`, where those IDs are available; the coordinator no longer performs broad `run_id` recovery. Startup recovery remains an explicit global operation, but the runs page invokes it only once per page lifetime before normal activation refreshes.

`JobSubmitter` treats task checkpoint callbacks as persistence boundaries. Exceptions from these callbacks propagate to `RunService`; they are not converted into ordinary `SubmitResult.errors`.

`RunRepository` uses a read-only fast path when schema v3 and legacy import are already complete. Repeated `RunService` construction for GUI reads therefore avoids `BEGIN IMMEDIATE`, DDL, and migration scanning. The full initialization transaction remains for new, old, or incomplete databases.

## Error handling

On submission failure, `RunService` releases pre-remote claims and recovers only its own still-incomplete operation IDs. A `remote_started` operation becomes `uncertain`. Recovery failures are attached to the raised exception without replacing the original failure.

## Testing

Regression tests cover exact-ID recovery, concurrent same-run isolation, callback exception propagation, one-time GUI activation recovery, and the repository read-only initialization path. Existing targeted and full test suites remain required.

## Follow-up hardening

All post-claim work belongs to one protected submission scope. Recovery and claim release are best-effort cleanup steps whose failures are accumulated and attached to the original exception; a cleanup exception must never replace the primary failure. A false recovery result is rechecked against the incomplete operation set and reported when the owned operation remains unfinished.

Scheduler submission results are finalized from every task outcome already accepted by the persistence callback, including when a later task fails during upload or script preparation.

Repository readiness requires schema v3, required tables, completed initial legacy scan, and WAL mode. Schema v3 stores trusted workspace roots and each new delete operation's workspace binding independently from operation payloads; global deletion recovery accepts only a registered root matching that binding. Migrated v2 delete operations remain unbound for manual reconciliation. Migration failures remain queryable but do not force every normal repository open through write initialization. Retrying legacy imports is an explicit method invoked by diagnostics rather than ordinary GUI reads.

Application startup owns recovery. MainWindow starts recovery before enabling run-producing actions. Submit recovery runs once globally; delete recovery runs only for workspace roots in the schema-v3 trusted registry. RunsResultsPage only refreshes recovered state and does not own startup replay.
