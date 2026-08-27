# 2026-08-19 JobDesk/ConfFlow 全量 remediation 执行收口台账

> 记录日期：2026-08-27。本文只汇总已有证据，不替代授权，也不改写历史记录。
> 状态含义：`completed` 为已有实现/记录；`independently verified` 为有可复核证据；`not executed` 为本计划要求但尚未执行；`blocked` 为有明确阻断。

## 版本与边界快照

- `JobDesk 0.7.3`：隔离分支本地 checkpoint `eb001b5be41758eee462a0e39adac4263408ec96`，未推送、未打 tag、未发布；见 `pyproject.toml`、`CHANGELOG.md`、`docs/superpowers/evidence/2026-08-27-jobdesk-release-closeout.md`。
- `ConfFlow 2.1.4`：隔离分支本地 checkpoint `3291e6e0232815f4e926c4b1d8643450f43fdc5e`，未推送、未打 tag、未发布；基线/已发布 `v2.1.3` 合并 SHA 为 `a7c570431976331bb067b204b6300ba17b1f3da5`。
- 已发布 JobDesk `v0.7.2`：合并 SHA `f63c1ca6d24bb76d25f1df021ddfe745dc3a33a8`，仅有 wheel（SHA256 `a9ef59f788a22c476d7a0558a53df286c7fc93c12ab2afef87c5c8995feb7139`）；现有记录没有完整 sdist、SHA256 manifest、SBOM、provenance/attestation 供应链。
- 已发布 ConfFlow `v2.1.3`：`a7c570431976331bb067b204b6300ba17b1f3da5`；依赖 manifest 的精确 filename 与 provenance 链未闭合，阻断正式 side-by-side。
- 当前配置生产可执行文件仍为 `/opt/confflow-current -> /opt/ConfFlow/.venv/bin/confflow`，报告 `2.0.0`，属于共享 `.venv`；没有批准的生产变更记录。Phase 0 中的旧路径记录保留于 `docs/superpowers/evidence/2026-08-19-full-remediation-phase0.md:30-42`。
- 未执行真实 workload、生产 promotion 或生产 endpoint 切换；正式 released side-by-side 在首个依赖/供应链 gate 停止。
- 2026-08-27 owner 单独授权后，live GitHub 回读确认 JobDesk 与 ConfFlow 两个仓库均为 immutable releases `enabled=true`；并分别启用 active tag ruleset `21647422` / `21647483`，匹配 `refs/tags/v*`，禁止 update/deletion，`bypass_actors=[]` 且 `current_user_can_bypass=never`。GitHub 的 immutable releases 只约束今后创建的 release，因此既有 `v0.7.2` / `v2.1.3` 仍为 `isImmutable=false`；两者的注释 tag 分别仍解引用到 `f63c1ca6d24bb76d25f1df021ddfe745dc3a33a8` / `a7c570431976331bb067b204b6300ba17b1f3da5`。

## Phase 0 — 基线、授权与隔离

- **completed**：已记录 JobDesk/ConfFlow 隔离候选树、共享源与受保护生产边界（`docs/superpowers/evidence/2026-08-19-full-remediation-phase0.md`）。
- **independently verified**：候选路径、历史生产版本及保护约束可由上述 evidence 与当前工作树复核。
- **not executed**：未获授权的共享源/生产环境改造、真实 workload、promotion 均未执行。
- **blocked**：生产边界没有批准记录，不能把候选安装或历史记录当作生产验收。

## Phase 1 — 环境与验证基线

- **completed**：Phase 0 evidence 已登记当时的 Linux/ext4、Windows 与 JobDesk 基线结果；历史记录保持不变。
- **independently verified**：本收口仅复核记录与树状态，未把历史测试数字冒充本轮重跑结果。
- **not executed**：未完成针对最终发布 pair 的 clean、跨环境、远端供应链复验。
- **blocked**：发布候选的依赖文件名/provenance 不完整，环境 gate 不能推进到正式 pair。

## Phase 2 — JobDesk 质量与发布候选

- **completed**：`0.7.3` fix-forward 的 release workflow、构建及其断言已落在候选树；详见 `docs/superpowers/evidence/2026-08-27-jobdesk-release-closeout.md`。
- **independently verified**：该 evidence 记录了本地 release-workflow 断言及 wheel/sdist digest；不扩写为远端 CI 或正式发布。
- **independently verified**：最终供应链 diff 经独立复审 `APPROVED`；Ruff、MyPy（189 个源码文件）通过。完整非集成测试为 `2216 passed, 31 skipped, 6 deselected`，其中 4 个 candidate-gate 用例首次因解释器错误加载 ConfFlow `2.0.0` 失败；显式加载 manifest 绑定的正式 `2.1.3` wheel 后该 4 项 `4 passed`。
- **not executed**：未对 `0.7.3` 打 tag、发布资产或执行生产切换。
- **blocked**：`v0.7.2` 仍缺完整供应链，故 `0.7.3` 不能被宣称为 released closeout。

## Phase 3 — ConfFlow canonical config 与 fixture

- **completed**：canonical config、durable fixture 基础记录见 Phase 0 evidence；2.1.4 候选新增依赖 lock/manifest 修复。
- **independently verified**：可复核 `confflow/fixture_agent.py`、`confflow/cli.py`、`tests/test_fixture_agent.py` 与 `tests/test_release_dependencies.py` 的候选改动。
- **independently verified**：最终 diff 经独立复审 `APPROVED`；32 个定向测试与复审的 20 个定向测试通过，21-wheel lock/manifest closure 对齐。2.1.4 wheel/sdist 本地构建成功，隔离 Windows venv 验证 fixture agent 与主 CLI 分别精确报告各自 `.exe`。
- **not executed**：未发布 2.1.4，未以该候选替换共享生产环境。
- **blocked**：Windows fixture executable identity 缺陷（无 `.exe` 时必须绑定 sibling `.exe`，并包含 realpath/device_inode）仍属于未发布修复。

## Phase 4 — JobDesk per-server config / WorkflowSpec

- **completed**：Phase 0 evidence 记录了 per-server config、WorkflowSpec admission 及对应 focused checks；实现仍在候选树。
- **independently verified**：可复核 `docs/architecture.md` 与 Phase 0 的绑定/准入说明；没有新增全链路验收结论。
- **not executed**：未在正式 released pair 上完成远端提交、恢复与真实 workload 验收。
- **blocked**：依赖/生产身份边界未闭合，不能将候选准入记录升级为正式发布验收。

## Phase 5 — schema v7、SQLite 与控制决策

- **completed**：schema v7 的 immutable `run_configuration_bindings` 与 SQLite authoritative control decision 已实现/记录；见 `docs/superpowers/evidence/2026-08-19-full-remediation-phase0.md:328-331`、`docs/architecture.md`。
- **independently verified**：控制所有权、digest、contract/executable identity 与不可绕过约束可由上述文档和候选源码复核。
- **not executed**：未把该候选控制后端切入生产，也未用真实 workload 验证 release pair。
- **blocked**：side-by-side 的 released dependency/provenance gate 先失败，控制决策不能作为整体 DoD 通过。

## Phase 6 — engine、worker 与执行身份

- **completed**：候选树包含 engine/worker 与执行服务的分层实现；ConfFlow 候选的身份校验位于 `confflow/application/execution/service.py`。
- **independently verified**：候选源码和 2.1.4 针对 identity/dependency 的测试文件可复核；本台账未声称这些测试已在正式 pair 运行。
- **not executed**：未启动 released 2.1.3/JobDesk pair 的真实计算或正式 launcher 验收。
- **blocked**：Windows launcher identity 修复已在本地 checkpoint 中提交但尚未发布，且 v2.1.3 provenance 链不足。

## Phase 7 — monitor、GUI 与状态展示

- **completed**：现行架构文档已区分普通 `SessionPool` 与 monitor transport，并保留 state ownership 说明（`docs/architecture.md`）。
- **independently verified**：文档与候选模块边界可复核；没有把 offscreen/历史 GUI 结果扩写为本次 released side-by-side 结果。
- **not executed**：未完成正式 pair 的 GUI/monitor 全链路与真实 workload 验收。
- **blocked**：上游 released dependency/provenance 首 gate 已阻断，故本阶段不能形成独立发布结论。

## Phase 8 — 文档、供应链、side-by-side 与 promotion

- **completed**：README/架构文档已区分 shared source、isolated candidate、released package、configured production executable；历史 evidence 未改写。
- **completed**：owner 授权的 GitHub 发布保护 gate 已执行并回读：两个仓库均启用 future immutable releases 与不可绕过的 `v*` tag update/deletion ruleset；未借此授权推送、建 PR、打 tag 或发布。
- **independently verified**：JobDesk `v0.7.2` 发布资产与 digest 见 `docs/superpowers/evidence/2026-08-27-jobdesk-release-closeout.md`；其未来 `0.7.3` workflow 仍是候选。
- **not executed**：未执行 `0.7.3`/`2.1.4` 发布、正式 released side-by-side 后续 gate、真实 launcher/workload、生产 promotion。
- **blocked**：released side-by-side 在首个 dependency/provenance gate 停止；`v2.1.3` manifest filename/provenance 链、JobDesk `v0.7.2` 完整供应链及 Windows fixture identity 均未闭合。

## 总体判定

- 计划尚未完成：`0.7.3`/`2.1.4` 已形成独立复审通过的本地 checkpoint，但均未推送、未发布；生产仍保持共享 `.venv` `2.0.0`。
- `completed` 与 `independently verified` 仅限上列证据；`not executed` 不得解释为通过；`blocked` 不得由候选测试替代。
- 最终独立代码复审：JobDesk 与 ConfFlow 候选均为 **APPROVED**。GitHub 发布保护设置 gate 已完成；外部发布 gate 仍为 **BLOCKED**：本地候选尚未获推送/建 PR/合并/发布授权，历史 release 的供应链缺口也未被设置变更补齐。随后正式 pair side-by-side、真实 workload 与 promotion 仍需各自单独授权，因此不得宣称 release/production DoD 通过。
