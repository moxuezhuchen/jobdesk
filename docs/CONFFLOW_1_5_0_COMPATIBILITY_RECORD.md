# ConfFlow / JobDesk 兼容发布周期记录

> Current authoritative summary (2026-08-09): use
> [`CONFFLOW_1_5_0_COMPATIBILITY_EVIDENCE_INDEX.json`](CONFFLOW_1_5_0_COMPATIBILITY_EVIDENCE_INDEX.json)
> as the machine-readable aggregation boundary. The canonical released
> control samples are a32, a36, a37, and a38; the canonical stable legacy
> closeout is a5. All four control samples use ConfFlow v1.5.3 and JobDesk
> v0.5.1, select `control` explicitly, record `fallback_used=false`, complete
> at revision 6, and prove reconnect, idempotent submit 1/0, fixed-cursor
> replay, terminal empty page, terminal cancel/resume fail-closed, and
> artifact/download SHA-256 integrity. Exact per-attempt roots are absent;
> the shared published runtime is intentionally retained.
> The a34 and a35 bundles remain `acceptance_failed=true`, supplemental, and
> non-counted. The formal counters are `control_backend_runs=4` and
> `legacy_backend_runs=1`; candidate-only, synthetic, historical, and failed
> evidence remains excluded. The formal decision is
> **COMPATIBILITY PERIOD CONTINUES** and `phase_f_ready=false`; period-wide
> fallback, recovery/retention, and closeout metrics remain open.
> The user explicitly waived the 72-hour observation minimum at 2026-08-11T01:16:53.4065317Z because the project has no intended use.
> Current scope is RELEASE_BOUNDARY_VALIDATION_ONLY: r6 is finalized only
> as release-boundary validation; no complete measured compatibility-period
> claim is made and Phase F remains false.

## 原始 v1.5.0 周期边界与不可变 provenance（historical; superseded below）

- 兼容周期真实 UTC 起始时间：`2026-08-01T15:57:13Z`
- `cycle_start_jobdesk_main`：`9904cbaae078344bb35162f3ddee354b1acd040c`
- `current_audit_jobdesk_main`：`ad5c6263ff02690f20e8f25b92303b533b13e284`
- `current_audit_confflow_main`：`0c7d804b297a2c3205741996ebfc1f12d070942b`
- ConfFlow 发布：`v1.5.0`
  - annotated tag object：`5333d4854aa9d430221f3e16f5c36461010a2b3e`
  - peeled commit：`0fff6439a4614ec155959b1d0d3781fc5342d736`
  - wheel：`confflow-1.5.0-py3-none-any.whl`
  - wheel SHA256：`d9ac87410f1b73b91e19eb740298431663ee5f07bd4ffaeb19779c3a53c2e8dc`
- stable ConfFlow `v1.4.6` 保持不变
  - tag object：`6f3b24106308c1b20a78105825b5f8ebbb5d7ec5`
  - peeled commit：`4e9e74a8991338aec0f393182073c8c087b4fa63`
  - stable wheel SHA256：`7d036a44784d581b5b2fec2443f9cac7a0b2257d08b85c1a1b797bae565f75f5`

不得修改或覆盖 `v1.4.6`。本历史段绑定的是正式、非 editable 的
`v1.5.0` wheel；被拒绝的旧候选 digest
`f90e5c605ccb36cf37b16dcd53093cb3ac0239e630aaf0a082faa39998615e69` 不属于发布物。

## 2026-08-09 authoritative published acceptance update

### Quantitative post-contract window (current; release-boundary-only waiver)

The quantitative contract remains the machine-readable authority for any full
compatibility-period claim. The user-authorized duration waiver applies to r6
because the project has no intended use. r6 is therefore finalized only as
RELEASE_BOUNDARY_VALIDATION_ONLY; its reviewed metrics remain immutable evidence,
but no complete measured compatibility-period claim is made and Phase F remains
false.

Independent index review promoted a52 as r6 control success #1 and a53 as
r6 legacy success #1; a54 is now r6 legacy success #2 and a55/a56 are r6
control successes #2/#3. All raw bundles remain supplemental with
counts_as_real_run=false; promotion is only in the independent index.
a53 evidence is:
- main C:\tmp\jobdesk-legacy-release-v146-20260809-a53\evidence.json, SHA-256
  182dbcc3fa6effa1d65f285a45f75490ec090c382616c6c70ad48e33eb5f7232;
- pre-cleanup snapshot SHA-256
  b6a3743a540008ff49a797ade28bed02b13c3fcc6376fcd1596c359e1844bd1e;
- cleanup proof SHA-256
  67457d334ba100eaf8d014692f854601d7ad7125f34c64834b162eccfd1e8576.

a54 evidence is:
- main C:\tmp\jobdesk-legacy-release-v146-20260809-a54\evidence.json, SHA-256
  864d6f6bd93e82473bf2160932493a458bc9685368ecd003218fbc80c7c62dfb;
- pre-cleanup snapshot SHA-256
  8ab24d08d7460682dc6366de53312189c428433262069980404e11af7838f297;
- cleanup proof SHA-256
  da3f4c9d79ed55ff13a7d4851150526d21fd9ad9af140b6260ce99ba941368de.

a55 evidence is:
- main C:\tmp\jobdesk-control-release-v153-20260810-a55\evidence.json, SHA-256
  401fa6f1d5ffc6f1ab926bb2409aa89a38f0cd99be573e1a5e526460f828a66f;
- pre-cleanup snapshot SHA-256
  7dbe5f726c1503547fab86a321b9480e868dcddadf37ce13742d32ec0c2e92b6;
- cleanup proof SHA-256
  37aed0c8e35345d3e636e5b603102b3799d7f2a1ab2709b62a027c09c3c7d738.

a56 evidence is:
- main C:\tmp\jobdesk-control-release-v153-20260810-a56\evidence.json, SHA-256
  3195e0622372c143af40060d028dba24150bd92265b81958d4fed983687db814;
- pre-cleanup snapshot SHA-256
  843f3aa50b1e5c5aa905a489c167dfa3c2e1876a7b7fc8c19cdb6f6bf5ca1c5a;
- cleanup proof SHA-256
  3f99820ad3a4248cb7e7b14b30af7fb7f725860f6aa0620c76c8285c92e9fbeb.

The reviewed a53 legacy run selected v1.4.6 explicitly with
fallback_used=false, submitted two tasks and idempotently resubmitted zero,
reconnected in flight to the same run/locator, reached terminal completion,
proved typed legacy events/resume boundaries and terminal cancel no-op,
verified g16 identity/no-write and artifact/download SHA-256 integrity, and
removed only its exact attempt root while retaining the shared published
runtime. The reviewed a54 run has the same explicit legacy selection,
in-flight recovery, boundary, artifact, and cleanup-proof properties. The
reviewed a55 control run has the same explicit control selection, in-flight
recovery, event/cursor,
artifact, g16 no-write, and cleanup-proof properties. The reviewed a56 control
run has the same properties. Current r6 metrics are attempted/submitted/terminal
5/5/5, eligible control/legacy 3/3 and 2/2, failed/cancelled/uncertain 0/0/0.
The independently reviewed n1 non-counted negative probe observed a typed
unsupported_protocol failure without submitting a workload and retained its
exact root; its evidence SHA-256 is recorded in the evidence index and it does
not increment period counters (main SHA
`9ec731ae87abb1c07b284509a484e5f36bf65ba59a7b71297cd3bd5a162e28da`). The negative/retention scenario is observed. The user-authorized duration
waiver finalizes r6 only as release-boundary validation; no full-period claim or
Phase F readiness is made.

This section is the current decision record. It supersedes the earlier a3,
v1.5.0, pre-release, candidate-only, synthetic, and historical status
snapshots below; those sections remain for provenance and are not current
compatibility evidence.

The published pair is JobDesk `v0.5.1` and ConfFlow `v1.5.3`:

The machine-readable aggregation boundary is
[`CONFFLOW_1_5_0_COMPATIBILITY_EVIDENCE_INDEX.json`](CONFFLOW_1_5_0_COMPATIBILITY_EVIDENCE_INDEX.json).
It keeps the immutable a10 raw bundle for provenance while explicitly marking
it superseded/non-counted by canonical a32; a28 is also non-counted because
its acceptance evidence file was not persisted. The supplemental a34
fixed-cursor trace is indexed separately with `counts_as_real_run=false` and
`acceptance_failed=true`.
The separate a35 released workflow reached revision `6` and `completed`, but
its harness stopped before replay/download capture because status-only polling
had not persisted a JobDesk cursor. Its read-only terminal capture is
`C:\tmp\jobdesk-control-release-v153-20260809-a35\post-failure-readonly.json`;
it is `acceptance_failed=true`, `synthetic=false`, and non-counted.

The separately authorized a36, a37, and a38 runs at
`C:\tmp\jobdesk-control-release-v153-20260809-a36\evidence.json`,
`C:\tmp\jobdesk-control-release-v153-20260809-a37\evidence.json`, and
`C:\tmp\jobdesk-control-release-v153-20260809-a38\evidence.json` are the
independently indexed second, third, and fourth canonical released control
computations. Each reached revision `6` and `completed` with
`fallback_used=false`, and recorded reconnect, idempotent submit `1` then `0`,
fixed-cursor replay, terminal empty next page, typed terminal cancel/resume
rejection, and manifest/download SHA-256 integrity. Each exact per-attempt root
was absent after bounded cleanup; the shared published runtime remains
intentionally retained.

- JobDesk release commit `ebb719b2b67d2095f2199a30c9b97d7f88ac8820`, wheel
  SHA-256 `892efb156e1d59c10018d25107ec54932625a9238067d125cec61801cd3a279e`.
- ConfFlow release commit `f37759954da2818d777ec4d06f81bd53aeafe6e3`, wheel
  SHA-256 `213eba551b344c7146450fa1135a884e3c00896371507a1edbf2eb18c7c0c5d6`.
- Published producer/consumer CI and release provenance were verified:
  ConfFlow matrix `31271946187`, coverage `31271946186`, JobDesk Consumer
  Contract `31271946207`, and release workflow `31272089279`. Candidate-only,
  synthetic, and historical results remain excluded.

The authoritative control samples are
`C:\tmp\jobdesk-control-release-v153-20260809-a32\evidence.json`,
`C:\tmp\jobdesk-control-release-v153-20260809-a36\evidence.json`,
`C:\tmp\jobdesk-control-release-v153-20260809-a37\evidence.json`, and
`C:\tmp\jobdesk-control-release-v153-20260809-a38\evidence.json`.
Together they are four real JobDesk control computations through the released
worker-handoff and supported launcher. a32 records the initial durable
selection, submit, reconnect, status/events, cancel/resume, artifact and
cleanup trace; a36/a37/a38 add independently reviewed complete lifecycle
traces, including fixed-cursor replay and terminal empty pages. All four
record `requested_mode=control`, `selected_backend=control`, and
`fallback_used=false`; all complete at producer revision `6`. The separately
retained a34 read-only trace proves fixed-cursor replay and the terminal
empty-page response but is `acceptance_failed=true`, `synthetic=false`, and
non-counted. The exact attempt roots for all four canonical bundles are absent;
the shared published runtime remains intentionally retained.

The authoritative legacy closeout sample is
`C:\tmp\jobdesk-legacy-release-v146-20260809-a5\evidence.json`.
It is one real JobDesk legacy computation using the published JobDesk consumer
and the exact stable ConfFlow `v1.4.6` rollback release. Two tasks completed
with remote exit code `0`; idempotent resubmit, reconnect/status refresh,
manifest/download/hash integrity, and exact cleanup passed. Legacy
events/resume unsupported and terminal cancel no-op were recorded as expected
legacy boundaries. It is counted as one real legacy run.

| Metric | Control v1.5.3 | Legacy v1.4.6 |
| --- | --- | --- |
| counted real runs | 4 | 1 |
| fallback | `false` (all four control bundles) | `false` |
| idempotent duplicate submissions | 0 | 0 |
| reconnect / status | all four control bundles prove identity; a36/a37/a38 also persist fixed-cursor replay and terminal empty next-page traces; a34 remains supplemental/non-counted | identity and refresh trace passed |
| cancel / resume | typed terminal-state rejection expected | terminal cancel no-op; resume unsupported expected |
| artifact integrity | manifest, download, size and SHA-256 passed | two manifests, downloads, size and SHA-256 passed |
| agent SQLite / producer double-write | none | none |

The rejected post-release v1.5.0 attempt at
`C:\tmp\jobdesk-legacy-release-v150-20260809-a1\evidence.json` stopped at
the approved-release validator and is explicitly non-counted. It is not a
compatibility success and does not justify widening the consumer gate.

Published releases, dual-repository CI, released worker-handoff, four real
control computations, and three canonical complete lifecycle traces with
reconnect, idempotency, cursor replay/empty-page, terminal cancel/resume, and
artifact-download integrity are evidenced. A complete measured published
compatibility cycle with period-wide fallback, reconnect, idempotency,
resume/cancel, artifact-integrity, and legacy-closeout metrics is still open.
Formal decision: **COMPATIBILITY PERIOD CONTINUES**; Phase F is not ready.
Retain both backends, the stable `v1.4.6` rollback path, and all fail-closed
gates. All five canonical release evidence bundles retain `phase_f_ready=false`.
No `/opt` or agent state was modified.

## 原始 v1.5.0 Gate 与双 backend 验收（historical; superseded above）

ConfFlow 正式发布前的 clean、隔离 worktree Gate 全部通过：

- Ruff：通过。
- MyPy：`Success: no issues found in 117 source files`。
- pytest：`1053 passed, 1 skipped`。
- build：最终 sdist/wheel 各生成一次；最终 wheel 仅构建一次。
- `git diff --check`：通过。
- 最终 wheel metadata 含 `jsonschema>=4.23.0`、`referencing>=0.30.0`、`rfc8785>=0.1.4` 及完整 transitive closure。
- 全新 production venv 仅按 release installer/manifest 安装，`pip check` 零错误；无 editable checkout、无 `.egg-link`、无预装依赖掩盖问题。
- `confflow --version`、build commit、wheel digest、tag provenance、capability/protocol schema 均与上述记录一致。
- normal CLI 与七个 control operation（`prepare`、`execute`、`status`、`events`、`cancel`、`resume`、`artifacts`）以及 malformed schema、unknown major、非法 path/cursor 的 fail-closed 行为通过。

JobDesk `main` 的正式 wheel 验收结果：

- JobDesk control/backend 定向回归：`56 passed`；真实 SSH 基础通道：`2 passed`。
- 使用正式 `v1.5.0` 非 editable wheel 的 synthetic/non-compute lifecycle 选择 `control`；backend、producer commit、wheel digest 已固化。
- attach/reconnect、cursor/revision、cancel、manifest 安全下载通过。
- resume 的不允许状态转换返回 typed control error，并 fail closed。
- malformed JSON 与 unknown protocol major 不静默 fallback。
- 未读取 agent SQLite，未发生 producer state 双写。
- 使用真实 stable `v1.4.6` wheel 的 probe 明确选择 `legacy`，且 `control` protocol 不支持；未提交计算任务。
- JobDesk 的远端 capability probe 保留了这一 stable legacy 例外；只有 legacy submitter 可以接受 `v1.4.6` 的固定 commit/wheel provenance，control submit 仍只接受当前 `v1.5.0` reference。
- 全程未运行真实 worker、Gaussian 或 g16；仅使用 synthetic/non-compute lifecycle 与真实 SSH 通道。

正式发布 artifacts 已发布并远端验证；`v1.4.6` 环境保留，`v1.4.6` tag 未修改。

## 2026-08-08 pre-restart execution snapshot (historical, superseded below)

The user explicitly authorized real external-program probes. These results are retained as evidence, but they are not counted as JobDesk SSH compatibility-period usage because they did not traverse the complete JobDesk control/legacy submit, reconnect, and download lifecycle:

- Pinned non-editable ConfFlow v1.5.0 capability negotiation passed with the recorded release commit and wheel digest.
- A direct v1.5.0 legacy workflow ran a one-molecule Gaussian 16 methane optimization under the required explicit g16 environment. It completed normally with energy `-40.51838331`; the evidence root was `/tmp/jobdesk_phasef_real_direct_20260808_a1`.
- A separate ORCA water optimization ran normally under `/opt/orca611/orca`; the evidence root was `/tmp/jobdesk_phasef_real_orca_20260808_a1`.
- A direct v1.5.0 control protocol probe completed capability negotiation, `prepare`, and `execute`; the producer returned the contractually valid `queued` state in `/tmp/jobdesk_phasef_control_direct_20260808_a4`. This is not a real computation: the pinned `_AgentControlExecutor` intentionally leaves actual launch to an external worker, and no worker handoff is supplied by the current control contract.
- The real JobDesk SSH/SFTP attempt could not start because the WSL SSH listener repeatedly returned `Exceeded MaxStartups`; the WSL network path showed `rtnl_dumpit`/`D`-state stalls. No g16/ORCA process was started by that attempt, and no `/opt/g16`, `/opt/ConfFlow`, or user state was modified. This is retained as pre-restart failure evidence; the current post-restart samples are recorded below.

## 2026-08-08 Phase F readiness recheck (historical; not entered; superseded)

- An elevated read-only WSL probe confirmed `Ubuntu-24.04` was running, but the
  system `ssh.service` remained stuck in `deactivating`/`sshd -t`; its control
  process and connection children were waiting in `rtnl_dumpit`.
- A service restart was attempted within the authorized acceptance scope but
  timed out in the same `rtnl_dumpit` path. A separate sshd listener on the
  exact temporary port `10022` reproduced the child-process stall, so this is
  not a JobDesk port-22 or launcher configuration defect.
- The temporary listener and its exact `/tmp/jobdesk_phasef_ssh_20260808_a1`
  root were stopped and removed. No WSL shutdown, forced kill, `/opt` write,
  producer state write, or user data cleanup was performed.
- Result at the time: no real JobDesk control/legacy sample had been created.
  This historical snapshot is superseded by the post-restart legacy and control
  launcher evidence below; the compatibility period remains open and Phase F
  remains blocked on worker handoff, publication, and a complete measured cycle.

## 样本边界（历史快照；已由 2026-08-09 样本覆盖）

> The zero-control baseline in this historical section is retained for
> provenance only. It is superseded by the authoritative 2026-08-09 summary
> and the released-v1.5.3 control sample below; current formal counters are
> `control_backend_runs=1` and `legacy_backend_runs=1`.

本记录必须区分正式 Gate/稳定回滚 probe 与兼容周期内的真实 JobDesk 运行样本：

- 当前有一条兼容周期内真实 JobDesk `legacy` 样本（固定 v1.5.0、两任务），以及一条独立 stable `v1.4.6` rollback probe；另有真实 JobDesk `control` launcher 的 queued、非计算 handoff 证据。direct producer/external-program evidence 不计入兼容周期样本。
- 当前真实 JobDesk `control` 计算样本数为 `0`：pinned producer 的 `control execute` 只返回 queued launch intent，尚无 external worker handoff。真实 JobDesk `legacy` 运行数为 `1`；本地历史运行记录和重启前失败尝试不计入该计数。
- “暂无可观察样本”不等于“零故障”，不得将缺少样本写成零故障或零 fallback。
- synthetic/non-compute 结果只证明协议、状态和 artifact 合约在该测试范围内可观察，不代表真实计算成功率。

## 原始周期观察指标（按 backend 分层；historical; superseded above）

周期内持续记录并按 backend 分层：

- `control` / `legacy` 各自的实际 run 数量与 backend 选择结果；
- unsupported-protocol fallback 数量、reason code，以及 fallback 是否符合 stable `v1.4.6` 预期；
- unexpected fallback，特别是已选择 `control` 的 run 是否曾降级到 `legacy`；
- protocol negotiation、typed error、malformed JSON、unknown major 的 fail-closed 结果；
- attach/reconnect、cursor replay、revision 单调性与 terminal non-regression；
- duplicate/idempotency conflict；
- cancel/resume 结果、重试次数与失败原因；
- artifact manifest path/digest/size/content-schema/download integrity failure；
- agent SQLite 读取或 producer state 双写证据；
- rollback 到 stable `v1.4.6` 的 legacy 可用性、producer state 隔离与恢复证据。

当前基线为：一条真实 JobDesk `legacy` v1.5.0 两任务样本、一次独立 stable `v1.4.6` rollback probe，以及一条正式 ConfFlow v1.5.3 的真实 JobDesk `control` 计算样本。生产周期统计仍必须按实际 JobDesk 运行数据分别填写 `control` 与 `legacy`；当前正式计数为 `control_backend_runs=1`、`legacy_backend_runs=1`，不代表零故障。该 control 样本的 in-memory reconnect/events/cancel/resume/raw-manifest 响应未持久化，完整发布周期指标仍未收齐。

## 原始周期未满足的 Phase F 条件（historical; superseded above）

- 完整兼容发布周期尚未结束。
- `control` / `legacy` 分层的完整兼容周期指标尚未收齐；当前已记录 `legacy_backend_runs=1`、`control_backend_runs=1`（真实计算样本）、run-scoped `fallbacks=0`，但不能由单一样本推导零故障。
- 支持 launcher 路径的真实 SSH/SFTP v1.5.3 control computation 已完成一次；canonical a32 的 response trace 只持久化一页，补充 a34 trace 已证明 fixed-cursor replay 与 next-page response，但完整周期内的 reconnect/events/cancel/resume/artifact、fallback 和 idempotency 指标仍需收齐。
- stable `v1.4.6` live rollback probe 已完成，但完整 rollback/recovery 维度和兼容周期统计仍未完成。
- agent 保留/弃用决策材料未完成。

## Phase F 边界（仍适用；当前 aggregate decision 不授权进入）

Phase F 仍未授权。最早只能在一个完整发布兼容周期结束后，且上述指标已收集齐全，同时保留支持 launcher 路径的真实 control computation acceptance 与 rollback evidence，才可提出是否移除或保留 legacy backend 的申请；在此之前必须保留双 backend、`v1.4.6` rollback 路径与 fail-closed 门。launcher acceptance 的执行设计见 [`docs/CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md`](CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md)；canonical a32 是当前真实 control 样本，其 response-trace 只持久化一页 events/`next_cursor`，补充 a34 trace 证明了固定 cursor replay 与 next-page response，但 a34 acceptance bundle 不计入 canonical counters，也不替代完整周期验收。

## 2026-08-08 JobDesk legacy-backend real sample (historical; superseded)

After the authorized WSL restart, the real JobDesk legacy backend completed one
isolated batch run (`confflow-batch`) containing water and methane. SSH upload,
detached execution, refresh-to-completion, output-manifest validation, fixed
metadata download, and local `run_summary.json` parsing all passed. Both
summaries were `confflow.run_summary.v1` with `final_conformers=1`; both
manifests were `confflow.output_manifest.v1` with the `quick_opt` terminal.
This current section supersedes the earlier pre-restart snapshot above that
reported no real JobDesk sample.

- local evidence root: `C:\tmp\jobdesk_pytest_real_legacy_20260808_b1`
- remote attempt root: `/tmp/jobdesk_phasef_real_legacy_20260808_b1`
- cleanup: the exact remote root was absent after the test's bounded cleanup
- compatibility counters for this sample: `legacy_backend_runs=1`,
  `legacy_tasks=2`, `control_backend_runs=0`, `fallbacks=0`, task failures `0`
- producer: pinned v1.5.0; this is not the separate stable v1.4.6 rollback
  probe required by the Phase F gate

The previous failed JobDesk attempt remains retained as non-counted failure
evidence. This successful legacy sample does not close the compatibility
period, establish control acceptance, or authorize Phase F: the real control
worker handoff, full rollback/recovery metrics, remote candidate CI, and one
complete published compatibility cycle with measured metrics are still open.

## 2026-08-09 pre-close status snapshot (historical; superseded)

The earlier zero-control-count snapshot is superseded by the released
v1.5.3 sample recorded below. The current formal counters are
`control_backend_runs=1` and `legacy_backend_runs=1`; candidate-only,
synthetic, and historical evidence remains excluded. The compatibility period
continues because a complete measured published cycle has not yet been
collected. The a34 trace closes the fixed-cursor response evidence gap as
supplemental non-counted provenance, but does not change the canonical
counters. Phase F remains not ready.

## 2026-08-08 real JobDesk control launcher acceptance (historical non-compute)

After the same authorized WSL restart, an isolated real JobDesk control path
completed capability negotiation, prepare, input-manifest upload, launcher
script upload, and `nohup` dispatch over SSH/SFTP. The launcher metadata was
read back from durable JobDesk state; a post-dispatch status/events query
returned producer state `queued`, revision `2`, two events, and cursor
`r00000000000000000002`.

- capability: v1.5.0, producer commit
  `0fff6439a4614ec155959b1d0d3781fc5342d736`
- local evidence root: temporary
  `C:\tmp\jobdesk_phasef_control_local_20260808_a1` (removed after capture)
- remote attempt root: `/tmp/jobdesk_phasef_real_control_20260808_a1/attempt`
  (absent after bounded cleanup)
- launcher scheduler: `nohup`; scheduler job id: `762`; state root was the
  attempt-root child `/tmp/jobdesk_phasef_real_control_20260808_a1/attempt/state`
- launcher command, script path, metadata path, log path, script SHA-256 and
  size were all recorded in `jobdesk.confflow.launcher.v1` durable state

This is real JobDesk control launcher evidence, but not a real control
computation sample: the pinned producer intentionally leaves `execute` at a
queued launch intent until an external worker handoff is supplied. No g16 or
ORCA process was started, and no `/opt` path or user state was modified. It
therefore does not close the Phase F control-computation gate.

The post-run WSL audit found `confflow-agent`, but it is an independent queue
worker with its own AgentStateDB and execution-state root; the pinned control
executor does not enqueue work there. Its separate workflow request digest and
state layout cannot be substituted for the JobDesk control request. No agent
was started, and this remains an external worker/release-scope gap rather than
a reason to bypass the control contract.

## 2026-08-08 stable v1.4.6 rollback probe (historical; superseded by a5)

A separate temporary server profile pinned the exact stable rollback executable
`/opt/confflow-1.4.6-prod-venv/bin/confflow`. The same two-molecule JobDesk
legacy path completed over SSH/SFTP; both `run_summary.json` and
`output_manifest.json` files passed validation and the exact remote root
`/tmp/jobdesk_phasef_real_legacy146_20260808_a1` was absent after bounded
cleanup. The local evidence root is
`C:\tmp\jobdesk_pytest_real_legacy146_20260808_a1`.

This closes the live stable rollback probe, but the compatibility period still
has no real JobDesk control computation, because the pinned v1.5.0 producer's
`control execute` remains a queued intent without an external worker handoff.
Phase F remains blocked on real control computation/worker handoff, published
candidate CI, and one complete published compatibility cycle with measured
control/legacy usage and fallback metrics.

## 2026-08-09 ConfFlow v1.5.3 producer release (published)

The producer-owned worker handoff is now published as ConfFlow `v1.5.3`.
The normal merge commit is `f37759954da2818d777ec4d06f81bd53aeafe6e3`,
with parents `147ebfef884e0339b1ece00164e86f1d8202bf60` and the reviewed
candidate `9fdeb7742c77cb2cf7bfebf63f4f4c8595d2f648`. The immutable annotated
tag `v1.5.3` peels to that merge commit. The formal GitHub Release wheel is
`confflow-1.5.3-py3-none-any.whl` with SHA-256
`213eba551b344c7146450fa1135a884e3c00896371507a1edbf2eb18c7c0c5d6`.
Downloaded `SHA256SUMS` verified all seven release assets;
`provenance.json` and `attestation.json` bind the wheel to repository
`moxuezhuchen/ConfFlow`, tag `v1.5.3`, and peeled commit `f377599`.
The exact release inputs are recorded by dependency-lock SHA-256
`a389b56baeaf75d3567175fd0c7c6282423df04ddf42469160fc8b476a1cd376` and
wheelhouse-manifest SHA-256
`ab3a940525f0230dda58e8087dab2e33c29bee6183940f72aee66c4c999adc7c`.

Remote CI passed at runs `31271946187` (full ConfFlow matrix),
`31271946186` (coverage), and `31271946207` (JobDesk Consumer Contract).
The release workflow `31272089279` completed successfully and published the
seven release assets. The connector returned 403 for PR metadata/merge
write operations; after the exact HEAD, checks, and independent review were
revalidated, the user-authorized normal `--no-ff` merge was created in an
isolated worktree and pushed. The SSH push reported a protected-ref PR-rule
bypass; this is recorded as provenance, not described as a connector merge.

The final exact production venv verified the release wheel, tag, attestation,
clean build commit, dependency lock, and wheelhouse manifest provenance.
This publication was followed by one real released-v1.5.3 JobDesk control
computation; the sample and its evidence are recorded below. The old v1.5.2
publication and the `9a5f213`/1.5.1 worker wheel remain historical or
candidate-only evidence and are not stable samples.

## 2026-08-09 released v1.5.3 real JobDesk control sample (a3 historical; superseded by a32)

The isolated JobDesk consumer used the immutable ConfFlow `v1.5.3` release,
peeled commit `f37759954da2818d777ec4d06f81bd53aeafe6e3`, and wheel SHA-256
`213eba551b344c7146450fa1135a884e3c00896371507a1edbf2eb18c7c0c5d6`. The
formal runtime was installed from the release wheel with verified attestation,
clean build provenance, dependency-lock SHA-256
`a389b56baeaf75d3567175fd0c7c6282423df04ddf42469160fc8b476a1cd376`, and
wheelhouse-manifest SHA-256
`ab3a940525f0230dda58e8087dab2e33c29bee6183940f72aee66c4c999adc7c`.

One real JobDesk control computation (`control-g16-v153-20260809-a3`) ran on
the approved remote test environment through the supported `nohup` launcher
and the released
`confflow-control-worker`; it was not candidate-only, synthetic, or historical.
The durable JobDesk control state recorded ConfFlow 1.5.3, schema 4, producer
commit `f377599`, `control_worker=true`, revision 6, and terminal
`completed`. Launcher metadata recorded `execute_rc=0`, `worker_started=true`,
and `worker_rc=0`; the producer log recorded the JSON states `queued` and
`completed`. The first submit dispatched one task, the idempotent resubmit
assertion observed `submitted_task_count=0`, and the reconnect/events/status,
terminal cancel/resume, artifact-manifest, and download stages advanced
without a harness error. A downloaded `output.xyz` was retained with SHA-256
`80dc8335046084e993161be1f631a1995cd6715512d5d74fa0e6e8888393c6f2`.

- local evidence: `C:\tmp\jobdesk-control-release-v153-20260809-a3\evidence.json`
- remote attempt root: `/tmp/jobdesk-control-release-v153-20260809-a3`
- cleanup: the exact attempt root was absent after capture; the separately
  created runtime root `/tmp/jobdesk-confflow-v153-release-f377599-20260809-a1`
  was then removed by an exact path-bound cleanup
- counters after this sample: `control_backend_runs=1`,
  `legacy_backend_runs=1`, run-scoped fallback `0`

The harness timed out only while deleting the large temporary runtime after
the attempt root had already been removed. The retained evidence therefore
marks the in-memory event trace, cancel/resume responses, and raw artifact
manifest as not persisted; it does not reconstruct those values. This is a
real control computation sample, but the evidence bundle is not a complete
compatibility-cycle record. A complete published cycle still requires durable
reconnect/event/cancel/resume/artifact metrics, fallback and idempotency
metrics across the release period, and the remaining rollback/closeout
measurements. Phase F remains **not ready** and the formal decision remains
**COMPATIBILITY PERIOD CONTINUES**.

## 2026-08-09 JobDesk v0.5.1 consumer release (historical period start; superseded)

The matching JobDesk consumer for the formal ConfFlow `v1.5.3` producer is now
published as `v0.5.1` at merge commit
`ebb719b2b67d2095f2199a30c9b97d7f88ac8820`:
`https://github.com/moxuezhuchen/jobdesk/releases/tag/v0.5.1`. The published
wheel is `jobdesk-0.5.1-py3-none-any.whl` with SHA-256
`892efb156e1d59c10018d25107ec54932625a9238067d125cec61801cd3a279e`.
The GitHub Release was published at `2026-08-09T01:52:53Z` after PR #9's
local review, PR checks (`31288788514`, `31288788511`, `31288788509`), normal
merge, and post-merge main checks (`31288979565`, `31288979564`) passed.

This is the first published producer/consumer pin for the v1.5.3
worker-handoff contract and starts the separately measured compatibility
period. It does not close that period. The real v1.5.3 control computation
recorded above remains counted as real control evidence, but it was captured
before this consumer release was published; no period-completion claim or
backdated cycle start is made. Current formal evidence counters remain
`control_backend_runs=1` and `legacy_backend_runs=1`; candidate-only,
synthetic, and historical samples remain excluded.

The period still requires durable runs/fallback/reconnect/idempotency,
resume/cancel, artifact-integrity, and legacy-closeout metrics. Phase F remains
**not ready** and the formal decision remains **COMPATIBILITY PERIOD CONTINUES**.

## 2026-08-09 ConfFlow v1.5.2 producer release (historical, superseded)

The producer-owned worker handoff is now published as ConfFlow `v1.5.2`.
PR #50 was merged at `0043f02bab65ebcfde72fdd2ef27a98371e9d6c1`, whose two
parents are the prior `main` commit `6ab11ddfc5066f70ecc981eafb8d19aa6e5b8785`
and reviewed worker candidate `b8988728d58b3141745cd6b7fb4aa64dffd5f468`.
The immutable annotated tag `v1.5.2` peels to the merge commit. The formal
GitHub Release wheel is
`confflow-1.5.2-py3-none-any.whl` with SHA-256
`4ed977c0454fef8856c4c5604e1c6237918e76ba3b6afe338ae8783de74398d4`.
Downloaded `SHA256SUMS` verified all seven release assets;
`provenance.json` and `attestation.json` bind the wheel to repository
`moxuezhuchen/ConfFlow`, tag `v1.5.2`, and peeled commit `0043f02`.

Remote CI passed at runs `31268370897` (coverage, Python 3.10--3.13,
Black, Ruff, and mypy) and `31268370905` (producer candidate against
JobDesk main). The connector did not have PR-write scope and returned 403;
the exact candidate HEAD, checks, and independent review were revalidated
before the user-authorized normal `--no-ff` merge. The SSH push reported a
protected-ref PR-rule bypass; this is recorded as provenance, not described
as a connector merge.

The JobDesk isolated consumer now pins the formal v1.5.2 commit and wheel
digest in its pending release commit. This publication does not itself add a
compatibility-period run: no released-v1.5.2 JobDesk control computation has
yet completed. The old `9a5f213`/1.5.1 worker wheel and its direct g16 run
remain candidate-only evidence and are not stable samples.

## 2026-08-08 producer candidate worker handoff (not a compatibility sample)

An isolated ConfFlow candidate now supplies the missing producer-owned worker
boundary. The current candidate commit is
`9a5f213`; its clean
`confflow-1.5.1-py3-none-any.whl` has SHA-256
`7c3bdfda3489fccdbd5b096d0ef170fddcd3988dc1f513740da39bdb782a634e`.
The candidate adds `worker-handoff.schema.json`, the `control_worker`
capability, and `confflow-control-worker`, which consumes the existing queued
producer token through `ExecutionService` and never calls `prepare` again.
It also locks the exact canonical handoff digest profile, owner-private
staging, dedicated-session recovery, and fixed sidecar publication before the
terminal completed transition.

After the mandatory g16 probe, the exact candidate wheel ran one isolated real
methane Gaussian 16 optimization in Ubuntu-24.04 WSL through the
`confflow-control-worker` console entrypoint. It completed in 10.149
seconds; producer revisions were `prepared -> queued -> running ->
checkpointed -> completed`. Evidence includes the output manifest,
`methane.txt`, `methanemin.xyz`, workflow summary/stats/state, G16 identity,
handoff digest, and file SHA-256 values. The worker returned exactly one
machine-readable JSON line with exit code 0. Evidence is retained at
`C:\tmp\jobdesk-control-worker-real-9a5f213-evidence.json`. The exact remote
attempt root `/tmp/jobdesk-control-worker-real-9a5f213` was absent after
bounded cleanup.

This candidate is unpublished and is not pinned by the stable JobDesk consumer;
the run therefore does not increment compatibility counters. Current counters
remain `control_backend_runs=0` and `legacy_backend_runs=1`. A real JobDesk
control computation still requires a published producer/consumer pin, the
candidate dual-repository CI, and a complete measured compatibility cycle.
Phase F remains **not ready** and the formal decision remains
**COMPATIBILITY PERIOD CONTINUES**.
