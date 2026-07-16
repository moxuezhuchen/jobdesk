# JobDesk 测试套件

## 测试分类

### 单元测试 (默认运行)

核心业务逻辑测试，不依赖外部服务：

```bash
# 运行所有单元测试
pytest tests/

# 运行特定模块
pytest tests/test_run_service.py -v

# 运行子目录
pytest tests/test_nodegraph/
pytest tests/test_gui_behavior/
```

测试文件分布：

| 目录 | 说明 |
|------|------|
| `tests/test_*.py` | 核心业务逻辑、配置、模型、解析器 |
| `tests/test_gui_behavior/` | GUI 组件行为测试 |
| `tests/test_nodegraph/` | 节点图模型和交互测试 |
| `src/jobdesk_app/confflow/tests/` | ConfFlow 内部测试 |

主要测试文件：
- `test_run_service.py` - RunService 业务逻辑
- `test_run_repository.py` - 数据持久化
- `test_run_monitor.py` - 状态监控
- `test_submitter.py` - 提交逻辑
- `test_config_loader.py` - 配置加载
- `test_manifest.py` / `test_manifest_ops.py` - 清单管理
- `test_analyzer.py` - 结果解析
- `test_input_builder.py` - 输入文件构建
- `test_sftp.py` / `test_ssh.py` - 文件传输 (mock)
- `test_cli.py` - 命令行接口

### 集成测试 (需环境变量)

需要真实 SSH/SFTP 服务器或 WSL 环境，使用 `pytest -m integration` 标记：

```bash
# 设置环境变量
export JOBDESK_TEST_SSH_HOST=your-ssh-server
export JOBDESK_TEST_SFTP_HOST=your-sftp-server
export JOBDESK_TEST_SSH_USER=username
export JOBDESK_TEST_SSH_KEY=~/.ssh/id_rsa

# 运行集成测试
pytest tests/integration/ -m integration -v
```

集成测试文件：

| 文件 | 说明 |
|------|------|
| `integration/test_real_submitter.py` | SSH/SFTP 真实提交测试 |
| `integration/test_real_ssh.py` | SSH 连接测试 |
| `integration/test_real_sftp.py` | SFTP 传输测试 |
| `integration/test_real_confflow_wsl.py` | WSL ConfFlow 集成 |
| `integration/test_real_confflow_real_g16.py` | 真实 g16 端到端 |

### Smoke 测试 (需真实 g16)

需要 WSL Ubuntu-24.04 + Gaussian 16 许可。**运行前必须通过 pre-flight 检查**：

```bash
# Pre-flight 检查 (在 PowerShell 中运行)
wsl -e bash -c '
file /opt/g16/g16 /opt/g16/l1.exe | grep -E "ELF 64-bit" | wc -l
head -c 4096 /opt/g16/g16 | grep -ac JOBDESK_MOCK
head -c 4096 /opt/g16/l1.exe | grep -ac JOBDESK_MOCK
ls /opt/g16/bsd/g16.profile
'
```

Smoke 测试文件：

| 文件 | 说明 |
|------|------|
| `test_confflow_real_g16_smoke.py` | 基本 g16 → confflow 流程 |
| `test_confflow_real_g16_chk_smoke.py` | g16 + chk 文件处理 |
| `test_confflow_real_g16_ts_smoke.py` | 过渡态计算 |

运行方式：

```bash
# 先运行 smoke 脚本生成测试数据
python scripts/smoke_confflow_real_g16_wsl.py

# 再运行 smoke 测试
pytest tests/test_confflow_real_g16_*.py -v
```

**注意**：smoke 脚本位于 `scripts/` 目录，测试数据输出到 `tmp60f7j8ix/`。

### GUI 测试

需要 Qt 环境 (PySide6)：

```bash
# 运行 GUI 行为测试
pytest tests/test_gui_behavior/ -p pytest-qt -v

# 仅运行特定测试
pytest tests/test_gui_behavior/test_runs_page.py -v
```

GUI 测试文件：

| 文件 | 说明 |
|------|------|
| `test_gui_behavior/test_runs_page.py` | 运行结果页面 |
| `test_gui_behavior/test_file_transfer_page.py` | 文件传输页面 |
| `test_gui_behavior/test_auto_refresh.py` | 自动刷新 |
| `test_gui_behavior/test_background_workers.py` | 后台线程 |
| `test_gui_behavior/test_main_window_excepthook.py` | 异常处理 |

> **提示**：无 PySide6 时测试自动跳过 (使用 `pytest.importorskip`)。

## 常见测试命令

```bash
# 快速冒烟测试 (跳过集成测试)
pytest tests/ --ignore=tests/integration -x -q

# 带覆盖率报告
pytest tests/ --cov=src/jobdesk_app --cov-report=html

# 并行运行 (需 pytest-xdist)
pytest tests/ -n auto

# 仅运行上次失败的测试
pytest tests/ --lf

# 运行特定关键字的测试
pytest tests/ -k "test_create"

# 显示本地变量 (调试用)
pytest tests/ -l

# 详细输出
pytest tests/ -v --tb=long

# 在首次失败时进入 PDB
pytest tests/ --pdb
```

## Fixtures

主要 fixtures 定义在子目录的 `conftest.py`：

### `tests/test_gui_behavior/conftest.py`

| Fixture | 说明 |
|---------|------|
| `qt_app` | QApplication 实例 |
| `runs_page` | RunsResultsPage 组件 |
| `fake_worker_factory` | 模拟后台 worker |

### `tests/test_nodegraph/conftest.py`

| Fixture | 说明 |
|---------|------|
| `node_library` | 节点库 |
| `empty_canvas` | 空画布 |
| `sample_workflow_spec` | 示例工作流规格 |

### `tests/integration/conftest.py`

| Fixture | 说明 |
|---------|------|
| `remote_env` | 远程服务器配置 |
| `real_ssh_client` | 真实 SSH 连接 |

### `tests/` 根目录

| Fixture | 说明 |
|---------|------|
| `tmp_path` (pytest 内置) | 临时目录 |
| `runs_dir` | 全局运行目录 |

## 测试数据目录

```
tmp60f7j8ix/              # Gitignored，临时测试数据
├── phase9g_real_g16/    # g16 smoke 测试输出
├── confflow_work/        # ConfFlow 工作目录
└── ...
```

## 与 CI 的关系

| 分类 | CI 触发条件 |
|------|-------------|
| 单元测试 | 所有 PR 和 push |
| 集成测试 | 需手动触发或特定标签 |
| Smoke 测试 | 需手动触发 (g16 许可) |
| GUI 测试 | 需手动触发 (PySide6) |

## 常见问题

**Q: 测试报 `ModuleNotFoundError: No module named 'jobdesk_app'`**

A: 需要设置 `PYTHONPATH` 或从项目根目录运行：

```bash
cd c:\dft\tool\jobdesk-dev
pytest tests/ -v
```

**Q: GUI 测试跳过**

A: PySide6 未安装或无显示环境。正常行为，不影响其他测试。

**Q: 集成测试报连接失败**

A: 检查环境变量配置，或确认远程服务器可达。集成测试不会在无配置时运行。
