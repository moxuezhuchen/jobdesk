# JobDesk

JobDesk 是面向 Windows/WSL 科研计算环境的桌面工具，通过 SSH/SFTP 提交远程任务、自动回传输出并预览分析结果。当前产品边界是：

- JobDesk 管理 Gaussian、ORCA 等单次任务的提交、监控、下载和解析。
- ConfFlow 管理其内部计算流程；JobDesk 可将一个或多个 `.xyz` 作为 ConfFlow 批次提交，并展示每个分子的执行摘要。
- JobDesk 不向用户提供自有的多步 workflow 编排入口。

## 安装与启动

需要 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
jobdesk-gui
```

## GUI 使用流程

1. 在 **Files** 页连接服务器并浏览远程目录。
2. 选择 `.gjf`、`.inp` 等输入文件并提交 Gaussian/ORCA 单次任务，或选择一个或多个 `.xyz` 点击 **运行 ConfFlow**。
3. 在 **Runs/Results** 页查看状态。启用自动刷新/自动下载时，任务完成后输出会自动下载并展示。
4. 需要终止仍在远端运行的任务时使用 **Cancel**；JobDesk 仅在远端终止请求成功后记录取消状态。

结果表中的成功、能量和终止标志仅表示输出已执行并被解析，不构成结构正确性、能量排序或科学结论验证。科研结论仍需人工复核。

## 服务器配置

配置文件默认位于 `%APPDATA%\JobDesk\servers.yaml`：

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
    scheduler:
      type: nohup
      default_cpus: 4
      default_memory_mb: 4096
      default_walltime_minutes: 60
```

默认会拒绝未知 SSH 主机密钥。首次连接受信任的本地 WSL 实例时，可在设置页显式启用 `trust_on_first_use`，连接并保存主机密钥后再关闭该选项。远程删除只允许在 JobDesk 已声明的任务目录范围内进行。

## CLI

CLI 适合诊断和脚本化；日常流程可完全在 GUI 中完成。

```powershell
# 文件操作
jobdesk files list-remote <server_id> <remote_path>
jobdesk files upload <server_id> <local_path> <remote_path>
jobdesk files download <server_id> <remote_path> <local_path>
jobdesk files preview <server_id> <remote_path>

# 单次任务管理
jobdesk run create <workspace> --server <id> --remote-dir <path> --command "g16 {name}" --files <f1> <f2>
jobdesk run submit <workspace> <run_id>
jobdesk run refresh <workspace> <run_id>
jobdesk run download <workspace> <run_id> --patterns "*.log" "*.out"
jobdesk run cancel <workspace> <run_id>
jobdesk run retry <workspace> <run_id>
```

## 开发验证

```powershell
python -m ruff check .
python -m mypy src
python -m pytest tests -q
python -m build
```

需要真实 WSL、Gaussian 或 ConfFlow 的集成测试默认跳过，仅在文档所列环境变量显式启用后执行。详见 [docs/CONFFLOW_WSL_SINGLE_RUN.md](docs/CONFFLOW_WSL_SINGLE_RUN.md)。


## License

This project is licensed under the [Apache License 2.0](LICENSE).
