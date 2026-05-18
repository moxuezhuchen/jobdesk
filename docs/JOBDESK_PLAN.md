# JobDesk 项目规划文档

> **文档状态**：v0.2 规划冻结版  
> **适用对象**：后续开发执行者（含 DeepSeek）、项目维护者  
> **最后更新**：2026-05-11  
> **项目名称**：JobDesk  
> **一句话定义**：一个运行在 Windows 11 本地、通过 SSH 管理远程 Linux 服务器，并完成“本地输入 → 远程执行 → 结果回传 → 本地分析”的科研计算工作台。

---

## 目录

1. [项目摘要](#1-项目摘要)  
2. [项目边界与核心原则](#2-项目边界与核心原则)  
3. [已确认决策](#3-已确认决策)  
4. [用户工作流](#4-用户工作流)  
5. [对象模型](#5-对象模型)  
6. [版本范围](#6-版本范围)  
7. [任务生命周期](#7-任务生命周期)  
8. [数据模型](#8-数据模型)  
9. [项目配置设计](#9-项目配置设计)  
10. [本地与远程目录约定](#10-本地与远程目录约定)  
11. [批次与 Manifest 设计](#11-批次与-manifest-设计)  
12. [文件传输设计](#12-文件传输设计)  
13. [任务提交与并行模型](#13-任务提交与并行模型)  
14. [状态检查与结果分析](#14-状态检查与结果分析)  
15. [输出文件规范](#15-输出文件规范)  
16. [GUI 规划](#16-gui-规划)  
17. [技术架构](#17-技术架构)  
18. [实施里程碑](#18-实施里程碑)  
19. [测试与验收](#19-测试与验收)  
20. [风险、非目标与扩展边界](#20-风险非目标与扩展边界)  
21. [建议默认项与后续可确认问题](#21-建议默认项与后续可确认问题)  

---

# 1. 项目摘要

## 1.1 JobDesk 是什么

JobDesk 是一个运行在 **Windows 11 本地** 的科研计算工作台。它通过 SSH 连接远程 Linux 服务器，帮助用户完成一条完整的科研计算链路：

```text
本地准备输入文件
→ 自动上传到远程服务器
→ 在远程服务器上后台并行执行
→ 查看服务器与任务状态
→ 手动下载已完成任务的输出文件
→ 在本地提取、分类、汇总结果
```

## 1.2 JobDesk 解决什么问题

当前科研计算中，用户通常需要手动完成：

- 在本地准备输入；
- 使用 WinSCP / scp / sftp 上传文件；
- SSH 登录服务器；
- 逐个进入目录执行 `g16 xx.gjf`、`orca xx.inp > xx.out`、`xtb xx.xyz ...`、`bash run.sh` 等命令；
- 手动控制同时运行任务数；
- 反复登录服务器查看任务是否完成；
- 下载输出；
- 再用脚本或 Excel 进行汇总分析。

JobDesk 将这些操作整理成统一流程，并补上以下长期可维护能力：

- 项目与批次管理；
- 任务生命周期追踪；
- Manifest；
- Dry-run；
- 断点恢复；
- 覆盖保护；
- 结果汇总；
- 本地 GUI 看板。

## 1.3 JobDesk 不是什么

JobDesk **不是**：

- 某种计算程序的专用前端；
- Gaussian / ORCA / xTB 的输入生成器；
- 服务器上的作业调度器；
- Slurm / PBS / SGE 替代品；
- 需要服务器安装常驻 agent 或守护进程的分布式系统；
- 通用远程文件管理器；
- 自动修复计算失败的 AI 系统。

## 1.4 版本目标

| 版本 | 定位 | 核心交付 |
|---|---|---|
| v0.1 | 只读管理台 | 服务器状态、项目列表、任务发现、状态检查、错误汇总、结果提取、分组汇总 |
| v0.2 | 完整基础工作流 | 本地输入发现、Manifest、上传、批量提交、并行控制、批次记录、下载、分析、重跑、断点恢复 |
| v0.3+ | 增强阶段 | 自动轮询、通知、归档清理、可选 rsync 后端、更多分析 hook、可选高级调度集成 |

---

# 2. 项目边界与核心原则

## 2.1 本地驱动，远程执行

- 主程序运行在 Windows 11 本地；
- 本地保存项目配置、输入、结果和 GUI；
- 远程服务器仅作为执行端；
- 服务器上不要求安装 JobDesk 软件、AI agent、systemd 服务或长期守护进程；
- 远程操作全部通过 SSH / SFTP 完成。

## 2.2 不按计算程序类型设计

JobDesk 不内置：

- Gaussian 模块；
- ORCA 模块；
- xTB 模块；
- 某种工作流专用模块。

它只管理“项目中的任务”。  
项目通过配置定义：

- 任务怎么发现；
- 输入文件怎么找；
- 单任务命令怎么执行；
- 成功、失败如何判断；
- 哪些文件需要上传、下载；
- 结果如何提取；
- 如何分组汇总。

示例命令仅作为配置：

```yaml
command: "g16 {input_name}"
command: "orca {input_name} > {stem}.out"
command: "xtb {input_name} --opt > {stem}.out"
command: "bash run.sh"
```

## 2.3 默认直接提交，不强制用户维护 `run.sh`

JobDesk 默认按项目配置直接执行用户命令：

```bash
cd <job_dir> && g16 xx.gjf
```

复杂任务可配置为：

```bash
bash run.sh
```

但 `run.sh` 不是系统硬要求。

为了提高状态判断可靠性，JobDesk 可以为任务自动生成 **内部隐藏包装脚本**，例如 `.jobdesk_run.sh`。  
它的作用是：

- 执行用户配置的原始命令；
- 写入 JobDesk 自己的状态标记文件；
- 记录退出码；
- 保留提交日志。

这类脚本是 JobDesk 内部控制产物，不等同于要求用户手写 `run.sh`。

## 2.4 配置驱动

所有项目差异都必须写入配置文件，而不能硬编码在 Python 代码中。  
包括：

- 本地目录；
- 远程目录；
- 任务发现；
- 输入文件匹配；
- 命名解析；
- 提交命令；
- 并行数；
- 上传规则；
- 下载规则；
- 状态判断；
- 结果提取；
- 分组字段；
- 可选 hook。

## 2.5 文件传输是核心功能

JobDesk 的目标不是“查看服务器上已有任务”，而是接管完整闭环：

```text
本地输入
→ 上传
→ 执行
→ 下载
→ 分析
```

因此上传、下载、路径映射、覆盖保护、断点恢复都属于核心能力。

## 2.6 GUI 不重复实现核心逻辑

- 状态判断、结果提取、分组、Manifest、远程命令拼装均由后端 core / remote 层实现；
- GUI 只负责：
  - 展示；
  - 收集用户输入；
  - 调用后端；
  - 显示进度与错误；
- 不允许 GUI 内另写一套业务规则。

---

# 3. 已确认决策

以下决策已确认，应作为后续实现基线。

## 3.1 `max_parallel` 采用方案 B

含义：

- 一次提交整批任务；
- 服务器端使用一次性的后台批处理过程，始终维持最多 `N` 个并行任务；
- 当前 Batch 中**已经入队**的任务，GUI 关闭或 SSH 断开后仍继续自动补位并跑完；
- 这不意味着服务器安装常驻执行器。

## 3.2 下载由用户手动触发

- v0.2 中，刷新状态不会自动下载输出；
- 用户通过“下载”按钮手动拉回结果；
- 原因：避免大文件在用户只想查看状态时意外占用带宽和磁盘。

## 3.3 GUI 关闭后不自动接纳新任务

- 当前 Batch 内已入队任务：后台批处理继续自动补位并完成；
- GUI 关闭后新出现、后来新增、尚未进入当前 Batch 的任务：不会被自动接纳或提交；
- 这是“是否自动接纳新任务”的限制，不是“当前批次是否自动补位”的限制。

## 3.4 重跑失败任务默认创建新 Batch

- 保留原始失败批次与失败记录；
- 重跑进入新的 Batch；
- v0.2 不把“同一 Batch 内覆盖重跑”作为默认行为。

## 3.5 `servers.yaml` 采用全局用户级配置

推荐位置：

```text
%APPDATA%\JobDesk\servers.yaml
```

原则：

- 服务器是用户级资源，不属于单个项目；
- `project.yaml` 只引用 `server_id`；
- 不在项目目录中重复保存服务器连接信息；
- 项目迁移到另一台电脑时，若引用的 `server_id` 不存在，JobDesk 应提示用户：
  - 绑定已有服务器；
  - 或新建同名服务器配置。

## 3.6 GUI 使用 PySide6

- 采用 Python + PySide6；
- 适合表格、树、日志、进度条等桌面应用需求；
- 与 Python 后端共享代码最直接；
- v0.2 不引入 Web 前端或 Electron / Tauri。

---

# 4. 用户工作流

## 4.1 标准工作流

| 阶段 | 用户动作 | JobDesk 行为 | 结果 |
|---|---|---|---|
| 1. 创建项目 | 创建 / 打开 `project.yaml` | 校验配置，绑定全局 `server_id` | 项目可用 |
| 2. 准备输入 | 将输入放入本地目录 | — | 本地输入就绪 |
| 3. 扫描任务 | 点击“扫描” | 发现输入，生成 Batch 与 Manifest | 任务进入 `local_ready` |
| 4. Dry-run | 点击“预览” | 展示上传、创建目录、命令与下载计划 | 用户确认 |
| 5. 上传 | 点击“上传” | 上传输入与共享文件 | 任务进入 `uploaded` |
| 6. 提交 | 点击“提交” | 生成后台批处理文件并通过 SSH 启动 | 任务进入 `submitted` / `running` |
| 7. 刷新状态 | 点击“刷新” | 读取远程状态标记与日志 | 更新运行状态 |
| 8. 下载 | 点击“下载” | 仅下载已完成任务的配置文件集合 | 任务进入 `downloaded` |
| 9. 分析 | 点击“分析” | 提取结果、生成汇总 | 任务进入 `analyzed` |
| 10. 重跑 | 选中失败任务 | 新建 Batch，重新走流程 | 保留历史 |
| 11. 归档 | 用户确认项目阶段结束 | 可后续导出 / 归档 | 完整记录保留 |

## 4.2 示例 A：普通单步任务

场景：20 个 Gaussian 输入文件。

```yaml
submit:
  input_glob: "*.gjf"
  command: "g16 {input_name}"
  max_parallel: 8
```

流程：

1. 用户将 `mol_001.gjf` ~ `mol_020.gjf` 放到本地 `inputs/`；
2. JobDesk 扫描，生成 20 个任务；
3. 上传到远程；
4. 一次性提交整批；
5. 远程后台最多同时跑 8 个；
6. GUI 关闭后批次继续；
7. 用户次日打开 GUI 刷新状态；
8. 手动下载已完成输出；
9. 本地提取能量并生成汇总；
10. 失败任务进入新 Batch 重跑。

## 4.3 示例 B：复杂任务

场景：每个任务目录里包含 `run.sh` 和若干辅助文件。

```yaml
submit:
  input_glob: "*/run.sh"
  command: "bash run.sh"
  max_parallel: 2
```

流程：

1. 本地 `inputs/system_A/run.sh`、`inputs/system_B/run.sh`；
2. JobDesk 扫描目录型任务；
3. 上传任务独立文件和共享文件；
4. 在远程各任务目录执行 `bash run.sh`；
5. 通过 `.jobdesk_status` 与用户日志联合判断状态；
6. 任务完成后手动下载指定输出；
7. 按配置提取结果。

---

# 5. 对象模型

JobDesk 的核心对象层级：

```text
Server
  └── Project
        └── Batch
              └── Task
                    └── Result / Failure
```

## 5.1 Server

代表一台可连接的远程服务器。  
属于用户级配置。

关键属性：

- `server_id`
- `display_name`
- `host`
- `port`
- `username`
- `auth_method`
- `key_path`
- `default_shell`
- 运行时连接状态

## 5.2 Project

代表一个本地科研项目。  
项目保存：

- 本地根目录；
- 项目配置；
- 关联 `server_id`；
- 远程根目录；
- 任务发现规则；
- 结果提取规则。

## 5.3 Batch

代表一次明确提交。  
Batch 是历史边界，也是恢复边界。

关键属性：

- `batch_id`
- `project_id`
- `created_at`
- `max_parallel`
- `task_count`
- `status`
- `manifest_path`
- `remote_batch_dir`

## 5.4 Task

代表 Batch 内的单个任务。

关键属性：

- `task_id`
- `batch_id`
- 本地输入路径；
- 远程任务目录；
- 渲染后的提交命令；
- 生命周期状态；
- 分组字段；
- 上传 / 提交 / 完成 / 下载 / 分析时间。

## 5.5 Result

代表一个任务或候选结果。

关键属性：

- `task_id`
- `batch_id`
- `result_id`
- 提取字段；
- 源文件；
- 是否为任务最佳结果；
- 分组相对值；
- 全局相对值。

## 5.6 Failure

代表一次失败记录。

关键属性：

- `task_id`
- `batch_id`
- 失败阶段；
- 失败原因；
- 匹配到的 pattern；
- 源日志；
- 上下文片段。

---

# 6. 版本范围

## 6.1 v0.1：只读管理台

### 必须实现

1. 服务器列表与 SSH 测试；
2. 服务器状态：
   - load；
   - CPU；
   - 内存；
   - 磁盘；
   - 当前用户主要进程；
3. 项目列表；
4. 远程任务发现；
5. 任务状态检查；
6. 失败汇总；
7. 结果提取；
8. 分组汇总；
9. GUI 基础展示。

### 不实现

- 上传；
- 提交；
- 下载；
- 重跑；
- 批次写操作；
- 自动轮询。

## 6.2 v0.2：完整基础工作流

### 必须实现

1. 本地输入发现；
2. Batch 创建；
3. Manifest 生成；
4. Dry-run；
5. 输入上传；
6. 共享文件上传；
7. 直接命令提交；
8. `max_parallel` 方案 B；
9. 批次后台运行；
10. 生命周期状态；
11. 手动下载；
12. 本地分析；
13. 失败任务新 Batch 重跑；
14. 断点恢复；
15. 覆盖保护；
16. 日志查看；
17. GUI 完整四页面。

### 不实现

- 自动下载；
- 自动轮询；
- 服务器常驻 agent；
- Slurm / PBS 适配；
- 项目模板市场；
- 3D 分子可视化；
- 自动修复失败；
- 跨服务器智能调度；
- 完整远程文件管理器。

## 6.3 v0.3 以后

可考虑：

- 自动轮询；
- 桌面通知；
- 归档与清理；
- 可选 rsync 后端；
- 可选 postprocess hook；
- 更丰富的历史对比；
- 可选集群调度器适配；
- 服务器级资源历史图；
- 全局项目索引与最近打开项目管理。

---

# 7. 任务生命周期

## 7.1 状态集合

| 状态 | 含义 |
|---|---|
| `local_ready` | 本地输入已准备，尚未上传 |
| `uploaded` | 输入已上传到远程 |
| `submitted` | 后台批处理已接受该任务 |
| `running` | 任务正在远程运行 |
| `remote_completed` | 远程执行成功结束 |
| `downloaded` | 输出已下载到本地 |
| `analyzed` | 本地结果已提取与汇总 |
| `failed` | 任务在上传、提交、运行、下载或分析任一阶段失败 |

## 7.2 状态迁移

```text
local_ready
   ↓ upload
uploaded
   ↓ submit
submitted
   ↓ remote start
running
   ↓ success
remote_completed
   ↓ manual download
downloaded
   ↓ analyze
analyzed

任何非终态都可能 → failed
failed → 新 Batch 重跑
```

## 7.3 状态判定原则

### 7.3.1 不仅依赖用户日志

仅靠：

- `pgrep`；
- 日志更新时间；
- 用户输出 pattern；

在通用任务下不够可靠。

### 7.3.2 v0.2 默认引入轻量状态标记

JobDesk 在提交任务时，自动生成任务包装层，例如：

```text
.jobdesk_run.sh
.jobdesk_status
.jobdesk_exit_code
.jobdesk_submit.log
```

包装层负责：

1. 写入 `submitted` / `running`；
2. 执行用户配置命令；
3. 记录退出码；
4. 根据退出码写入 `completed` 或 `failed`；
5. 保留最小提交日志。

这不是服务器常驻执行器，而是任务级的一次性控制工件。

### 7.3.3 与用户日志联合判断

状态判定优先级建议：

1. `.jobdesk_status` / `.jobdesk_exit_code`；
2. 用户配置的 `success_pattern` / `fail_pattern`；
3. 远程文件存在性；
4. 进程与日志辅助信息。

## 7.4 边界情况

| 情况 | 处理 |
|---|---|
| 有部分输出，但退出码非 0 | `failed`，保留输出 |
| 退出码 0，但 `success_pattern` 不匹配 | 标记为 `needs_review` 或 `remote_completed_with_warning` 的内部诊断；v0.2 可先记录警告 |
| manifest 写 `running`，远程状态文件已显示完成 | 恢复时推进状态 |
| manifest 写 `uploaded`，远程输入不存在 | 回退为 `local_ready` |
| 下载中断 | 保持 `remote_completed`，下次可重试下载 |
| 分析失败 | `failed`，失败阶段为 `analysis`，不污染原始输出 |

---

# 8. 数据模型

## 8.1 Server

```yaml
server_id: wcm
display_name: WCM Server
host: 1.2.3.4
port: 22
username: xianj
auth_method: key
key_path: C:/Users/xianj/.ssh/id_ed25519
```

持久化位置：

```text
%APPDATA%\JobDesk\servers.yaml
```

## 8.2 Project

```yaml
project:
  name: example_project
  description: optional

server_id: wcm
```

项目只引用 `server_id`，不保存连接信息。

## 8.3 Batch

```json
{
  "batch_id": "20260511_143022_123456",
  "project_name": "example_project",
  "created_at": "2026-05-11T14:30:22.123456",
  "max_parallel": 4,
  "status": "running",
  "task_count": 16,
  "remote_batch_dir": "/home/user/example/20260511_143022_123456"
}
```

## 8.4 Manifest 行

建议字段：

```text
task_id
batch_id
group_key
local_input_path
remote_job_dir
remote_input_name
rendered_command
status
uploaded_at
submitted_at
started_at
completed_at
downloaded_at
analyzed_at
error_message
```

## 8.5 TransferRecord

用于记录：

- 上传了什么；
- 下载了什么；
- 源路径；
- 目标路径；
- 文件大小；
- 是否跳过；
- 是否覆盖；
- 时间；
- 状态。

## 8.6 ResultRecord

建议字段：

```text
task_id
batch_id
result_id
group_key
source_file
field_name
value
unit
is_best_for_task
relative_group
relative_global
```

## 8.7 FailureRecord

建议字段：

```text
task_id
batch_id
fail_stage
reason
matched_pattern
source_file
context
detected_at
```

---

# 9. 项目配置设计

## 9.1 顶层结构

```yaml
project:
server_id:
local_paths:
remote_paths:
task_discovery:
name_parser:
group_by:
submit:
upload:
download:
status:
extract:
hooks:
output:
```

## 9.2 字段说明

### `project`

```yaml
project:
  name: example_project
  description: optional
```

### `server_id`

```yaml
server_id: wcm
```

### `local_paths`

```yaml
local_paths:
  input_dir: "./inputs"
  result_dir: "./results"
```

### `remote_paths`

```yaml
remote_paths:
  work_dir: "/home/user/projects/example"
  shared_dir: "{work_dir}/_shared"
```

### `task_discovery`

```yaml
task_discovery:
  mode: "flat"           # flat / directory
  input_glob: "*.gjf"
```

### `name_parser`

```yaml
name_parser:
  regex: "^(?P<task_id>.+)\\.gjf$"
```

### `group_by`

```yaml
group_by:
  - ligand
  - face
```

### `submit`

```yaml
submit:
  command: "g16 {input_name}"
  max_parallel: 4
  shell: "bash"
```

允许变量：

```text
{task_id}
{job_dir}
{input_file}
{input_name}
{stem}
{batch_id}
```

### `upload`

```yaml
upload:
  task_files:
    - "{input_file}"
  shared_files:
    - "basis_set.gbs"
  skip_if_same_size: true
```

### `download`

```yaml
download:
  patterns:
    - "*.log"
    - "*.out"
    - "*.xyz"
  completed_only: true
  overwrite_policy: "deny_cross_batch"
```

### `status`

```yaml
status:
  success_patterns:
    - "Normal termination"
  failure_patterns:
    - "Error termination"
  check_globs:
    - "*.log"
    - "*.out"
```

### `extract`

```yaml
extract:
  results:
    - name: energy
      source_glob: "*.out"
      regex: "SCF Done:\\s+E\\(.+\\)\\s+=\\s+(?P<value>-?[\\d.]+)"
      strategy: "last"
      type: "float"
      unit: "hartree"
```

### `hooks`

v0.2 仅保留 schema，不执行复杂自定义逻辑：

```yaml
hooks:
  post_download: null
  post_analysis: null
```

### `output`

```yaml
output:
  relative_energy_unit: "kcal_mol"
  hartree_to_kcal_mol: 627.509474
```

## 9.3 示例配置 A：普通单步任务

```yaml
project:
  name: catalyst_screen

server_id: wcm

local_paths:
  input_dir: "./inputs"
  result_dir: "./results"

remote_paths:
  work_dir: "/home/user/catalyst_screen"

task_discovery:
  mode: "flat"
  input_glob: "*.gjf"

name_parser:
  regex: "^(?P<task_id>.+)\\.gjf$"

group_by: []

submit:
  command: "g16 {input_name}"
  max_parallel: 8

upload:
  task_files:
    - "{input_file}"
  shared_files: []

download:
  patterns:
    - "*.log"
    - "*.chk"
  completed_only: true

status:
  success_patterns:
    - "Normal termination"
  failure_patterns:
    - "Error termination"
  check_globs:
    - "*.log"

extract:
  results:
    - name: energy
      source_glob: "*.log"
      regex: "SCF Done:\\s+E\\(.+\\)\\s+=\\s+(?P<value>-?[\\d.]+)"
      strategy: "last"
      type: "float"
      unit: "hartree"
```

## 9.4 示例配置 B：复杂任务

```yaml
project:
  name: md_batch

server_id: gpu_server

local_paths:
  input_dir: "./inputs"
  result_dir: "./results"

remote_paths:
  work_dir: "/scratch/user/md_batch"

task_discovery:
  mode: "directory"
  input_glob: "*/run.sh"

name_parser:
  regex: "^(?P<task_id>[^/]+)/run\\.sh$"

group_by: []

submit:
  command: "bash run.sh"
  max_parallel: 2

upload:
  task_files:
    - "{task_dir}/*"
  shared_files:
    - "common_params.json"

download:
  patterns:
    - "run.log"
    - "output.dat"
    - "trajectory.xyz"
  completed_only: true

status:
  success_patterns:
    - "Job finished successfully"
  failure_patterns:
    - "FATAL ERROR"
  check_globs:
    - "run.log"

extract:
  results:
    - name: final_energy
      source_glob: "output.dat"
      regex: "Final Energy:\\s+(?P<value>-?[\\d.]+)"
      strategy: "last"
      type: "float"
```

---

# 10. 本地与远程目录约定

## 10.1 全局用户配置目录

```text
%APPDATA%\JobDesk\
├── servers.yaml
└── app_state.json
```

## 10.2 本地项目目录

```text
<project_root>/
├── project.yaml
├── inputs/
├── results/
│   ├── batches/
│   │   └── <batch_id>/
│   │       └── <task_id>/
│   └── aggregate/
│       ├── final_results.tsv
│       ├── group_summary.tsv
│       └── summary.json
└── .jobdesk/
    └── batches/
        └── <batch_id>/
            ├── batch.json
            ├── manifest.tsv
            ├── job_status.tsv
            ├── failures.tsv
            ├── final_results.tsv
            ├── group_summary.tsv
            └── summary.json
```

### 权威源规则

- 每个 Batch 自己目录下的：
  - `manifest.tsv`
  - `job_status.tsv`
  - `failures.tsv`
  - `final_results.tsv`
  - `group_summary.tsv`

  是该 Batch 的**权威源**。

- `results/aggregate/` 下的全局合并表只是**派生聚合视图**，不可作为恢复或历史权威源。

## 10.3 远程目录

```text
<remote_work_dir>/
└── <batch_id>/
    ├── _batch/
    │   ├── tasks.tsv
    │   ├── batch_control.sh
    │   └── batch_control.log
    ├── <task_id>/
    │   ├── input...
    │   ├── .jobdesk_run.sh
    │   ├── .jobdesk_status
    │   ├── .jobdesk_exit_code
    │   ├── .jobdesk_submit.log
    │   └── user_outputs...
    └── ...
```

## 10.4 目录冲突策略

- 新 Batch 必须使用新 `batch_id`；
- 跨 Batch 默认不覆盖；
- 重跑默认新建 Batch；
- 若远程 Batch 已存在：
  - 默认拒绝；
  - 需用户显式选择恢复或改用新 Batch；
- 本地结果同理。

---

# 11. 批次与 Manifest 设计

## 11.1 为什么必须有 Batch

Batch 是以下能力的基础：

- 历史可追溯；
- 失败重跑不污染旧结果；
- 断点恢复；
- 结果隔离；
- 并行提交边界；
- GUI 切换批次。

## 11.2 为什么 Manifest 是权威清单

Manifest 记录一次提交的明确事实：

- 哪些任务属于本 Batch；
- 每个任务对应哪个输入；
- 远程目录是什么；
- 实际执行命令是什么；
- 当前状态是什么。

后续流程都应消费 Manifest，而不是重新猜测目录内容。

## 11.3 Manifest 的使用场景

| 场景 | Manifest 作用 |
|---|---|
| 上传 | 明确本地输入与远程目标 |
| 提交 | 提供任务目录与渲染后的命令 |
| 下载 | 明确哪些任务属于当前 Batch |
| 恢复 | 对照远程实际状态修复本地状态 |
| 分析 | 建立任务与结果关系 |
| 重跑 | 选择失败任务生成新 Batch |

---

# 12. 文件传输设计

## 12.1 v0.2 推荐实现

默认实现建议：

- Python SFTP（例如 Paramiko）；
- 保持 `TransferBackend` 抽象；
- v0.3 可新增 rsync 后端。

## 12.2 上传

支持：

- 任务独立文件；
- 共享文件；
- 仅上传缺失文件；
- 已存在且大小相同可跳过；
- Dry-run 预览。

## 12.3 下载

支持：

- 仅下载已 `remote_completed` 的任务；
- 下载选中任务；
- 下载全部已完成任务；
- 按配置 pattern 下载；
- 手动触发；
- 允许仅下载最终结果文件，而非全部中间文件。

## 12.4 校验与恢复

v0.2 最小策略：

- 传输后校验文件大小；
- 失败可重试；
- 上传 / 下载记录写入 TransferRecord；
- 下载中断不改变远程完成状态；
- 下次可继续下载。

## 12.5 覆盖策略

| 情况 | 默认行为 |
|---|---|
| 远程 Batch 目录已存在 | 拒绝，除非用户选择恢复 |
| 本地结果目录已有同 Batch 文件 | 可按恢复逻辑复用 |
| 跨 Batch 结果重名 | 不覆盖 |
| 手动下载重复文件 | 若大小一致可跳过 |
| 明确覆盖 | 需二次确认 |

---

# 13. 任务提交与并行模型

## 13.1 单任务命令模型

用户在配置中定义：

```yaml
command: "g16 {input_name}"
```

JobDesk 在远程任务目录中执行用户命令，但默认包裹在内部控制脚本中：

```bash
#!/usr/bin/env bash
set +e
echo "running" > .jobdesk_status
<rendered_user_command> > .jobdesk_submit.log 2>&1
rc=$?
echo "$rc" > .jobdesk_exit_code
if [ "$rc" -eq 0 ]; then
  echo "completed" > .jobdesk_status
else
  echo "failed" > .jobdesk_status
fi
exit "$rc"
```

这既保持“直接提交”的语义，又提升状态可判定性。

## 13.2 整批并行提交

已确认采用：

```text
整批提交 + 后台批处理 + 最多 N 并行
```

### 推荐控制方式

- JobDesk 生成：
  - `tasks.tsv`
  - 每个任务的 `.jobdesk_run.sh`
  - `batch_control.sh`
- `tasks.tsv` 直接来源于 Manifest；
- `batch_control.sh` **只消费 Manifest 已确定的信息**，不重新发现输入文件，也不使用 `ls` 猜测。

### 示例逻辑

```text
tasks.tsv:
task_id<TAB>remote_job_dir<TAB>runner_path
```

`batch_control.sh` 的职责：

1. 读取 `tasks.tsv`；
2. 按 `max_parallel` 运行任务 runner；
3. 记录批次开始与结束；
4. 批次结束后写入 `BATCH_COMPLETED`。

具体实现可使用：

- `xargs -P N`；
- 或在不支持时回退到普通 shell 并发控制。

## 13.3 GUI 关闭后的行为

| 情况 | 行为 |
|---|---|
| 当前 Batch 已入队任务 | 继续自动补位并跑完 |
| GUI 关闭后新增加的本地任务 | 不自动接纳 |
| GUI 关闭后 SSH 断开 | 后台 Batch 继续 |
| 用户重新打开 GUI | 通过状态刷新重新同步 |

## 13.4 提交模式

| 模式 | 含义 |
|---|---|
| 提交全部 | 提交当前 Batch 全部 `uploaded` 任务 |
| 提交选中 | 仅提交选中的 `uploaded` 任务 |
| 提交未完成 | 基于状态筛选未进入终态的任务，但是否进入当前 Batch 需在创建 Batch 时明确 |
| 重跑失败 | 创建新 Batch，并复制失败任务到新清单 |

## 13.5 防重复提交

- 只允许对 `uploaded` 任务提交；
- 已处于 `submitted` / `running` / `remote_completed` 的任务不得重复提交；
- 重跑必须走新 Batch；
- Batch 已经启动后，默认不再追加新任务。

---

# 14. 状态检查与结果分析

## 14.1 远程状态检查

优先使用：

1. `.jobdesk_status`
2. `.jobdesk_exit_code`
3. 用户配置的日志成功 / 失败 pattern
4. 用户日志与输出文件存在性
5. 进程信息作辅助诊断

## 14.2 失败扫描

失败分阶段：

- `upload`
- `submit`
- `runtime`
- `download`
- `analysis`

失败信息应保留：

- 原因；
- 源文件；
- 匹配 pattern；
- 上下文；
- 时间；
- 是否仍有可下载输出。

## 14.3 结果提取

支持：

- 一个任务一个结果；
- 一个任务多个候选结果；
- 多字段提取；
- 多个源文件；
- `first` / `last` / `all` 匹配策略；
- 无数值项目只做状态管理。

## 14.4 最佳候选与相对值

若项目定义了可比较数值字段，例如 `energy`：

- 可按任务找最佳候选；
- 可按组计算组内相对值；
- 可按全局计算全局相对值；
- 具体比较字段由配置决定。

---

# 15. 输出文件规范

## 15.1 Batch 权威文件

| 文件 | 一行代表 | 用途 |
|---|---|---|
| `manifest.tsv` | 一个 Task | Batch 权威任务清单与状态 |
| `batch.json` | 一个 Batch | Batch 元数据 |
| `job_status.tsv` | 一个 Task 快照 | GUI 状态表 |
| `failures.tsv` | 一个 FailureRecord | 错误详情 |
| `final_results.tsv` | 一个 ResultRecord | 提取结果 |
| `group_summary.tsv` | 一个组 | 分组汇总 |
| `summary.json` | 一个 Batch 摘要 | GUI 总览 |

## 15.2 全局聚合文件

| 文件 | 性质 |
|---|---|
| `results/aggregate/final_results.tsv` | 派生聚合视图 |
| `results/aggregate/group_summary.tsv` | 派生聚合视图 |
| `results/aggregate/summary.json` | 派生聚合视图 |

这些文件可以重建，不可作为恢复权威源。

---

# 16. GUI 规划

## 16.1 Servers 页面

显示：

- 服务器列表；
- 在线状态；
- host / port / username；
- CPU / 内存 / 磁盘 / load；
- 当前用户主要计算进程。

操作：

- 添加服务器；
- 编辑服务器；
- 测试连接；
- 删除服务器。

## 16.2 Projects 页面

显示：

- 项目名；
- `server_id`；
- 本地路径；
- 远程路径；
- 最近批次；
- 当前任务统计。

操作：

- 打开项目；
- 新建项目；
- 编辑配置；
- 刷新项目；
- 打开终端。

## 16.3 Tasks 页面

显示：

- Batch 选择器；
- Task ID；
- 生命周期状态；
- 分组字段；
- 上传 / 提交 / 完成 / 下载 / 分析时间；
- 失败摘要；
- 最佳结果摘要。

操作：

- 扫描；
- Dry-run；
- 上传；
- 提交；
- 刷新；
- 下载；
- 分析；
- 重跑选中。

## 16.4 Results 页面

显示：

- failures；
- final results；
- group summary；
- 最佳候选；
- 相对值。

操作：

- 查看日志；
- 打开本地结果目录；
- 导出；
- 重新分析。

## 16.5 GUI 原则

- 长操作必须放后台 worker；
- 状态变化必须清晰；
- 修改远程状态的操作必须有确认；
- Dry-run 先于上传 / 提交 / 重跑；
- GUI 不重写后端逻辑。

---

# 17. 技术架构

## 17.1 推荐技术栈

- Python 3.13；
- PySide6；
- YAML 配置；
- SFTP 传输；
- SSH 远程命令；
- Windows 11 原生开发；
- 不依赖 WSL 作为运行前提。

## 17.2 模块分层

```text
src/
└── jobdesk_app/
    ├── core/
    │   ├── models.py
    │   ├── lifecycle.py
    │   ├── manifest.py
    │   ├── batch.py
    │   ├── analyzer.py
    │   ├── grouping.py
    │   └── outputs.py
    ├── config/
    │   ├── loader.py
    │   ├── schema.py
    │   └── servers.py
    ├── remote/
    │   ├── ssh.py
    │   ├── sftp.py
    │   ├── submitter.py
    │   ├── status.py
    │   └── server_info.py
    └── gui/
        ├── app.py
        ├── pages/
        ├── models/
        └── workers.py
tests/
```

## 17.3 依赖原则

```text
config → core → remote → gui
```

- `core` 不依赖 SSH、GUI；
- `remote` 依赖 core / config；
- `gui` 调用 core / remote；
- 状态机、提取、分组、Manifest 必须保持纯逻辑可测试。

## 17.4 测试重点

关键纯逻辑模块必须有充分单元测试：

- 配置解析；
- Manifest 读写；
- 生命周期迁移；
- 结果提取；
- 分组汇总。

远程模块必须有真实服务器集成测试或高质量 mock 测试。

---

# 18. 实施里程碑

## M0：需求冻结

**目标**：冻结规划、确认未决项。  
**验收**：本计划与决策文档一致。

## M1：配置与数据模型

实现：

- 全局 `servers.yaml`；
- `project.yaml` schema；
- 数据模型；
- Batch；
- Manifest 读写。

不实现：

- SSH；
- GUI；
- 传输；
- 提交。

验收：

- 合法配置可加载；
- 非法配置报错清晰；
- Manifest 可正确读写。

## M2：纯 core 分析能力

实现：

- 本地 fixture 输入；
- 状态记录结构；
- 结果提取；
- 分组汇总；
- 输出文件生成。

不实现：

- 真实远程连接；
- 上传 / 下载；
- 提交。

验收：

- 多字段提取正确；
- 多候选保留正确；
- 分组汇总正确；
- 空 extract 不报错。

## M3：远程连接与服务器状态

实现：

- SSH；
- 连接测试；
- 服务器状态；
- 远程目录扫描；
- 远程状态文件读取。

验收：

- 在线 / 离线状态清楚；
- 远程状态可准确读取；
- 超时与错误可处理。

## M4：文件传输

实现：

- 上传；
- 共享文件上传；
- 下载；
- 大小校验；
- Dry-run；
- 覆盖保护。

验收：

- 上传 / 下载正确；
- 已有文件可跳过；
- 冲突可提示；
- 中断后可恢复。

## M5：提交与并行

实现：

- `.jobdesk_run.sh`；
- `tasks.tsv`；
- `batch_control.sh`；
- 方案 B；
- 后台 `nohup` 提交；
- Manifest 消费；
- 防重复提交。

验收：

- 整批提交后 GUI 关闭仍继续；
- 最多 N 个并行；
- 不使用 `ls` 猜输入；
- 状态标记正确。

## M6：恢复与保护

实现：

- 断点恢复；
- 状态回放；
- 覆盖保护完善；
- 下载重试；
- 重跑新 Batch。

验收：

- 重启程序后状态可恢复；
- 重跑不污染旧 Batch；
- 误覆盖被阻止。

## M7：GUI

实现：

- Servers；
- Projects；
- Tasks；
- Results；
- 后台 worker；
- 表格与日志；
- 确认与提示。

验收：

- 完整流程可通过 GUI 完成；
- GUI 不复制业务逻辑；
- 长操作不卡 UI。

## M8：真实项目验收

目标：

- 选取多个不同类型项目；
- 完成从本地输入到结果汇总的全流程；
- 验证中断恢复；
- 验证重跑；
- 验证大型输出下载策略。

---

# 19. 测试与验收

## 19.1 单元测试

覆盖：

- 配置解析；
- 服务器配置解析；
- Manifest；
- 生命周期；
- 命令渲染；
- 结果提取；
- 分组；
- 输出文件生成。

## 19.2 Fixture 测试

必须覆盖：

- 普通单步任务；
- 复杂任务；
- 成功任务；
- 失败任务；
- 已上传未提交；
- 已提交运行中；
- 远程完成未下载；
- 已下载未分析；
- 多任务分组；
- 多候选结果；
- 文件冲突；
- 断点恢复；
- 无结果提取项目。

## 19.3 集成测试

必须覆盖：

- SSH 连接；
- SFTP 上传 / 下载；
- 远程后台提交；
- `max_parallel` 方案 B；
- GUI 关闭后的后台继续运行；
- 状态恢复；
- 手动下载。

## 19.4 GUI 手工验收

至少验证：

- 新建项目；
- 选择服务器；
- 扫描；
- Dry-run；
- 上传；
- 提交；
- 刷新；
- 下载；
- 分析；
- 重跑失败；
- 查看日志。

---

# 20. 风险、非目标与扩展边界

## 20.1 主要风险

| 风险 | 缓解 |
|---|---|
| Windows / Linux 路径混淆 | 本地用 `Path`，远程用 `PurePosixPath` |
| 只靠日志难以判定状态 | 默认生成 JobDesk 状态标记文件 |
| 批处理层重新猜输入导致错误 | 批处理只消费 Manifest |
| 文件覆盖导致结果污染 | Batch 隔离 + 默认拒绝跨 Batch 覆盖 |
| 全局聚合表被误当权威源 | 明确 Batch 文件才是权威源 |
| GUI 重写业务逻辑 | 架构约束 |
| 服务器不支持某些 shell 工具 | 提交前做 preflight；必要时提供回退 |
| 传输大文件耗时 | 手动下载、按 pattern 下载、后续可选 rsync |
| 过度设计成调度器 | v0.2 严格不做长期队列与智能调度 |

## 20.2 v0.2 明确不做

- 服务器常驻 agent；
- 多服务器负载均衡；
- 自动轮询；
- 自动下载；
- Slurm / PBS；
- 3D 可视化；
- 输入文件生成；
- 自动失败修复；
- 完整远程文件浏览器；
- Web 版。

---

# 21. 建议默认项与后续可确认问题

## 21.1 已建议默认，但非核心冻结项

以下设计可作为 v0.2 默认实现：

1. 默认传输后端：Python SFTP；
2. 传输校验：v0.2 先做大小校验；
3. 共享文件：按项目配置上传；
4. `hooks`：保留 schema，不执行复杂 hook；
5. 自动轮询：v0.2 不做；
6. 结果目录：Batch 为权威源，aggregate 为视图。

## 21.2 后续可再确认的问题

这些问题不阻塞 M1–M3，但在进入对应实现前应确认：

1. 是否接受 v0.2 默认使用 Python SFTP，而不是直接依赖 rsync；
2. 对超大文件下载是否需要设置默认大小上限；
3. 是否需要在 v0.2 允许“下载最终文件”和“下载全部文件”两种预设；
4. 是否需要定义 `needs_review` 这类非终态诊断状态；
5. 是否需要在 v0.2 提供 CLI 调试入口，或仅保留 GUI。

---

# 附录 A：冻结版核心决策摘要

```text
1. Windows 11 本地主程序
2. 远程 Linux 服务器只作为执行端
3. 不按计算程序类型设计
4. 默认直接提交命令
5. 服务器无常驻 agent
6. max_parallel = 方案 B
7. 当前 Batch 内任务后台自动补位并跑完
8. GUI 关闭后不自动接纳新任务
9. 输出下载手动触发
10. 重跑失败任务创建新 Batch
11. servers.yaml 为全局用户级配置
12. GUI 使用 PySide6
13. Manifest 为 Batch 权威清单
14. Batch 自身结果文件为权威源，aggregate 仅为视图
15. v0.2 默认生成轻量任务状态标记文件
```
