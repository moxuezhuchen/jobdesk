# JobDesk / ConfFlow full remediation execution plan

Date: 2026-08-19

Original status on 2026-08-19: plan only; no implementation, release, endpoint
switch, or workload was authorized at that time.

Current execution status on 2026-08-31: all nine gates are complete. Immutable
releases are JobDesk `v0.7.10` at
`54f7735698f148371adb70397813c04ea569c245` and ConfFlow `v2.1.6` at
`45bfac11f721b2152eeff5ee26e50463fcc6f657`. Candidate 3 consumed exactly one
submit and completed one real G16 optimization with normal termination. The
shared ConfFlow source `.venv` was replaced and reverified with both historical
and timestamped rollback environments retained. Production was atomically
promoted to the exact released ConfFlow `2.1.6` environment; post-switch CLI and
JobDesk non-compute smokes, persisted promotion provenance, stable `2.0.0`
rollback, and G16 identity checks all passed.

Scope: `C:\dft\tool\jobdesk` and WSL `Ubuntu-24.04:/opt/ConfFlow`

This document is the current execution plan produced from the 2026-08-19
live review. It refreshes and operationalizes
`2026-08-11-post-phase-f-architecture-remediation.md`; that earlier document
remains historical design evidence. Where the two differ, this plan controls
execution order and gates.

## Final execution status addendum - 2026-08-31

This addendum reports current execution reality without rewriting the original
review, starting-point measurements, problem register, or normative gates
below. Historical counters and observations remain evidence of their capture
date, not claims about the current release or production endpoint.

| Gate | Current status | Remaining boundary |
|---|---|---|
| Gate 0 | Completed and recorded | Preserve exact refs and protected shared trees through final closeout. |
| Gate 1 | **Completed** | Shared `.venv` is a healthy source-bound ConfFlow `2.0.0` environment; external import, `pip check`, focused tests, identity checks, and independent review passed. The old environment remains at `.venv.rollback-20260831T1804-v200`; `.venv.previous-c6a4263` is also preserved. |
| Gate 2 | **Completed** | Enforced warning and 85% coverage gates remained green through final audit. |
| Gate 3 | **Completed** | Producer-owned canonical contract and compatibility facades passed release and final verification. |
| Gate 4 | **Completed** | Per-server contract binding, exact producer identity, remote validation, and compatibility matrices passed. |
| Gate 5 | **Completed** | SQLite-authoritative decisions, durable reconciliation, one-submit evidence, and zero redispatch passed. |
| Gate 6 | **Completed** | Engine/worker decomposition and state-ownership boundaries passed release and compatibility verification. |
| Gate 7 | **Completed** | Connection budgets, monitor separation, and GUI responsibility boundaries passed regression verification. |
| Gate 8 | **Completed** | Released artifacts and compatibility matrices passed; Candidate 3 completed the strict one-submit real G16 acceptance; production is ConfFlow `2.1.6`; CLI and JobDesk non-compute smokes, persisted promotion provenance, and stable `2.0.0` rollback verification passed. |

### Current acceptance and production boundary

- Candidate 3 run `jd0710-cf216-real-methane-candidate3-9c42f6a1` has one atomic
  submit marker, one submitted task, and a completed control trajectory.
- JobDesk downloaded the sole manifest-declared `g16_opt/output.xyz`. Same-run
  recovery evidence, without resubmission, verified the remote manifest,
  summary, output hash, and Gaussian log. The log records optimization
  completion and normal Gaussian 16 termination.
- `/opt/confflow-current` now points to
  `/opt/confflow-2.1.6-prod-venv/bin/confflow`; the producer reports build
  `45bfac11f721b2152eeff5ee26e50463fcc6f657`, wheel SHA-256
  `d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548`,
  and verified install provenance.
- `/opt/confflow-current.pre-v2.1.6-20260831T1807` points to the stable
  `/opt/confflow-2.0.0-prod-venv/bin/confflow` rollback. Promotion provenance is
  persisted at `/opt/confflow-promotions/20260831T1807-v2.1.6.json`.
- Post-switch capability, control, configuration, JobDesk probe/validation, and
  G16 before/after identity checks passed.

## 1. Outcome

Complete the remediation without weakening the working control protocol,
provenance checks, resume semantics, artifact safety, GUI contracts, or release
discipline. The finished system must have:

- reproducible clean development environments for both repositories;
- one producer-owned canonical workflow-configuration contract;
- a small orchestration-only JobDesk control client with recoverable local
  state transitions;
- a decomposed ConfFlow workflow engine and control worker;
- bounded, observable SSH connection usage;
- smaller GUI responsibilities without cross-widget or repository leakage;
- enforced coverage, warning, contract, packaging, and architecture gates;
- truthful current-product documentation and an independently reversible
  producer-first release path.

## 2. Verified starting point (historical; superseded as current status)

These facts were captured for Phase 0 and are preserved without alteration.
They are historical starting-point evidence, not permanent assumptions or a
description of the current release and production state. Use the execution
status addendum above for current reality.

### JobDesk

- Worktree: `C:\dft\tool\jobdesk`.
- Branch/HEAD: `codex/gui-ux-remediation` at
  `154ee77b065cd71787418be312700c996bf01c57`.
- Verified remote `main`: `2c6696b520e6fe345a12ed98035441aa4dfee729`.
- Worktree was clean and five commits ahead of `main` at review time.
- Ruff, mypy, build, offscreen GUI smoke, and the non-integration suite passed;
  the suite result was `1951 passed, 31 skipped, 6 deselected`.
- Measured coverage was about 84%; JobDesk CI does not currently enforce the
  same 85% gate used by ConfFlow.
- Coverage exposed test-side unclosed SQLite connections as
  `ResourceWarning`s.
- The configured WSL endpoint uses
  `/opt/confflow-2.0.0-prod-venv/bin/confflow`; the real SSH capability probe
  passed with capability schema 4 and control-worker support.

### ConfFlow

- Shared source tree: `/opt/ConfFlow`, `main` at
  `c6a4263bf3ec84669fd5279ec336b10ab2e18c9f`.
- The shared worktree contains an uncommitted 559-line modification to
  `tests/test_workflow_execution_adapter.py`. It must be preserved.
- The source `.venv` is stale: its editable package points to a missing
  `/mnt/c/tmp/...` checkout, and `.venv/bin/confflow --version` fails to import
  `confflow.main`.
- Tests run from the source directory only because the current directory wins
  import resolution; that is not a valid installed-environment proof.
- Dirty-tree suite: `1052 passed, 6 skipped`, coverage 85.06%.
- Clean clone of the exact HEAD: `1030 passed, 6 skipped`, coverage 84.57%,
  below `fail_under = 85` on Python 3.12. The dirty test file therefore contains
  required behavioral coverage and must be reviewed, not discarded.
- Pytest 9.1 reports unknown option `cache_dir` from `pyproject.toml`.
- Production is separate and healthy:
  `/opt/confflow-current` points to the versioned 2.0.0 environment. No source
  environment repair may mutate it.

## 3. Confirmed problem register

| ID | Problem | Primary phase |
|---|---|---|
| B1 | ConfFlow source `.venv` is bound to a missing checkout | 1 |
| B2 | Clean ConfFlow baseline misses the 85% coverage gate | 1 |
| B3 | Dirty ConfFlow coverage tests are valuable but unreviewed/uncommitted | 1 |
| B4 | Obsolete pytest `cache_dir` configuration emits warnings | 1 |
| B5 | JobDesk has no enforced project coverage gate and high-risk modules are near 70% | 2 |
| B6 | JobDesk tests leak SQLite connections | 2 |
| A1 | ConfFlow has duplicate configuration semantics in `config.models` and `core.models` | 3 |
| A2 | JobDesk maintains a partial offline semantic mirror of ConfFlow | 4 |
| A3 | `WorkflowSpec` combines codec, migration, form mapping, lint, and storage concerns | 4 |
| A4 | `SSHConfFlowClient` concentrates negotiation, state, staging, launch, reconciliation, and artifacts | 5 |
| A5 | `control_backend.json` and SQLite projection are sequential local commits | 5 |
| A6 | `run_workflow()` and `run_control_worker()` are orchestration monoliths | 6 |
| A7 | ConfFlow still has a `calc -> blocks` layer inversion around refine behavior | 6 |
| A8 | Run monitoring creates a separate long-lived SSH connection per watched run | 7 |
| A9 | Runs/Results and File Transfer pages remain oversized responsibility clusters | 7 |
| D1 | README and architecture documents describe stale model/schema/session ownership | 8 |
| R1 | Candidate, release, side-by-side install, workload acceptance, and promotion need separate authorization | 8 |

No item above alone proves a current production data-loss or duplicate-submit
bug. The plan preserves the existing successful response-loss,
reconciliation, cancellation, provenance, and artifact-safety behavior while
removing the architectural conditions that make future defects likely.

## 4. Protected invariants and prohibitions

These apply to every phase.

1. Do not reset, stash, clean, switch, move, or delete the shared
   `/opt/ConfFlow` worktree or its dirty test change. The only planned
   exception is the separately authorized, rollback-preserving `.venv`
   replacement in Task 1.5 after the dirty test patch is preserved and
   reviewed; it may not change tracked source files.
2. Do not mutate `/opt/confflow-2.0.0-prod-venv`, `/opt/confflow-current`, the
   configured JobDesk `wsl` endpoint, or accepted producer identity during
   development.
3. Use clean isolated worktrees/clones from recorded refs. Each repository has
   its own branch, PR, validation, and rollback.
4. Do not change `confflow.control.v1`, capability schema v4, worker handoff,
   event ordering, durable run layout, artifact manifest rules, launcher
   marker, idempotency key, or cancellation semantics in structural PRs.
5. ConfFlow remains authoritative for remote aggregate state, revisions,
   events, checkpoints, tokens, and artifact manifests. JobDesk remains
   authoritative for local journal, approved provenance, handoff evidence,
   launcher reconciliation, and monotonic projection.
6. Unsupported producer identity, contract version/hash, fingerprint,
   revision, artifact path, symlink, size, or digest fails closed.
7. No fallback to the retired legacy backend, no duplicate dispatch after an
   ambiguous response, and no silent workflow-field loss.
8. Do not lower coverage thresholds, add broad omissions, blanket-ignore
   warnings, or exclude the difficult modules to make a gate pass.
9. Do not run g16, ORCA, or any scientific workload without separate explicit
   authorization. Synthetic protocol fixtures are not real-launcher evidence.
10. Commit, push, PR, merge, tag, publish, install, endpoint switch, and
    production promotion are independent authorization boundaries.

## 5. Delivery model

Use test-first, one-repository PRs. Every implementation PR follows:

```text
approved clean base
  -> failing characterization/fitness test
  -> smallest behavior-preserving change
  -> targeted tests
  -> full repository gates
  -> independent full-diff review
  -> fix and re-review until approved
  -> explicit authorization for publish/merge/promotion
```

Recommended PR sequence:

1. `CF-0`: source-environment and clean coverage baseline.
2. `JD-0`: warnings, coverage baseline, and architecture fitness tests.
3. `CF-1`: canonical configuration contract and compatibility facades.
4. `JD-1`: per-server configuration consumer and `WorkflowSpec` split.
5. `JD-2a`: behavior-compatible control-client decomposition; no durable
   format or database change.
6. `JD-2b`: SQLite-authoritative local control decision journal and compatible
   JSON projection.
7. `CF-2`: workflow engine, control worker, and layer decomposition.
8. `JD-3`: connection budget and GUI responsibility split.
9. `DOC/REL`: two-direction matrix, documentation, release, acceptance, and
   separately authorized promotion.

Do not combine structural moves with a wire schema, database schema, durable
format, or user-visible behavior change. If a phase discovers that such a
change is necessary, stop and create a separately approved migration plan.

## 6. Phase 0 - freeze evidence and fitness functions

Repositories: both. Behavior change: none.

### 0.1 Create isolated implementation trees

- Resolve remote refs without updating either shared worktree.
- Create a clean JobDesk worktree and a clean ConfFlow clone/worktree at the
  exact approved bases.
- Record HEAD, branch, worktree status, Python version, installed distributions,
  dependency source, executable realpath, capability/provenance JSON, wheel
  digest, config files, and test commands.
- Establish one checked-in lock policy before recreating environments:
  ConfFlow Linux/x86_64 lock files for Python 3.10, 3.11, 3.12, and 3.13;
  JobDesk Windows/x86_64 lock files for Python 3.11, 3.12, and 3.13. Generate
  each lock in its real interpreter/platform environment from `pyproject.toml`,
  include hashes, record the resolver version, and reject manual lock edits.
- Hash the dirty ConfFlow test patch and save a read-only diff artifact; do not
  apply it until Phase 1 review.
- Recheck the shared worktrees after setup and prove they are unchanged.

### 0.2 Add/confirm characterization coverage

JobDesk must lock:

- prepare/execute response-loss reconciliation and duplicate refusal;
- launcher marker and `dispatching -> submitted` recovery;
- bounded marker-absence reconciliation and the existing
  `confirm_unresolved_dispatch_not_accepted()` proof path, including its
  evidence, audit timestamp, attempt limit, and retry authorization;
- worker handoff digest/path containment and one-task enforcement;
- capability, executable, producer, wheel, and artifact provenance rejection;
- monotonic revision/event cursor behavior and stale snapshot rejection;
- pause/resume/cancel, including confirmation that cancellation reaches the
  worker/process path rather than only changing local UI state;
- artifact path, symlink, size, digest, temp-file, and atomic-placement safety;
- session lease ownership across submit failure, refresh, cancel, and handles;
- saved workflows, presets, nodegraph metadata, aliases, DAG inputs, and
  resume/fingerprint fixtures.

ConfFlow must lock:

- `ExecutionService` transition/CAS behavior and event order;
- token/lease ownership, abandoned launch recovery, SQLite reconnect;
- `confflow.workflow.engine.run_workflow()` callback order, checkpoints,
  resume, finalization, sidecars, and artifact manifests;
- `confflow.control_worker.run_control_worker()` handoff validation, staging,
  cancellation, token consumption, and crash recovery;
- all public constructors, attributes, validation errors, serialization, and
  imports exposed through `confflow.config` and `confflow.core.models`;
- v2.0 canonical dictionaries, calc digests, confgen signatures, workflow
  state, manifests, and resume decisions.

### 0.3 Architecture fitness tests

Add narrow AST/import tests that reject new violations:

- JobDesk GUI importing repositories or remote implementations;
- a page reading another widget's private fields;
- control collaborators reaching `RunCoordinator.service.repository`;
- code outside the designated local decision boundary writing control journal,
  provenance, and task projection independently;
- ConfFlow `core` or `calc` importing from `blocks`;
- new ConfFlow code importing `core.models` instead of the canonical config API;
- engine/worker entrypoints regaining extracted responsibilities;
- monitor code opening unbudgeted per-run sessions.

Existing violations receive only exact, temporary allowlist entries. Each
later phase removes its own entry.

Gate 0: both isolated trees are clean; baselines are reproducible or their
failure is recorded; protected paths are unchanged; every confirmed problem
has a test or a named evidence artifact.

## 7. Phase 1 - repair the ConfFlow development baseline

Repository: ConfFlow. Production environment: untouched.

### 1.1 Review and upstream the dirty behavioral tests

- Apply the preserved diff for `tests/test_workflow_execution_adapter.py` to
  the isolated `CF-0` branch.
- Review every new case against `run_workflow_through_service()` and the real
  `run_workflow()` call chain. Remove duplicated or implementation-coupled
  assertions, but preserve missing behavior coverage.
- Split the patch into focused tests if needed. Do not copy `__pycache__` or
  other source-tree residue.
- Demonstrate which branches raise clean coverage from 84.57% to at least 85%.

### 1.2 Recreate a source-owned environment

- Add checked-in, hash-verified ConfFlow development locks for Linux/x86_64
  Python 3.10-3.13. Generate them in the matching interpreter containers from
  `pyproject.toml[project]`, the `dev` extra, and `build-system.requires`;
  record the exact resolver version and regeneration command, and verify a
  clean regeneration diff.
- Build a new environment from the isolated source tree with
  `pip install --require-hashes -r <matching-lock>` followed by
  `pip install --no-deps --no-build-isolation -e .`. Use the locked build
  backend with `python -m build --no-isolation`; unbounded lower-version
  metadata alone is not reproducibility evidence.
- Verify editable metadata resolves to that exact isolated checkout.
- Verify `python -c` imports `confflow` and `confflow.main` from the exact
  checkout, and verify the installed `confflow` console entrypoint resolves to
  `confflow.main:main`. Do not require `python -m confflow`: the current public
  package has no `confflow.__main__` module.
- Run `pip check`, installed-wheel smoke, and a subprocess outside the source
  directory so current-directory imports cannot mask packaging errors.
- Keep this isolated environment as the acceptance source until Task 1.5; do
  not mutate the shared stale `.venv` while its consumers are unknown.

### 1.3 Remove obsolete pytest configuration

- Replace/remove the unsupported `cache_dir` option in `pyproject.toml`.
- Direct transient cache/base-temp outputs to explicit paths in commands or
  CI configuration supported by the pinned pytest version.
- Add a targeted warning gate proving no unknown pytest config option remains.

### 1.4 Establish a clean, cross-version coverage gate

- Preserve `fail_under = 85` and current coverage source/omit rules.
- Mirror the current CI matrix: run the full suite on Python 3.12, and run it
  with `tests/test_install_release_wheel.py` excluded on Python 3.10, 3.11,
  and 3.13. Python 3.11 owns the authoritative coverage gate.
- On Python 3.11, run Ruff, mypy, and Black against the changed Python files
  selected from the approved base using the same diff filter as CI.
- Add behavior tests until every clean matrix entry passes and the Python 3.11
  coverage result is at least 85%. Do not count a dirty shared worktree as
  acceptance evidence.
- Publish coverage XML/term reports and list risk hotspots; do not use a total
  percentage as a substitute for workflow/worker branch coverage.

### 1.5 Replace the broken shared-tree `.venv` under a separate authorization

After the dirty test patch has been reviewed and preserved in the candidate:

1. inventory the stale `.venv` realpath, editable metadata, running processes,
   scheduled jobs, shell profiles, and rollback value;
2. obtain explicit filesystem authorization before changing `/opt/ConfFlow`;
3. build `/opt/ConfFlow/.venv-candidate-<short-head>` from the matching hashed
   lock and bind it to the unchanged `/opt/ConfFlow` source;
4. prove imports, `confflow --version`, `pip check`, targeted tests, and a
   subprocess outside the source tree;
5. rename the old `.venv` to a timestamped rollback path, rename the verified
   candidate to `.venv`, and immediately repeat identity tests;
6. preserve the rollback environment until the full Phase 1 matrix and an
   independent review pass. Deletion requires another explicit authorization.

If authorization is withheld, mark `/opt/ConfFlow` and its stale `.venv` as a
protected historical source environment, record the new canonical development
tree, and do not claim B1 fixed; Gate 1 remains incomplete rather than silently
substituting the isolated environment.

Gate 1: a clean exact-ref ConfFlow checkout has a healthy source environment,
entrypoint and wheel imports agree, pytest emits no config warning, the full
CI-shaped suite passes on Python 3.10-3.13, Python 3.11 coverage is at least
85% without threshold/omit changes, and the shared source environment has been
replaced and reverified under authorization. `/opt/confflow-current` is
unchanged and the old source environment remains available for rollback.

## 8. Phase 2 - harden the JobDesk quality baseline

Repository: JobDesk. Behavior change: none except deterministic resource
cleanup in tests.

### 2.1 Close test-owned SQLite resources

- Locate every test helper that uses `sqlite3.connect()` or a repository
  context manager as though it closed the connection.
- Use explicit close/finally or a closing helper; keep production repository
  ownership unchanged unless a production leak is independently demonstrated.
- Run SQLite-focused tests with `ResourceWarning` promoted to an error.

### 2.2 Add an enforced coverage ramp

- Capture a clean per-module report using the same Python and dependencies as
  CI.
- Add `pytest-cov` to JobDesk's `dev` extra and to the checked-in Windows
  Python 3.11-3.13 hashed locks. A clean `pip install -e ".[dev]"` environment
  must understand every required coverage argument without a CI-only install
  step.
- Generate each JobDesk lock in its matching Windows interpreter, record the
  resolver version/regeneration command, and verify clean hash-locked installs
  in `.venv-py311`, `.venv-py312`, and `.venv-py313` before running the matrix.
- Add missing behavior tests first for
  `services/ssh_confflow_client.py`, `services/ssh_confflow_control.py`, and
  `core/workflow_spec.py`.
- Set the repository gate to 85% only after the branch reaches it. Future PRs
  may not reduce the total or reduce coverage of a touched high-risk module
  without an explicit reviewed exception.
- Keep optional integration and real-workload evidence separate from unit
  coverage; they are not substitutes for one another.

### 2.3 Protect the current GUI contract

- Retain offscreen checks for repeat success, failure then recovery, uncaught
  Qt callbacks, object names, accessibility labels, focus, and busy-state
  cleanup.
- Add architecture assertions before the page splits in Phase 7 so refactors
  cannot reintroduce direct repository/remote access.

Gate 2: clean non-integration tests pass without SQLite resource warnings,
JobDesk enforces at least 85% coverage, the high-risk paths have direct
behavior tests, and the 16-step GUI smoke remains green.

## 9. Phase 3 - create one canonical ConfFlow configuration contract

Repository: ConfFlow. This creates a candidate only. It does not change
`confflow.control.v1` or capability schema v4.

### 3.1 Freeze compatibility and make the SemVer decision

- Snapshot every public type exported by `confflow.config` and
  `confflow.core.models`, including constructor signatures, accepted values,
  attributes, Pydantic methods/fields, exceptions, equality, and serialization.
- Inventory all fields/aliases/extensions consumed by the engine, dry-run,
  config-show, worker, JobDesk, examples, and persisted workflows.
- If rule-free compatibility facades cannot preserve the v2 surface, stop and
  propose v3. Do not hide a breaking change in a minor release.

### 3.2 Add the canonical package

The following are proposed new modules, not current APIs:

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

- Raw YAML/mappings enter through one parser.
- Canonical typed objects own environment-independent defaults, aliases,
  coercion, cross-field rules, and normalized serialization.
- Runtime preflight alone checks executables, paths, permissions, chemistry,
  and other environment facts.
- `confflow/config/models.py` and `confflow/core/models.py` become rule-free
  compatibility views that delegate to the canonical parser.
- Engine, dry-run, config-show, rerun, and worker consume canonical types and
  may not reinterpret recognized raw values.

### 3.3 Generate and expose a versioned contract

- Generate a packaged JSON Schema from the same metadata used by the parser.
- Regeneration must be deterministic and fail CI on a dirty diff.
- Add new, independent commands:

```text
confflow config contract --json
confflow config validate --json --stdin
```

- Define a new typed response/parser for this interface. Do not add an
  undeclared `configuration_contract` field to JobDesk's current
  `ConfFlowCapabilities` or silently alter capability schema v4.
- Bind contract response schema, workflow schema version/hash, semantic
  version, and producer provenance.
- Validation performs no workload or environment probe and emits exactly one
  machine-readable document on stdout.

### 3.4 Preserve durable compatibility

- Use immutable v2.0 fixtures for calc/confgen fingerprints, workflow state,
  manifests, aliases, defaults, and resume decisions.
- Read understood legacy generations before declaring data stale.
- Unknown/ambiguous generations fail with a typed error and never delete,
  rename, or rewrite artifacts.
- Any necessary fingerprint/manifest change becomes a separate durable-format
  migration PR with forward/backward readers and rollback evidence.

Gate 3: one parser decides semantics; old public views retain characterized v2
behavior; schema/parser/package parity passes; old resume/fingerprint fixtures
are unchanged; candidate wheel subprocess tests pass outside the source tree.

## 10. Phase 4 - make JobDesk a per-server contract consumer

Repository: JobDesk. Depends on an accepted Phase 3 producer candidate.

### 4.1 Discover and persist the selected contract

- Add a proposed `ConfigurationContractClient` application port and a typed
  response model distinct from `ConfFlowCapabilities`.
- Resolve the contract through the selected server/executable; bind it to
  server ID, executable realpath, producer provenance, response schema, and
  workflow schema hash.
- Cache only verified immutable documents. Invalidate on server, executable,
  producer, wheel, response-schema, or schema-hash change.
- Permit an exact, checked-in fallback only for the approved 2.0.0 producer
  that predates the command. Any other missing/unsupported contract fails
  before workload upload.

### 4.2 Keep local authoring chem-optional

- Base JobDesk uses the verified schema for structural editor feedback.
- A matching local `chem` install may improve diagnostics, but it is never
  authoritative for a remote server and never overrides a mismatch.
- Remote `config validate` is the final semantic acceptance gate before
  upload/prepare.
- Remove duplicated semantic rules from `_confflow_validation.py` only after
  parity and fallback tests prove no supported workflow loses diagnostics.

### 4.3 Split `WorkflowSpec` behind a compatibility facade

Proposed responsibilities:

- document model and version migration;
- YAML/JSON codec;
- form/nodegraph mapping;
- schema-backed editor lint;
- remote canonical validation;
- storage/preset metadata.

Keep `core.workflow_spec.WorkflowSpec` as a thin compatibility facade until all
callers migrate. Preserve saved v0.5/v0.6 workflows, aliases, advanced fields,
wizard metadata, DAG inputs, and unknown extension data according to an
explicit lossless/versioned policy.

### 4.4 Run the producer/consumer matrix

Test JobDesk stable/candidate, base/chem installs, stable 2.0.0 producer, and
the candidate producer. Report separately:

- legacy/public authoring compatibility;
- configuration-contract compatibility;
- expected fail-closed production admission for an unapproved candidate.

Gate 4: every run persists the selected verified contract; base install works
without ConfFlow; local and remote mismatch cannot silently select local
semantics; saved workflows round-trip without loss; unsupported producers fail
before upload.

## 11. Phase 5 - decompose JobDesk control and local decisions

Repository: JobDesk. Deliver this phase as two independently reviewed PRs.

### 5.1 Extract collaborators from `SSHConfFlowClient`

Keep current application protocols and introduce only missing ports. Proposed
implementation names, subject to import-cycle review:

```text
services/confflow_control_handoff.py
services/confflow_control_launcher.py
services/confflow_control_artifacts.py
services/confflow_control_run_state.py
```

- `confflow_control_handoff`: path containment, private directory creation,
  canonical envelope/digest, and staging.
- `confflow_control_launcher`: scheduler/resource selection, script assembly,
  dispatch, launcher marker, ambiguous-response reconciliation, bounded marker
  checks, and proof-based operator resolution. Preserve the public
  `confirm_unresolved_dispatch_not_accepted()` behavior until a reviewed
  application command replaces it; never turn marker absence into blind retry.
- `confflow_control_artifacts`: terminal mapping, remote metadata validation,
  SFTP temp staging, integrity checks, and atomic local placement.
- `confflow_control_run_state`: typed serialization/recovery around existing
  `control_backend.json` compatibility.
- `SSHConfFlowClient` retains negotiation, orchestration, and run-handle
  construction only. It cannot reach a repository through a coordinator.

Golden serialized bytes, launch scripts, marker semantics, remote paths, and
download layouts must remain identical in the structural PR.

Gate 5a: the `JD-2a` extraction passes all existing byte/behavior fixtures,
including definitive scheduler rejection, response loss, bounded unresolved
dispatch, operator proof, and retry authorization. It changes no SQLite schema,
operation payload, JSON bytes, or user-visible behavior.

### 5.2 Make the existing SQLite operations journal authoritative

Do not claim filesystem JSON plus SQLite can be one physical transaction.
The `JD-2b` design is fixed as follows:

- reuse the existing schema-v6 `operations` table; do not add a table or bump
  the SQLite schema solely for this work;
- store one deterministic `kind="confflow_control"` operation per run with a
  versioned canonical payload, local revision, expected previous revision,
  decision ID, and desired JSON projection digest;
- add a proposed intention-specific repository operation such as
  `commit_confflow_control_decision(...)` that uses one `BEGIN IMMEDIATE`/
  compare-and-swap transaction to commit the control operation, accepted
  provenance, operation journal outcome, and monotonic task projection;
- make the SQLite operation authoritative after import. Keep
  `control_backend.json` as a byte-compatible derived projection for rollback
  and old readers;
- on first access to a run with no SQLite control operation, validate the
  legacy JSON, bind it to the existing run/provenance, and import it
  idempotently. Never delete or rewrite the source JSON during import;
- after an SQLite commit, atomically replace the JSON projection. If that
  write fails or its digest is stale, retain the committed desired digest and
  regenerate before any later dispatch decision. JSON failure cannot roll
  back a proven remote submission or authorize redispatch;
- before rolling back to pre-`JD-2b` code, require every JSON projection to
  match its authoritative SQLite digest. The old code then reads the preserved
  compatible JSON without understanding the new operation kind.

Add crash injection before/after journal CAS, provenance/task projection,
remote launcher marker, SQLite commit, JSON temp write, and JSON replace.
Migration and recovery must be idempotent and must never redispatch an
unresolved launch.

### 5.3 Make session ownership explicit

- Short operations borrow scoped leases.
- A returned `RemoteRunHandle` retains only immutable reference data and a
  session provider/factory. It never owns a raw SSH/SFTP client or a
  reference-counted/live lease.
- Every `status`, `events`, `cancel`, `resume`, `artifacts`, and `download`
  operation acquires and releases its own scoped `SessionPool` lease, using
  `need_sftp=False` unless file transfer is required.
- Close/cancel/error are idempotent and cannot close another operation's lease.
- No mutable channel is shared concurrently unless its adapter guarantees it.

Gate 5b: `SSHConfFlowClient` is orchestration-only; the existing operations
schema remains version 6 unless unrelated approved work has already advanced
it; migration/import/rollback fixtures pass; crash injection never exposes an
unrecoverable decision; response loss never causes duplicate dispatch; handle
tests show no retained lease, leak, or double-close; artifact safety is
unchanged.

## 12. Phase 6 - decompose ConfFlow execution and repair layers

Repository: ConfFlow. Depends on Phase 3 canonical types.

### 6.1 Split `run_workflow()`

Introduce internal typed boundaries for:

- preparation/planning and DAG validation;
- resume/checkpoint planning;
- one-step execution and lineage collection;
- finalization, statistics, sidecars, and artifact publication.

Keep `confflow.workflow.engine.run_workflow()` as the compatible facade.
Callback order, failure classification, resume decisions, output names,
digests, events, and artifact manifests must match golden fixtures.

### 6.2 Split `run_control_worker()`

Extract internal components for:

- handoff envelope/path/digest validation;
- task/run staging and containment;
- token/lease acquire/renew/release;
- cancellation beacon and child-process stop supervision;
- workflow invocation/resume loop;
- terminal sidecar/event publication.

Keep `confflow.control_worker.run_control_worker()` token-bound orchestration.
Cancellation acceptance must prove that the running child/process chain stops
or reaches a documented terminal confirmation; a local flag alone is not
success.

### 6.3 Remove layer inversion

- Move neutral refine request/result/port types to a lower layer owned by
  `calc` or a neutral domain module.
- Inject the refine implementation at the composition boundary.
- Add import fitness tests proving `core` and `calc` do not import `blocks`.
- Keep public re-exports only where required by characterized compatibility.

### 6.4 Keep `ExecutionService` as the mutation owner

Extract pure transition/validation policy only. Do not create a second service
that can mutate the same aggregate, revision, or event stream independently.

Gate 6: engine and worker entrypoints are small orchestrators; exact workflow,
resume, cancellation, event, sidecar, and artifact fixtures pass; import
inversions are zero; full clean coverage remains at least 85%.

## 13. Phase 7 - bound connections and split GUI responsibilities

Repository: JobDesk.

### 7.1 Introduce an observable connection budget

- Keep `SessionPool` as the owner of ordinary per-server serialized SSH/SFTP
  leases.
- Introduce a proposed monitor transport/provider that distinguishes
  long-lived event-tail channels from short operations.
- Enforce configurable per-server and global watcher limits, bounded queues,
  idle expiry, exponential backoff with jitter, and cancellation on page/run
  disposal.
- Expose metrics/log fields for active, queued, reconnecting, and rejected
  watchers without logging secrets.
- Test SSH banner loss, MaxStartups-style refusal, reconnect cursor replay,
  server deletion, rapid page switching, and many watched runs.

Do not multiplex concurrent mutable reads through one unsafe Paramiko channel.
The goal is bounded ownership, not a shared-channel shortcut.

### 7.2 Split oversized pages through application-facing controllers

For Runs/Results, separate run list/filtering, selection/detail projection,
actions, artifact presentation, and monitor subscription. For File Transfer,
separate browser model, transfer queue, remote edit/download, and connection
snapshot presentation where still combined.

- Pages depend on application ports/controllers, not repositories or SSH.
- `MainWindow` and sibling pages exchange immutable public snapshots/events,
  never underscore-prefixed widget state.
- Qt objects are touched only on the GUI thread.
- Preserve public signals, object names, accessibility labels, focus order,
  selection restoration, busy gates, and repeat/failure-recovery behavior.

Gate 7: connection counts remain within budget under stress; cursor replay
loses/duplicates no events; pages have no repository/remote imports or
cross-widget private access; full GUI tests and offscreen smoke pass without
uncaught callbacks.

## 14. Phase 8 - documentation, CI, release, and promotion

Repositories: both. Every subsection is a separate authorization gate.

### 8.1 Correct current-product documentation

- README must no longer claim that JobDesk and ConfFlow currently consume the
  same Pydantic models; describe the final producer-owned contract instead.
- `docs/architecture.md` must list the real current schema version and modules,
  distinguish ordinary pooled leases from monitor transports, and describe
  the actual state-ownership boundary.
- Document four identities separately: shared source tree, isolated candidate,
  released package, and configured production executable.
- Mark old phase documents as historical without rewriting their evidence.

### 8.2 Required clean validation

JobDesk, from its repository environment:

```powershell
.venv-py311\Scripts\python.exe -m ruff check . --no-cache
.venv-py311\Scripts\python.exe -m mypy src --cache-dir .mypy_tmp_full
.venv-py311\Scripts\python.exe -m pytest tests/ -q `
  --ignore=tests/integration --basetemp .pytest_tmp_py311 -p no:cacheprovider
.venv-py312\Scripts\python.exe -m pytest tests/ -q `
  --ignore=tests/integration --basetemp .pytest_tmp_py312 -p no:cacheprovider
.venv-py313\Scripts\python.exe -m pytest tests/ -q `
  --ignore=tests/integration --basetemp .pytest_tmp_py313 -p no:cacheprovider
.venv-py311\Scripts\python.exe -m pytest tests/ `
  --ignore=tests/integration --cov=jobdesk_app --cov-report=term-missing `
  --cov-report=xml:.coverage_full.xml --cov-fail-under=85 `
  --basetemp .pytest_tmp_cov -p no:cacheprovider
.venv-py311\Scripts\python.exe scripts/smoke_gui_offscreen.py
.venv-py311\Scripts\python.exe -m build --no-isolation --outdir .build_full
```

ConfFlow, from its isolated Linux/ext4 environment:

Set `APPROVED_BASE` to the exact Phase 0 base commit before selecting changed
Python files.

```bash
.venv-py311/bin/python -m ruff check . --no-cache
.venv-py311/bin/python -m mypy confflow --cache-dir /tmp/confflow_mypy_full
mapfile -t changed_python < <(git diff --name-only --diff-filter=ACMR \
  "$APPROVED_BASE" HEAD -- '*.py')
if ((${#changed_python[@]})); then
  .venv-py311/bin/python -m black --check "${changed_python[@]}"
fi
.venv-py310/bin/python -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_install_release_wheel.py --basetemp /tmp/confflow_pytest_py310
.venv-py311/bin/python -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_install_release_wheel.py --basetemp /tmp/confflow_pytest_py311
.venv-py312/bin/python -m pytest -q -p no:cacheprovider \
  --basetemp /tmp/confflow_pytest_py312
.venv-py313/bin/python -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_install_release_wheel.py --basetemp /tmp/confflow_pytest_py313
.venv-py311/bin/python -m pytest tests/ -q -p no:cacheprovider \
  --ignore=tests/test_install_release_wheel.py \
  --cov=confflow --cov-report=term-missing \
  --cov-report=xml:/tmp/confflow_coverage.xml --cov-fail-under=85 \
  --basetemp /tmp/confflow_pytest_cov
.venv-py311/bin/python -m build --no-isolation --outdir /tmp/confflow_dist
```

Run `pip check` in every matrix environment. Also run installed-wheel
subprocess tests outside each source tree, schema regeneration/dirty-diff
checks, package-data verification, and both repositories' contract workflows.

### 8.3 Two-direction compatibility gate

Remote CI must publish separate results for:

- ConfFlow candidate x JobDesk stable public/config compatibility;
- JobDesk candidate x stable producer and final candidate;
- JobDesk base and chem installs;
- matching and mismatched local ConfFlow installations;
- saved-workflow and old-run fingerprint/resume corpus;
- control schemas, config response schemas, generated workflow schema hashes,
  wheel package data, provenance, and executable identity;
- expected fail-closed admission of an unapproved candidate.

Local green tests are supporting evidence, not a substitute for both remote
workflows.

### 8.4 Producer-first release without promotion

1. Revalidate exact clean ConfFlow HEAD and all gates.
2. Build/tag/publish ConfFlow from a clean release tree only after explicit
   authorization; record wheel/sdist hashes, SBOM/attestation, schemas, and
   provenance.
3. Keep production/default endpoints unchanged.
4. Pin the JobDesk candidate to the exact published producer artifact and
   rerun the matrix.
5. Build/tag/publish JobDesk only after a second explicit authorization.

Never reuse a failed tag or mutate a published artifact; fix forward.

### 8.5 Side-by-side non-compute acceptance

Install released packages into new versioned environments and use explicit
temporary candidate endpoints/state roots. Verify identity, config contract,
remote validation, prepare/execute fixture path, token consumption, reconnect,
cursor replay, status/events/cancel/resume, artifact validation/download,
saved-workflow migration, old fingerprint readability, and rollback commands.

Stop on the first failed gate. Do not switch launchers, patch an installed
environment, relax a criterion, or update production.

### 8.6 Separately authorized real-launcher acceptance and promotion

Only with explicit authorization, run one bounded JobDesk submission through
one supported real launcher and g16 or ORCA:

```text
JobDesk submit
  -> contract discovery and canonical validation
  -> control prepare/execute
  -> released worker token/lease
  -> real workflow step
  -> reconnect/events/status
  -> artifact manifest and JobDesk download
```

Do not retry automatically after failure. Promotion requires a further
authorization, one endpoint switch, a non-compute post-switch smoke, persisted
new provenance, and verified rollback. Without workload authorization, record:

`RELEASED AND SIDE-BY-SIDE VERIFIED; PRODUCTION PROMOTION NOT AUTHORIZED`

Gate 8: docs describe reality, both remote matrices pass, released artifacts
are reproducible and attested, side-by-side acceptance passes, and production
remains unchanged until the separate workload and promotion gates are met.

## 15. Phase acceptance matrix

| Problem | Required completion evidence |
|---|---|
| Stale ConfFlow source environment | Clean isolated environment passes; separately authorized shared `.venv` replacement passes identity checks and retains rollback, otherwise Gate 1 remains incomplete |
| Dirty-only ConfFlow coverage | Reviewed tests committed in candidate; clean Python 3.11/3.12 runs both meet 85% |
| Pytest config warning | No unknown config option under the pinned pytest version |
| JobDesk warnings/coverage | No test-owned SQLite warnings; enforced 85% gate; direct high-risk behavior tests |
| Duplicate config semantics | One canonical parser; legacy views contain no independent rules |
| Contract discovery | Separate typed config interface, schema hash and producer binding; capability v4 unchanged |
| Offline validator drift | Local lint is schema-backed/advisory; remote producer validation decides acceptance |
| Saved workflow/fingerprint risk | Lossless migration corpus; understood legacy readers; unknown generations fail without cleanup |
| Oversized JobDesk control client | Extracted typed collaborators; client is orchestration-only |
| Split local commit risk | One SQLite decision transaction or durable intent/recovery protocol passes every crash point |
| Duplicate dispatch risk | Response-loss and unresolved-launch tests prove zero redispatch |
| Proof-based dispatch recovery | Bounded marker checks plus audited operator non-acceptance evidence are required before retry authorization |
| Run-handle session lifetime | Handles retain no session/lease; every operation acquires and releases an appropriate scoped pool lease |
| Engine/worker monoliths | Typed planner/resume/executor/finalizer and worker components; facade fixtures unchanged |
| Layer inversion | Import tests report zero `core/calc -> blocks` edges |
| Cancellation ambiguity | Acceptance proves worker/child stop or explicit terminal confirmation |
| SSH watcher scale | Per-server/global budgets, observable queues, backoff, and stress tests pass |
| GUI concentration | Controllers/models own responsibilities; no repository/remote/private-widget leaks |
| Documentation drift | Current docs match live schema, modules, ownership, identities, and release state |
| Promotion risk | Published artifact, side-by-side, real launcher, and endpoint switch remain separate gates |

## 16. Stop conditions

Stop the active phase and request a new decision if any of these occurs:

- a protected worktree or production endpoint changes unexpectedly;
- a compatibility facade cannot preserve a public v2 behavior;
- a durable schema/fingerprint/manifest migration becomes necessary;
- response-loss reconciliation could permit a second dispatch;
- cancellation cannot prove actual worker/process termination semantics;
- clean coverage requires lowering a threshold or broad exclusion;
- installed-wheel behavior differs from source-tree behavior;
- a candidate cannot be accepted without weakening identity or hash checks;
- real launcher/workload execution or production promotion is required but not
  explicitly authorized.

## 17. Definition of done

The remediation is complete only when all nine phase gates pass on exact
recorded refs, every temporary architecture allowlist is removed, clean
installed artifacts pass both repositories' remote compatibility matrices,
documentation matches the final deployed reality, rollback is recorded and
verified, and an independent final review reports no unresolved blocker.

Implementation completion is not release completion. Release completion is
not production promotion. If the real-launcher or promotion authorization is
withheld, the technical result stops at the exact accepted gate and says so.
