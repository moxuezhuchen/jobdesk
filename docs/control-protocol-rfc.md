# ConfFlow Control Protocol RFC

- **Status:** Phase B design freeze
- **Protocol:** `confflow.control.v1`
- **Schema dialect:** JSON Schema Draft 2020-12
- **Owner:** ConfFlow producer repository; JobDesk consumes pinned release bundles

## Scope

This RFC freezes the JSON shapes for the eight one-shot operations below. It does not add a CLI entry point, implement `ExecutionService`, modify the legacy SSH/file backend, or change current runtime behavior.

```text
confflow control capabilities --json
confflow control prepare --request <request.json> --json
confflow control execute --run-id <id> --json
confflow control status --run-id <id> --json
confflow control events --run-id <id> --after <cursor> --json
confflow control cancel --run-id <id> --json
confflow control resume --run-id <id> [--checkpoint <id>] --json
confflow control artifacts --run-id <id> --json
```

The authoritative machine-readable definitions are the eight files in `confflow/schemas/control/`. Examples in this RFC are tested by `tests/test_control_protocol_schemas.py`. JobDesk adds `jsonschema` only to its development extras so fixtures validate Draft 2020-12 documents; it is not a runtime entry point or protocol implementation dependency.

## Envelope and identity

Every response has `protocol_schema: confflow.control.v1`, a non-negative integer `revision`, a stable `run_id` where the operation is run-scoped, and an optional typed `error`. Human messages are presentation-only. Clients branch on `error.code`, never on message text.

`prepare` accepts a client-generated `run_id`, idempotency key, workflow-config digest, input-manifest digest, and expected executable identity. The request digest is the canonical JSON digest of the complete request excluding transport metadata. The producer persists a durable prepared record without starting a process.

## State ownership and transitions

ConfFlow owns `pending`, `running`, `paused`, and terminal state transitions. JobDesk owns submission intent, upload/prepare/launch journal, and a monotonic local projection. A JobDesk projection never writes producer state.

| From | Allowed transitions | Owner |
|---|---|---|
| `pending` | `running`, `cancelled` | ConfFlow |
| `running` | `paused`, `completed`, `failed`, `cancelled` | ConfFlow |
| `paused` | `running`, `cancelled`, `failed` | ConfFlow |
| `completed` | none | ConfFlow |
| `failed` | none | ConfFlow |
| `cancelled` | none | ConfFlow |

`prepared` is a durable pre-execution record, not a running state. `execute` may transition it to `pending`/`running` according to the producer scheduler adapter, but must not daemonize or select a scheduler.

## Revisions and event cursors

Revisions are monotonically increasing per run and start at zero before the first durable record. Every accepted state mutation increments revision exactly once. Responses carrying an older revision are stale and must not overwrite a newer projection. Terminal revisions never regress.

Event cursors are opaque, producer-issued, and totally ordered within a run. `events(after=x)` returns events strictly after `x`, preserves order, and returns a cursor that can be replayed after reconnect. An unknown cursor is a stable `cursor.invalid` error; replaying a consumed cursor is valid and idempotent.

## Idempotency and recovery

The same idempotency key with the same canonical request digest returns the original handle and revision without creating another run. The same key with a different digest returns `idempotency.conflict` and does not mutate state. If JobDesk persists locally after prepare failure, it retries by idempotency key, reattaches to the original prepared record, and either completes local persistence or cancels the still-prepared run. Prepared records never self-start.

Concurrent execute requests resolve to one accepted transition; subsequent identical requests return the current snapshot. Invalid transitions return `state.invalid_transition`.

## Error registry

| Code | Meaning |
|---|---|
| `request.invalid` | Schema or semantic request validation failed |
| `run.not_found` | Run identifier is unknown |
| `run.server_mismatch` | Run belongs to another server identity |
| `idempotency.conflict` | Key is reused with a different request digest |
| `state.invalid_transition` | Requested operation is not legal from current state |
| `cursor.invalid` | Cursor is malformed, unknown, or from another run |
| `checkpoint.not_found` | Checkpoint is unavailable |
| `checkpoint.stale` | Checkpoint cannot be resumed from current revision |
| `artifact.invalid_path` | Manifest contains an unsafe path |
| `artifact.conflict` | Artifact targets duplicate or conflict |
| `capability.unsupported` | Requested protocol capability is unavailable |
| `internal.failure` | Sanitized producer failure; details are not contractual |

## Artifact path safety

Artifact paths are UTF-8 relative POSIX paths. Reject absolute paths, drive-letter paths, `..`, `.`, empty components, backslashes, duplicate paths, and two entries resolving to the same target. The producer must resolve every path below the run work directory before returning it. Consumers must verify the manifest again before download.

## Compatibility policy

A major protocol revision is breaking and requires an explicit migration. A minor revision may add optional fields only; readers must ignore unknown optional fields while writers must not make them required until the next major. Required-field, enum, state-transition, error-code, and path-safety changes are breaking. At least one released consumer migration cycle is retained for a minor revision.

## Scheduler boundary

The control adapter is a foreground process. `nohup`, Slurm, and PBS are launcher concerns owned by JobDesk or a scheduler adapter. They all invoke the same `execute` contract; the producer does not select, wrap, or daemonize the scheduler job.

## Review decisions required before Phase C

Reviewers must record agreement on producer ownership of state/revisions/events/artifacts, scheduler boundaries, the error-code compatibility period, and the dual-repository CI matrix. Phase C remains separately authorized and is not part of this RFC.
