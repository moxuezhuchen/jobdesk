# ConfFlow / JobDesk Compatibility Record (historical mirror with current index)

> Current authoritative summary (2026-08-09): use
> [`CONFFLOW_1_5_0_COMPATIBILITY_EVIDENCE_INDEX.json`](CONFFLOW_1_5_0_COMPATIBILITY_EVIDENCE_INDEX.json)
> and the current a32/a36/a37/a38/a5 entries in the release-boundary documents. The
> supplemental a34 fixed-cursor response trace is recorded separately at
> `C:\tmp\jobdesk-control-release-v153-20260809-a34\events-readonly-trace.json`;
> it is `acceptance_failed=true`, `synthetic=false`, and non-counted.
> The separate a35 released workflow reached producer revision `6` and
> `completed`, but its harness stopped before replay/download capture because
> status-only polling had not persisted a JobDesk cursor. Its read-only terminal
> capture is `C:\tmp\jobdesk-control-release-v153-20260809-a35\post-failure-readonly.json`;
> it is also `acceptance_failed=true`, `synthetic=false`, and non-counted.
> The separately authorized a36, a37, and a38 runs at
> `C:\tmp\jobdesk-control-release-v153-20260809-a36\evidence.json`,
> `C:\tmp\jobdesk-control-release-v153-20260809-a37\evidence.json`, and
> `C:\tmp\jobdesk-control-release-v153-20260809-a38\evidence.json` are
> canonical released control computations: each reached revision `6` and
> `completed` with `fallback_used=false`, proved reconnect, idempotent submit,
> fixed-cursor replay, terminal empty page, typed terminal cancel/resume
> rejection, and manifest/download SHA-256 integrity. Each exact per-attempt
> root was absent after bounded cleanup; the shared published runtime remains
> intentionally retained.
> v1.5.0 sections below are retained for provenance only and are superseded;
> their zero-control, candidate, synthetic, and incomplete-response claims
> must not be used as current counters. The formal decision remains
> **COMPATIBILITY PERIOD CONTINUES** and `phase_f_ready=false`. The current
> formal counters are `control_backend_runs=4` and `legacy_backend_runs=1`.

## Period-wide metric contract (authoritative; currently open)

The machine-readable `period_metric_contract` in the evidence index now defines
the remaining compatibility gate. A new observation window starts only with the
first newly authorized real workload after this contract is published on the
merged JobDesk `v0.5.1` / ConfFlow `v1.5.3` pair. It must remain open for at
least 72 hours and include at least three real `control` attempts and two real
`legacy` attempts, all of which must be eligible completed successes after
independent index promotion. Every authorized attempt must have a terminal
classification; failed, cancelled, or uncertain attempts stay in the denominator,
never become stable success samples, and block close until a replacement window
is authorized after review.

The closeout thresholds are zero unexpected selected-control-to-legacy
fallbacks, duplicate idempotency conflicts, protocol/reconnect/cursor failures,
artifact-integrity failures, orphan jobs/processes, unclassified attempts,
failed attempts, cancelled attempts, and uncertain attempts.
The window also requires one in-flight control reconnect recovery, one control
contract cancel or typed policy observation, one live legacy rollback/recovery
probe, and one retained failure or explicitly non-counted negative probe. Legacy
usage at close and the retain/remove decision must be recorded separately.
The synthetic fixture workflow below is protocol-only preflight and cannot start
or satisfy this real-workload window. The existing a32/a36/a37/a38/a5 bundles remain canonical release-boundary
evidence and formal aggregate counters, but are explicitly excluded from this
new window denominator. Phase F remains false until the contract is closed and
independently reviewed.

### Post-contract window observation (blocked; replacement required)

The first post-contract window `post-contract-20260809` is blocked. Supplemental
a39 was a real completed control run but was not eligible because its bundle did
not bind the in-flight disconnect/reopen timing and protected external executable
identity required by the profile. Control a40 captured those checks and reached
completed computation, but its harness raised `NameError: persist` while
finalizing evidence after cleanup; its bundle is therefore
`acceptance_failed=true`, `failed_attempts=1`, and non-counted. The failed attempt
is retained as denominator evidence and cannot be silently removed or retried.
Because failure-retention was not satisfied after the cleanup-time persistence
bug, a newly authorized replacement window with a new unique attempt root is
required. The formal counters remain `control_backend_runs=4` and
`legacy_backend_runs=1`; Phase F remains false.

The first replacement window `post-contract-replacement-20260809` is also
blocked by a41. Its real control computation completed, but the harness failed
the pre-cleanup JSON round-trip assertion; the exact remote attempt root and
logs remain retained (`remote_attempt_root_absent=false`). a41 is a failed
denominator observation and is not a stable sample. It cannot be retried in
place.

The newly authorized replacement window
`post-contract-replacement-20260809-r2` is now **blocked** by a45. Independent
index review had promoted a44 as one eligible completed control success and
a42/a43 as two eligible completed legacy successes for r2 only; all raw bundles
remain `counts_as_real_run=false`/supplemental. a45 reached computation
revision 6/completed and its pre-cleanup evidence snapshot parsed, but the
harness then compared JSON-decoded lists with in-memory tuples during the
post-cleanup final-evidence verification and raised `RuntimeError`. Its raw
bundle is `acceptance_failed=true`, `failed_denominator=true`, SHA-256
`994f3fb4db6ff9913d5a1504e12b6e6417af3372b996c28bfbcbf03fddb67b51`, and
non-counted. The exact attempt root was already removed before that verification
failure (`remote_attempt_root_absent=true`), so a45 cannot satisfy the required
failure-retention scenario. r2 records `attempted=4`, `submitted=4`,
`terminal=4`, `failed_attempts=1`, and cannot be retried in place; its successes
cannot carry into another window. Phase F remains false.

After independent review of a45, the user-authorized replacement window
`post-contract-replacement-20260809-r3` opened at
`2026-08-09T14:45:58.5234809Z` with fresh roots and no inherited denominator.
It is now **blocked** by a46: the real computation reached revision 6/completed
and the main evidence was durably persisted before cleanup, but the harness
inverted the shared-runtime retention check (`not exists` was treated as
retained) and raised after attempt-root cleanup. The cleanup-proof sidecar was
not produced. Main evidence SHA-256 is
`1a0813fa4d58293c75840256e403281c560520cf3c3db35991946e4c592438f0`; the
authoritative failure marker SHA-256 is
`9081edbbc7fc7d0ed9cf6af4a9ff0912e1750bcb91268380dddd231276aab9ac`.
a46 is `acceptance_failed=true`, `failed_denominator=true`, and non-counted;
it cannot satisfy failure retention or be promoted. r3 records
`attempted=1`, `submitted=1`, `terminal=1`, `failed_attempts=1` and cannot be
retried in place. Phase F remains false.

After independent review of a46, the user-authorized replacement window
`post-contract-replacement-20260809-r4` opened at
`2026-08-09T15:11:36.8267925Z` with fresh roots and no inherited denominator.
Independent index review promoted a47 and a48 as the first two eligible
completed control successes for r4. Their raw evidence remains
`counts_as_real_run=false`/supplemental; the separate cleanup-proof SHAs
`c3bab86268db3b72af74e884ee3ee25369274e00bb22cc8f056f9cee1322e070` and
`11bdf5cbddbfc43ccc380833f035df90cf6f8b218387b53c1abf2430dc2cd1eb` verify
exact attempt-root removal and shared-runtime retention. a50 is a failed
denominator observation: its immutable local bundle failed during JobDesk run
initialization because the harness passed unsupported `confflow_executable` to
`ConfFlowAdapter.build_spec`; it reached no submit or SSH boundary and remains
non-counted with its local evidence/workspace retained. Current r4 metrics are
`attempted=3`, `submitted=2`, `terminal=3`, `failed_attempts=1`, eligible
successes control=2/3 and legacy=0/2, so r4 is **BLOCKED** and cannot be retried
in place. a49 was an earlier pre-execution import failure with no root or
bundle and is outside the denominator. A newly authorized r5 window with a
new unique root/run is required; r3/r2, a40/a41/a45/a46, candidate, synthetic,
historical, and incomplete evidence do not satisfy r4. Phase F remains false.

## 周期边界与不可变 provenance（historical; superseded below）

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

不得修改或覆盖 `v1.4.6`。本记录绑定的是正式、非 editable 的 `v1.5.0` wheel；被拒绝的旧候选 digest `f90e5c605ccb36cf37b16dcd53093cb3ac0239e630aaf0a082faa39998615e69` 不属于发布物。

## Gate 与双 backend 验收（historical; superseded below）

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
> and the released-v1.5.3 control samples above; the counters at the time were
> `control_backend_runs=1` and `legacy_backend_runs=1`.

本记录必须区分正式 Gate/稳定回滚 probe 与兼容周期内的真实 JobDesk 运行样本：

- 该历史快照当时有一条兼容周期内真实 JobDesk `legacy` 样本（固定 v1.5.0、两任务），以及一条独立 stable `v1.4.6` rollback probe；另有真实 JobDesk `control` launcher 的 queued、非计算 handoff 证据。direct producer/external-program evidence 不计入兼容周期样本。
- 该历史快照当时真实 JobDesk `control` 计算样本数为 `0`：pinned producer 的 `control execute` 只返回 queued launch intent，尚无 external worker handoff。真实 JobDesk `legacy` 运行数为 `1`；本地历史运行记录和重启前失败尝试不计入该计数。
- “暂无可观察样本”不等于“零故障”，不得将缺少样本写成零故障或零 fallback。
- synthetic/non-compute 结果只证明协议、状态和 artifact 合约在该测试范围内可观察，不代表真实计算成功率。

## 兼容周期观察指标（按 backend 分层；historical; superseded above）

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

## 当前未满足的 Phase F 条件（historical mirror; current decision above）

- 完整兼容发布周期尚未结束。
- `control` / `legacy` 分层的完整兼容周期指标尚未收齐；当前已记录 `legacy_backend_runs=1`、`control_backend_runs=1`（真实计算样本）、run-scoped `fallbacks=0`，但不能由单一样本推导零故障。
- 支持 launcher 路径的真实 SSH/SFTP v1.5.3 control computation 已完成一次；canonical a32 的 response trace 只持久化一页，补充 a34 trace 已证明 fixed-cursor replay 与 next-page response，但完整周期内的 reconnect/events/cancel/resume/artifact、fallback 和 idempotency 指标仍需收齐。
- stable `v1.4.6` live rollback probe 已完成，但完整 rollback/recovery 维度和兼容周期统计仍未完成。
- agent 保留/弃用决策材料未完成。

## Phase F 边界

Phase F 仍未授权。最早只能在一个完整发布兼容周期结束后，且上述指标已收集齐全，同时保留支持 launcher 路径的真实 control computation acceptance 与 rollback evidence，才可提出是否移除或保留 legacy backend 的申请；在此之前必须保留双 backend、`v1.4.6` rollback 路径与 fail-closed 门。launcher acceptance 的执行设计见 [`docs/CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md`](CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md)；canonical a32 是已完成但 response-trace 只持久化一页的真实 control 样本，补充 a34 trace 证明了固定 cursor replay 与 next-page response，但不替代完整周期验收。

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

## 2026-08-09 pre-a37 status snapshot (historical; superseded)

The earlier zero-control-count snapshot is superseded by the released
v1.5.3 samples recorded above. At that time the formal counters were
`control_backend_runs=1` and `legacy_backend_runs=1`; candidate-only,
synthetic, and historical evidence remains excluded. The compatibility period
continues because a complete measured published cycle has not yet been
collected. The a34 trace closes the fixed-cursor response evidence gap as
supplemental non-counted provenance, but does not change the canonical
counters. Phase F remains not ready.

## 2026-08-08 real JobDesk control launcher acceptance (historical non-compute; superseded)

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

## 2026-08-09 released v1.5.3 real JobDesk control sample (historical a3; superseded by canonical a32)

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
