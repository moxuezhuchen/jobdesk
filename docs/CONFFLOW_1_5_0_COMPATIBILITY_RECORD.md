# ConfFlow 1.5.0 × JobDesk 兼容发布周期记录

## 周期边界与不可变 provenance

- 兼容周期真实 UTC 起始时间：`2026-08-01T15:57:13Z`
- JobDesk `main`：`9904cbaae078344bb35162f3ddee354b1acd040c`
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

## 兼容周期观察指标

周期内持续记录并按 backend 分层：

- 各 backend 的 run 数量，以及 unsupported-protocol fallback 次数与 reason code；
- protocol、attach/reconnect、cursor/revision 失败；
- duplicate/idempotency conflict；
- resume/cancel 结果；
- artifact manifest/download integrity 失败；
- 周期结束时仍在使用 legacy backend 的比例与原因；
- control typed error、malformed JSON、unknown major 的 fail-closed 计数。

本周期起始验收基线为：control 与 legacy 双 backend 各有明确、可追溯的选择结果；正式 control synthetic lifecycle 通过；未观察到意外 fallback、SQLite 读写或 artifact integrity failure。生产周期统计以实际 JobDesk 运行数据为准，不把本次 synthetic 验收冒充真实计算成功。

## Phase F 边界

Phase F 仍未授权。最早只能在一个完整发布兼容周期结束后，且上述指标已收集齐全，同时完成支持 launcher 路径的真实 control acceptance 与 rollback evidence，才可提出是否移除或保留 legacy backend 的申请；在此之前必须保留双 backend、`v1.4.6` rollback 路径与 fail-closed 门。
