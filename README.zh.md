# JobDesk

JobDesk 是面向 Windows 的桌面与命令行工具，通过 SSH/SFTP 管理单次科学计算任务（Gaussian / ORCA）。它负责准备输入、提交任务到远程机器或本地 WSL 环境、监控状态、下载输出、解析并预览结果。

JobDesk 当前为公开预览项目，适合源代码评审与受控本地使用，但尚未作为稳定的公开发行包。

## 当前架构候选（2026-08-12）

当前隔离的 JobDesk 架构候选为 commit
`6b3af7704a9e6107016ec308b0c1b1b33a9a4a1d`，包版本元数据为 `0.7.0`。
下一轮兼容性证据对应已正式发布的 ConfFlow `v2.1.1`，tag peeled commit
为 `338b53b3a34593271b926fc9e96010186141a386`。发布 wheel 的 SHA-256 为
`3425d97246ee6d37369ecce672dfa154643179cc3ee744eb332aee4b94dbc5f3`，
workflow schema 的 SHA-256 为
`87991f09a0edbd56aed354bdd03b012775a2f2b98504297ab459e524f4542427`。

| 角色 | 引用 / 身份 | 状态 |
|---|---|---|
| JobDesk 稳定基线 | `e4d8f74` / v0.6.0 | 已发布的生产基线 |
| JobDesk 架构候选 | `6b3af7704a9e6107016ec308b0c1b1b33a9a4a1d` / v0.7.0 | 隔离，尚未发布 |
| ConfFlow 稳定回滚 | `6981935` / v2.0.0 | 保持不变；wheel digest 为 `04ea51666d4c12538c14f2e47eb3000148bbb666ca401318edd87f301a636e3f` |
| ConfFlow 当前配对 producer | `v2.1.1` / peeled `338b53b3a34593271b926fc9e96010186141a386` | 已正式发布；候选验收仍待完成 |
| 生产 / promotion endpoint | v0.6.0 + v2.0.0 | 未切换 |

ConfFlow v2.1.1 已发布，但发布本身不等于 endpoint promotion。side-by-side
验收、真实 launcher 验收、JobDesk 发布和生产 promotion 仍是独立门禁；旧的
ConfFlow `1a0d760` / 计划版本 2.1.0 仅保留为历史候选证据，不能当作当前验收证据。

## 适用范围

- 提交、监控、取消、刷新、下载、重试单任务 Gaussian / ORCA 计算
- 通过 ConfFlow 集成提交一个或多个 `.xyz` 输入，并在 UI 中显示每个分子的执行摘要
- 通过 SSH/SFTP 管理远端文件，删除操作受保护范围约束
- 多步工作流编排保留在 JobDesk 主界面之外

## 系统要求

- Windows 11
- Python 3.11 或更新版本
- 已配置的远程机器或 WSL 环境的 SSH 访问

## 从源码安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
jobdesk-gui
```

## 服务器配置

JobDesk 默认将服务器配置存储在 `%APPDATA%\JobDesk\servers.yaml`。

```yaml
servers:
  wsl:
    display_name: WSL Local
    host: 127.0.0.1
    port: 22
    username: root
    auth_method: key
    key_path: C:/Users/me/.ssh/id_rsa
    wsl_distro: Ubuntu
    trust_on_first_use: false
    env_init_scripts: []
    ssh_access:
      config_alias: wsl
      proxy_command: ""
      proxy_jump: ""
    external_tools:
      terminal_provider: windows_terminal
      ssh_alias: wsl
      putty_session: ""
      terminal_path: ""
    scheduler:
      type: nohup
      default_cpus: 4
      default_memory_mb: 4096
      default_walltime_minutes: 60
```

未知的 SSH 主机密钥默认会被拒绝。仅在首次连接信任主机时启用 `trust_on_first_use`，并在主机密钥保存后再次关闭。

JobDesk 不会存储 SSH 密码，也不会在命令行上传递密码。请使用基于密钥的认证或外部 SSH 配置。

## CLI 示例

```powershell
jobdesk files list-remote <server_id> <remote_path>
jobdesk files upload <server_id> <local_path> <remote_path>
jobdesk files download <server_id> <remote_path> <local_path>
jobdesk files preview <server_id> <remote_path>

jobdesk run create <workspace> --server <id> --remote-dir <path> --command "g16 {name}" --files <f1> <f2>
jobdesk run submit <workspace> <run_id>
jobdesk run refresh <workspace> <run_id>
jobdesk run download <workspace> <run_id> --patterns "*.log" "*.out"
jobdesk run cancel <workspace> <run_id>
jobdesk run retry <workspace> <run_id>
jobdesk run recover <workspace>
jobdesk run confirm-submitted <workspace> <run_id> --tasks <task_id> --job-id <task_id>=<job_id>
jobdesk run abandon-submit <workspace> <run_id> --tasks <task_id>
```

## 运行数据库

JobDesk 默认使用 SQLite 将运行和任务状态存储在 `%APPDATA%/JobDesk/runs/jobdesk.db`。WAL 模式与事务化更新允许 GUI 与 CLI 共享状态而无需重写 manifest 文件。

当前为 Schema v5。Schema v2 引入了可重放的 submit / delete 操作日志；v3 增加了独立的受信工作区注册表与 delete 操作到工作区的绑定；v4 增加了可续期的 submit 所有权租约，租约时间戳以 UTC 存储与比较；v5 新增 `submit_activity_log` 表，用于持久化提交页活动日志，使应用重启后活动记录得以保留。恢复操作仅接管无主的遗留提交或租约已过期的提交。v2 到 v3 的迁移仅从活跃 run 行种子化工作区信任，并将旧 delete 操作保留为未绑定；日志负载永远不会被视为信任锚点。在用本版本首次打开旧数据库之前，请备份完整的 SQLite 文件集。已完成的日志条目保留 7 天；未完成的条目永远不会被自动清理。

新增的运行以绝对路径作为工作区锚点持久化。删除准备必须匹配该活跃锚点；没有锚点的遗留行需要手动清理。

首次访问时，运行目录下的旧版 `run.json` 与 `manifest.tsv` 文件会被一次性导入。旧文件作为只读恢复输入保留；新增的运行不会创建这些文件。导入失败会记录在数据库中，但不会阻止合法运行的加载。

备份时，请关闭 JobDesk 并同时复制 `jobdesk.db` 以及任何存在的 `jobdesk.db-wal` 与 `jobdesk.db-shm` 文件。恢复时，请在 JobDesk 关闭状态下替换这组完整文件。应用运行时不要只复制主数据库文件。升级回滚请参考 [TROUBLESHOOTING.md § Rolling back a failed schema upgrade]。

`uncertain` 任务意味着远程 submit 命令可能已启动但 JobDesk 无法确认是否被接受。在判定前请检查调度器或远程进程。仅在确认远程任务存在后使用 `confirm-submitted`（已知 `--job-id <task_id>=<job_id>` 时一并提供）。`abandon-submit` 会使任务重新可被提交，如果原始任务实际已启动，则可能在远端产生重复任务。

SSH/SFTP 连接由 `SessionPool` 拥有。每个服务器的租约是独占的，调用方必须及时释放；应用关闭时会等待所有活跃租约归还后再关闭连接池。GUI 对象不直接拥有或共享底层会话。

## 开发

```powershell
python -m ruff check .
python -m mypy src
python -m pip install -e ".[dev,chem]"  # 工作流测试需要
python -m pytest tests -q --basetemp .pytest_tmp_dev -p no:cacheprovider
python -m build --outdir .build_dev
```

真实的 SSH/SFTP 与 ConfFlow 集成测试在设置了文档化的环境变量后才会运行。控制下的真实环境测试形态请参考 `docs/CONFFLOW_WSL_SINGLE_RUN.md`。

## ConfFlow 集成

ConfFlow 工作流引擎是**可选**依赖。JobDesk 的 GUI 在不安装它时也能加载、编辑并无损保存工作流；需要本地 producer 执行路径时才安装 `pip install -e ".[chem]"`，并且远端 Linux 计算节点必须安装匹配的 ConfFlow wheel。当前 JobDesk 合约是 `confflow>=2.0,<3.0`；稳定兼容性行仍按已发布的 2.0.0 wheel 验证，架构候选则以已发布的 v2.1.1 wheel 及其 provenance 进行验收。v2.1.1 的 wheel SHA-256 为 `3425d97246ee6d37369ecce672dfa154643179cc3ee744eb332aee4b94dbc5f3`，workflow schema SHA-256 为 `87991f09a0edbd56aed354bdd03b012775a2f2b98504297ab459e524f4542427`。JobDesk 不导入或镜像 ConfFlow 的 Python 语义模型，结构/迁移提示仅供编辑器使用，远端 canonical dry-run 和配置合约才是语义接受门。远端 capability 必须是 schema v4，并包含匹配的 `artifacts`、producer、executable 和安装 provenance 契约。control 路径必须显式选择 `control`，使用 producer-owned worker handoff，禁止静默降级到 legacy；v1.5.3 与 v1.4.6 仅保留为历史 release evidence，不属于当前生产路径。发布 v2.1.1 本身不构成 endpoint promotion；side-by-side、真实 launcher 与生产 promotion 仍是独立门禁。

```powershell
# Windows（JobDesk 端）
# 如果包索引没有提供化学版本，请先安装已发布的 ConfFlow 2.1.1 wheel；
# 离线 wheel 流程见 docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md：
# python -m pip install /path/to/confflow-2.1.1-py3-none-any.whl
python -m pip install -e ".[chem]"
```

```bash
# Linux 计算节点也安装相同的已发布 ConfFlow 2.1.1 wheel。
# 离线 wheel 流程见 docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md。
python -m pip install /path/to/confflow-2.1.1-py3-none-any.whl
```

### 提交页（Phase 14）

提交页（GUI 第二个标签页）是统一的提交界面。它把过去的 ConfFlow 向导与输入文件生成器对话框整合为一个内嵌组件，并新增了从文件页"作为输入"推送的入口（在文件页右键 → "作为输入 → 提交"）。

布局（自上而下）：

1. **输入源面板** — 本地 / 远端两个标签页。支持拖放、"添加文件…"、"添加目录…"（含递归复选框）添加 `.xyz` / `.gjf` / `.inp` 文件。
2. **模式标签** —
   - **生成输入文件**：Gaussian / ORCA 输入文件生成器（预设下拉、方法/基组/关键词/nproc/内存）。
   - **生成工作流**：完整的 ConfFlow 工作流（方法/基组校验、步骤列表、`work_dir`、高级选项、实时 YAML 预览）。
3. **操作行** — 服务器状态标签、最大并行数微调框、**提交** / **仅创建任务** / **刷新预览**。
4. **实时预览** — `.gjf` / `.inp` 内容或 `workflow.yaml`。
5. **活动记录** — 最近 50 条状态消息，已持久化到 SQLite（Schema v5），应用重启后可恢复。

在文件页的本地或远端表任意行上右键，即可将其作为输入推送到提交页。提交页是"用户希望提交"这一动作的唯一入口；页面级工作线程回调（位于 `MainWindow`）负责上传与 `RunCoordinator.create_and_submit` 调用。

确认后（工作流模式），提交页会把 `workflow.yaml` 与输入文件放入本次提交独占的远端命名空间。首次输入上传前和提交阶段，JobDesk 都要求远端 ConfFlow 返回 schema v4、版本范围 `>=2.0,<3.0`、producer/executable provenance 和 `artifacts` 字段逐项匹配的能力信息，并以 `--dry-run` 执行每个任务的真实命令；control 模式还必须通过 released worker-handoff 和明确的 backend selection。只有全部预检成功后才通过选定的 launcher 启动；control 不得静默 fallback 到 legacy。

### SSH 断连韧性

`nohup` 与 ConfFlow 的恢复机制处理的是两类故障。`nohup` 让已经启动的进程在 SSH 控制连接断开后继续运行；首次启动不带 `--resume`。如果工作流进程之后停止或失败，用户显式重试时 JobDesk 会复用该 run 原有的隔离命名空间，并且只添加一次 `--resume`，由 ConfFlow 从持久化状态继续。control watcher 通过 producer-owned cursor/events/status/artifact contract 重连；legacy 兼容路径才读取 `events.log`，并且两条路径都只同步任务声明的 `.workflow_state.json` 与 `workflow_stats.json` 精确路径。

### 自动同步进度

`services/run_monitor.py` 轮询远端 `events.log` 中的 `DONE` / `RUNNING` 行，并在每次循环中额外探测一次 `workflow_stats.json` 的 mtime。该文件一旦变化，会触发一个合成的 DoneEvent，立即刷新 Runs 页的 **Progress** 列，使步骤进度（`done: confgen, preopt; current: opt`）在两次 DONE 行之间也能更新。当前 control watcher 使用 producer-owned control protocol；历史 legacy 兼容路径不再属于生产路径。

## 安全提示

- 远端删除仅限 JobDesk 声明的运行目录，受保护的根目录会被拒绝
- 下载前会校验声明的结果路径
- 远端提交前会校验调度器资源设置
- 解析出的科学结果仅供参考，并不证明结构正确性、能量排序或科学结论

## 许可证

JobDesk 基于 Apache License 2.0 发布。详见 `LICENSE`。

## 中文用户指南

详细的中文使用说明请参考 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。
