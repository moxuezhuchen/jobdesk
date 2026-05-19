# JobDesk User Guide

JobDesk 是 Windows 本地的科研计算工作台，通过 SSH 管理远程 Linux 服务器上的
Gaussian/ORCA 等计算任务。

无需创建 project.yaml。所有操作以当前本地目录 + 远端目录为中心。

## 基本工作流

1. 在 Settings 页配置服务器连接
2. 在 Files 页连接服务器，浏览远端目录
3. 选择远端文件（如 `.gjf`、`.inp`），设置命令模板（如 `g16 {name}`）
4. 点 Run → 自动生成 run 记录并提交到远端执行
5. Runs 页自动刷新状态、下载结果（通过 SSH tail -f 实时监听）
6. Results 页查看分析输出

## 重要文件

```text
%APPDATA%/JobDesk/
  servers.yaml          # 服务器配置
  gui_settings.yaml     # GUI 设置（下载模式、并发等）
  runs/                 # 全局 run 记录
    <run_id>/
      run.json          # run 元数据
      manifest.tsv      # 任务清单与状态
```

本地工作目录（workspace）：
```text
<workspace>/
  results/
    <run_id>/
      <task_id>/        # 下载的输出文件
      analysis_preview.tsv
```

## Run ID 格式

Run ID 格式为 `YYMMDD-NNN`（如 `260519-001`），每天从 001 开始递增。

## 服务器配置

`%APPDATA%/JobDesk/servers.yaml`:

```yaml
servers:
  wcm:
    host: example.com
    port: 22
    username: user
    auth_method: key
    key_path: ~/.ssh/id_ed25519
    env_init_scripts:
      - /opt/g16/bsd/g16.profile
```

`env_init_scripts` 声明任务执行前需要 source 的脚本。

## 下载模式

Settings 页可按软件配置自动下载的文件模式：
- Gaussian: `*.log,*.chk`（默认）
- ORCA: `*.out,*.gbw`（默认）

系统根据命令模板自动识别软件类型。

## 状态自动更新

JobDesk 使用 SSH `tail -f` 监听远端 `events.log`：
- 任务开始运行 → 状态自动变为"运行中"
- 任务完成 → 自动刷新 + 下载 → 状态变为"已下载"
- 如需手动刷新：右键 run → "刷新状态"

## 命令模板变量

| 变量 | 含义 | 示例 |
|------|------|------|
| `{name}` | 文件名 | `mol.gjf` |
| `{stem}` | 不含扩展名 | `mol` |
| `{path}` | 完整路径 | `/root/uma/mol.gjf` |
| `{dir}` | 所在目录 | `/root/uma` |

所有变量自动 shell 转义。

## CLI 命令

```powershell
# 文件操作
jobdesk files list-remote <server_id> <remote_path>
jobdesk files upload <server_id> <local_path> <remote_path>
jobdesk files download <server_id> <remote_path> <local_path>

# 运行管理
jobdesk run create <workspace> --server <id> --remote-dir <path> --command "g16 {name}" --files <f1> <f2>
jobdesk run list <workspace>
jobdesk run submit <workspace> <run_id>
jobdesk run refresh <workspace> <run_id>
jobdesk run download <workspace> <run_id>
jobdesk run cancel <workspace> <run_id>
jobdesk run delete <workspace> <run_id>
jobdesk run retry <workspace> <run_id>
```

## 故障恢复

- 网络断开：monitor 自动重连，下次连接成功时补齐状态
- 应用关闭期间任务完成：重启后首次激活 Runs 页时自动检测
- 下载失败：状态保持 `remote_completed`，可手动右键刷新重试
