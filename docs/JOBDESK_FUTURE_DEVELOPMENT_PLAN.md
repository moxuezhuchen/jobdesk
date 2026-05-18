# JobDesk 后续开发总计划

> 当前基线：M8.6C + lifecycle hardening patch 已完成。  
> 最近验证：`pytest -q` 为 `350 passed, 4 skipped, 1 warning`。  
> 目标：把 JobDesk 从“核心架构可运行”推进到“真实科研批量计算可长期使用”的 Windows 11 本地工作台。

---

## 1. 当前状态判断

JobDesk 当前已经具备核心闭环：

```text
project.yaml
-> task_discoveries
-> TaskPackage
-> create_batch
-> batch.json / manifest.tsv
-> upload
-> submit
-> refresh
-> download
-> analyze
-> results/failures/job_status
```

M8.6C 完成后，核心模型已经从旧的 `1 task = 1 input file` 转向 TaskPackage / TaskRecord / ExecutionProfile / RuntimeBinding / shared_files 的新模型。

已经稳定的关键能力：

- 多 `task_discoveries`
- mixed-profile batch
- multi-server upload / submit / refresh / download 分组
- `server_id` / `remote_work_dir` / `max_parallel` 在 batch 创建时冻结进 manifest
- `shared_files` 在 batch 创建时冻结进 batch.json
- upload/download/submit/refresh 的 failure record 落盘
- `failures.tsv` 追加写，不覆盖历史失败
- `batch.json` / `manifest.tsv` 原子写
- `list_batches()` / `load_batch()` / `load_latest_batch()`
- GUI Tasks 页自动选择 latest batch
- GUI Projects 页显示并刷新 binding 状态

仍需推进的方向不是继续改领域模型，而是：

- 真实环境端到端验证
- GUI 可操作性
- 结果展示和失败诊断
- 项目创建体验
- 发布、打包、文档和稳定性

---

## 2. 总体完成定义

JobDesk 达到“功能完成”时，应满足以下条件。

### 2.1 用户闭环

用户可以在 Windows 11 上完成：

1. 创建或打开项目
2. 配置服务器
3. 配置每个 execution_profile 的 runtime binding
4. 扫描输入任务
5. 创建 batch
6. 上传 task files 和 shared files
7. 提交远程后台并行执行
8. 关闭 GUI 后重新打开并恢复 batch
9. 刷新状态
10. 下载结果
11. 分析结果
12. 查看结果、失败、日志和运行元数据
13. 导出汇总结果

### 2.2 架构边界

必须保持：

- JobDesk 不理解 Gaussian/ORCA/xTB/ConfFlow 的程序语义。
- JobDesk 不成为 Slurm/PBS 替代品。
- JobDesk 不引入复杂 workflow DAG 作为 1.0 必需能力。
- 项目配置描述“项目规则”，本机配置描述“运行绑定”。
- 已创建 batch 的执行计划不受后续 runtime binding 修改影响。

### 2.3 质量门槛

每个里程碑完成时必须满足：

- 全量单元测试通过
- lifecycle 相关回归测试覆盖新增行为
- 真实远程集成测试可以在有环境变量时运行
- GUI import/state 测试通过
- 文档更新

---

## 3. 里程碑路线图

| 里程碑 | 名称 | 目标 |
|---|---|---|
| M8.7 | Real Lifecycle Validation | 用真实远程环境验证 multi-profile/multi-server 端到端闭环 |
| M8.8 | Results & Diagnostics GUI | 让用户能看懂 mixed-profile 结果、失败和状态 |
| M8.9 | Workflow UX Hardening | 简化任务页操作，减少误操作，增强恢复体验 |
| M9.0 | Project Wizard MVP | 用向导创建合法 project.yaml 和 runtime binding |
| M9.1 | Template & Config Validation | 强化配置校验、dry-run 预检和路径安全报告 |
| M9.2 | Packaging & Windows App Polish | Windows 可安装/可运行版本、日志目录、配置目录稳定 |
| M9.3 | Documentation & Examples | 示例项目、用户手册、故障排查手册 |
| v1.0 RC | Release Candidate | 真实科研使用试运行，修复阻塞问题 |
| v1.0 | Stable Local Workbench | 稳定发布 |
| v1.x | Enhancements | 便利功能、性能、可选高级能力 |

---

## 4. M8.7 Real Lifecycle Validation

### 4.1 目标

把当前 mock/fake 层验证推进到真实 Linux 服务器验证，确认：

- mixed-profile batch 能真正上传、提交、刷新、下载、分析
- 多 server 分组不会互相污染
- `shared_files` 在真实 SFTP 下只上传到每个 `(server_id, remote_work_dir)` 一次
- `control_subdir = _batch/{execution_profile}` 在真实远程路径下生效
- GUI 操作和 service API 的行为一致

### 4.2 范围

新增或完善：

- `tests/integration/test_real_lifecycle.py`
- `tests/integration/fixtures/` 或本地测试项目生成 helper
- 真实远程测试说明文档
- 可重复清理远程临时目录的测试工具

不做：

- 不做 Project Wizard
- 不做新调度系统
- 不做计算程序专用解析

### 4.3 任务

#### Task 1: 定义真实集成测试环境变量

文件：

- 修改：`tests/integration/test_real_lifecycle.py`
- 修改：`docs/M8_REAL_BACKEND_LOG.txt` 或新增 `docs/REAL_INTEGRATION_TESTS.md`

环境变量：

```powershell
$env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SERVER_ID_A = "server_a"
$env:JOBDESK_TEST_SERVER_ID_B = "server_b"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
```

如果只配置一个 server，则 multi-server 测试 skip，但 mixed-profile single-server 测试仍可运行。

验收：

- 未设置环境变量时测试 skip
- 设置一个 server 时 single-server lifecycle 测试运行
- 设置两个 server 时 multi-server lifecycle 测试运行

#### Task 2: 生成临时 mixed-profile 项目

测试项目结构：

```text
project/
  project.yaml
  inputs/
    g16/a.inp
    orca/b.inp
  shared/
    basis.dat
```

命令不调用真实 Gaussian/ORCA，而用 shell 命令模拟：

```yaml
execution_profiles:
  g16:
    label: G16 Fake
    command: "bash {entry_name}"
  orca:
    label: ORCA Fake
    command: "bash {entry_name}"
```

输入脚本写出：

```bash
echo "profile=g16" > result.out
echo "energy=-1.23" >> result.out
```

验收：

- `scan_inputs()` 返回两个 TaskPackage
- batch manifest 中两个 TaskRecord 的 `execution_profile` 不同
- `shared_files` 冻结到 batch.json

#### Task 3: 真实 upload / submit / refresh / download / analyze

测试流程：

```python
packages = svc.scan_inputs()
batch = svc.create_batch(packages, resolved_contexts)
records, failures = svc.upload_tasks(...)
submit_results = svc.submit_batch(...)
refresh_results, refresh_failures = svc.refresh_batch(...)
download_records, download_failures = svc.download_completed(...)
results, analyze_failures, summaries = svc.analyze_batch(...)
```

验收：

- upload 无失败
- submit 至少提交两个任务
- refresh 最终得到 `remote_completed`
- download 下载 `result.out`
- analyze 读出两个结果
- `failures.tsv` 不存在或仅包含预期失败

#### Task 4: 真实 shared_files 验证

检查远程：

```text
{remote_work_dir}/{batch_id}/_shared/basis.dat
```

验收：

- 同一 `(server_id, remote_work_dir)` 只上传一次
- 不同 remote_work_dir 各上传一次
- 命令里的 `{shared_dir_abs}` 能访问到 shared 文件

#### Task 5: 真实 failure 场景

构造一个脚本返回 `exit 1`。

验收：

- refresh 后该任务为 `failed`
- 其他任务不受影响
- `failures.tsv` 包含 runtime/refresh 信息
- download 只处理 completed 任务

### 4.4 M8.7 完成标准

- `pytest tests/integration/test_real_lifecycle.py -v` 在真实环境通过
- 没有真实环境时集成测试全部 skip
- 本地 `pytest -q` 通过
- 文档说明如何配置真实测试环境

---

## 5. M8.8 Results & Diagnostics GUI

### 5.1 目标

让用户在 GUI 里看得懂 batch 发生了什么，尤其是 mixed-profile 情况。

### 5.2 Results Page 最小增强

文件：

- 修改：`src/jobdesk_app/gui/pages/results_page.py`
- 修改：`src/jobdesk_app/gui/table_models.py`
- 新增/修改：`tests/test_gui_state.py`

能力：

- 显示 `job_status.tsv`
- 显示 `failures.tsv`
- 显示 `final_results.tsv`
- 显示 `summary.json`
- 支持按 batch 选择

必须展示字段：

- `task_id`
- `discovery_name`
- `execution_profile`
- `server_id`
- `remote_work_dir`
- `status`
- `error_message`

验收：

- mixed-profile job_status 能在 Results 页加载
- failures.tsv 不因为 task_id 为空而显示失败
- shared_files 不出现在 task result 表里

### 5.3 Diagnostics Panel

不做复杂 UI，只增加一个“Diagnostics”视图或区域：

- 最近一次 workflow 操作的失败数
- failures.tsv 路径
- job_status.tsv 路径
- batch manifest 路径

验收：

- 用户能从 GUI 找到失败原因
- GUI 弹窗错误也落入 log area

### 5.4 analyze metadata 贯通

当前 analyzer 输出以结果为核心。M8.8 需要在展示层 join TaskRecord 元数据。

建议不修改 `ResultRecord` 主模型，先在 Results GUI 中根据 `task_id` join manifest：

```text
final_results.tsv + manifest.tsv -> enriched table
```

验收：

- 结果表可显示 profile/discovery/server
- 导出的 enriched results 不改变原始 final_results.tsv 语义

---

## 6. M8.9 Workflow UX Hardening

### 6.1 目标

让 Tasks 页从“按钮集合”变成可靠工作流面板。

### 6.2 当前问题

- 用户可能不知道下一步该做什么
- repeated submit 虽已防护，但 GUI 仍可让用户困惑
- refresh/download/analyze 的可用状态还比较粗
- latest batch 自动选择已有，但历史 batch 操作缺少上下文

### 6.3 最小任务

#### Task 1: Workflow Step Indicator

显示当前 batch 的聚合状态：

```text
Local Ready: 10
Uploaded: 10
Submitted: 10
Running: 3
Completed: 7
Downloaded: 7
Failed: 0
```

验收：

- 状态来自 manifest
- 切换 batch 后刷新
- 操作完成后刷新

#### Task 2: Button Guard Messages

按钮 disabled 时提供明确原因：

- Upload disabled: no local_ready tasks
- Submit disabled: no uploaded tasks
- Refresh disabled: no submitted/running tasks
- Download disabled: no remote_completed tasks

验收：

- GUI 不再只有灰按钮
- 不引入复杂新 UI

#### Task 3: Batch Detail Header

显示：

- batch_id
- task_count
- execution_profiles
- server_ids
- shared_files_count
- manifest path

验收：

- 打开项目后自动显示 latest batch header
- 选择历史 batch 后更新

#### Task 4: Operation Summary

每次操作完成后写入 log：

```text
Upload complete: 12 transferred, 3 skipped, 1 failed
Submit complete: 10 submitted, 0 failed
Refresh complete: 7 changed, 0 connection failures
Download complete: 14 files, 1 failed task
Analyze complete: 12 results, 0 failures
```

验收：

- summary 数字来自 records/failures/results
- 失败时指向 failures.tsv

---

## 7. M9.0 Project Wizard MVP

### 7.1 启动条件

只有在 M8.7-M8.9 完成后再做 Project Wizard。

原因：

- Wizard 生成的是项目规则，底层 lifecycle 必须先稳定
- 过早做 Wizard 会把不稳定 schema 固化到 UI

### 7.2 目标

帮助用户生成合法的：

- `project.yaml`
- 初始目录结构
- 可选 runtime binding

### 7.3 Wizard 页面

#### Step 1: Project Basics

输入：

- project_id
- project name
- project root
- input_dir
- result_dir

校验：

- project_id 只允许 `[A-Za-z0-9_.-]`
- project root 不覆盖已有 project.yaml，除非用户确认

#### Step 2: Task Discoveries

支持三种模式：

- flat_single
- grouped_by_stem
- directory

输入：

- discovery name
- entry_glob
- execution_profile
- task_id_prefix

验收：

- 至少一个 discovery
- discovery name 唯一
- 引用的 execution_profile 存在

#### Step 3: Execution Profiles

输入：

- profile name
- label
- command template
- default max_parallel
- requirements tags

验收：

- command 不为空
- profile name 唯一
- 支持 `{entry_name}` / `{entry_stem}` / `{shared_dir_abs}`

#### Step 4: Upload Rules

输入：

- task_files include/exclude
- require_entry_file
- shared_files base_dir/include/exclude/target_subdir

验收：

- target_subdir 安全
- shared base_dir 不越出项目根

#### Step 5: Binding Setup

为每个 execution_profile 选择：

- server_id
- remote_work_dir
- max_parallel override

保存到：

```text
%APPDATA%/JobDesk/runtime_bindings.yaml
```

不写入 project.yaml。

### 7.4 Wizard 验收

- 生成的项目能立即 Scan Inputs
- 能 create_batch
- Wizard 不生成旧 schema 字段
- Wizard 生成的 project.yaml 能通过 config loader 测试

---

## 8. M9.1 Template & Config Validation

### 8.1 目标

把错误尽量提前到 create_batch / dry-run 阶段。

### 8.2 Dry-run Preflight

新增 `WorkflowService.preflight_batch()` 或独立 service：

检查：

- project.yaml schema
- task_discoveries 是否能扫描
- duplicate task_id
- execution_profile 引用
- runtime binding 是否齐全
- servers.yaml 是否包含 server_id
- remote_work_dir 是否为 POSIX 绝对路径
- upload rules 是否选择到 entry file
- shared_files 是否存在并无冲突
- command template 是否全部变量可解析

输出：

```python
PreflightReport(
    errors: list[PreflightIssue],
    warnings: list[PreflightIssue],
    task_count: int,
    profiles: list[str],
    servers: list[str],
)
```

验收：

- GUI Create Batch 前可运行 preflight
- CLI/test 可直接调用
- error 阻止 create_batch
- warning 不阻止但显示

### 8.3 Path Safety Hardening

继续强化：

- remote_work_dir 必须是绝对 POSIX 路径
- task_id 禁止路径分隔符、空字符串、`..`
- shared target_subdir 禁止绝对路径、反斜杠、`..`
- remote_task_files 禁止 `..`
- directory mode 保留相对目录时不允许逃逸

验收：

- 每个风险都有测试
- 错误信息包含 discovery rule 或 task_id

### 8.4 Shell Template Policy

当前 `render_command()` 对变量做 shell quoting。M9.1 需要正式文档化策略：

- JobDesk quote 变量值
- 用户负责 command template 的命令结构
- 不支持把变量放进已有引号内部

示例：

推荐：

```yaml
command: "g16 {entry_name}"
```

不推荐：

```yaml
command: "g16 '{entry_name}'"
```

验收：

- docs 说明清楚
- 测试覆盖空格、分号、美元符号、括号

---

## 9. M9.2 Packaging & Windows App Polish

### 9.1 目标

让非开发用户可以运行 JobDesk。

### 9.2 Packaging

候选：

- Python zipapp
- PyInstaller
- 简单 venv + launcher bat

第一版建议：

- 先做 venv + launcher
- 再评估 PyInstaller

目录约定：

```text
%APPDATA%/JobDesk/
  servers.yaml
  runtime_bindings.yaml
  logs/
  cache/
```

### 9.3 Logging

新增应用日志：

```text
%APPDATA%/JobDesk/logs/jobdesk-YYYYMMDD.log
```

记录：

- project opened
- batch created
- upload/submit/refresh/download/analyze start/end
- exception trace
- config path

验收：

- GUI log area 显示摘要
- 文件日志包含 traceback
- 不记录密码

### 9.4 Settings

最小 settings：

- default servers.yaml path
- default runtime_bindings path
- log level
- default remote tmp root for tests

不做账号密码管理。

---

## 10. M9.3 Documentation & Examples

### 10.1 用户手册

新增：

- `docs/USER_GUIDE.md`
- `docs/CONFIG_REFERENCE.md`
- `docs/TROUBLESHOOTING.md`
- `docs/EXAMPLES.md`

### 10.2 示例项目

建议提供：

```text
examples/
  shell_basic/
  mixed_profiles_fake/
  directory_mode/
  shared_files/
  analysis_regex/
```

每个示例包含：

- project.yaml
- inputs
- README
- 预期输出

### 10.3 Troubleshooting

覆盖：

- SSH 连接失败
- SFTP 权限失败
- remote_work_dir 不存在
- command not found
- no uploaded tasks
- manifest corrupted
- failures.tsv 怎么看
- batch 关闭后如何恢复

---

## 11. v1.0 Release Candidate

### 11.1 RC 入口条件

必须满足：

- M8.7-M9.3 完成
- 本地测试全绿
- 至少一次真实服务器端到端通过
- GUI 可以完成完整闭环
- 文档足够让新用户跑通 shell_basic 示例

### 11.2 RC 测试矩阵

| 场景 | 必须通过 |
|---|---|
| flat_single shell task | 是 |
| grouped_by_stem shell task | 是 |
| directory mode shell task | 是 |
| mixed profile single server | 是 |
| mixed profile multi server | 至少环境可用时通过 |
| shared_files | 是 |
| upload interrupted then retry | 是 |
| submit repeated | 是 |
| refresh connection failure | 是 |
| download partial failure | 是 |
| analyze partial result | 是 |
| GUI close/reopen latest batch | 是 |

### 11.3 RC Bug Policy

P0：

- 数据损坏
- manifest/batch 无法恢复
- 错误 server/remote_work_dir 被使用
- 上传/下载路径逃逸
- submit 重复导致明显不可控远程执行

P1：

- GUI 阻断核心闭环
- failure 不落盘
- mixed-profile 状态错乱
- shared_files 目录不一致

P2：

- 文案不清楚
- 结果展示不够方便
- 性能问题但不影响小批量

---

## 12. v1.0 Stable

v1.0 不追求功能很多，只追求可靠。

### 12.1 v1.0 必须有

- 完整本地 GUI 闭环
- project.yaml 新 schema
- runtime_bindings.yaml
- servers.yaml
- TaskPackage / TaskRecord / manifest
- batch.json
- upload.task_files
- upload.shared_files
- submit / refresh / download / analyze
- failures.tsv / job_status.tsv / final_results.tsv
- latest batch 恢复
- 基础项目创建向导
- 用户文档和示例

### 12.2 v1.0 不必须有

- Slurm/PBS
- workflow DAG
- 内置 Gaussian/ORCA 语义解析
- 远程 agent
- SQLite
- 云同步
- 多用户权限系统

---

## 13. v1.x 后续增强

### 13.1 可选 CLI

命令：

```powershell
jobdesk scan <project>
jobdesk create-batch <project>
jobdesk upload <project> <batch_id>
jobdesk submit <project> <batch_id>
jobdesk refresh <project> <batch_id>
jobdesk download <project> <batch_id>
jobdesk analyze <project> <batch_id>
```

价值：

- 自动化测试
- 高级用户脚本化
- GUI 故障时仍可操作

### 13.2 Better Result Views

- 按 group_key 聚合
- 排序和过滤
- 导出 CSV/XLSX
- 比较多个 batch

### 13.3 Remote Cleanup

谨慎加入：

- dry-run cleanup
- 只允许清理当前 batch_id 目录
- 永远不递归删除 remote_work_dir 本身

### 13.4 Optional SQLite

只有当以下问题成为阻塞才考虑：

- batch 数量很多导致 TSV 扫描慢
- GUI 多维过滤非常复杂
- 需要跨 batch 查询
- 需要事务级更新多个表

在 v1.0 前不建议引入 SQLite。

### 13.5 Advanced Scheduler Adapter

远期可以支持：

- local shell backend
- SSH direct backend
- Slurm adapter

但 Slurm/PBS 不替代 JobDesk 核心模型，只作为可选 submit backend。

---

## 14. 推荐下一轮任务

下一轮建议直接做：

```text
M8.7 Real Lifecycle Validation
```

最小可交付范围：

1. 新增 `tests/integration/test_real_lifecycle.py`
2. 自动生成 fake mixed-profile project
3. 支持一个 server 的真实端到端测试
4. 有两个 server 环境变量时运行 multi-server 测试
5. 写 `docs/REAL_INTEGRATION_TESTS.md`
6. 保持无真实环境时测试 skip

不要同时做：

- Project Wizard
- 大改 GUI
- 新结果图表
- SQLite

完成 M8.7 后，再进入 M8.8 Results & Diagnostics GUI。

---

## 15. 每轮开发固定流程

每轮都按这个顺序：

1. 写 failing test
2. 跑窄测试确认红灯
3. 实现最小代码
4. 跑窄测试确认绿灯
5. 跑相关模块测试
6. 跑全量 `pytest -q`
7. 更新 docs
8. 写完成报告

每轮完成报告必须包含：

- 修改文件
- 行为变化
- 新增测试
- 测试结果
- 残留风险
- 下一轮建议

