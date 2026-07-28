# JobDesk / ConfFlow Milestone 2 设计与发布决策

**状态：M2-1～M2-3 已实现并通过回归；M2-4 wheel/WSL 发布候选待单独门禁**

## 1. 目标与边界

Milestone 2 的目标是把“远端能运行 ConfFlow”提升为“JobDesk 能证明本次提交使用了确定的
ConfFlow 身份、确定的产物契约，并能在中断后安全恢复”。本阶段只处理跨仓库协议、持久化、
提交事务、DAG/多终端语义和发布门禁；不改变 Gaussian/ORCA 计算器实现，不引入隐式聚合，
不以兼容旧文件为理由放宽安全校验。

当前基线证据：

- JobDesk 的 `build_confflow_preflight_shell()` 默认执行 `confflow --capabilities --json`，
  `JobSubmitter.generate_task_runner()` 又在任务执行时重新解析命令；两者尚未绑定同一个已解析
  的可执行文件。
- JobDesk `ServerConfig` 没有 ConfFlow 可执行文件字段；run repository 当前 schema 为 v5，
  manifest 只保存结果路径与资源预算。
- ConfFlow capability schema 当前为 v3，payload 有 version、capabilities、artifacts、
  commands、build(commit/dirty)，没有 resolved executable 或 wheel identity。
- ConfFlow `WorkflowStateStore.save()` 已使用临时文件 + `os.replace()`；但 state 没有内容
  schema 标识。`workflow/presenter.py` 直接写 `workflow_stats.json` 与 `run_summary.json`，
  没有 schema 包装且未使用统一原子写入。
- engine 已调用 `workflow.dag.explicit.build_step_graph/topo_order`；`workflow.dag` 仍同时
  公开旧的 `DAGGraph`/`WorkflowDAG`，形成两套 DAG 语义。
- `WorkflowSupervisor` 是可重建 handle 的持久轮询组件，但当前由 workflow 层独立导出，
  agent 层也有自己的远程服务边界；其所有权需要明确而不是继续扩大隐式耦合。

## 2. 推荐决策（实现前必须确认）

### D1. 可执行文件身份

在 `ServerConfig` 增加可选 `confflow_executable`。配置为空时仅作为兼容回退使用
`command -v confflow`；一旦解析成功，JobDesk 记录 requested value、解析后的绝对路径和
解析时的 `realpath`，并把同一个 shell-quoted 路径注入 capability probe、dry-run、runner
和 resume runner。每个阶段重新执行一次 `realpath` 并与记录比较；路径或 inode 变化即拒绝，
不允许 probe 一个二进制、执行另一个二进制。

推荐字段：`requested_executable`、`resolved_executable`、`resolved_realpath`。禁止把未解析的
`PATH` 命令名当作运行身份写入结果。

### D2. capability 与 provenance

将 capability schema 升级为 v4。保留 v3 字段，并增加：

```json
{
  "producer": {
    "distribution": "confflow",
    "version": "1.4.3",
    "build_commit": "…",
    "dirty": false,
    "wheel_sha256": "…",
    "install_root": "…"
  },
  "executable": {"resolved_path": "…", "realpath": "…"}
}
```

`wheel_sha256` 在正式 v4 发布中为必填；开发树或无法证明 wheel 身份的安装只能被标记为
diagnostic，不得作为生产提交的通过条件。JobDesk 保存原始 capability JSON 以及规范化字段，
写入 run DB 和结果 manifest；结果目录再写一份只读 provenance manifest，供离线验收。

### D3. 版本化 JSON 产物

定义三个独立的内容 schema：`run_summary.v1`、`workflow_stats.v1`、`workflow_state.v1`。
文件顶层统一包含 `content_schema`、`producer`、`run_id`、`generated_at`；当前 v1 保留历史
业务字段在顶层，消费者同时接受一个可选的 `payload` envelope。未知 schema 版本对必需字段
fail-closed，旧的无包装 JSON 只允许通过明确的 legacy adapter 读取，不再被当作当前格式写出。
三个 producer writer 统一使用临时文件、flush/fsync 和 `os.replace`，并在写后做最小 schema
校验。

### D4. 多终端输出

终端（terminal）是 DAG 中没有后继的命名节点。每个 terminal 必须产出一个声明式 artifact
列表，写入 `output_manifest.v1`；JobDesk 下载 manifest 声明的文件并按 terminal 分组展示。
聚合只能由显式 aggregation/calculation step 完成，禁止消费者猜测“最后一个文件”或隐式
拼接。资源统计同时保留 workflow 总量和 terminal/step 分项，避免多终端结果重复计费。

本阶段不把“多个终端”降级成旧的单 `final_output` 字段；旧字段作为兼容摘要保留，但不能
表达完整语义。

### D5. durable submit operation

沿用 JobDesk 已有 operation journal，新增 submission transaction 状态机（建议 DB schema v6）：

`prepared → staged → uploaded → probed → created → submitted → confirmed`。

每个阶段持久化 idempotency key、resolved executable/provenance、远端 JobDesk-owned staging
root、上传清单和最后错误。失败时只对该 staging root 执行补偿清理；确认提交后转为保留/可回收
状态。重启通过 owner lease 继续未完成阶段，不重新盲传；清理失败进入可见的 recovery queue，
而不是吞错。现有 `SubmitResult` 和 GUI activity log 保持兼容适配。

### D6. DAG 与 supervisor 所有权

`dag/explicit.py` 成为唯一执行语义（规范化 inputs、波次拓扑排序、终端判定）。旧
`DAGGraph`/`WorkflowDAG` 在一个兼容发布周期内迁移到 `dag/legacy.py`，保留导入但发出弃用
诊断；新代码不得依赖它们。`WorkflowSupervisor` 归 workflow engine 的运行时层所有，agent
只提供可序列化 executor/handle 适配，不再复制 supervisor 状态机。兼容周期后移除旧 DAG
导出，并把 supervisor 的公共 API 与 engine 的 state contract 一起版本化。

## 3. 实施顺序与门禁

1. **M2-0 设计批准**：确认 D1-D6；冻结 v4 capability、JSON schema、DB v6 迁移和兼容期。
2. **M2-1 ConfFlow producer release**：先实现 v4 capability、provenance、三个 artifact
   writer/schema、显式 terminal/output manifest；单仓库测试、ruff、mypy、diff check 全绿。
3. **M2-2 JobDesk consumer/migration**：加入 executable binding、capability 解析与持久化、
   DB v6、manifest provenance、strict/legacy readers；不连真实计算任务先通过全量测试。
4. **M2-3 提交事务与 GUI**：把 upload/create/submit 收拢到 journaled operation，加入恢复、
   补偿清理、幂等和多终端下载/展示/资源核算测试。
5. **M2-4 发布候选验收**：构建并校验唯一 wheel（含 sha256），在持久 WSL 配置上复跑 capability
   probe、dry-run、resume 和真实非计算 smoke；确认两仓库远端 ref 与本地提交一致后才允许
   发布。Gaussian/ORCA 只在该门禁全绿且另行批准后执行。

## 4. 必须新增的测试矩阵

- executable path：显式路径、PATH fallback、symlink/inode 替换、probe/runner 不一致均有测试。
- provenance：v4 完整 payload、缺 wheel identity、dirty build、旧 v3 payload 的拒绝/诊断。
- artifact schema：三种 v1、未知版本、legacy adapter、截断/半写文件和原子替换竞态。
- multi-terminal：两个 terminal、显式 aggregation、terminal manifest 下载、GUI 分组、分项
  resource accounting，以及无显式聚合的语义拒绝。
- durable submit：每个阶段重启恢复、幂等重试、上传失败补偿、提交后不删除、staging root
  越界清理拒绝。
- DAG compatibility：explicit helper 与 engine 一致、legacy import 警告、循环/未知 predecessor
  拒绝、supervisor 只由 engine 状态驱动。

## 5. 当前状态与下一步

M2-1～M2-3 已落地：producer capability v4、原子 artifact schemas、JobDesk executable/provenance/DB v6、durable submit journal、DAG 多终端输出与 JobDesk 结果解析均已通过各自全量回归。M2-4 仍是独立发布门禁：构建并安装唯一 wheel、在持久 WSL 配置上复跑 capability probe/dry-run/resume 与非计算 smoke，确认两仓库远端 ref 后再允许 commit/push；Gaussian/ORCA 仍需另行批准。
