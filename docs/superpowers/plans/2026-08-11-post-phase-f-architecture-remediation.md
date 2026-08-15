# Post-Phase F JobDesk / ConfFlow architecture remediation plan

Date: 2026-08-11
Last state refresh: 2026-08-14

Status: forward plan; partially superseded by landed JobDesk boundary fixes;
remaining implementation and acceptance work has not started

## State refresh (2026-08-14)

This document remains a remediation plan, not an acceptance or release record.
The review that produced it is a historical snapshot.  The current planning
checkout is JobDesk local `main` at `6d0010e`; its parent/origin source
baseline is `1937829`, and `6d0010e` adds this plan only.  Neither ref is a
production promotion or endpoint-switch authorization.

The current known ConfFlow source checkout is
`Ubuntu-24.04:/opt/ConfFlow` `main` at `c6a4263`.  That source ref is not the
installed production identity; its worktree, package, executable, and
configuration-contract state must be revalidated in an authorized environment
before implementation or acceptance.  The authoritative production boundary
remains the separately released JobDesk `v0.6.0` / `e4d8f74` and ConfFlow
`v2.0.0` / `6981935` pair.  Nothing in this refresh changes a configured
endpoint, installed environment, tag, or compatibility-period decision.

The following JobDesk fixes are present in the current source baseline (the
boundary work landed in `3903eb5`, an ancestor of `1937829`):

- GUI source no longer accesses `RunService.repository`; `MainWindow` consumes
  the immutable `FileTransferConnectionSnapshot`, and architecture tests cover
  both repository and cross-widget private-state boundaries.
- `RunOperationOutcome.errors` stores structured `OperationFailure` values and
  `refresh_result` uses a typed protocol instead of `Any`.
- `_SubmitOwnershipGuard` accepts its heartbeat interval explicitly and
  `submit_ownership` no longer imports `run_service` at import time.  The
  `_submit.py` lazy self-facade import remains a compatibility/test seam, so
  the full follow-up cleanup in Phase 2 is still open.

The remaining work is therefore a plan for the oversized control/page
decompositions, the residual dependency seam above, ConfFlow configuration and
workflow boundaries, compatibility corpus, and release/promotion gates.  No
green test result or historical release evidence below should be read as proof
that those phases are complete.

## Goal

Address the remaining architecture debt confirmed by the 2026-08-11
post-Phase F review while preserving the now-working control-only production
boundary.

This plan covers:

1. decomposition of JobDesk's oversized control adapter;
2. completion and independent re-review of the JobDesk application/GUI
   boundaries (the initial repository/private-state fixes are already landed);
3. verification of the landed typed JobDesk operation failures and removal of
   the remaining submit ownership import seam;
4. one canonical ConfFlow workflow-configuration model and validation path;
5. decomposition of the ConfFlow workflow engine and correction of layer
   inversions;
6. documentation, source-worktree, virtual-environment, release, and
   cross-repository verification cleanup.
7. compatibility-safe configuration-contract discovery, saved-workflow and
   resume-fingerprint migration, and acceptance-before-promotion release
   closure.

This is not a new migration. Phase F is complete. The control protocol,
producer state ownership, worker handoff, and fail-closed legacy retirement are
the protected baseline for every task below.

## Verified baseline

The implementation must revalidate these values before creating any branch or
worktree because they may drift after this plan is written.

### JobDesk producer-consumer application

- Current planning source: local `main` `6d0010e` (docs-only child of
  `origin/main` `1937829`).
- Production release reference (historical, unchanged): `v0.6.0` / package
  version `0.6.0` at `e4d8f74af0dff80b233f7bd9cb360b43d040069f`.
- Any installed package, endpoint, or source-worktree identity must be
  revalidated before implementation; the current planning checkout is not
  evidence that production has moved beyond `v0.6.0`.
- ConfFlow dependency window: `confflow>=2.0,<3.0`.
- Production submission is control-only; `auto` and `legacy` backend modes and
  historical legacy handles fail closed.

### ConfFlow producer

- Current known source checkout: `/opt/ConfFlow` `main` `c6a4263` (source
  baseline only; revalidate worktree and runtime identity before use).
- Production release reference (historical, unchanged): `v2.0.0` / package
  version `2.0.0` at `69819350d340a6aeccf95aa175edfd1c3f63404b`.
- Production wheel (historical release evidence):
  `confflow-2.0.0-py3-none-any.whl`.
- Wheel SHA-256:
  `04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f`.
- Recorded production venv evidence: `/opt/confflow-2.0.0-prod-venv` with
  verified provenance and a clean `pip check` (revalidate before acceptance).
- Recorded endpoint evidence: `/usr/local/bin/confflow` and the JobDesk `wsl`
  server entry resolved the production venv (revalidate before acceptance).
- The `/opt/ConfFlow` source checkout is not the production runtime; do not
  modify, reset, clean, or delete it as an incidental part of code remediation.
- `confflow-agent` has been retired. The supported lifecycle boundary is
  `control` plus `confflow-control-worker` over one `ExecutionService` state
  store.

### Current validation evidence

- Historical release evidence: the live v2.0.0 `control capabilities --json`
  response validated against the JobDesk response schema.
- JobDesk's five vendored control documents have the same canonical JSON hash
  as the ConfFlow v2.0.0 producer bundle.
- Historical targeted suites: JobDesk architecture/control/launcher `58
  passed, 1 skipped`; ConfFlow execution-service/SQLite/schema/control-worker/
  engine on WSL ext4 `131 passed`.

These are characterization baselines, not permission to skip each phase's
full validation.

## Confirmed problems in scope

### A. JobDesk control adapter concentration

`src/jobdesk_app/services/ssh_confflow_client.py` is currently 1,918 lines.
`SSHConfFlowClient` spans roughly 1,016 class lines and currently owns backend probing,
durable state, provenance, worker-handoff construction and staging, launcher
dispatch and reconciliation, local projection, handle operations, and artifact
download.

### B. JobDesk application and GUI boundary leaks

`RunsResultsPage` remains roughly 2,532 class lines and `FileTransferPage`
roughly 1,470 class lines, so their decomposition remains open.  The specific
repository/private-state leaks from the original review are already fixed in
the current JobDesk source baseline: GUI code uses public `RunService` queries,
`MainWindow` consumes `FileTransferConnectionSnapshot`, and
`SSHConfFlowClient` no longer reaches `RunCoordinator.service.repository`.
Current architecture tests cover those boundaries; their continued green
result is a prerequisite, not proof that the oversized pages are decomposed.

### C. Weak JobDesk outcome typing and import cycle

The typed outcome and import-time dependency fixes are already present in the
current JobDesk source baseline: `RunOperationOutcome.errors` stores
`OperationFailure`, `refresh_result` uses `RefreshResultProtocol`, and
`submit_ownership.py` receives the heartbeat interval without importing
`run_service`.  A lazy self-facade import remains in `_submit.py` for the
historical monkeypatch seam; removing that seam and independently re-reviewing
the typed decision/rendering paths remains part of Phase 2.

### D. Duplicate workflow-configuration semantics

- ConfFlow runtime execution uses `config.models.WorkflowConfig`,
  `GlobalOptions`, and `CalcStepParams`.
- Those runtime models are not yet a closed validation boundary:
  `config.models` imports `_coerce_freeze_indices` and
  `_coerce_two_atom_indices` from `core.models`, direct dataclass construction
  can bypass `from_mapping()`, and `StepConfig.params` remains an unvalidated
  `dict[str, Any]` until later execution code resolves it.
- ConfFlow separately exposes independent Pydantic validation in
  `core.models.GlobalConfigModel` and `CalcConfigModel`.
- JobDesk `WorkflowSpec` imports the second model family.
- JobDesk also maintains `_confflow_validation.py` and a list of deliberate
  `KNOWN_DIVERGENCE` fixtures.
- JobDesk documentation incorrectly states that the remote engine consumes the
  same Pydantic models imported by the GUI.

### E. ConfFlow workflow and layer concentration

- `workflow.engine.run_workflow()` spans roughly 385 lines and owns loading,
  DAG preparation, resume validation, input resolution, execution, checkpoint
  persistence, statistics, and finalization.
- `core.chem_validation` imports `blocks.confgen` implementations.
- `calc.runner` and `calc.postprocess` depend on
  `blocks.refine.result.RefineResult`.
- `ExecutionService` is cohesive but has begun accumulating pure validation
  and state-transition policy that can be separated from orchestration.

### F. Documentation and development-environment ambiguity

- The current JobDesk planning checkout (`main` `6d0010e`) is a docs-only child
  of source baseline `1937829`; it is not the released `v0.6.0` tree or a
  promotion candidate.
- `/opt/ConfFlow` is a source checkout at the known `c6a4263` ref, while the
  production runtime remains a separately verified versioned environment.  Do
  not infer installed or endpoint identity from the source checkout.
- Historical compatibility records intentionally retain
  `COMPATIBILITY PERIOD CONTINUES`, while current release documents describe a
  Phase F owner exception. They need unmistakable historical/superseded
  banners without rewriting the evidence.
- Current README/configuration-model wording is factually inaccurate.

### G. Configuration compatibility and discovery gaps

- ConfFlow publicly exports `WorkflowConfig`, `StepConfig`, `GlobalOptions`,
  and `CalcStepParams`; changing their constructors or replacing
  `StepConfig.params` with a different runtime value is a public API change,
  not automatically a compatible v2.1 refactor.
- JobDesk installs ConfFlow only through the optional `chem` extra, so its base
  installation cannot require producer Python models for editing or startup.
- JobDesk may target multiple servers with different approved ConfFlow
  releases. It currently has no producer-owned, remotely discoverable
  workflow-configuration contract identity distinct from
  `confflow.control.v1`.
- JobDesk v0.6 production admission is intentionally pinned to the exact
  ConfFlow v2.0.0 producer identity. A future producer can be published first,
  but it cannot be promoted or accepted by the existing consumer merely
  because its ordinary version window is `<3.0`.

### H. Persisted workflow and fingerprint compatibility

- Calc reuse is keyed by the digest of `CalcStepParams.canonical_dict()`;
  changes to defaults, normalization, or serialization can make a v2.0
  manifest appear stale.
- Confgen signatures, workflow state, saved JobDesk YAML/presets, nodegraph
  documents, wizard metadata, aliases, and DAG `inputs` are durable user data.
  The existing plan did not require a cross-version corpus or forbid cleanup
  on an unknown digest generation.

### I. Remaining orchestration concentration

- JobDesk `core/workflow_spec.py` is roughly 1,014 lines and owns optional
  dependency loading, legacy normalization, form mapping, YAML codecs,
  validation, dry-run presentation, and file writing.
- ConfFlow `config/models.py` is already roughly 709 lines; adding typed
  confgen fields, diagnostics, schema generation, and serialization in that
  file would create a new monolith.
- ConfFlow `control_worker.py` is roughly 578 lines and combines handoff
  parsing, path/digest validation, input staging, token lease ownership,
  sidecar publication, workflow invocation, and CLI handling.

### J. Promotion and local atomicity gaps

- The prior release order switched configured/default endpoints before final
  acceptance.
- A generic `RunProjectionStore` port does not by itself preserve the atomic
  boundary among the JobDesk journal, provenance, projection, and launcher
  state, or define ownership of SSH/SFTP session leases when a run handle
  outlives its submitting client.

## Protected invariants and non-goals

1. Do not change `confflow.control.v1` request/response shapes, schema hashes,
   state names, error registry, cursor semantics, or artifact safety rules in
   an internal refactor PR.
2. ConfFlow remains the sole owner of remote aggregate state, revisions,
   events, checkpoints, idempotency, launch tokens, and terminal artifacts.
3. JobDesk owns only upload/prepare/launcher journaling and its monotonic local
   projection. It must never write producer SQLite directly.
4. Do not reintroduce automatic legacy fallback, `confflow-agent`, or a second
   remote state machine.
5. Do not merge the repositories. Preserve independent releases and the
   two-direction contract CI matrix.
6. Do not rewrite, reset, stash, clean, switch, or delete either existing dirty
   worktree. Implementation uses isolated worktrees or fresh clones from the
   exact approved remote refs.
7. Do not replace, mutate, or delete the v2.0.0 production venv during normal
   implementation.
8. Do not run Gaussian, ORCA, g16, a scheduler workload, or a destructive
   remote cleanup without a separate explicit acceptance authorization.
9. Do not use file-size reduction as the acceptance criterion. Boundaries,
   dependencies, behavior, and state ownership are the gates.
10. Every phase starts with a failing characterization/architecture test and
    ends with targeted tests, the repository's full non-integration suite,
    lint/type/build checks, and an independent review.
11. Do not call a change v2.x-compatible until every publicly exported
    configuration type, constructor, attribute, exception, and serialization
    behavior has an explicit compatibility result. A breaking result requires
    a major-version plan; line-count or lack of known internal callers is not
    sufficient.
12. The JobDesk base install remains usable without the `chem` extra. Local
    producer Python models may improve matching-version diagnostics, but they
    are never the only submission acceptance path.
13. Never delete or rewrite an existing calc/confgen artifact because a new
    release does not understand its digest generation. Unknown or ambiguous
    fingerprints fail closed and leave user data untouched.
14. Publishing a release, installing a side-by-side environment, accepting a
    candidate, and switching the production/default endpoint are four separate
    gates. Promotion happens only after acceptance through the candidate
    endpoint.
15. Because Phase 5 changes the real workflow engine and control worker, one
    supported-launcher scientific workload is required before production
    endpoint promotion. If that authorization is not granted, stop after
    package release and side-by-side non-compute acceptance.

## Target architecture

```text
JobDesk GUI
  -> application queries/commands
     -> WorkflowDocument / WorkflowCodec
        -> producer-owned schema lint
        -> per-server ConfigContractResolver
           -> remote canonical validation
     -> ConfFlowClient / RemoteRunHandle
        -> ControlRunCoordinator
           -> ControlTransport
           -> WorkerHandoffStager
           -> LauncherDispatcher
           -> RunProjectionStore
           -> ArtifactDownloader
              -> SSH / SFTP / scheduler adapters

ConfFlow control CLI / control worker
  -> WorkerHandoffValidator
  -> WorkerInputStager
  -> TokenLeaseManager
  -> ExecutionService
     -> transition and validation policy
     -> ExecutionRepository
     -> WorkflowExecutor port
        -> WorkflowApplication
           -> WorkflowPlanner
           -> ResumePlanner
           -> StepExecutor
           -> WorkflowFinalizer

ConfFlow WorkflowConfig
  -> one canonical parser/validator
  -> generated versioned workflow schema bundle
  -> versioned config-contract discovery document
  -> JobDesk chem-optional local editor validation
  -> remote dry-run and execution
```

## Delivery strategy

Use small, sequential PRs. Every PR belongs to exactly one repository. A
coordinated release records paired producer/consumer refs in both repositories,
but never treats two repositories as one atomic PR. Do not combine structural
moves with protocol, schema, database, or user-visible behavior changes.

Recommended branch family:

- JobDesk: `codex/post-phase-f-architecture-*`
- ConfFlow: `codex/post-phase-f-architecture-*`

Each PR records its approved base commit and must be rebased or rebuilt from a
fresh isolated worktree if that base changes materially.

## Phase 0 - recapture baselines and add fitness functions

Repositories: both, separate no-behavior-change PRs if tests must be added.

### Task 0.1 - create isolated implementation worktrees

1. Query remote `refs/heads/main` without updating the dirty shared worktrees.
2. Create one clean isolated worktree/clone per repository from the exact
   approved `main` commit.
3. Record status, HEAD, tag containment, Python, dependency lock/install
   state, capability/provenance payload, schema hashes, and test commands.
4. Verify that the shared JobDesk worktree and `/opt/ConfFlow` have not changed.

Gate: both implementation trees are clean and match the approved remote refs;
all protected paths remain untouched.

### Task 0.2 - lock the current working behavior

JobDesk characterization coverage must include:

- prepare/execute reconciliation after ambiguous launcher responses;
- idempotent resubmission refusal;
- nohup, Slurm, and PBS launcher script equivalence;
- persisted `control_backend.json` recovery;
- event cursor replay and stale-revision rejection;
- pause/resume/cancel;
- artifact path, digest, size, symlink, and local-target validation;
- worker-handoff canonical digest and one-task enforcement;
- crash injection before and after journal, provenance, launcher-marker, and
  local-projection commits;
- session/lease ownership when submission fails, cancellation races with
  refresh, and a `RemoteRunHandle` outlives the submitting client;
- saved v0.5/v0.6 workflow YAML, presets, nodegraph documents, wizard metadata,
  aliases, advanced options, and DAG `inputs`, in both base and `chem` installs.

ConfFlow characterization coverage must include:

- `ExecutionService` transition and CAS behavior;
- worker token ownership and abandoned-launch recovery;
- SQLite reconnect and event ordering;
- workflow resume/checkpoint behavior;
- `run_workflow()` callback order, final outputs, sidecars, and artifact
  manifest behavior;
- current configuration coercion and rejection behavior;
- the public import/constructor/attribute/exception/serialization behavior of
  every object exported from `confflow.config` and `confflow.core.models`;
- v2.0 calc configuration digests, confgen signatures, workflow state, and
  resume decisions using checked-in immutable fixtures;
- control-worker handoff validation, path containment, digest verification,
  POSIX lease behavior, sidecar publication, and crash recovery.

Record an explicit compatibility matrix before implementation:

| Consumer | Local `chem` | Remote producer | Expected result |
|---|---|---|---|
| JobDesk stable | absent/present | current stable | unchanged pass |
| JobDesk stable | matching next candidate | next candidate | authoring/legacy-facade compatibility passes; production identity admission remains an expected fail-closed result |
| JobDesk candidate | absent | stable and next | schema lint plus remote canonical acceptance passes |
| JobDesk candidate | matching | stable and next | local diagnostics agree with the selected remote contract |
| JobDesk candidate | mismatched | stable or next | mismatched local parser is non-authoritative; selected remote contract controls acceptance |
| Any consumer | any | unsupported contract/version/hash | fail before workload upload |

Do not change production code in this task unless a characterization test
reveals a real regression; such a regression becomes a separately reviewed
fix.

### Task 0.3 - add architecture fitness tests

Add AST/import tests that fail when:

- JobDesk GUI accesses `.repository` or imports repository modules;
- one GUI page reads another page's underscore-prefixed fields;
- `SSHConfFlowClient` or extracted control collaborators reach
  `RunCoordinator.service.repository`;
- `services.submit_ownership` imports `services.run_service`;
- code outside the repository/application transaction boundary mutates
  journal, provenance, or projection tables directly;
- ConfFlow `core` imports `blocks`;
- ConfFlow `calc` imports a result type owned by `blocks.refine`;
- new code imports `confflow.core.models` instead of the canonical config API;
- ConfFlow configuration parsing, schema generation, diagnostics, and step
  models collapse back into one oversized module after their extraction;
- control-worker CLI/orchestration owns path validation, staging, lease, or
  sidecar implementation details after those components are extracted.

Initially mark only confirmed existing violations with narrow temporary
allowlists. Every later phase removes its own allowlist entry. No broad path or
module exemptions are allowed.

Gate: fitness tests describe every confirmed violation and reject a newly
introduced violation outside the temporary allowlist.

## Phase 1 - decompose the JobDesk control backend

Repository: JobDesk.

This is a behavior-preserving extraction. Do not change the control schema,
wire JSON, durable state format, launcher scripts, task projection, or download
layout.

### Task 1.1 - introduce explicit application ports

Add small protocols under `src/jobdesk_app/application/`:

- `RunProjectionStore` for run/task/provenance reads and projection writes;
- `ControlLauncher` for one prepared launch dispatch and reconciliation;
- `WorkerHandoffStager` for safe remote staging and canonical digest
  production;
- `ControlArtifactDownloader` for fail-closed artifact transfer.

`RunProjectionStore` is not a generic CRUD surface. Its methods represent
complete application decisions such as recording accepted producer provenance,
committing a monotonic remote snapshot, or attaching a reconciled launcher
result. The repository implementation owns the SQLite transaction; callers
cannot interleave half of a journal/provenance/projection update.

Implement these protocols under `services/`; keep SSH/SFTP/scheduler types out
of `application/`.

`SSHConfFlowClient` receives these dependencies explicitly. It must no longer
reach `coordinator.service.repository`.

Gate: application protocols import only stable core/application types and
contain no Paramiko, PySide6, repository implementation, or scheduler import.

### Task 1.2 - extract durable state and worker handoff

Refactor:

- keep `services/confflow_control_state.py` as the sole serializer/validator
  for `control_backend.json`;
- move `_control_state`, `_state_*`, and capability/identity serialization
  helpers out of `ssh_confflow_client.py` into that module or a dedicated
  `confflow_control_run_state.py`;
- move `_worker_handoff`, digest, safe-component, path-under-root, remote-file
  staging, and private-directory creation into
  `confflow_control_handoff.py`;
- make the handoff stager return a frozen typed result containing paths,
  digests, and the canonical envelope rather than an untyped dictionary.

Tests must compare serialized bytes/digests and durable JSON against the
pre-refactor golden fixtures.

### Task 1.3 - extract launcher dispatch and reconciliation

Move scheduler/resource selection, launcher script assembly, dispatch,
successful marker persistence, and ambiguous-response reconciliation into
`confflow_control_launcher.py`.

The launcher component must:

- accept one already-prepared run;
- never call `prepare`;
- never write producer state;
- treat a missing/ambiguous launcher response as unresolved until reconciled;
- refuse a duplicate dispatch when the durable journal cannot prove safety;
- continue to use existing nohup/Slurm/PBS adapters.

### Task 1.4 - extract artifact transfer and run handle

Move artifact terminal mapping, remote metadata checks, SFTP transfer,
temporary local staging, integrity verification, and final atomic placement to
`confflow_control_artifacts.py`.

Move `SSHControlRunHandle` to its own module if doing so does not create an
application-to-services cycle. It remains the implementation of the existing
`RemoteRunHandle` protocol.

### Task 1.5 - reduce SSHConfFlowClient to orchestration

After extraction, `SSHConfFlowClient` owns only:

- capability/provenance negotiation;
- orchestration of prepare -> stage -> launch -> durable projection;
- construction/attachment of a run handle;
- dependency/session lifetime.

Define session ownership explicitly:

- short-lived collaborators borrow a scoped lease and cannot retain the raw
  session;
- a returned run handle owns a session factory or independently reference-
  counted lease, never a borrowed session from the submitting call;
- close/cancel/error paths are idempotent and cannot close a lease still owned
  by another handle;
- SSH/SFTP/scheduler operations document thread-safety and may not share a
  mutable channel across concurrent refresh/download operations unless the
  underlying adapter guarantees it.

No extracted staging, script-generation, artifact-download, or JSON-layout
helper may remain in this file. A line-count target may guide review, but the
gate is responsibility and dependency ownership, not a numeric threshold.

### Phase 1 validation

Run at minimum:

```powershell
python -m pytest tests/test_control_protocol_schemas.py `
  tests/test_confflow_control_backend.py `
  tests/test_confflow_launcher_integration.py `
  tests/test_architecture_boundaries.py -q -p no:cacheprovider
python -m pytest -m "not integration" -q -p no:cacheprovider
python -m ruff check .
python -m mypy src
python scripts/smoke_gui_offscreen.py
python -m build
```

Gate: all pre-refactor control fixtures are byte/behavior compatible; no
duplicate dispatch, split local commit, leaked/double-closed session, state
regression, or artifact-path relaxation is accepted.

## Phase 2 - repair JobDesk application and GUI boundaries

Repository: JobDesk. Depends on Phase 1.

### Task 2.1 - verify public run query APIs

The basic public queries are already present in the current JobDesk baseline.
Keep them as the only GUI-facing query surface and verify any remaining
application-port gaps before extracting more code.  The required surface is:

- `load_run(run_id)`;
- `load_tasks(run_id)`;
- `load_run_provenance(run_id)`;
- result-workspace/task summaries needed by the GUI;
- immutable connection state needed by MainWindow.

GUI code and control adapters consume these APIs; they do not access
`RunService.repository` directly. Keep repository mutation behind existing
journalled `RunService`/`RunCoordinator` operations.  This task is a
characterization/review checkpoint for the landed boundary, not permission to
reintroduce direct repository access during extraction.

### Task 2.2 - complete cross-widget private-state access review

The immutable `FileTransferConnectionSnapshot` and `MainWindow` call sites are
already present.  Re-review the public snapshot contract and add only the
remaining stale-callback/generation evidence if required:

- connected flag;
- server ID/label;
- remote directory;
- connection generation or stable identity needed to reject stale callbacks.

`FileTransferPage` exposes a public snapshot/signal API. `MainWindow` must no
longer read or assign `_service`, `_connected_server_id`, or nested connection
controller fields.  The current architecture test already enforces this
negative rule.

### Task 2.3 - split Runs & Results responsibilities

Extract from `RunsResultsPage`:

- run/task query and projection into `runs_results_view_model.py`;
- lifecycle commands into `runs_results_actions.py` or an application
  controller;
- table/selection presentation into a Qt model or focused view component;
- control-event polling/reconciliation into a non-Qt service with a Qt adapter.

The page remains responsible for widget composition and signal wiring only.
Preserve visible behavior, translations, selection, busy gates, and worker
lifetime semantics.

### Task 2.4 - split File Transfer responsibilities

Complete the existing `file_transfer_connections.py` extraction:

- one connection controller owns service creation/closure and server identity;
- one browser/query component owns directory listing and navigation;
- one transfer-command component owns upload/download/delete requests;
- the page owns widget composition and signal wiring.

### Task 2.5 - verify typed operation failures

The current baseline already uses the string-compatible structured
`OperationFailure` type with fields such as:

- stable code;
- stage/operation;
- message;
- retryable;
- optional task ID;
- optional sanitized diagnostic detail.

Keep `RunOperationOutcome.errors` typed and `refresh_result` protocol-typed;
do not replace the landed compatibility shape without a separately reviewed
API decision.  Add/retain tests that branch on code/stage rather than parse
message text, and that render legacy text separately.

Compatibility helpers may expose rendered messages during one JobDesk release,
but new code must branch on code/stage, not parse message text.

### Task 2.6 - remove the submit ownership cycle

The guard now receives its heartbeat interval through its constructor.  Finish
the remaining explicit test seam so submit code does not need to import its
own `run_service` facade merely to preserve monkeypatch behavior.

Keep the already-removed lazy `run_service` import from
`submit_ownership.py` absent as a regression assertion.  Remove the remaining:

- the self-reexported heartbeat constant once its compatibility seam has an
  explicit replacement;
- equivalent lazy package imports in `_submit.py` where explicit dependency
  injection can replace them.

### Phase 2 validation

Add architecture assertions that:

- no file under `gui/` accesses `.repository`;
- `MainWindow` does not access underscore-prefixed fields of child pages;
- `submit_ownership` and `run_service` are not an import cycle;
- GUI and application layers do not depend on concrete SSH/SFTP types;
- all operation error rendering is tested separately from error decisions.

Run the JobDesk full non-integration suite, GUI offscreen smoke, Ruff, mypy,
build, and the existing real-test collection check without executing real
workloads.

Gate: all Phase 0 JobDesk allowlist entries are removed.

## Phase 3 - build a compatible canonical ConfFlow configuration contract

Repository: ConfFlow. Producer candidate only; do not publish a final release
or switch an endpoint in this phase. It must not change `confflow.control.v1`
or the existing capability schema v4.

### Task 3.1 - freeze the v2 public API and make the SemVer decision

Treat every type exported from `confflow.config` and
`confflow.core.models` as public. Check the Phase 0 snapshot plus repository,
documentation, downstream JobDesk, and available package-consumer evidence.

For the compatible v2 line, keep the existing public classes as legacy views:

- constructor signatures and accepted typed values remain compatible;
- observable attributes such as `StepConfig.params` retain their v2 runtime
  shape;
- `from_mapping()`, `as_legacy_shape()`, Pydantic `model_validate`,
  `model_dump`, `model_fields`, exception classes/messages required by tested
  callers, equality, and serialization remain characterized;
- the views delegate all semantic decisions to the canonical parser and own no
  independent defaults, accepted-value lists, coercions, or cross-field rules.

Introduce additive canonical domain types in a separate namespace, for
example `confflow.config.canonical`:

- `CanonicalWorkflowConfig`;
- `CanonicalGlobalOptions`;
- `CanonicalStepConfig`;
- `CanonicalCalcStepParams`;
- `CanonicalConfgenStepParams`.

New engine code consumes only canonical types. Legacy views may adapt a
canonical result back to the v2 shape. Mark legacy imports for removal in v3,
but do not change their runtime shape in v2.1. If this compatibility layer
cannot be implemented without observable breakage, stop Phase 3 and replace
the v2.1 proposal with a separately approved v3 migration; do not relabel the
break as a minor release.

### Task 3.2 - inventory every field, alias, and extension before typing

Build a checked-in decision table covering every global, calc, confgen, DAG,
resume, cleanup, TS, executable, resource, and advanced field consumed by the
engine, dry-run, config-show, worker, JobDesk, examples, or tests. Classify each
input as:

- canonical semantic field;
- compatibility alias with a canonical replacement and deprecation policy;
- editor-only metadata that must never reach execution;
- documented namespaced extension;
- unsupported/typo input with a stable issue code.

Preserve the characterized v2 behavior for currently consumed unnamespaced
fields. Do not classify a field as non-semantic merely because it is absent
from the current dataclass. New extensions use an explicit namespace and are
excluded from execution unless a registered producer extension owns them.

Define three non-overlapping validation stages:

1. canonical parse/coercion validates all environment-independent workflow,
   global, step, DAG, and cross-field rules;
2. canonical serialization emits normalized semantic values and documented
   extensions;
3. runtime preflight alone validates paths, executables, permissions, input
   chemistry/artifacts, and other environment-dependent facts.

Fatal issues use one typed configuration exception with stable code, severity,
and field path. Non-fatal warnings are carried outside the semantic model's
equality and hash payload.

### Task 3.3 - split the configuration package around responsibilities

Move freeze/two-atom and all other configuration coercion primitives out of
`core.models` into the canonical package. Use focused modules such as:

```text
confflow/config/canonical/global_options.py
confflow/config/canonical/calc_step.py
confflow/config/canonical/confgen_step.py
confflow/config/parser.py
confflow/config/issues.py
confflow/config/serialization.py
confflow/config/schema.py
confflow/config/legacy.py
```

`confflow.config` must not import `core.models`. Raw mappings/YAML enter only
through factories. Canonical typed constructors accept already-normalized
values and enforce their own invariants. Engine, dry-run, config-show, rerun,
and worker code cannot reinterpret recognized values from raw dictionaries.

### Task 3.4 - generate the schema and expose a separate config contract

Generate, rather than hand-maintain, a producer-owned workflow schema from the
same canonical field metadata used by the parser. Commit the generated artifact
for review and package it in wheel/sdist, for example:

```text
docs/workflow_config/v1/workflow-config.schema.json
share/confflow/workflow_config/v1/workflow-config.schema.json
```

The build regenerates the schema and fails on a dirty diff. Record the schema
generator version, canonical JSON algorithm, semantic contract version, and
SHA-256. JSON Schema covers expressible structural rules; the canonical parser
remains authoritative for non-expressible semantic rules.

Add a separate versioned, machine-readable configuration interface rather
than modifying control protocol v1 or capability schema v4:

```text
confflow config contract --json
confflow config validate --json --stdin
```

The contract response identifies its own response schema, workflow schema
version/hash, semantic contract version, and producer provenance binding. The
validate command accepts only workflow configuration input, performs no
scientific workload or filesystem/executable probe, emits one protocol JSON
document on stdout, and returns structured issues. Both commands suppress
import-time noise and have packaged-wheel subprocess tests.

An exact approved v2.0.0 producer may use a JobDesk-owned frozen fallback
mapping because it predates this command. Absence of the command on any other
producer is unsupported, not an invitation to guess from a broad version
range.

### Task 3.5 - preserve calc/confgen fingerprints and resume behavior

Freeze v2.0 canonical calc dictionaries/digests, confgen signatures, workflow
state, manifests, aliases, defaults, and resume decisions as immutable
fixtures. For semantically identical v2 input, the compatible release must
continue producing and accepting the v2 digest/signature bytes.

When reading existing artifacts:

- compare a supported legacy fingerprint before declaring it stale;
- distinguish understood mismatch from unknown fingerprint generation;
- an unknown/ambiguous generation raises a typed, actionable error without
  deleting, renaming, or rewriting any artifact;
- recomputation cleanup requires an explicit understood mismatch and the
  existing path-safety checks.

If compatibility requires a new fingerprint or manifest version, stop and
create a separate durable-format migration PR with forward/backward readers,
rollback evidence, and explicit authorization. Do not hide it inside the
configuration refactor.

### Task 3.6 - convert old Pydantic models into rule-free facades

Refactor `GlobalConfigModel` and `CalcConfigModel` so all validation delegates
to canonical submodel factories. Preserve the characterized v2 Pydantic
surface needed by JobDesk stable while it migrates. Add a deprecation warning
only where it does not change normal machine-readable output.

### Task 3.7 - configuration architecture and parity gates

Reject:

- imports from `confflow.core.models` under `confflow.config`;
- independent defaults/coercions/accepted values in legacy models;
- engine code consuming raw YAML dictionaries after canonical parsing;
- recognized semantic step fields stored only as untyped dictionaries in the
  canonical representation;
- hand-edited generated schemas or a second schema generator;
- schema/parser differences for any JSON-Schema-expressible rule;
- constructor states that canonical parsing would reject;
- non-idempotent parse -> canonical serialize -> parse results;
- loss of alias, issue-path, extension, installed-wheel, or source-tree parity.

### Phase 3 validation and candidate gate

Run the complete ConfFlow Linux/ext4 suite, Ruff, mypy, wheel/sdist build,
installed-wheel tests, schema regeneration/hash/provenance checks, v2 public-API
snapshots, old-run resume fixtures, and the configuration contract subprocess
tests.

Run producer-candidate x JobDesk-stable CI in two separately reported modes:

- legacy authoring/API compatibility must pass against the candidate;
- JobDesk stable's rejection of an unapproved production identity remains an
  expected fail-closed result, not a disguised green production acceptance.

Gate: an approved candidate wheel exists, but no final producer release is
published. One canonical parser decides semantics; legacy public views retain
their v2 behavior without independent rules; existing artifacts remain safe.

## Phase 4 - build the JobDesk per-server configuration consumer

Repository: JobDesk. Depends on an approved ConfFlow Phase 3 candidate, not a
final producer release. This phase produces a consumer candidate only.

### Task 4.1 - resolve and persist the remote configuration contract

Add a `ConfigContractResolver` behind an application port. After ordinary
producer capability/provenance admission and before workload upload, it:

- queries `confflow config contract --json` for new producers;
- uses the exact frozen v2.0.0 fallback only for that approved identity;
- verifies response schema, producer binding, workflow-schema hash, semantic
  version, and locally packaged/vendored bundle bytes;
- caches by server ID plus immutable executable/producer identity, never by a
  global current version;
- persists the accepted configuration contract with each run's provenance;
- fails closed before workload upload for unknown version/hash/identity.

Do not add these fields to `confflow.control.v1` or reinterpret the control
schema as the workflow-configuration schema.

### Task 4.2 - keep the base install chem-optional

Replace the claim that JobDesk owns/loads the producer's Pydantic model with
three explicit layers:

1. `WorkflowDocument` is JobDesk's lossless authoring document, not a semantic
   mirror of ConfFlow runtime types;
2. the selected producer-owned JSON Schema supplies chem-optional structural
   editor lint and stable field-path feedback;
3. `confflow config validate --json --stdin` on the selected remote producer
   is the authoritative environment-independent submission acceptance step.

If matching ConfFlow Python code is installed locally through `chem`, its
canonical parser may add immediate diagnostics only when its contract
version/hash matches the selected remote producer. A missing or mismatched
local package cannot approve/reject submission; show a bounded warning and use
schema plus remote canonical validation.

Editor-only stricter rules are warnings, never producer rejections. Runtime
path/executable/input checks remain later producer-owned preflight.

### Task 4.3 - split WorkflowSpec responsibilities

Replace the 1,014-line concentration with focused modules, for example:

```text
core/workflow_document.py
core/workflow_codec.py
core/workflow_migrations.py
core/workflow_form_mapper.py
core/workflow_editor_lint.py
services/confflow_config_contract.py
services/confflow_config_validation.py
```

Keep Qt outside core/application codecs. Atomic file writing is a small storage
adapter. Form defaults, wizard metadata, user-facing filtering, YAML
normalization, remote contract selection, and semantic acceptance do not share
one class. Retain a compatibility facade only for current JobDesk callers and
give it a removal milestone.

### Task 4.4 - migrate saved workflows without silent loss

Build a checked-in corpus from v0.5/v0.6 workflow YAML, legacy token layouts,
presets, nodegraph serialization, wizard metadata, advanced options, aliases,
explicit DAG roots/fan-in/fan-out, disabled steps, and unknown fields.

For every fixture define whether the result is byte-preserved, semantically
normalized, warned, or rejected. Opening and saving cannot flatten DAG inputs,
drop recognized advanced fields, move editor metadata into engine YAML, or
replace an original file before successful parse/validation. A format-changing
migration writes a backup and records its format version.

### Task 4.5 - remove duplicate semantic validation

Remove production/test acceptance imports of `GlobalConfigModel` and
`CalcConfigModel`. Replace `_confflow_validation.py` and `KNOWN_DIVERGENCE` as
semantic acceptance mechanisms. Tests may retain named editor-warning
differences, but JobDesk cannot maintain its own producer semantic rule list.

### Task 4.6 - validate every supported producer/consumer pairing

Run the Phase 0 compatibility matrix with base and `chem` installations,
stable v2.0 and next candidate remotes, matching and mismatched local packages,
unsupported hashes, unavailable contract commands, and multiple server entries
in one JobDesk database. Verify the selected contract and provenance survive
restart/recovery and cannot be swapped between runs.

### Task 4.7 - correct dependency and architecture documentation

Update README and `docs/confflow_dependency_decision.md` to state:

- control JSON and workflow configuration are separate versioned contracts;
- ConfFlow alone owns canonical workflow semantics;
- JobDesk owns a lossless authoring document and producer-schema editor lint;
- remote canonical validation is authoritative for submission;
- the base install works without `chem`, while matching local ConfFlow is an
  optional diagnostic accelerator;
- runtime-only validation remains producer-owned.

### Phase 4 validation

Run workflow document/codec/migration, nodegraph bridge, wizard, remote
configuration validation, provenance/recovery, stable/next matrix, full
non-integration, base-install, `chem`-install, Ruff, mypy, GUI offscreen smoke,
and package build tests.

Gate: JobDesk has no independent producer-semantic validator and no acceptance
path imports `confflow.core.models`; saved user documents are preserved under
the declared migration policy; every run is bound to the selected server's
verified configuration contract. Do not publish the JobDesk release yet.

## Phase 5 - decompose ConfFlow workflow execution and repair layers

Repository: ConfFlow. Depends on the canonical configuration model from Phase
3. It may be split into multiple PRs, but each PR must be behavior-preserving.

Phase 5 must preserve the approved Phase 3 configuration-contract command,
response schemas, workflow schema bytes/hash, canonical semantics, and legacy
facades. Any required change returns to Phase 3 review and then re-runs the
entire Phase 4 consumer matrix; it cannot be absorbed as an incidental engine
refactor.

### Task 5.1 - introduce a prepared workflow model

Create a typed `PreparedWorkflow`/execution-plan object containing:

- canonical `WorkflowConfig`;
- validated DAG/topological order;
- enabled steps;
- resolved input lineage rules;
- state/checkpoint locator;
- output and artifact expectations.

Move loading, configuration parsing, DAG validation, and initial-state
construction out of `run_workflow()` into a planner.

### Task 5.2 - extract resume planning

Move state-file loading, schema/version validation, step-name validation,
checkpoint compatibility, completed-output reuse, and resume diagnostics into
`workflow/resume.py` or an equivalent focused component.

The result must be a typed resume decision, not a collection of loosely
related dictionaries and strings.

### Task 5.3 - extract step execution

Move per-step input resolution, handler dispatch, status callback ordering,
result lineage updates, and checkpoint writes into a `WorkflowExecutor`.

Preserve:

- explicit DAG fan-out/fan-in lineage;
- disabled-step behavior;
- custom calc executor injection;
- callback order;
- failure messages and persisted state schema.

### Task 5.4 - extract finalization

Move workflow statistics, run summary, output manifest, fixed sidecar
publication, and final state transitions into a finalizer. The finalizer must
fail before producer `completed` when a required sidecar or manifest cannot be
published.

`run_workflow()` remains as the public compatibility facade and should only
construct/call the planner, resume component, executor, and finalizer.

### Task 5.5 - move neutral result types out of blocks

Move `RefineResult` to a neutral domain owned by `calc` or a shared domain
module. Update `calc.runner`, `calc.postprocess`, and refine implementations to
depend on that neutral type.

If the old `confflow.blocks.refine.result.RefineResult` import is public,
retain a tested deprecation re-export for one release; it must not remain the
canonical owner.

### Task 5.6 - remove `core -> blocks` dependencies

Inventory every caller of `core.chem_validation`.

- If the functions are confgen-specific, move the public API to
  `blocks.confgen` and update callers.
- If multiple domains need them, define neutral chemistry validation ports and
  types in a shared domain module; confgen supplies the implementation.

After migration, `core` must not import `blocks`, including through lazy
function imports. A temporary compatibility shim requires an explicit
deprecation test and removal milestone; it cannot be silently exempted from
the final gate.

### Task 5.7 - keep ExecutionService an orchestrator

Extract pure request/artifact/cursor/identity validation and pure transition
policy from `application/execution/service.py` into focused modules. Keep
repository CAS calls, executor handoff, and lifecycle orchestration in
`ExecutionService`.

Do not split atomic decisions across multiple state owners or let adapters
write the repository directly.

### Task 5.8 - decompose the control worker security boundary

Extract from `control_worker.py`:

- `WorkerHandoffValidator` for schema, identifier, path-containment, digest,
  executable-identity, and one-task validation;
- `WorkerInputStager` for private-directory creation and verified atomic file
  staging;
- `TokenLeaseManager` for the existing POSIX ownership lease, live-process
  reconciliation, idempotent terminal attachment, and abandoned-owner policy;
- `WorkerSidecarPublisher` for fixed sidecar and artifact publication;
- a workflow-runner adapter that invokes the prepared workflow application.

`run_control_worker()` remains the orchestration entry point and must consume
only an existing queued intent with its non-empty lifecycle token. It cannot
create/repair producer state, bypass `ExecutionService`, weaken executable
identity, or replace the validated ownership lease with callback-only duplicate
rejection. CLI parsing and one-JSON stdout encoding remain thin boundaries.

Freeze handoff canonical bytes, path rejection cases, lease owner files,
sidecar bytes, lifecycle callback order, crash/restart behavior, and exact
PREPARED/PAUSED/RUNNING/terminal rejection or attachment behavior.

### Phase 5 validation

Run at minimum on Linux/ext4:

```bash
python -m pytest \
  tests/test_engine.py \
  tests/test_config_models.py \
  tests/test_execution_service.py \
  tests/test_sqlite_execution_repository.py \
  tests/test_workflow_execution_adapter.py \
  tests/test_control_adapter.py \
  tests/test_control_worker.py -q -p no:cacheprovider
python -m pytest -q -p no:cacheprovider
python -m ruff check confflow tests
python -m mypy confflow
python -m build
```

Also run an installed-wheel control/worker lifecycle fixture in a private ext4
state root, including process crash after token claim, concurrent duplicate
worker launch, invalid digest/path, and idempotent terminal reattachment.

Gate: `run_workflow()` is orchestration-only; ConfFlow import fitness tests
have no `core -> blocks` or `calc -> blocks.refine` violation;
`run_control_worker()` is orchestration-only while the POSIX lease and
lifecycle-token boundary remain intact; persisted state, fingerprint/artifact
bytes, and control responses are compatible. Produce the final ConfFlow
candidate ref/wheel for Phase 6, but do not publish or promote it here.

## Phase 6 - documentation, environment, and release closure

Repositories: both, after Phases 1-5.

### Task 6.1 - update current architecture documentation

Make README, `docs/architecture.md`, and `docs/USER_GUIDE.md` the maintained
current-product entry points and update them to describe the final boundaries,
SubmitDialog/nodegraph flow, configuration-contract selection, optional
`chem` behavior, extracted modules, supported producer/consumer pairings, and
promotion/rollback process.

Include an explicit table distinguishing:

- the dirty historical shared worktree and its package metadata;
- the current released production baseline;
- the exact implementation candidate refs;
- the final released/promotion endpoints when they exist.

Add clear `historical evidence; superseded for current product behavior`
banners to compatibility-period and phase documents where needed. Do not edit
historical counters, decisions, bundle hashes, commands, or acceptance facts.
Do not describe a local branch package version as the current production
version merely because that branch is open in the desktop app.

### Task 6.2 - resolve development-worktree ambiguity

Present the owner with a read-only inventory and choose explicitly between:

- keeping the dirty historical JobDesk worktree and creating a new clean
  release-development worktree; or
- archiving/reconciling it after its uncommitted changes are reviewed.

For `/opt/ConfFlow`, choose explicitly between:

- updating/recreating it as the current clean source-development worktree; or
- marking it historical and using a separately named current source tree.

Do not delete, reset, move, or replace either tree without a separate explicit
authorization. An old editable venv may be removed only after its realpath,
consumers, processes, and rollback value are checked.

Classify root-level `.gatec-*`, `.pytest_tmp*`, `.codex_tmp_*`, ad-hoc scripts,
and other generated residue by owner, purpose, tracked state, process use, and
evidence/rollback value. Do not treat a Ruff error in generated residue as a
source-code defect, do not blanket-ignore files that the public-tree guard is
supposed to catch, and do not delete or change `.gitignore` until each class has
an explicit preserve/archive/ignore/delete decision and authorization.

### Task 6.3 - run two-direction contract CI

Required remote gates:

- ConfFlow final candidate x JobDesk stable legacy/public-API compatibility;
- JobDesk candidate x current stable producer x ConfFlow final candidate;
- JobDesk base and `chem` installs x every supported remote pairing;
- matching/mismatched local ConfFlow x selected per-server configuration
  contract;
- expected fail-closed production admission of an unapproved candidate is
  reported separately from compatibility success;
- control, configuration-contract response, validation-response, and generated
  workflow-schema bundle parity;
- installed-wheel provenance and package-data verification;
- saved-workflow and old-run resume/fingerprint corpus;
- no acceptance of an unsupported producer, configuration contract, response
  schema, workflow schema hash, or executable identity.

Publish remote CI evidence from both repositories. A local matrix or one
repository invoking only its own tests is supporting evidence, not the
two-direction gate.

### Task 6.4 - publish producer first without promoting it

1. Revalidate the exact ConfFlow final-candidate HEAD, Phase 3/5 gates, remote
   CI, clean worktree, and version decision.
2. Release ConfFlow from a clean tagged worktree. Verify tag peel, wheel/sdist
   hashes, SBOM/attestation/provenance, generated schemas and config-contract
   documents, package data, `pip check`, and installed-wheel tests.
3. Do not update `/usr/local/bin/confflow`, JobDesk server entries, or any
   production/default endpoint merely because the producer release exists.
4. Update the JobDesk candidate to the exact published producer version,
   commit, wheel digest, configuration-contract/schema hashes, and supported
   pairing table. Re-run all Phase 4 and two-direction gates against published
   artifacts, not a locally rebuilt equivalent.
5. Release JobDesk from a clean tagged worktree only after those gates pass.

Do not mutate published artifacts or reuse tags after failure; fix forward with
a new version. ConfFlow Phase 3 and Phase 5 are one producer release train, not
two final producer releases.

### Task 6.5 - install side by side and accept through explicit candidates

Install both released packages into new versioned environments. Preserve
v2.0.0/v0.6.0 environments, configured endpoints, state roots, wheel bytes,
and exact rollback commands.

Using only explicit temporary candidate endpoints and a private acceptance
state root, verify without a scientific workload:

- executable identity, capability, provenance, and configuration-contract
  discovery;
- base-install schema lint and remote canonical configuration validation;
- control prepare/execute and worker token consumption through the released
  worker/fixture path;
- reconnect, cursor replay, status/events/cancel/resume;
- artifact manifest validation/download and saved-workflow migration;
- old v2.0 fingerprint/resume fixtures remain readable and untouched;
- rollback commands restore the old endpoint and identity.

Failure stops acceptance. Do not retry with another launcher, patch an
installed environment, relax a criterion, or switch a default endpoint.

### Task 6.6 - require real launcher acceptance before promotion

Because the release changes `run_workflow()` and the control worker, production
promotion requires separate authorization for one bounded JobDesk submission
through a supported real launcher using g16 or ORCA. Run the exact chain:

```text
JobDesk submit
  -> remote config contract + canonical validation
  -> control prepare/execute
  -> released worker lease/token consumption
  -> real workflow step execution
  -> reconnect/events/status
  -> artifact manifest and JobDesk download
```

Stop at the first failed gate. Do not retry, substitute a launcher, use a
legacy path, or relax acceptance. If authorization is not granted, record
`RELEASED AND SIDE-BY-SIDE VERIFIED; PRODUCTION PROMOTION NOT AUTHORIZED` and
leave all production/default endpoints on v2.0.0/v0.6.0.

### Task 6.7 - promote only after acceptance and verify rollback

After both non-compute and authorized real-launcher acceptance pass:

1. revalidate released executable/wheel/configuration-contract identities;
2. record old and new endpoints plus exact rollback commands;
3. switch the configured/default endpoint once;
4. run a bounded post-switch identity/config-contract/control smoke without a
   second scientific workload;
5. verify persisted JobDesk provenance names the new producer and config
   contract;
6. exercise or dry-run the recorded endpoint rollback, then restore the
   accepted endpoint only if that check passes.

Promotion failure restores the old endpoint and leaves both versioned
environments intact for investigation.

## Acceptance matrix

| Problem | Completion evidence |
|---|---|
| Oversized JobDesk control adapter | Staging, launcher, state, projection, and artifact responsibilities have explicit ports/implementations; `SSHConfFlowClient` is orchestration-only |
| GUI repository access | Architecture test finds zero `.repository` access under `gui/`; existing GUI tests remain green |
| Cross-widget private access | `MainWindow` consumes a public immutable connection snapshot; zero child-page underscore-field access |
| SSH client repository leak | Control client receives `RunProjectionStore`; zero `coordinator.service.repository` access |
| Split JobDesk local commits | Intention-specific projection operations commit journal/provenance/projection atomically; crash injection never exposes a half decision |
| Session lifetime ambiguity | Run handles own independent factories/reference-counted leases; failure/cancel/concurrent refresh tests show no leak, double close, or unsafe shared channel |
| String/Any outcomes | Stable typed errors and typed refresh payloads are used by service, CLI, and GUI decisions |
| Submit import cycle | Import graph has no `run_service <-> submit_ownership` cycle; tests inject interval/factory explicitly |
| Duplicate config semantics | ConfFlow canonical parser is the only producer-semantic validator; legacy views are rule-free; JobDesk has no semantic mirror |
| Public v2 configuration compatibility | Exported constructor/attribute/Pydantic/serialization snapshots pass; canonical types are additive; any incompatible result is moved to an approved v3 plan |
| Canonical-model bypasses | Canonical constructors, mapping/YAML parsing, and round-trip serialization enforce identical invariants; typed canonical step payloads replace recognized raw dictionaries; `config` has no import from legacy `core.models` |
| Field/extension ambiguity | Every consumed field and alias has a checked-in classification; editor metadata and true extensions cannot silently affect or disappear from execution |
| Schema becomes a second validator | Schema is reproducibly generated from canonical field metadata; regeneration is clean; parser/schema fixtures and packaged hashes agree |
| Per-server contract ambiguity | Each run persists a verified config-contract/schema hash bound to server and executable identity; unsupported or swapped contracts fail before upload |
| Base install requires ConfFlow | JobDesk base-install tests pass without `chem`; matching local ConfFlow is optional and never overrides remote canonical acceptance |
| Offline validator divergence | JobDesk editor lint is schema-backed and warning-only; remote canonical validation alone decides semantic acceptance |
| Saved workflow loss | v0.5/v0.6 YAML, presets, nodegraphs, wizard metadata, aliases, advanced fields, and DAG inputs satisfy the declared lossless/versioned migration policy |
| Resume fingerprint regression | v2.0 calc/confgen fingerprints and workflow state remain readable; unknown generations fail without cleaning user artifacts |
| New configuration monolith | Parser, global/calc/confgen models, issues, serialization, schema generation, and legacy views have separate dependency-tested modules |
| Monolithic WorkflowSpec | Codec, migration, form mapping, editor lint, remote contract, validation, and storage are separated; compatibility facade is thin |
| Monolithic workflow engine | Planner, resume, executor, and finalizer have explicit typed boundaries; facade behavior is unchanged |
| `core -> blocks` inversion | Import fitness test reports zero violations |
| `calc -> blocks.refine` inversion | Neutral result model is owned outside `blocks`; zero violations |
| ExecutionService policy concentration | Pure validation/transition policy is separated while one service remains the state mutation owner |
| Control-worker concentration | Handoff validation, staging, lease, sidecars, and workflow invocation have explicit components; worker entry remains token-bound orchestration |
| Documentation drift | Current docs describe v2+/Phase F behavior; historical evidence is clearly labeled and unchanged |
| Runtime/source ambiguity | Installed JobDesk/ConfFlow identities, active source worktrees, configured endpoint, and default commands are documented and mutually consistent |
| Release bootstrap/deadlock | Phase 3/4 create candidates, Phase 5 creates one final producer candidate, Phase 6 publishes producer then consumer against published artifacts |
| Promotion before acceptance | Side-by-side candidate endpoints pass non-compute and authorized real-launcher acceptance before one production/default switch |

## Global rollback policy

- Every PR is independently revertible and leaves the preceding phase green.
- Structural refactors do not alter database schemas, control schemas, durable
  JSON shapes, remote directory layouts, or published artifact names.
- Configuration-contract rollout is producer-first. JobDesk does not pin a
  final producer identity until the producer artifact is published and its
  wheel, schemas, config-contract documents, and remote CI pass. Development CI
  may use a candidate without treating it as an approved production identity.
- Keep the last accepted JobDesk and ConfFlow versioned environments and wheel
  digests until post-promotion rollback verification is accepted.
- Never roll back by editing a published artifact, reusing a tag, weakening an
  accepted identity/hash, or enabling an unsupported producer pairing.
- Never roll back configuration work by silently discarding fields, rewriting
  a saved workflow, or cleaning an unknown calc/confgen fingerprint.
- Package publication does not authorize endpoint promotion. If real-launcher
  acceptance is unavailable, keep production on the previous endpoints.
- On ambiguous remote dispatch, reconcile; never submit twice as a rollback.
- Rollback never means enabling the retired legacy backend or restoring
  `confflow-agent`.

## Recommended execution order

1. Phase 0: baseline and fitness tests.
2. Phase 1: JobDesk control-adapter decomposition.
3. Phase 2: JobDesk application/GUI boundaries, typed outcomes, import cycle.
4. Phase 3: compatible ConfFlow canonical configuration and config-contract
   candidate; no final release.
5. Phase 4: chem-optional, per-server JobDesk configuration consumer and saved
   workflow migration candidate; no final release.
6. Phase 5: ConfFlow workflow/ExecutionService/control-worker decomposition and
   one final producer candidate.
7. Phase 6: documentation/environment closure, two-direction matrices,
   producer-first publication, consumer publication, side-by-side acceptance,
   separately authorized real workload, then production promotion.

No phase starts merely because the preceding code was written. It starts only
after the preceding phase's tests, independent review, commit/ref evidence,
and explicit authorization are recorded.
