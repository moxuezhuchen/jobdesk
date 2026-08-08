# ConfFlow 1.5.0 × JobDesk 兼容发布周期记录

## 周期边界与不可变 provenance

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

## Gate 与双 backend 验收

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

## 2026-08-08 Phase F readiness recheck (not entered)

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

## 样本边界

本记录必须区分正式 Gate/稳定回滚 probe 与兼容周期内的真实 JobDesk 运行样本：

- 当前有一条兼容周期内真实 JobDesk `legacy` 样本（固定 v1.5.0、两任务），以及一条独立 stable `v1.4.6` rollback probe；另有真实 JobDesk `control` launcher 的 queued、非计算 handoff 证据。direct producer/external-program evidence 不计入兼容周期样本。
- 当前真实 JobDesk `control` 计算样本数为 `0`：pinned producer 的 `control execute` 只返回 queued launch intent，尚无 external worker handoff。真实 JobDesk `legacy` 运行数为 `1`；本地历史运行记录和重启前失败尝试不计入该计数。
- “暂无可观察样本”不等于“零故障”，不得将缺少样本写成零故障或零 fallback。
- synthetic/non-compute 结果只证明协议、状态和 artifact 合约在该测试范围内可观察，不代表真实计算成功率。

## 兼容周期观察指标（按 backend 分层）

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

当前基线为：一条真实 JobDesk `legacy` v1.5.0 两任务样本、一次独立 stable `v1.4.6` rollback probe，以及一条真实 SSH/SFTP `control` launcher queued 非计算验收。生产周期统计仍必须按实际 JobDesk 运行数据分别填写 `control` 与 `legacy`；当前 control 计算样本为 `0`，不代表零故障。完整 reconnect/cursor、idempotency、resume/cancel、artifact-integrity 与 fallback 维度仍未达到完整周期统计要求。

## 当前未满足的 Phase F 条件

- 完整兼容发布周期尚未结束。
- `control` / `legacy` 分层的完整兼容周期指标尚未收齐；当前已记录 `legacy_backend_runs=1`、`control_backend_runs=0`（计算样本）、`fallbacks=0`，但不能由单一样本推导零故障。
- 支持 launcher 路径的真实 SSH/SFTP queued handoff 已完成一次非计算验收；pinned producer 的 `control execute` 仍需要一个明确的 worker handoff 才能从 queued 意图进入真实计算。
- stable `v1.4.6` live rollback probe 已完成，但完整 rollback/recovery 维度和兼容周期统计仍未完成。
- agent 保留/弃用决策材料未完成。

## Phase F 边界

Phase F 仍未授权。最早只能在一个完整发布兼容周期结束后，且上述指标已收集齐全，同时完成支持 launcher 路径的真实 control computation acceptance 与 rollback evidence，才可提出是否移除或保留 legacy backend 的申请；在此之前必须保留双 backend、`v1.4.6` rollback 路径与 fail-closed 门。launcher acceptance 的执行设计见 [`docs/CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md`](CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md)；其中已授权并完成的 queued/non-compute handoff 与 rollback probe，不替代仍待单独授权的 worker handoff、真实 control computation 及完整周期验收。

## 2026-08-08 JobDesk legacy-backend real sample (current evidence)

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

## 2026-08-08 real JobDesk control launcher acceptance (non-compute)

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

## 2026-08-08 stable v1.4.6 rollback probe (current evidence)

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
