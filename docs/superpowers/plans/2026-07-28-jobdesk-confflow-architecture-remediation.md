# JobDesk / ConfFlow architecture remediation plan

Date: 2026-07-28

Status: approved for delegated implementation and independent acceptance

## Goal

Close the current JobDesk-to-WSL ConfFlow blocker and the confirmed
cross-project correctness defects without inventing a new release protocol in
the same change.

This plan is deliberately split into:

1. an executable remediation milestone for current blockers and backwards-
   compatible hardening; and
2. a follow-up architecture milestone that requires an explicit version,
   migration, and release decision before implementation.

## Verified baseline

- JobDesk: `C:\dft\tool\jobdesk-dev`, clean `main`, HEAD `7876ddb`, equal to
  `origin/main`.
- ConfFlow: Ubuntu-24.04 `/opt/ConfFlow`, clean `main`, HEAD `d67d1ee`, equal to
  `origin/main`.
- Deployed producer: `/usr/local/bin/confflow` resolves to
  `/opt/ConfFlow/.venv/bin/confflow`; wheel version `1.4.3`, schema `3`, build
  commit `7b37c223d2c07a062ab62965911c3cd8d6641591`, dirty `false`, and wheel
  SHA-256 `415875e294a454ffd6d6a12835f087f33c8b5731cec74cb5e36588036ff7671d`.
- Persistent JobDesk server config for `wsl` still sources
  `/root/.jobdesk_confflow_1_4/env.sh`, which selects ConfFlow `1.4.0`, schema
  `1`; the normal JobDesk preflight therefore fails before upload.
- A temporary no-env probe accepts the deployed producer as version `1.4.3`,
  schema `3`.
- A three-node fake DAG (`root -> {alpha, beta}`) proves that ConfFlow executes
  both leaves but reports only `beta` in `final_output` and `final_outputs`.

## Ownership and safety boundaries

- Do not commit, push, create/move tags, rebuild or replace the deployed
  `v1.4.3` wheel, or rewrite Git history.
- Do not reset, stash, clean, or overwrite pre-existing user work. Re-check
  both worktrees before every implementation phase.
- Preserve `/root/.jobdesk_confflow_1_4`; it is historical deployment state.
  Only remove its reference from the `wsl` server entry.
- Before changing `%APPDATA%\JobDesk\servers.yaml`, create a timestamped backup
  beside it and preserve every field except the one confirmed stale entry.
- Do not run a real Gaussian calculation during delegated implementation. The
  primary agent owns the final real-WSL acceptance decision.
- All code changes must start with a failing regression and finish with the
  narrow test plus the relevant full suite.

## Milestone 1 - executable remediation

### Task 0 - recapture the baseline

1. Record `git status --short --branch`, HEAD, and `origin/main` for both
   repositories.
2. Record the current `wsl.env_init_scripts`, SSH-side `command -v confflow`,
   `readlink -f`, version, and capability payload.
3. Stop immediately if either worktree is no longer clean or the verified
   baseline has materially changed.

Gate: implementation starts only from the two clean, synchronized worktrees
described above.

### Task 1 - remove the stale WSL environment override

1. Back up `%APPDATA%\JobDesk\servers.yaml` with a timestamped `.bak` suffix.
2. In the `wsl` server only, remove
   `/root/.jobdesk_confflow_1_4/env.sh` from `env_init_scripts`.
3. Preserve other server entries and all unrelated keys byte-for-byte where
   practical; use the project's YAML loader for round-trip validation.
4. Run the real JobDesk SSH capability probe with `require_dag=True` using the
   persisted configuration, not a temporary override.
5. Assert version `1.4.3`, schema `3`, all five artifact names, all seven
   commands, build commit `7b37c22...`, and `dirty=false`.

Gate: the persisted `wsl` entry resolves the supported `/opt/ConfFlow` wheel
and JobDesk accepts the capability payload without a temporary configuration.

### Task 2 - fail closed on multiple terminal workflow steps in JobDesk

Decision: this milestone does not invent multi-output aggregation. Until that
protocol exists, an executable workflow must have exactly one semantic terminal
step.

1. Add focused failing tests in `tests/test_nodegraph/test_spec_bridge.py`:
   - a fan-out with two terminal calculation nodes is rejected;
   - the error names the terminal nodes and tells the user to add a final merge
     or calculation step;
   - a diamond that joins back into one terminal step remains accepted;
   - a canonical linear workflow remains accepted.
2. Update `gui/nodegraph/spec_bridge.py::to_workflow_spec()` to count semantic
   leaves among emitted workflow steps. Ignore XYZ/OUTPUT sentinels and OUTPUT
   visualization edges.
3. Reject more than one semantic leaf before YAML serialization. Keep
   `from_workflow_spec()` able to display historical multi-leaf YAML, but block
   resubmission until it is made single-terminal.
4. Update the OUTPUT tooltip and tests: OUTPUT represents the one final workflow
   result; it does not aggregate arbitrary upstream paths.

Gate: JobDesk cannot serialize or submit a graph whose final-result semantics
would be ambiguous, while linear and joined DAG workflows still round-trip.

### Task 3 - add producer-side single-terminal defence

1. Add a failing ConfFlow regression in `tests/test_workflow_dag.py` proving
   that an explicit DAG with two sinks raises before any step handler runs.
2. Add a joined diamond case proving that one terminal sink still executes and
   becomes `final_output`.
3. Validate the sink count after `build_step_graph()` and cycle/unknown-input
   validation, but before work directories or calculations are started.
4. Apply the rule only to executable workflows. Keep the legacy linear fallback
   behavior unchanged.
5. Update any existing mixed-root fixture so it either has an explicit join or
   intentionally asserts the new failure.
6. Do not rebuild or deploy the 1.4.3 wheel in this milestone; the source change
   is for the next producer release. Current safety is supplied immediately by
   the JobDesk consumer guard from Task 2.

Gate: both projects independently reject ambiguous multi-sink execution, and a
single-sink DAG continues to work.

### Task 4 - accept producer PEP 440 prerelease strings without changing policy

Existing policy must remain:

- reject prereleases at the minimum supported release (`1.4.3rc1`);
- accept prereleases above the minimum and below major 2 (`1.9.0rc1`);
- reject `2.0.0` and later;
- reject epochs, dev/post/local releases, leading-zero/noncanonical releases,
  and arbitrary malformed text.

1. Add tests for both producer spellings `1.9.0rc1` and `1.9.0-rc.1`.
2. Add minimum-boundary tests for `1.4.3rc1` and `1.4.3-rc.1`.
3. Replace the handwritten parser with `packaging.version.Version` or an
   equivalently complete implementation. If `packaging` is imported directly,
   declare it as a direct JobDesk dependency.
4. Keep version-window constants in `confflow_contract.py`; do not duplicate
   bare version literals in the validator.

Gate: normalized wheel metadata versions follow the existing compatibility
policy and all current strictness tests remain green.

### Task 5 - make state-aware artifact parsing structurally strict

1. Add failing tests for:
   - `{}` as `run_summary.json`;
   - a non-dict or non-integer `step_status_counts` value;
   - a non-list `steps` field in workflow stats;
   - malformed entries inside `steps`.
2. For state-aware APIs, return `ParseState.MALFORMED` when required current
   producer fields or their types are invalid. Do not silently convert an
   incompatible object into an OK zero/empty result.
3. Preserve legacy wrapper behavior where documented: legacy callers may still
   receive an empty/default object, but it must be derived from the explicit
   MALFORMED state rather than an OK parse.
4. Continue accepting unknown extra keys for forward compatibility.

Gate: current 1.4.3 artifacts parse successfully, malformed shapes are visible,
and legacy APIs remain source-compatible.

### Task 6 - align architecture and operator documentation

JobDesk:

- Update `docs/architecture.md` to describe four GUI pages, the actual method
  preset module, the two-part local-model/remote-CLI contract, single-terminal
  DAG semantics, and the fact that `>=1.4.3,<2.0` is a window rather than an
  exact shared model pin.
- Add a dated update to `jobdesk_confflow_architecture_review.md` replacing the
  stale HEAD/deployment statements with the current deployment-versus-env-script
  distinction.
- Update WSL deployment/troubleshooting guidance so obsolete isolated env
  scripts cannot shadow `/usr/local/bin/confflow` unnoticed.

ConfFlow:

- Remove the false archived/monorepo banner from `README.md`; retain the public
  alpha and trust-boundary warnings.
- Correct `docs/ARCHITECTURE.md` so the directory map names files that exist,
  documents `contract.py`, `agent/`, explicit DAG helpers, workflow state, and
  the current sequential DAG execution model.
- State explicitly that this milestone requires one terminal DAG sink and does
  not promise wave-level concurrency or multi-output aggregation.

Gate: documentation matches current code and deployment behavior; no historical
claim is presented as a current fact.

## Milestone 1 verification matrix

### JobDesk targeted

```powershell
python -m pytest tests/test_nodegraph/test_spec_bridge.py -q
python -m pytest tests/test_confflow_preflight.py tests/test_version_consistency.py -q
python -m pytest tests/test_confflow_results.py tests/test_confflow_parse_state.py -q
python -m pytest tests/test_confflow_validation_differential.py -q
```

### JobDesk full gate

```powershell
python -m ruff check .
python -m mypy src
python -m pytest -q --basetemp C:\tmp\jobdesk_confflow_remediation_acceptance
```

Use a Python environment containing the declared `.[dev,chem]` dependencies.
Do not call a green run against unsupported ConfFlow 1.4.2 CI parity.

### ConfFlow targeted and full gate

```bash
cd /opt/ConfFlow
.venv/bin/python -m pytest tests/test_workflow_dag.py -q
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy confflow
.venv/bin/python -m pytest -q --basetemp /tmp/confflow_remediation_acceptance
```

### Persisted WSL handshake

Run JobDesk's real SSH probe through server id `wsl` with `require_dag=True` and
the saved server configuration. Record executable path, version, schema,
artifacts, commands, and build provenance.

### Final hygiene

- Both worktrees contain only planned changes.
- `git diff --check` passes in both repositories.
- No deployed wheel, tag, branch, or remote ref changed.
- Report every changed file and every skipped/deselected test.

## Independent acceptance owned by the primary agent

After delegated implementation, the primary agent must independently:

1. review both diffs and trace the affected call chains;
2. rerun the targeted tests and broad gates rather than trusting the executor's
   report;
3. repeat the persisted real SSH capability probe;
4. reproduce the multi-leaf rejection on both sides and a joined-DAG success;
5. decide whether to run the opt-in two-molecule Gaussian integration only after
   all non-compute gates are green;
6. issue an explicit approved/blocked verdict, separating code defects,
   configuration, documentation, test environment, and deferred architecture.

## Milestone 2 - requires a separate design and release decision

Do not implement these items during Milestone 1:

1. Add an explicit per-server ConfFlow executable path and bind capability
   probe, dry-run, and task execution to the same resolved binary.
2. Persist capability payload, resolved executable, producer version/build
   commit, and wheel identity in JobDesk's run database and result manifest.
3. Introduce versioned JSON content schemas for run summary, workflow stats,
   and workflow state; make producer writes atomic.
4. Design true multi-terminal output semantics, including aggregation, download
   manifests, GUI presentation, and resource accounting.
5. Move upload/create/submit into one durable application operation with
   compensating cleanup or resumable journaling.
6. Consolidate ConfFlow's duplicate DAG abstractions and decide whether the
   exported supervisor becomes part of the engine or is removed.

These changes require a new capability schema and producer release, plus a
JobDesk SQLite migration. They need a separate approved design before code.

## Executor handoff requirements

The delegated executor must:

- follow Milestone 1 exactly and not start Milestone 2;
- use test-first changes;
- not commit or push;
- stop and report if it cannot safely edit `/opt/ConfFlow` or the user server
  configuration;
- finish with a concise list of changed files, tests, unresolved issues, and
  exact worktree status for both repositories.
