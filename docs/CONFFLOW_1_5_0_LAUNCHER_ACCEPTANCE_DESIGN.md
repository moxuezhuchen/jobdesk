# ConfFlow 1.5.0 launcher-path control acceptance design

> Status: design plus execution evidence (2026-08-08). This document is not a passing acceptance record. The SSH/SFTP acceptance remains incomplete and the evidence below is deliberately separated into direct producer probes, real external-program probes, and JobDesk lifecycle samples.

## Scope and provenance

The proposed launcher acceptance is Route B compatibility observation work. The design itself does not authorize a worker, scheduler workload, Gaussian, g16, ORCA, or Phase F. The 2026-08-08 direct g16/ORCA probes were run only after explicit user authorization and are recorded as separate evidence; they are not JobDesk SSH compatibility-period samples.

- Cycle start UTC: `2026-08-01T15:57:13Z`
- `cycle_start_jobdesk_main`: `9904cbaae078344bb35162f3ddee354b1acd040c`
- `current_audit_jobdesk_main`: `ad5c6263ff02690f20e8f25b92303b533b13e284`
- `current_audit_confflow_main`: `0c7d804b297a2c3205741996ebfc1f12d070942b`
- ConfFlow `v1.5.0` tag object: `5333d4854aa9d430221f3e16f5c36461010a2b3e`
- ConfFlow `v1.5.0` peeled commit: `0fff6439a4614ec155959b1d0d3781fc5342d736`
- ConfFlow `v1.5.0` wheel SHA256: `d9ac87410f1b73b91e19eb740298431663ee5f07bd4ffaeb19779c3a53c2e8dc`
- Stable rollback `v1.4.6` wheel SHA256: `7d036a44784d581b5b2fec2443f9cac7a0b2257d08b85c1a1b797bae565f75f5`

The immutable cycle boundary and current sample statement are maintained in the [compatibility record](CONFFLOW_1_5_0_COMPATIBILITY_RECORD.md). The release and compatibility constraints come from the public post-M2 compatibility plan.

## Current call chain, launcher handoff, and remaining worker gap

The current JobDesk symbols trace the control path as follows:

```text
SSHConfFlowClient.probe / submit_with_outcome / attach
  -> SSHControlTransport.capabilities / prepare / execute / status / events / cancel / resume / artifacts
  -> remote `confflow control <operation> ... --json`
  -> ConfFlow v1.5.0 `confflow/control.py`
  -> application execution service (`ExecutionService`)
  -> workflow adapter / launcher-owned process boundary
```

The JobDesk side is implemented by [`SSHConfFlowClient`](../src/jobdesk_app/services/ssh_confflow_client.py), [`SSHControlTransport`](../src/jobdesk_app/services/ssh_confflow_control.py), and durable control state in [`confflow_control_state.py`](../src/jobdesk_app/services/confflow_control_state.py). The producer-side v1.5.0 symbols to revalidate against the pinned, non-editable wheel before execution are `confflow.control`, `confflow.application.execution.service.ExecutionService`, and its workflow adapter. The existing legacy launchers are [`SchedulerAdapter`](../src/jobdesk_app/remote/scheduler.py), including `NohupAdapter`, `SlurmAdapter`, and `PBSAdapter`, and the legacy submit boundary is [`submitter.py`](../src/jobdesk_app/remote/submitter.py).

The launcher handoff is now implemented in `_submit_control`: it writes a per-run launcher script and metadata, selects the configured `SchedulerAdapter` (`NohupAdapter`, Slurm, or PBS), submits the script, and persists the scheduler job id plus the nohup log path. The handoff is covered by fake-adapter regression tests, but it has not yet passed a real SSH/SFTP run because the WSL SSH listener is currently wedged in the kernel networking path (`rtnl_dumpit`, `Exceeded MaxStartups`).

There is a separate producer-side execution gap. ConfFlow v1.5.0 `control execute` claims the prepared run and returns `queued`; `_AgentControlExecutor.ensure_launched` intentionally leaves actual launch to an external worker. The current JobDesk launcher invokes that command, but no supported worker/agent handoff is supplied by the pinned producer contract. Therefore a direct control probe can prove prepare/execute state transitions, but cannot be called a real control-to-g16/ORCA computation acceptance. A worker handoff is a new design/release scope, not a reason to bypass the control contract or fall back silently to legacy.

## Precise acceptance definition

“Supported launcher path” means all of the following hold for one run:

1. JobDesk negotiates `control`, persists the backend, protocol, state locator, provenance, and idempotency identity before the process is launched.
2. `prepare` is performed once against an isolated request/input root and returns a durable locator without starting computation.
3. Exactly one launcher owns process start: initially the authorized `NohupAdapter` path, or a separately authorized Slurm/PBS adapter path. The launcher starts the ordinary foreground command `confflow control execute --state-root <isolated-root> --run-id <run-id> --json`; `execute` must not daemonize or choose a scheduler.
4. ConfFlow owns durable execution state, events, revisions, terminal transitions, and the artifact manifest. JobDesk owns the client-side durable handle and projects the producer state; neither side reads or writes agent SQLite for this acceptance.
5. The run remains `control` from negotiation through terminal state. An explicit control run must fail closed on an unsupported or malformed response; it must not silently become `legacy`.

The current code proves the transport operations and durable-handle surfaces, and fake tests prove item 3's local construction. Real launcher-path acceptance remains blocked by the unavailable SSH service and the producer worker handoff described above.

## Minimum non-compute workflow

The smallest permitted workflow is a pinned ConfFlow v1.5.0 synthetic lifecycle fixture that:

- uses the real SSH/SFTP control transport and the real launcher handoff under test;
- executes only a deterministic in-process or producer-owned synthetic step, such as writing `synthetic-output.json` and its declared schema under the per-run root;
- emits observable prepare, running, event, terminal, and artifact-manifest state;
- does not enqueue an agent job, read agent SQLite, invoke a scheduler workload, spawn an external computational program, or depend on Gaussian, g16, ORCA, `iprog`, or any `/opt/g16` path;
- has no claim about scientific or real-computation success rate.

Fake transports remain useful for unit tests, but they do not count as launcher-path acceptance. The acceptance must use the real SSH/SFTP boundary once separately authorized.

## Isolated remote resources and safety boundary

The acceptance must use a fresh, per-attempt root selected and approved before execution, for example:

```text
<authorized-remote-tmp>/jobdesk-control-acceptance/<nonce>/
  input/
  state/
  run/
  launcher/
  evidence/
```

Expected resources are limited to the synthetic input/manifest, an explicit producer `state_root`, a launcher script or scheduler submission record, stdout/stderr or nohup log, and the synthetic artifact. The request file under `state_root/jobdesk-requests/` is temporary and must be accounted for by the evidence. JobDesk’s local test run directory and durable control state must also be isolated from user runs.

Before execution, the operator must prove that the resolved state root and run directory are descendants of the approved attempt root. If the installed control protocol cannot accept an explicit isolated `--state-root` and would instead use the user’s default `$HOME/.local/state/confflow/control`, stop; do not run the acceptance.

The following safety checks are mandatory:

- Use the exact v1.5.0 wheel and recorded digest; no editable checkout and no dependency upgrade.
- Reject any workflow, launcher script, or command containing `g16`, `gaussian`, `orca`, `iprog`, `/opt/g16`, `/opt/ConfFlow`, or a user run root.
- Verify the remote command identity and resolved paths before `prepare` and before launcher submission.
- Do not create or alter `/opt/g16`, `/opt/ConfFlow`, agent SQLite, or producer state outside the isolated attempt root.
- Do not submit a user workflow or upload a real input. The only upload is the synthetic fixture and its manifest.
- Cleanup may target only the exact attempt root after evidence capture. No broad `/tmp`, home, state-root, or user-run cleanup is permitted.

## Executable acceptance sequence

The SSH/SFTP steps below remain gated by a fresh attempt root and evidence capture. Results must be recorded separately for `control` and `legacy`; the current compatibility record still has no real JobDesk cycle-period run sample.

### 0. Preflight gate

Verify the refs, tag objects, peeled commits, wheel digest, clean/non-editable producer artifact, SSH identity, approved remote root, and selected launcher. Record the resolved `state_root`, `remote_dir`, and local evidence root. A dirty producer checkout, unresolved path, or missing scheduler capability is a stop condition.

### 1. Negotiation and prepare

Run capability negotiation and assert protocol major, producer provenance, and explicit backend selection. Upload only the synthetic manifest/input, call `prepare`, and verify that no worker or launcher process exists. Save the durable locator, backend, protocol, run id, idempotency key, and prepare response.

### 2. Launcher handoff

Submit the plain foreground `control execute` command through the selected launcher. Capture the launcher command, PID or scheduler job id, resolved working directory, state root, stdout/stderr or nohup log path, and JobDesk durable state. Prove that `execute` was launched by the supported adapter rather than by a direct SSH foreground call. Local construction is implemented; this step is still blocked for real acceptance until SSH/SFTP is healthy and a worker handoff is explicitly supported by the pinned producer.

### 3. Detach, reconnect, and durable recovery

Close the initial SSH/session objects. Recreate the client, call `attach`/durable-handle restore using the saved locator, and reconnect through a new SSH lease. Prove that backend, run id, state locator, provenance, and idempotency identity are recovered without reading agent SQLite or inventing a new run.

### 4. Events, cursor, revision, and terminal non-regression

Collect events from the initial cursor, replay from the same cursor, and collect the next page. Prove cursor replay behavior, non-decreasing revisions, stable event identity, and no terminal-to-nonterminal transition. Capture typed errors for malformed JSON, unknown major, invalid cursor, or invalid transition; these cases must fail closed and must not fallback.

### 5. Cancel or safe termination

Use the control cancel contract while the synthetic run is cancellable, or use the selected adapter’s documented cancellation path if it is still queued/running. Prove the terminal state, retry count and failure reason, no orphan process/job, and no second backend selection. Do not use an ad-hoc kill command as a substitute for the contract.

### 6. Manifest and download integrity

Read the producer manifest through `SSHControlRunHandle.artifacts`. For every declared artifact, verify allowed relative path, content schema, byte size, SHA256 digest, and downloaded-content equality. Reject traversal, undeclared files, digest mismatch, size mismatch, schema mismatch, or a download outside the attempt root. Record the manifest, local hashes, and download result.

### 7. Stable rollback probe

After the control attempt is stopped and its evidence is captured, use the exact stable `v1.4.6` wheel in a separate isolated legacy probe. Prove that capability negotiation selects `legacy`, the legacy path remains usable, the control state root is not reused, and no producer state is double-written or polluted by the control attempt. This must be live rollback/recovery evidence, not only a unit test or a tag/digest check. The user has authorized this class of probe, but the 2026-08-08 SSH failure prevented the live JobDesk rollback run; the direct legacy g16 evidence used v1.5.0 and therefore does not satisfy this gate.

### 8. Cleanup and evidence retention

Capture evidence before cleanup. On success, remove only the exact per-attempt root and record a path-bound cleanup proof. On failure or uncertainty, retain the attempt root and logs, stop further remote actions, and request direction; do not broaden cleanup to recover from an ambiguous state.

## Evidence bundle

The minimum bundle is:

```text
provenance.json
capabilities.json
prepare.json
launcher-submission.json
launcher.log
status-*.json
events-*.json
cancel.json
artifacts.json
downloaded/<declared-files>
download-sha256.json
rollback-probe.json
cleanup-proof.json
```

The bundle must state which results are synthetic/non-compute. It must not contain or imply a real calculation success rate.

## Stop conditions

Stop immediately and preserve evidence if any of the following occurs:

- the launcher handoff is bypassed, ambiguous, or cannot identify the PID/job id;
- a selected `control` run falls back to `legacy`, or any fallback has no documented unsupported-protocol reason;
- a malformed response, unknown major, typed error, invalid cursor, duplicate idempotency conflict, non-monotonic revision, cursor regression, or terminal regression is observed;
- the state locator, artifact path, log path, or cleanup target escapes the approved attempt root;
- an agent SQLite read/write, producer state double write, `/opt/g16`/`/opt/ConfFlow` write, real computational command, user workflow, or orphan process is detected;
- manifest path/digest/size/content-schema/download verification fails;
- stable rollback cannot be isolated from the control attempt.

Failure evidence is retained, no broad cleanup is attempted, and the result is reported as a blocker. A failed or partial attempt is not converted into a success by retrying without a new authorization and a new attempt root.

## Separate authorization checklist

Before any real execution, the user must separately authorize all of the following that apply:

- non-compute launcher-path control acceptance;
- the remote host, SSH identity, exact temporary root, and launcher type (`nohup` first; Slurm/PBS separately if required);
- creation of one isolated remote synthetic run and upload of its synthetic fixture;
- live stable `v1.4.6` legacy rollback/recovery probe;
- retention or cleanup of remote failure evidence.

Real worker, scheduler workload, `/opt/g16` changes, producer code changes, release/tag/wheel changes, and Phase F remain outside this design and require separate authorization. The user separately authorized the 2026-08-08 direct g16/ORCA probes; those probes do not claim that launcher acceptance, rollback evidence, or Phase F readiness is complete.
