# JobDesk / ConfFlow collaboration remediation plan

## Goal

Repair every confirmed issue from the 2026-07-21 cross-project review and make
the supported production path safe and testable:

`JobDesk -> SFTP/SSH -> nohup -> confflow CLI -> state/result files`.

The ConfFlow agent remains an optional ConfFlow entry point. Its pause/resume
contract must be repaired, but JobDesk must not be migrated to the agent in
this change. That migration needs a separate architecture decision after the
CLI path is reliable.

## Ownership and safety boundaries

- Preserve every pre-existing JobDesk and `/opt/ConfFlow` modification. Review
  overlapping edits before changing them, and do not discard, reset, or delete
  user-owned files.
- Treat the untracked JobDesk file `=` and the two earlier plan documents as
  user-owned. Do not delete them as part of this remediation.
- Do not stage, commit, push, tag, publish, or merge either repository.
- Do not terminate WSL, restart WSL globally, install network packages, or
  alter firewall/system proxy settings without a separate explicit approval.
- Do not execute or replace `/opt/g16/l1.exe`. Installer safety tests must mock
  all remote operations.
- Real integration tests must use a new isolated remote directory under
  `/tmp/jobdesk_test`; never reuse a production calculation directory.
- For `/opt/ConfFlow`, separate semantic edits from existing CRLF-only noise.
  Avoid repository-wide line-ending normalization or broad formatting.
- Keep compatibility with existing JobDesk run records and public entry points
  unless a migration is implemented and tested in the same change.

## Architecture decisions

1. **CLI plus nohup stays the JobDesk control plane.** `nohup` protects an
   active process from SSH disconnects; ConfFlow `--resume` repairs a stopped
   process from persisted state. These are complementary, not alternatives.
2. **Every submission owns a unique mutable namespace.** Files shared across
   submissions are read-only inputs only. Config, work directory, logs, state,
   and generated outputs must be unique even when two runs use the same source
   directory and basename.
3. **Declared state paths are authoritative.** JobDesk must not guess that
   state files are under `_batch`; the adapter declares the exact remote state
   paths and the monitor synchronizes those paths.
4. **Progress sync is separate from result download.** Active jobs may fetch a
   strict allowlist of small state/statistics files. Full result download stays
   gated by remote completion.
5. **Compatibility is checked before launch.** JobDesk verifies the remote
   ConfFlow version/capabilities and performs an actual remote dry run before
   starting a detached calculation.
6. **Agent pause is durable.** A paused job is not claimable. Only explicit
   resume requeues it, and resumed execution reaches the engine with
   `resume=True`.

## Phase 0 - capture the baseline

1. Record `git status --short`, current branch, and HEAD for both projects.
2. Record semantic diffs with line-ending-only changes filtered separately.
3. Confirm the current JobDesk default and targeted suites and ConfFlow
   pure-Python suite baselines. Record known failures rather than editing tests
   merely to hide them.
4. Inventory the real WSL command path, imported ConfFlow source, distribution
   metadata version, and SSH readiness without changing services.

Gate: no implementation begins until the pre-existing dirty files and the
authorized write set are explicitly listed in the implementer's report.

## Phase 1 - isolate every JobDesk submission

### Implementation

- Add an injectable submission/workspace identifier at the orchestration
  boundary. It must be stable for one submission and different for two
  submissions, including two runs with the same molecule basename.
- Allocate a remote submission root below the configured remote directory,
  such as `.jobdesk_submissions/<submission-id>/`.
- Stage JobDesk-owned mutable inputs, including `workflow.yaml`, in that root.
- Build ConfFlow commands with explicit unique config and work paths. Avoid
  relying on the shell's current working directory for isolation.
- Persist exact remote config, work, state, stats, log, and expected result
  paths in the prepared/run model so monitoring and download use the same
  contract that launch used.
- Preserve reading of legacy run records that do not yet contain the new path
  fields. Legacy fallback must be read-only and must not make new runs collide.

### Tests

- Submit two same-basename tasks to the same configured remote directory and
  prove that every mutable path and generated command is distinct.
- Prove that a retry of one run reuses only its own namespace.
- Cover quoting for spaces and shell metacharacters in configured paths.
- Cover legacy record loading/fallback.

Gate: no two new run IDs can write the same config, state, work, log, or output
path.

## Phase 2 - make WSL SSH startup deterministic

### Implementation

- After the WSL distribution startup probe succeeds, poll SSH readiness with a
  bounded deadline instead of connecting immediately.
- Read and validate an SSH identification banner, not merely an open TCP port.
- Make timeout and poll interval injectable for tests.
- On timeout, raise a clear error that distinguishes distribution startup from
  SSH service readiness and includes host, port, distribution, and elapsed
  time. Do not silently restart or reconfigure sshd.
- Ensure all sockets/process handles are closed on success, retry, and failure.

### Tests

- Delayed banner becomes ready within the deadline.
- Open port with no SSH banner remains unready.
- Deadline expiry returns the specific readiness error.
- Already-ready servers do not trigger WSL startup.

Gate: startup either reaches a verified SSH banner or fails within the declared
bound with actionable diagnostics.

## Phase 3 - synchronize live progress correctly

### Implementation

- Extend the adapter/prepared-run contract to expose exact remote
  `.workflow_state.json` and `workflow_stats.json` paths for each task.
- Replace `_batch` directory guessing in the monitor with those declared paths.
- Add a progress-only downloader that:
  - accepts active/submitted/running tasks;
  - fetches only the two declared small progress files;
  - writes atomically to the corresponding local run directory;
  - treats a not-yet-created remote file as normal pending state;
  - surfaces authentication, permission, and malformed-state errors.
- Run progress transfer off the GUI thread. Only refresh widgets after the
  transfer finishes or reports a handled absence.
- Keep full output download restricted to remotely completed tasks.

### Tests

- Nested ConfFlow work paths are discovered through declarations, not scans.
- Active tasks can download progress but cannot trigger full result download.
- Remote missing-file, partial-transfer, and malformed-JSON cases are safe.
- The checkpoint UI action performs a transfer before rereading local state and
  does not block the GUI thread.

Gate: a running remote workflow can update JobDesk progress without being
marked complete and without downloading arbitrary output files.

## Phase 4 - add preflight, version handshake, and recovery semantics

### ConfFlow implementation

- Add a stable `confflow --version` command.
- Add a machine-readable capability response, preferably
  `confflow --capabilities --json`, containing at least semantic version,
  capability schema version, workflow state support, resume support, and DAG
  support.
- Keep the output usable without importing chemistry backends or executing a
  calculation.
- Align source fallback version, package metadata expectations, and tests at
  `1.4.0`.

### JobDesk implementation

- Bound the dependency to the supported major line: `confflow>=1.4.0,<2.0`.
- Before a production launch, query remote capabilities and reject missing or
  incompatible features with a concise error.
- After upload and before nohup, execute the real task command with
  `--dry-run`. Launch nothing if preflight fails.
- Define first-launch and retry behavior explicitly. A retry must use
  `--resume` in the same run namespace. If first launch also uses `--resume`,
  prove that an empty namespace initializes cleanly.
- Persist enough launch metadata to reconstruct the same command and namespace
  after JobDesk restart.
- Update English and Chinese documentation so `nohup`, disconnect recovery,
  retry, and `--resume` have distinct meanings.

### Tests

- Parse supported, unsupported-major, missing-capability, malformed, and command
  failure responses.
- Prove dry-run occurs after upload and before detached launch.
- Prove a dry-run failure cannot launch nohup.
- Prove retry commands carry `--resume` and preserve the run namespace.
- Prove first launch cannot inherit state from another run.

Gate: every new remote launch passes capability and dry-run checks, and every
retry has a deterministic, isolated resume target.

## Phase 5 - repair ConfFlow agent pause/resume

### Implementation

- Add explicit resume intent to the queued job/context model.
- Normal submission enqueues `resume=False`; resume enqueues `resume=True`.
- Make the runner forward that value to `run_workflow`.
- Make claim transition only `pending` jobs. A `paused` job must never be
  automatically reclaimed.
- On pause, remove/deactivate the claimable queue entry while preserving state.
- On explicit resume, atomically transition the job back to pending and create
  exactly one claimable entry.
- Make duplicate pause/resume commands idempotent or return a clear conflict.
- Preserve crash recovery for genuinely running/pending jobs without converting
  paused jobs into active work.

### Tests

- A paused job stays paused across multiple worker polling cycles and process
  restart.
- Resume requeues exactly once and invokes the engine with `resume=True`.
- A fresh job invokes the engine with `resume=False`.
- Concurrent resume commands do not duplicate execution.
- Completed and failed jobs remain terminal.

Gate: pause is durable and only an explicit resume can make the job claimable.

## Phase 6 - harden mock Gaussian restoration

### Implementation

- Before copying `/opt/g16/l1.exe.real` over the live executable, validate the
  backup itself, not merely the post-copy hash.
- Reject a missing, symlinked, tiny, shell-script, mock-marker-containing, or
  non-ELF backup. Preserve the current live executable on every rejection.
- If installation records an original hash/metadata manifest, verify it during
  restore. Make legacy behavior fail safe when authenticity cannot be proven.
- Improve error text so operators know that no overwrite occurred.

### Tests

- Valid ELF backup is accepted through mocked remote operations.
- Missing, symlink, small, script, mock marker, non-ELF, and hash mismatch are
  rejected before unlink/copy.
- A failed probe never issues a command that mutates the destination.

Gate: restore cannot replace a real Gaussian executable with an unverified
backup.

## Phase 7 - deployment metadata and repository hygiene

- Repair the stale ConfFlow version expectation and add CLI/capability tests.
- Reinstall the local `/opt/ConfFlow` editable package offline with
  `--no-deps` only after all source tests pass, then prove source version,
  import version, CLI version, and distribution metadata agree at `1.4.0`.
- Remove only formatting/check failures in files materially changed by this
  remediation. Do not mass-normalize CRLF files or unrelated historical files.
- Classify the JobDesk `=` file, old plan files, ConfFlow disabled test, and
  line-ending-only modifications in the final report. Do not delete them.
- Record publication work (commit/tag/push/package release) as deferred; local
  correctness is not proof that a matching package was published.

## Verification matrix

### JobDesk

1. Ruff check for all changed Python files.
2. Ruff format check for all changed Python files.
3. Focused tests for remote SSH readiness, adapter/submit isolation, monitoring,
   progress transfer, GUI refresh, retry/resume, capability preflight, and mock
   installer restoration.
4. Existing ConfFlow integration and lifecycle tests that do not execute a real
   chemistry calculation.
5. Full default test suite with the repository's normal deselection policy.

### ConfFlow

1. Ruff check and format check for materially changed files.
2. Focused CLI/version/capability, workflow state/resume, agent queue, and agent
   runner tests.
3. Full pure-Python test suite.
4. Offline editable reinstall/version consistency probe.

### Real WSL acceptance

Run only after the read-only SSH readiness diagnosis succeeds and an isolated
remote directory is selected:

1. Verify a real SSH banner and remote ConfFlow capability response.
2. Submit one isolated non-destructive workflow and observe live state transfer
   before completion.
3. Interrupt only the isolated ConfFlow process, relaunch through JobDesk retry,
   and prove completed steps are skipped by resume.
4. Download final results and prove the run record owns the same namespace used
   at launch.
5. Clean up only the explicitly created `/tmp/jobdesk_test/...` directory.

If SSH/WSL service repair, Gaussian execution, or process interruption needs a
new authority, stop at the boundary and report the exact remaining acceptance
item. Unit and mocked integration success must not be described as real WSL or
Gaussian validation.

## Final acceptance criteria

- Two concurrent same-basename JobDesk runs share no mutable remote path.
- JobDesk verifies an SSH banner, ConfFlow capabilities, and remote dry-run
  before detached launch.
- Active workflows synchronize declared progress files without full download.
- A retry resumes only its own persisted state.
- ConfFlow agent pause remains paused and explicit resume reaches the engine as
  `resume=True` exactly once.
- Unsafe mock restore is rejected before any live executable mutation.
- JobDesk and ConfFlow focused and full non-chemistry suites pass.
- WSL source/import/CLI/distribution versions agree at `1.4.0`.
- Real WSL acceptance is either passed with exact evidence or separately marked
  blocked by an identified environment/authority boundary.
- All pre-existing user changes are preserved; neither repository is staged,
  committed, pushed, tagged, or published.
