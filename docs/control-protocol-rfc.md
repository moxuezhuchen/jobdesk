# ConfFlow control protocol v1 consumer record

- **Status:** current consumer candidate record; immutable release evidence is
  stored separately under `confflow/schemas/control/releases/`
- **Producer release:** `v2.0.0`
- **Producer commit:** `69819350d340a6aeccf95aa175edfd1c3f63404b`
- **Producer wheel:** `confflow-2.0.0-py3-none-any.whl`
- **Wheel SHA-256:** `04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f`
- **Protocol:** `confflow.control.v1`
- **Schema dialect:** JSON Schema Draft 2020-12

JobDesk does not define a second control protocol. The authoritative producer
bundle is `docs/control_protocol/v1/` in the pinned ConfFlow release. The
immutable v2.0.0 bundle is vendored under
`confflow/schemas/control/releases/v2.0.0/`, while the files directly under
`confflow/schemas/control/` are the current candidate snapshot used by local
candidate tests. The candidate currently experiments with asynchronous cancel
intent responses; it must not be described as a change to the immutable v2.0.0
release. The v2.0.0 `control_worker` release contract contains all five
documents; historical producers that do not advertise that capability retain
only the four-file core. Both release and candidate snapshots are checked by
`tests/test_control_protocol_schemas.py` using canonical JSON digests; changing
a release schema requires a new pinned producer release and a review of the
cross-repository contract.

## Bundle

- `common.schema.json`: protocol identifiers, state, error, digest, locator,
  and artifact definitions.
- `requests.schema.json`: the eight operation request shapes (`capabilities`,
  `prepare`, `execute`, `status`, `events`, `cancel`, `resume`, and
  `artifacts`).
- `responses.schema.json`: the corresponding response envelope with
  `protocol_schema`, `operation`, `ok`, snapshots, typed errors, event pages,
  and artifact manifests.
- `input-manifest.schema.json`: the ordered input file manifest referenced by
  `prepare`.
- `worker-handoff.schema.json`: the producer-owned, one-task external-worker
  envelope released with v2.0.0 and required when `control_worker` is
  advertised.

The producer response envelope uses `state` (not `status`) and advertises
`supported_protocols` (not an operation list). A `prepare` request carries
content locators (`workflow_config` and `input_manifest`) plus
`expected_executable_identity`; the request digest binds the complete semantic
frame. These names are intentionally mirrored by
`jobdesk_app.services.confflow_control`.

The current JobDesk `.jobdesk-control/input-manifest.json` is a private
launcher journal (`jobdesk.confflow.input-manifest.v1`) because the consumer
has only persisted remote names at this boundary. It is not the producer
`confflow.control.input-manifest.v1` document. An external worker handoff must
stage the files, compute their byte digests/sizes, and construct the producer
manifest before invoking a real calculation.
The released worker handoff also publishes fixed `{stem}.txt` and
`{stem}min.xyz` sidecars beside the task work directory. The JobDesk
adapter must retain the established `<stem>_confflow_work` work-directory
name so the existing metadata bridge can map those sidecars; this naming rule
is part of the v2.0.0 producer extension. The four-file core remains the
stable operation snapshot, while the worker-handoff file is the formal fifth
release member whenever the producer advertises `control_worker`.

The JobDesk tree also carries a `worker-handoff.schema.json` snapshot for the
released ConfFlow worker extension. It is part of the pinned v2.0.0 release
and is sent only after the producer capability advertises `control_worker`.
The handoff envelope is one-task (`maxItems=1`); JobDesk rejects larger
batches rather than truncating them.

## Ownership and launcher boundary

ConfFlow owns durable run state, revisions, event cursors, idempotency, typed
errors, and the output manifest. JobDesk owns upload/prepare/launch journaling
and its local projection. The control command is foreground; `nohup`, Slurm,
and PBS are launcher concerns in JobDesk and invoke the same producer
`control execute` operation.

`prepared` is a durable producer record, not a running state. `execute` may
return `queued` when an external worker owns the eventual calculation handoff;
it must not be interpreted as a completed scientific calculation. JobDesk
therefore removed the legacy backend from the production path. The v1.4.6
rollback record remains historical evidence and does not authorize a current
control run.

## State, revision, and recovery rules

The producer is the sole state owner. Successful response states are
constrained by the producer schema. The table below is the current candidate
contract. The immutable v2.0.0 release snapshot retains terminal-only cancel
acknowledgements (`cancelled`); the compatibility matrix compares that release
wheel only with `releases/v2.0.0/`.

| operation | allowed successful state |
|---|---|
| `prepare` | `prepared` |
| `execute` | `queued`, `running`, `paused`, `completed`, `failed`, or `cancelled` |
| `status`, `events` | any declared state |
| `cancel` | `queued`, `running`, `paused`, or `cancelled` |
| `resume` | `queued` or `running` |
| `artifacts` | `completed`, `failed`, or `cancelled` |

`revision` is a non-negative producer sequence and never moves backward.
JobDesk may keep a local projection, but it must not write producer state or
replace a newer snapshot with an older one. Event revisions are strictly
increasing within a page. Event cursors are opaque strings matching the
producer cursor grammar; the current implementation happens to emit `r`
followed by a zero-padded revision, but consumers must not decode that form.

`cancel` is an asynchronous durable intent. A successful response in
`queued`, `running`, or `paused` means that the producer persisted the
cancellation request; it is not a terminal cancellation result. The worker or
a later `status` response must confirm the terminal `cancelled` state before
JobDesk projects the run as cancelled.

`prepare` binds `run_id`, `idempotency_key`, the complete request digest, both
content locators, and the expected executable identity. A retry with the same
semantic frame is idempotent; a different frame for the same key is an
`idempotency_conflict`. Once `execute` has consumed the prepared record,
JobDesk reconciles by `status`/launcher metadata rather than issuing a second
`prepare`; this keeps a lost launcher response from becoming duplicate work.
The launcher marker is only a submission proof after it records an explicit
successful execute exit code.

The stable error registry is owned by `common.schema.json` and consists of:
`invalid_request`, `unsupported_protocol`, `unknown_run`,
`idempotency_conflict`, `invalid_state_transition`, `invalid_checkpoint`,
`already_running`, `terminal_run`, `executable_identity_mismatch`,
`artifact_path_invalid`, `artifact_integrity_failed`,
`repository_unavailable`, and `internal`. Error responses always carry
`ok: false` and the typed `{code, message, retryable}` object; success
responses must not include an error object.

Artifact `path` values are relative POSIX paths with no empty, `.`, `..`,
absolute, backslash, or repeated-slash segments. Artifact terminal names are
single safe identifiers (`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`). JobDesk verifies
the manifest digest, size, path components, symlink status, and local target
before downloading any selected file.

## Compatibility and safety

Readers may ignore future optional fields, but required-field, state,
error-code, identity, and artifact-path changes are breaking contract changes.
Artifact paths are relative POSIX paths below the producer run directory; both
producer and consumer validate them before download. Stable control submission
is accepted only for the exact v2.0.0 production provenance. The current
async-cancel candidate is not a stable release and requires a separately
versioned producer contract before publication. The v1.4.6 record is
historical and the retired legacy backend is not a current submission path.
