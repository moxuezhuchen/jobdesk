# JobDesk / ConfFlow Post-M2 验收失败修复计划

**日期：** 2026-07-30  
**状态：** 待批准执行  
**性质：** Release closure 修复；不包含路线 B control protocol / ExecutionService 重构  
**JobDesk 基线：** `44719e9415071933a9edb1021182788871be7e9c`  
**ConfFlow 原基线：** `10e457daa92a2f6c48608428197cc3503b82d08f`  
**默认修复版本：** `1.4.5`

## 1. 修复目标

本计划只修复 2026-07-30 独立验收发现的 release closure 问题：

1. 取消对 dirty candidate wheel 的生产认可，建立 clean、可审计的最终 release；
2. 完成尚未闭环的 JobDesk producer/consumer contract、版本 pin、CI 和 executable identity 集成；
3. 修复 checkpoint `opt -> SP guess=read` 的 ConfFlow 解析失败；
4. 移除 smoke 脚本把失败返回码改判为 PASS 的旁路；
5. 重新执行 Gate A、Gate B、Gate C、M2-4A 和经单独授权的 M2-4B；
6. 输出可以逐项复核的 commit、tag、wheel、provenance、测试和 g16 identity 证据。

以下内容不在本计划范围：

- 路线 B 的 `ConfFlowClient`、`RemoteRunHandle`、`ExecutionService`、`control --json`；
- monorepo、submodule 或 agent backend 改造；
- 修改 `/opt/g16/g16`、`/opt/g16/l1.exe` 或 `/opt/g16/bsd/g16.profile`；
- 清理 `/opt/ConfFlow` 现有 4 个用户修改文件；
- 删除旧 production venv。

路线 B 后续阶段保持冻结，直至本计划全部验收通过。

## 2. 已确认的失败基线

执行前必须重新核对，但不得把以下事实改写为“已通过”：

1. 本地 annotated `v1.4.4` 指向 `fe4020da0b21b76632bd042619a6de3540c8d1e6`；
2. 该 tag tree 中 `pyproject.toml` 仍为 `1.4.3`；
3. tagged worktree 另有 4 个未提交变更：版本修改、installer shebang fix、依赖安装行为和对应测试；
4. 已安装 wheel/capability 报告 `build.dirty=true`；
5. candidate wheel SHA256 为 `7f2a661ee5cc11b298fefde705787f873b054a213afc5a43cd272a273cb4511b`；
6. 一次 tagged-final rebuild 的 SHA256 为 `d75c41212c03a9963c50e00fb8edcceb64edb7e0c8b9148b7703c5eb954f5342`；两者不同本身不一定是缺陷，但证明“重建哈希一致”的旧验收记录无效；
7. 远端不存在 `refs/tags/v1.4.4`，因此 Gate B 的远端发布验证未完成；
8. JobDesk 仍有 1.4.3 dependency/CI/docs/test pin，artifact contract 仍只有五字段；
9. JobDesk targeted acceptance 当前为 `8 failed, 53 passed, 16 skipped`；
10. checkpoint smoke 的 `.workflow_state.json` 为 `final_status=failed`，step 07 manifest 为 `failed`，且缺少最终 summary/stats/output manifest；
11. 单步 methane opt 真实计算通过，两次 Gaussian 进程均有 normal termination；这些事实不能覆盖 ConfFlow workflow 失败。

## 3. 不可突破的规则

### 3.1 Git、tag 与用户修改

1. 不得 reset、checkout 覆盖、stash 或删除两个仓库的现有用户修改；
2. 当前错误的本地 `v1.4.4` 不得 push；
3. 默认不移动、不删除、不重建 `v1.4.4`，修复发布使用 `v1.4.5`；
4. 如果用户明确要求继续使用 `1.4.4`，执行者必须先停止并请求“删除并重建仅本地 tag”的单独授权，同时再次证明远端 tag 和外部 release 均不存在；
5. tag 创建、push、release artifact 上传、持久 server config 修改均是独立授权动作；
6. source commit、tag peeled commit、wheel build commit 必须是同一个 clean commit。

### 3.2 Wheel 与 provenance

1. Gate A candidate 和 Gate B tagged-final 是两次独立构建；不要求二者 wheel 字节哈希相同；
2. tag annotation 不写 Gate A candidate wheel 哈希，因为最终 wheel 尚未产生；
3. Gate B 只信任从 clean tag checkout 一次性构建的最终 wheel；
4. 最终 wheel 的实际 SHA256、`SHA256SUMS`、attestation subject、install provenance 和 JobDesk reference fixture 必须完全相同；
5. `build.dirty` 必须为 `false`；任何测试不得把 `dirty=true` 固化为 production 预期；
6. production 安装必须使用新版本化 venv，不覆盖 `/opt/confflow-1.4.4-prod-venv` 或 `/opt/ConfFlow/.venv`。

### 3.3 g16

1. 修复 parser 和 smoke 脚本时只使用现有日志/fixture，不先运行真实计算；
2. 重新运行 M2-4B 前必须取得新的明确授权；
3. 运行前后分别记录 `/opt/g16/g16`、`/opt/g16/l1.exe` 的 realpath、size、mtime、device/inode 和 SHA256；
4. 任一 identity 变化立即停止，不继续第二个 smoke。

## 4. 实施阶段

### Phase 0 — 冻结失败证据并建立安全工作区

**责任仓库：** 两仓库  
**目标：** 保留当前现场，避免在错误 tag 或用户工作树上继续叠加。

步骤：

1. 记录 JobDesk、`/opt/ConfFlow`、`/tmp/confflow-1.4.4-stage1` 的 `git status --short --branch`、HEAD、remote 和 tag refs；
2. 保存当前 `v1.4.4` tag object、peeled commit、tag tree 版本和远端 tag 查询结果；
3. 保存两个 wheel 的实际 SHA256、现有 install-provenance 和 capability JSON；
4. 保存两个 M2-4B 输出目录中的 state、manifest、summary/stats 存在性和 Gaussian 日志信号；
5. 从已提交的 Phase 1 producer commit 建立新的隔离 repair worktree/branch，把属于 release 的 4 个未提交修改逐项审核后迁入；不得直接复用 dirty worktree 构建；
6. JobDesk 在当前用户修改基础上工作，但先列出重叠文件；有不明来源的重叠时停止并报告。

**验收：** 失败证据完整；没有 tag、远端、venv、g16 或用户工作树变更。

### Phase 1 — ConfFlow clean release candidate

**责任仓库：** ConfFlow  
**目标版本：** `1.4.5`

步骤：

1. 将 `pyproject.toml`、`confflow/__init__.py` fallback、CHANGELOG、README、release docs 和版本测试统一到 1.4.5；
2. 将 installer shebang fix 和对应回归测试纳入正式 commit；
3. 审核 installer 依赖安装模型：依赖按批准 lock/wheelhouse 安装，目标 ConfFlow wheel 仍使用 `pip install --no-deps <exact-wheel>`，不得用临时删掉 `--no-deps` 掩盖环境问题；
4. 保留并验证 Phase 1 producer 功能：
   - `__build__.py` 只包含 commit/dirty build provenance；
   - `install_provenance.py` fail closed；
   - DAG legacy lazy deprecation；
   - capability schema v4、六字段 artifacts、四个完整内容 schema；
5. 修复 executable identity 生成与安装后 probe，使 clean environment 下 executable path、Python、package、build 和 sys.prefix 属于同一个目标 venv；
6. 增加测试，拒绝 provenance 中 package/version/commit/digest/tag/repository/executable 任一不一致；
7. 完成代码和文档后提交，确认 worktree clean；
8. 从该 clean commit 构建 Gate A candidate wheel，验证 wheel 内 commit 等于 HEAD 且 dirty=false；
9. candidate 只安装到新的 candidate venv，不激活为 production，不修改 JobDesk pin。

验证至少包括：

- producer targeted tests；
- producer 全量 non-real-compute tests；
- Ruff、MyPy、build/wheel inspection；
- candidate install、capability、dry-run、resume-state 非计算 smoke；
- source/editable/missing/invalid provenance 均不能通过 production validator。

**Gate A：** 全绿后停止，提交证据并请求创建和 push `v1.4.5` annotated tag 的授权。

### Phase 2 — Tagged-final release 与 production candidate

**责任仓库：** ConfFlow  
**前置：** Gate A 通过且用户授权 tag/release 动作。

步骤：

1. 在 Gate A clean commit 创建 annotated `v1.4.5`；annotation 记录版本、commit 和 release intent，不记录 candidate wheel 哈希；
2. 新建 clean tag checkout，确认 `git status` 无修改、版本为 1.4.5；
3. 清空专用 dist 后只构建一次最终 wheel/sdist；
4. 验证 wheel metadata、`__build__.COMMIT`、`DIRTY=false` 和 `v1.4.5^{}`；
5. 为最终 wheel 生成 `SHA256SUMS`、SBOM 和绑定 repository/tag/peeled commit 的 attestation；
6. 安装到全新 `/opt/confflow-1.4.5-prod-venv`，写入并复核 install-provenance；
7. 以 clean environment 调用绝对路径的 `confflow --capabilities --json`，核对：
   - version 1.4.5；
   - schema v4、六字段 artifacts；
   - build/producer build dirty=false；
   - wheel digest 等于最终 wheel；
   - install provenance status=verified；
   - executable path 位于 1.4.5 venv；
8. push tag 和上传 artifacts 后，从远端重新查询 tag peeled commit 和 release artifact digest；
9. 未完成远端 ref/artifact 验证前，只能称为 local production candidate，不能宣布 Gate B 通过。

**Gate B：** tag、remote ref、最终 wheel、checksum、attestation、安装记录和 capability 全部绑定同一 clean commit/digest。

### Phase 3 — JobDesk Gate C consumer integration

**责任仓库：** JobDesk  
**前置：** Gate B 已有可复核的远端 producer 证据。

步骤：

1. 将以下位置统一到 1.4.5：
   - `pyproject.toml` dependency；
   - `core/confflow_contract.py::MIN_VERSION`；
   - `.github/workflows/ci.yml` 两处 checkout、wheel glob 和版本断言；
   - `.github/workflows/optional-coverage.yml`；
   - `tests/test_version_consistency.py`；
   - README 和部署文档；
2. `ConfFlowArtifactContract` 和 `EXPECTED_ARTIFACTS` 增加必填 `output_manifest`，同步 `_parse_artifacts()`；
3. 增加并使用四个完整 schema 常量：
   - `confflow.run_summary.v1`；
   - `confflow.workflow_stats.v1`；
   - `confflow.workflow_state.v1`；
   - `confflow.output_manifest.v1`；
4. 实现 output manifest parser：验证 schema、terminal、相对路径、文件列表、重复目标和工作目录边界；
5. 下载只接受 manifest 声明的安全相对路径；拒绝绝对路径、`..`、symlink escape 和冲突目标；
6. production validator 明确拒绝 dirty、unknown、development、unverified、digest mismatch 和 executable mismatch；
7. 删除 `dirty is True` 的测试旁路，固定最终 wheel `dirty is False`；
8. 完成 probe 后、runner exec 前的 immutable executable identity guard，覆盖 normal/resume × nohup/scheduler；
9. CI 和本地 parity test 必须使用 Gate B 最终 wheel/tag/peeled commit，而不是 candidate wheel；
10. 不修改持久 server config；先用显式 1.4.5 executable 完成验证。

验证至少包括：

- version consistency；
- contract/preflight/results/output-manifest；
- executable binding 与 submitter/runner；
- targeted pytest；
- 全量 non-integration pytest；
- Ruff、MyPy、offscreen smoke、`git diff --check`；
- 真实 WSL 非计算 capability/dry-run/resume-state/parity smoke。

**Gate C：** 全部测试通过，JobDesk 接受 clean 1.4.5，拒绝现有 dirty 1.4.4；没有远端计算副作用。

### Phase 4 — 修复 checkpoint runner/parser 与 smoke 判定

**责任仓库：** ConfFlow + JobDesk  
**本阶段先不运行 g16。**

ConfFlow 步骤：

1. 使用已保存的 step 07 Gaussian log 和 manifest 建立最小失败 fixture；
2. 写失败测试复现 `SP guess=read` 出现 Gaussian normal termination、`SCF Done: E(RHF)`，但 parser 报 `No energy parsed`/无 output XYZ；
3. 确认根因位于 energy、geometry 或 output-XYZ 生成的哪一层，再做最窄修复；不得对任意 parse error 统一视为成功；
4. 验证 RHF/UHF/ROHF 和已有 DFT/优化输出不回归；
5. workflow 只有在 step 07 manifest completed、output XYZ 可用、最终 state completed 后才能返回 0。

JobDesk smoke 脚本步骤：

1. 删除 `rc==2` 的 PASS 分支；任何非零 ConfFlow 返回码均失败；
2. 分别读取 step 06、step 07 的独立日志，分别断言 Gaussian normal termination，不能在拼接输出中做模糊双匹配；
3. 自动断言四行 preflight，第四行必须精确确认 `/opt/g16/bsd/g16.profile`；
4. capability probe 必须断言 version、dirty=false、verified digest 和 1.4.5 executable；
5. smoke 成功必须同时满足：
   - process rc=0；
   - `.workflow_state.json final_status=completed`；
   - summary/stats/state/output manifest 全部存在且 schema 正确；
   - 所有 step manifest completed；
   - checkpoint/OldChk 传递证据存在；
   - 两个 Gaussian 日志各自 normal termination；
6. 为脚本判定逻辑增加 fake-output 单元测试，至少覆盖 rc=2、单个 normal termination、failed state、缺 artifact 和真正成功。

**验收：** 不运行真实 g16 的测试全部通过，旧失败目录必须被新判定逻辑判为 FAIL。

### Phase 5 — M2-4A 与真实 M2-4B 重验

**前置：** Gate A/B/C 和 Phase 4 全绿。

1. 重新执行 M2-4A 全部非计算 smoke，归档 exact executable/tag/commit/wheel digest；
2. 停止并请求本次真实 g16 运行授权；
3. 授权后先记录 g16/l1.exe before identity；
4. 运行 methane opt smoke；失败立即停止；
5. 运行 checkpoint opt -> SP readchk smoke；
6. 对每个 smoke 检查 rc、四类 artifact、terminal state、step manifest 和各自 Gaussian 日志；
7. 记录 after identity 并逐字段比较；
8. 不因 Gaussian normal termination 豁免 ConfFlow parser/workflow 失败。

**M2-4B Gate：** 两个 smoke 都是完整 workflow PASS，且 g16 identity 前后完全一致。

### Phase 6 — 最终审计、提交与发布收口

1. 分别列出 ConfFlow 和 JobDesk 的最终 diff、commit、status；
2. 两仓库分别运行最终 targeted/full/lint/type/diff-check 验证；
3. 核对 remote `v1.4.5^{}`、release artifact digest 和 production install provenance；
4. 核对 JobDesk CI ref、wheel reference、MIN_VERSION 和文档镜像一致；
5. 明确列出仍保留的用户修改、旧 venv、失败输出和临时诊断目录；不擅自清理；
6. 提交需要用户明确授权；push、release 发布和持久 server config 激活分别报告边界；
7. 只有所有 Gate 均通过后，才把原 Post-M2 plan 的 release closure checklist 标记完成；路线 B 从下一独立阶段恢复。

## 5. 提交边界

建议最少保持以下独立提交：

1. ConfFlow：clean release/provenance/installer/version；
2. ConfFlow：checkpoint parser regression fix；
3. JobDesk：1.4.5 contract/version/CI/identity integration；
4. JobDesk：strict g16 smoke assertions；
5. 文档/最终证据更新，如与代码无关可单独提交。

不得将两个仓库压成一个不可审计提交，也不得把路线 B 架构改造混入上述提交。

## 6. 最低执行轮次

若所有本地测试顺利，最低需要三轮执行：

1. **第一轮：** Phase 0、Phase 1、Phase 4 的非计算修复与 Gate A；结束时请求 tag/release 授权；
2. **第二轮：** Phase 2、Phase 3、M2-4A、Gate B/C；结束时请求真实 g16 授权；
3. **第三轮：** M2-4B、最终审计、经授权的提交/push/release 收口。

没有预先授予 tag/push/release 和真实 g16 权限时，不能诚实地压缩成一次无人值守执行。每轮内部应连续完成实现、测试和独立复核，不再拆成无意义的小回合。

## 7. 最终验收清单

- [ ] 当前错误的本地 `v1.4.4` 未被 push 或静默移动
- [ ] `v1.4.5` tag 指向包含版本、installer fix 和全部 release 代码的 clean commit
- [ ] 最终 wheel `COMMIT == v1.4.5^{}` 且 `DIRTY == false`
- [ ] wheel/SHA256SUMS/attestation/install provenance/JobDesk fixture digest 一致
- [ ] remote tag 和 release artifacts 已反向验证
- [ ] production capability 使用 1.4.5 venv、六字段 artifacts、verified provenance
- [ ] JobDesk dependency、MIN_VERSION、CI 三处、测试和文档全部同步
- [ ] JobDesk output manifest parser 和下载边界 fail closed
- [ ] executable identity guard 覆盖 normal/resume × nohup/scheduler
- [ ] source/editable/dirty/unverified/mismatch producer 均被 production gate 拒绝
- [ ] targeted/full/Ruff/MyPy/offscreen/diff-check 全绿
- [ ] 旧 checkpoint 失败证据会被 smoke 脚本判为 FAIL
- [ ] 新 checkpoint workflow rc=0、state completed、四类 artifact 完整
- [ ] 两个真实 g16 smoke 均通过，且 g16/l1.exe identity 未变化
- [ ] 两仓库提交、tag、remote ref、残余用户修改和未清理目录均明确记录

任何一项未完成时，结论必须写成“未通过/部分完成”，不得使用“全部完成”“Gate 全绿”或等价表述。
