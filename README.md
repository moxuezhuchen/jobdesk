# JobDesk

Windows 本地的科研计算工作台，通过 SSH 管理远程 Linux 服务器，完成"选择远端文件 → 执行命令 → 结果回传 → 本地分析"的完整链路。

## 工作流

1. 在 GUI **Files** 页连接服务器，浏览远端目录
2. 选择远端文件（如 `.gjf`、`.inp`），输入命令模板（如 `g16 {name}`）
3. 点 Run → 自动生成 run 记录、提交到远端并行执行
4. **Runs** 页刷新状态、下载结果
5. **Results** 页查看分析输出

无需创建 `project.yaml`。所有操作以当前本地目录 + 远端目录为中心。

## 安装

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

需要 Python 3.11+。

## 启动 GUI

```powershell
jobdesk-gui
```

## CLI 命令

```powershell
# 文件操作
jobdesk files list-remote <server_id> <remote_path>
jobdesk files upload <server_id> <local_path> <remote_path>
jobdesk files download <server_id> <remote_path> <local_path>
jobdesk files mkdir <server_id> <remote_path>
jobdesk files preview <server_id> <remote_path>

# 运行管理
jobdesk run create <workspace> --server <id> --remote-dir <path> --command "g16 {name}" --files <f1> <f2>
jobdesk run list <workspace>
jobdesk run submit <workspace> <run_id>
jobdesk run refresh <workspace> <run_id>
jobdesk run download <workspace> <run_id> --patterns "*.log,*.chk"
jobdesk run cancel <workspace> <run_id>
jobdesk run delete <workspace> <run_id>
jobdesk run retry <workspace> <run_id>
jobdesk run rerun <workspace> <run_id>
```

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

`env_init_scripts` 声明任务执行前需要 source 的脚本，解决非交互 shell 不加载 `.bashrc` 环境的问题。

## 运行测试

```powershell
pytest tests/ -v
```

## 项目结构

```
src/jobdesk_app/
├── cli.py              # CLI 入口 (run + files)
├── config/             # Pydantic schema + servers.yaml 加载
├── core/               # 纯模型与逻辑 (manifest, lifecycle, analyzer, outputs)
├── remote/             # SSH/SFTP 封装 + 任务提交 + 状态刷新
├── services/           # RunService + FileTransferService + GuiSettings
└── gui/                # PySide6 界面 (Files/Runs/Results/Servers/Settings)
```
