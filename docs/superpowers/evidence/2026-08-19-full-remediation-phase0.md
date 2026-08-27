# Full remediation Phase 0 evidence

Captured: 2026-08-19 Asia/Shanghai

Plan source:
`C:\dft\tool\jobdesk\docs\superpowers\plans\2026-08-19-jobdesk-confflow-full-remediation.md`

This ledger records evidence only. It is not release, merge, endpoint-switch,
or workload authorization.

## Protected state

### JobDesk shared worktree

- Path: `C:\dft\tool\jobdesk`
- Branch: `codex/gui-ux-remediation`
- HEAD: `154ee77b065cd71787418be312700c996bf01c57`
- User-owned residue: the remediation plan is untracked.
- Verified remote `main`: `2c6696b520e6fe345a12ed98035441aa4dfee729`.

### ConfFlow shared source

- Path: `/opt/ConfFlow`
- Branch: `main`
- HEAD and verified remote `main`:
  `c6a4263bf3ec84669fd5279ec336b10ab2e18c9f`.
- Preserved dirty file: `tests/test_workflow_execution_adapter.py`.
- No implementation agent may write this tree.

### ConfFlow production

- `/opt/confflow-current` resolves to
  `/opt/confflow-2.0.0-prod-venv/bin/confflow`.
- Version: `2.0.0`.
- Capability schema: `4`.
- DAG and control worker: enabled.
- Producer build: `69819350d340a6aeccf95aa175edfd1c3f63404b`,
  clean.
- Wheel: `confflow-2.0.0-py3-none-any.whl`.
- Wheel SHA-256:
  `04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f`.
- Production executable and endpoint remain protected.

## Isolated implementation trees

### JobDesk candidate

- Path:
  `C:\dft\tool\jobdesk\.worktrees\jobdesk-full-remediation-154ee77-20260819`
- Branch: `codex/full-remediation-20260819-local`
- Base: `154ee77b065cd71787418be312700c996bf01c57`
- The first `C:\tmp` worktree was owned by the interactive Windows account and
  was not writable by subagents. It remains clean and is not used for work.

### ConfFlow editable candidate

- Windows path:
  `C:\dft\tool\jobdesk\.worktrees\confflow-full-remediation-c6a4263-20260819`
- WSL path:
  `/mnt/c/dft/tool/jobdesk/.worktrees/confflow-full-remediation-c6a4263-20260819`
- Branch: `codex/full-remediation-20260819-edit`
- Base: `c6a4263bf3ec84669fd5279ec336b10ab2e18c9f`
- Subagents edit this copy with the patch tool. WSL runs targeted tests here;
  its results are supporting evidence rather than the final Linux/ext4 gate.

### ConfFlow Linux/ext4 acceptance candidate

- Path: `/opt/ConfFlow-remediation-c6a4263-20260819`
- Branch: `codex/full-remediation-20260819`
- Base: `c6a4263bf3ec84669fd5279ec336b10ab2e18c9f`
- The first `/tmp` clone was not persistent across desktop WSL command
  boundaries; no implementation occurred there. After each reviewed phase,
  root mechanically applies the exact Git patch from the editable candidate
  here and runs the final Linux/ext4 gate.

## Phase gates

| Gate | State | Evidence required before approval |
|---|---|---|
| Phase 0 baseline | approved | candidate status, dirty-patch hash, dependency identities, architecture fitness inventory |
| CF-0a | approved | independently reviewed behavior tests, pytest warning removed, Linux/ext4 coverage 85.04% |
| JD-0a | approved | SQLite cleanup, public behavior coverage, locked dev matrix, enforced 85% gate |
| CF-1a foundation | approved | canonical typed-config modules, v2 facade compatibility, dual-copy full gate at 85.23% |
| CF-1b durable fixtures | approved | immutable v2 SHA fixture tree, fail-closed calc generations, cross-process artifact lock |
| Later phases | not started | preceding gate approval and phase-scoped task packet |

## Architecture fitness inventory

Independent read-only audit found these existing protections:

- JobDesk architecture tests already cover broad module dependency direction,
  PySide6/Paramiko isolation, selected `.repository` access, and two named
  widget-private-field cases.
- JobDesk launcher tests already cover response loss, bounded reconciliation,
  operator non-acceptance proof, authorized retry, and duplicate refusal.
- ConfFlow dependency tests already reject `core/shared -> blocks` and one
  legacy refine-result import.
- ConfFlow worker tests cover local child-process cancellation, lease, and
  recovery behavior.

Confirmed fitness gaps to address in later scoped PRs:

- general JobDesk GUI repository imports and cross-widget private access;
- JobDesk GUI/control reach-through to `coordinator.service`;
- per-watcher raw SSH creation without a shared observable session budget;
- end-to-end JobDesk cancellation evidence reaching a real producer worker;
- ConfFlow `calc/postprocess.py -> blocks.refine` dependency;
- runtime imports of legacy `core.models` outside compatibility facades;
- responsibility-boundary guards for `run_workflow()` and
  `run_control_worker()` after their decompositions.

These gaps are evidence for later task packets. They do not authorize starting
a later phase before CF-0a and JD-0a pass independent review.

## CF-0a acceptance

- Editable candidate changes are limited to `pyproject.toml`,
  `tests/test_workflow_execution_adapter.py`, and
  `tests/test_pytest_configuration.py`.
- The protected source test remains unchanged. Its copied behavioral patch was
  reviewed and upstreamed without the private `_resolve_executable` assertion;
  the executable identity check now uses `sys.executable` on Windows and Linux.
- Two remaining direct `_run()` calls were rejected in review and replaced by
  the public `ExecutionService.execute()` launch path with duplicate-launch,
  error propagation, cancellation beacon, and durable state assertions.
- Independent re-review result: `APPROVED`.
- Root independently passed Ruff and 17 targeted tests in the editable
  candidate.
- The reviewed files were mechanically copied to the isolated ext4 candidate
  and verified by SHA-256 before the final gate.
- Final ext4 gate: Ruff passed; mypy passed for 116 source files; pytest
  reported `1053 passed, 6 skipped`; total coverage was `85.04%` with
  `--cov-fail-under=85`; no `PytestConfigWarning` occurred. The only warnings
  were two existing Numba performance warnings.
- `/opt/ConfFlow`, `/opt/confflow-current`, and the production environment were
  not modified.

## JD-0a acceptance

- Test-owned SQLite connections in `tests/test_run_repository.py` and
  `tests/test_run_service.py` now close deterministically; the two files pass
  with `ResourceWarning` promoted to an error (`200 passed`).
- Public behavior coverage now exercises `WorkflowSpec` YAML parsing and
  serialization plus the `SSHConfFlowClient` submit, attach, status, event,
  artifact, download, cancel, resume, and refresh facade. A weak event-cursor
  assertion was rejected and replaced by the exact sequence
  `[None, "cursor-1"]`.
- `pytest-cov` and the locked PEP 517 backend are part of the dev extra. CI
  enforces `--cov-fail-under=85` and no longer installs pytest-cov separately.
- Root's final Windows Python 3.13 non-integration gate reported
  `1962 passed, 31 skipped, 6 deselected`, total coverage `85.05%`, exit 0.
- Three checked-in Windows x86_64 locks cover Python 3.11, 3.12, and 3.13.
  They were generated with uv 0.11.5, a fixed `exclude-newer` timestamp, exact
  versions, and SHA-256 hashes. Python 3.11/3.12 resolve NumPy 1.26.4; Python
  3.13 resolves NumPy 2.3.5. No chem extra dependency is present.
- CI installs each matching lock with `--require-hashes`, installs JobDesk with
  `--no-build-isolation --no-deps`, builds with `--no-isolation`, and reruns a
  deterministic lock-drift check.
- A clean Python 3.13 temporary environment successfully installed the locked
  dependencies and editable project. Independent code and lock re-reviews both
  returned `APPROVED`.
- Cleanup residue remains outside the candidate diff: five stale untracked
  `.coverage.jd0.*` files and the exact temporary environment
  `C:\Users\moxue\AppData\Local\Temp\jobdesk-lock-venv-py313-20260819-a`.
  Their deletion was attempted once and blocked by the external approval quota;
  no workaround was used.

## CF-1a foundation acceptance

- Typed workflow configuration implementations now live under
  `confflow.config.canonical`; `confflow.config.models` remains a distinct
  frozen-dataclass v2 compatibility facade rather than mutating class
  `__module__` identities at runtime.
- Nested factories preserve facade types for legacy callers, while the internal
  parser accepts an explicit model type and the public loader continues to
  return the v2 facade. Signatures, type hints, pickle lookup, equality,
  aliases, coercion, exact digest behavior, error behavior, and legacy helper
  imports are covered by public-surface tests.
- Independent review rejected the first identity implementation and later
  caught a missing `format_orca_blocks` re-export. Both issues were fixed; the
  final independent re-review returned `APPROVED`.
- Root's full mounted-candidate gate passed Ruff, mypy for 121 source files,
  `1061 passed, 6 skipped`, and 85.23% total coverage with
  `--cov-fail-under=85`.
- Seven reviewed CF-1a files were mechanically copied to
  `/opt/ConfFlow-remediation-c6a4263-20260819` and verified byte-for-byte.
  The final ext4 gate reproduced Ruff and mypy success, `1061 passed,
  6 skipped`, and 85.23% coverage.
- Shared coercion primitives now explicitly separate legacy-v2 wire-preserving
  program/task/memory behavior from the typed-v2 normalized behavior. The
  three public Pydantic models retain their identities, complete field/default
  surface, `extra="allow"`, error keywords, accepted-value matrices, and JSON
  round trips. Independent review rejected incomplete snapshots; the expanded
  matrices and all three model snapshots passed re-review.
- Ordered environment-independent diagnostics now live in
  `confflow.config.canonical.validation`. The old shared/core paths remain thin
  compatibility views. Independent review rejected a first version that moved
  `os.path.exists()` into the canonical layer; the final design keeps the
  legacy Gaussian/ORCA path-existence shim only in the shared compatibility
  wrapper while canonical results remain host-independent.
- After the Pydantic and diagnostics slices, root's complete mounted-candidate
  gate passed Ruff, mypy for 122 source files, `1197 passed, 6 skipped`, and
  85.37% coverage. Eight reviewed files were then copied to the isolated ext4
  candidate and verified byte-for-byte; the complete ext4 gate reproduced
  `1197 passed, 6 skipped` and 85.37% coverage.
- An immutable `tests/fixtures/config_contract/v2` tree now freezes aliases and
  defaults, legacy/canonical bytes, calc canonical bytes and digest, confgen
  signature sensitivity, completed calc manifest bytes, current and
  schema-absent workflow state, unknown generations, and resume decisions. An
  exact allowlist manifest binds every fixture path to SHA-256; fixture review
  rejected a broad xfail, an unfrozen state-save path, and mixed-variable
  resume cases before returning `APPROVED`.
- The fixture initially exposed destructive handling of unknown calc manifest
  generations. `CalcArtifactManager.prepare()` now uses strict schema/content
  validation and raises a dedicated typed error before mutation for unknown,
  missing, ambiguous, malformed, or invalid-UTF-8 manifests; the public
  permissive `load()` compatibility path remains unchanged.
- Calc artifact transactions now use a shared reentrant thread lock plus a
  persistent cross-process sidecar lock outside the step artifact tree. POSIX
  uses `flock`; Windows uses one-byte `msvcrt.locking`. All official manager
  writers participate, lock release is exception-safe, and spawned-process
  tests prove a writer blocks across validation and stale cleanup. Independent
  review returned `APPROVED` after rejecting the earlier process-local guard.
- Root's complete mounted-candidate gate after durable hardening passed Ruff,
  mypy for 122 source files, `1228 passed, 6 skipped`, no xfail, and 85.43%
  coverage. The reviewed source/tests/fixture tree were copied to ext4 and
  verified byte-for-byte; the ext4 gate reproduced `1228 passed, 6 skipped`,
  no xfail, and 85.45% coverage.
- `/opt/ConfFlow`, `/opt/confflow-current`, and the production environment were
  not modified.

## CF-1c schema and machine-CLI acceptance

- A deterministic v2 workflow JSON Schema is generated from canonical field,
  alias, normalization, and domain metadata. The same metadata is consumed by
  parsing and diagnostics; the checked-in schema, golden copy, fixture manifest,
  build-time drift check, wheel package data, and source distribution are bound
  together by exact bytes and SHA-256.
- The strict machine-contract loader accepts only the declared JSON-compatible
  YAML wire domain and reports typed, deterministic JSON paths. The public v2
  loader deliberately retains its historical falsey-document and wider coercion
  behavior. Independent schema review rejected semantic schema drift, duplicate
  rule sources, raw step-type diagnostics, falsey-loader drift, ambiguous scalar
  coercions, and an overstated closed program/task domain before final approval.
- Canonical validation now has one structured `ConfigIssue` predicate engine.
  Legacy validators are ordered message projections, and the CLI consumes only
  structured paths. No second semantic rule implementation remains.
- `confflow config contract --json` and
  `confflow config validate --json --stdin` are dispatched before importing the
  legacy CLI or running environment/workload probes. Every handled branch emits
  exactly one compact, sorted JSON document plus one newline, keeps stderr empty,
  and returns the frozen 0/1/2 status contract. The emergency exit-2 document is
  independent of schema/provenance binding failures.
- Machine diagnostics preserve exact nested field/index paths but use stable,
  generic codes and messages. Sentinel tests prove arbitrary configuration
  values and step names do not cross the output boundary. Independent CLI review
  also rejected a removed legacy test seam, weak cold-import guards, incomplete
  failure containment, duplicate semantic predicates, root-only schema paths,
  and raw-message privacy leakage before final approval.
- Root's final mounted-candidate gate passed the schema/golden drift check, Ruff,
  mypy for 127 source files, and the complete suite with `1297 passed, 7 skipped`;
  coverage was 85.66% with `--cov-fail-under=85`. The first run exposed only an
  obsolete test literal coupling the package version to `2.0.0`; the approved
  fix instead asserts the emitted producer version against the running package's
  public version source, without changing the config ABI.
- A candidate wheel built from the mounted checkout passed the installed,
  checkout-outside, site-packages black-box test. Exact compact bytes were
  verified for contract, successful validation, and invalid YAML responses.
- The reviewed CF-1c files were mechanically copied to
  `/opt/ConfFlow-remediation-c6a4263-20260819` and checksum parity was verified.
  The ext4 gate reproduced the schema check, Ruff, mypy, `1297 passed, 7 skipped`,
  and 85.66% coverage. A fresh wheel built from the ext4 candidate passed the
  same installed black-box gate (`1 passed`).
- Candidate-only ACL residue remains in the mounted editable checkout under
  `.dist_cf1c` and `.pytest_cf1c*`. Exact-path deletion was attempted and denied;
  permissions were not weakened and none of this residue was copied to ext4.
- `/opt/ConfFlow`, `/opt/confflow-current`, and the production environment were
  not modified.

## JD-1a configuration-contract acquisition acceptance

- JobDesk now has a typed `ConfigurationContractClient` boundary separate from
  `ConfFlowCapabilities`. Its verified binding includes server and configured
  executable identity, remote executable identity, producer/build/wheel/install
  provenance, response and workflow schema identities/hashes, fixture binding,
  canonical schema bytes, and the remote or approved-fallback source.
- The remote adapter strictly parses the frozen `contract-response.v1` and
  `validate-response.v1` machine documents, rejects duplicate or extra JSON,
  validates canonical schema bytes against the advertised hash, and keeps
  diagnostics within the producer's privacy-safe ABI. SSH now accepts a
  backwards-compatible optional byte stdin stream, which is half-closed after
  flush and never stages a remote temporary file.
- Contract cache reuse is guarded by a fresh capability/identity probe and a
  complete immutable binding key. Tests cover per-server isolation, all binding
  dimensions changing, cache revalidation, exact stdin bytes, and coordinator
  facade revalidation. The facade is intentionally not connected to upload,
  submit, SQLite, or run persistence in this slice.
- The checked-in fallback is immutable and can be selected only after the
  existing strict production validator accepts the exact approved ConfFlow 2.0.0
  identity. A missing or unsupported command from any other producer fails
  closed. Stable fallback remote semantic admission is deferred to JD-1c.
- Independent review returned `APPROVED` after comparing the JobDesk parser
  field-for-field against the current ConfFlow producer CLI and auditing cache,
  fallback, stdin, and call-chain boundaries.
- Root acceptance passed `144` focused coordinator/SSH/preflight/contract tests,
  Ruff for `src` and `tests`, and the full non-integration JobDesk suite:
  `1978 passed, 31 skipped, 6 deselected`. A first root invocation used a denied
  `C:\\tmp` base-temp path and produced only setup errors; it was rerun in the
  candidate-local base-temp path and is not counted as a test failure.

## JD-1b document and GUI-fidelity acceptance

- `WorkflowSpec` is now a thin compatibility facade over a lossless versioned
  document, codec, mapping, and schema-lint split. The accepted corpus covers
  v0.5 aliases, v0.6 extensions and DAG inputs, disabled steps, wizard
  metadata, top-level/global unknown fields, malformed scalar steps, and a
  closed SHA-256 fixture manifest.
- The real `WorkflowPage` load/save route preserves unknown document fields and
  safe DAG data. Incompatible graphs are explicitly readonly with a reason;
  they are never silently replaced with an empty graph. Normal graphs remain
  editable, downstream references follow a rename, and a duplicate-name save
  returns failure without overwriting the document.
- Final targeted GUI/nodegraph acceptance passed `59` tests with Ruff, mypy,
  and diff checks. No ConfFlow runtime or production endpoint was touched.

## JD-1c binding-store and admission-boundary acceptance

- Schema v7 adds an immutable `run_configuration_bindings` record that is
  created atomically with a run. It stores the accepted configuration digest,
  complete contract identity, producer/executable identity, canonical JSON
  provenance, and validation timestamp. SQLite constraints and triggers reject
  direct update/delete, replace, and upsert bypasses while preserving only the
  parent-run foreign-key cascade.
- Independent binding-store review approved the v6-to-v7 migration, trigger
  repair on non-ready v7 reopen, canonical JSON/hash constraints, and direct
  SQL bypass coverage. Focused repository/run-service acceptance passed `216`
  tests in the candidate worktree.
- `RunCoordinator.admit_configuration` is independently approved as the
  side-effect-free remote admission boundary: one SSH session, two
  capability/contract identity checks, exact-byte stdin validation, stable
  fallback rejection, and privacy-safe fail-closed errors. Root regression
  acceptance passed `45` contract/coordinator tests, Ruff, mypy, and diff
  check. The subsequent delivery wiring remains in progress; this entry does
  not claim that uploads or submission paths are connected yet.

## Authorization boundary

Not authorized by this implementation start:

- commit, push, PR, merge, tag, or publication;
- mutation of `/opt/ConfFlow` or its stale `.venv`;
- mutation of `/opt/confflow-current` or the 2.0.0 production environment;
- candidate installation into a production/default endpoint;
- g16, ORCA, or any real scientific workload;
- production promotion.
