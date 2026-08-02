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
- 全程未运行真实 worker、Gaussian 或 g16；仅使用 synthetic/non-compute lifecycle 与真实 SSH 通道。

正式发布 artifacts 已发布并远端验证；`v1.4.6` 环境保留，`v1.4.6` tag 未修改。

## 样本边界

本记录必须区分正式 Gate/稳定回滚 probe 与兼容周期内的真实 JobDesk 运行样本：

- 当前只有 synthetic/non-compute control lifecycle 与 stable `v1.4.6` probe。
- 当前没有兼容周期内真实 JobDesk `control` 或 `legacy` run 样本；本地历史运行记录不在本周期起始时间之后，不能作为本周期样本。
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

当前起始基线仅为：`control` synthetic/non-compute lifecycle 选择并保持 `control`，stable `v1.4.6` probe 选择 `legacy`；这两项是可追溯的验收/探针结果，不是兼容周期运行统计。生产周期统计必须来自实际 JobDesk 运行数据，并分别填写 `control` 与 `legacy`；在获得真实样本前，相关栏位应记录为“暂无样本”，不能推导零故障。

## 当前未满足的 Phase F 条件

- 完整兼容发布周期尚未结束。
- `control` / `legacy` 分层的真实运行、fallback、reconnect、cursor、cancel/resume、artifact integrity 指标尚无样本。
- 支持 launcher 路径的真实 control acceptance 未完成。
- complete live rollback/recovery evidence 未完成。
- agent 保留/弃用决策材料未完成。

## Phase F 边界

Phase F 仍未授权。最早只能在一个完整发布兼容周期结束后，且上述指标已收集齐全，同时完成支持 launcher 路径的真实 control acceptance 与 rollback evidence，才可提出是否移除或保留 legacy backend 的申请；在此之前必须保留双 backend、`v1.4.6` rollback 路径与 fail-closed 门。launcher acceptance 的执行设计见 [`docs/CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md`](CONFFLOW_1_5_0_LAUNCHER_ACCEPTANCE_DESIGN.md)，其中所有实际运行步骤均待单独授权。
