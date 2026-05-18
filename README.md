# JobDesk

Windows 11 本地的科研计算工作台，通过 SSH 管理远程 Linux 服务器，完成"本地输入 → 远程执行 → 结果回传 → 本地分析"的完整科研计算链路。

**当前阶段：M7.1（GUI 接入后端工作流）**

**当前 GUI 能力：**
- Servers: Reload servers.yaml + Test Connection（Worker 后台）
- Projects: Open Project + 显示 ProjectContext + 联动 Tasks/Results
- Tasks: Scan / Create Batch / Upload / Submit / Refresh / Download / Analyze
  - 全部通过 BackgroundWorker 执行，不阻塞 UI
  - Submit 前有确认对话框 + Dry-run 预览
  - 操作完成后自动更新按钮状态
- Results: 查看 final_results / failures / group_summary / job_status / summary.json

**当前 GUI 限制：**
- 无自动轮询
- 无自动下载
- 无打包 exe

## 手工验收流程

```powershell
# 1. 准备 fake 项目（任意 .gjf 文件）
mkdir test_proj\inputs
echo "" > test_proj\inputs\mol_001.gjf
echo "" > test_proj\inputs\mol_002.gjf

# 2. 创建 project.yaml（使用 fake 命令）
# submit.command: "echo {input_name} && sleep 5" 或类似安全命令

# 3. 启动 GUI
python -m jobdesk_app.gui.app

# 4. Projects → Open Project → 选择 test_proj
# 5. Tasks → Scan Inputs → Create Batch
# 6. Dry-run Upload → Upload
# 7. Dry-run Submit → Submit (确认对话框)
# 8. 等待片刻 → Refresh
# 9. Download → Analyze
# 10. Results 查看输出
```

## 安装开发环境

```powershell
# 需要 Python 3.13+
python --version

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\Activate.ps1

# 安装依赖（以可编辑模式安装 src 下的 jobdesk_app 包）
pip install -e ".[dev]"
```

## 运行测试

```powershell
pytest tests/ -v
```

## 项目结构

```
jobdesk/                          # 项目根目录
├── src/
│   └── jobdesk_app/              # Python 包（import jobdesk_app）
│       ├── __init__.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── models.py         # BatchMeta / ResultRecord / FailureRecord
│       │   ├── batch.py          # batch.json 读写
│       │   ├── manifest.py       # Manifest / TaskRecord TSV 读写
│       │   ├── lifecycle.py      # TaskStatus 枚举 + 状态迁移
│       │   ├── template.py       # 命令模板渲染
│       │   ├── transfer.py       # TransferRecord / 传输状态
│       │   ├── submit.py          # SubmitMode / SubmitPlan / SubmitResult
│       │   ├── analyzer.py       # 结果提取引擎
│       │   ├── grouping.py       # 分组汇总
│       │   └── outputs.py        # TSV/JSON 输出
│       ├── remote/
│       │   ├── __init__.py
│       │   ├── errors.py         # RemoteError / SSHConnectionError 等
│       │   ├── ssh.py            # SSHClientWrapper + SSHResult
│       │   ├── sftp.py            # SFTPClientWrapper (上传/下载)
│       │   ├── submitter.py       # JobSubmitter (脚本生成+提交)
│       │   ├── server_info.py     # 服务器状态采集
│       │   ├── status.py          # 远程任务状态标记读取
│       │   └── status_refresh.py  # 状态恢复刷新
│       ├── services/
│       │   ├── __init__.py
│       │   ├── errors.py          # ServiceError 等
│       │   ├── project_service.py # ProjectContext 创建
│       │   ├── batch_service.py   # 输入发现 + Batch 创建
│       │   └── workflow_service.py # WorkflowService facade
│       └── config/
│           ├── __init__.py
│           ├── schema.py        # Pydantic 配置模型
│           ├── servers.py       # servers.yaml 加载
│           └── loader.py        # project.yaml 加载
├── tests/
│   ├── test_config_loader.py
│   ├── test_manifest.py
│   ├── test_batch.py
│   ├── test_lifecycle.py
│   ├── test_template.py
│   ├── test_m2_analysis.py
│   ├── test_remote_ssh.py
│   ├── test_server_info.py
│   ├── test_remote_status.py
│   ├── test_sftp.py
│   ├── test_submitter.py
│   └── integration/
│       ├── test_real_ssh.py      # 真实 SSH 集成测试（默认 skip）
│       └── test_real_sftp.py     # 真实 SFTP 集成测试（默认 skip）
├── docs/
│   ├── JOBDESK_PLAN.md
│   └── JOBDESK_DECISIONS.md
├── pyproject.toml
└── README.md
```

## 导入示例

```python
from jobdesk_app.config.loader import load_project
from jobdesk_app.config.servers import load_servers
from jobdesk_app.core.lifecycle import TaskStatus, can_transition
from jobdesk_app.core.manifest import TaskRecord, Manifest
from jobdesk_app.core.batch import create_batch, write_batch_json
from jobdesk_app.core.analyzer import analyze_tasks
from jobdesk_app.core.grouping import compute_summary
from jobdesk_app.core.outputs import write_final_results_tsv, write_summary_json
from jobdesk_app.remote.ssh import SSHClientWrapper, SSHResult
from jobdesk_app.remote.server_info import collect_server_info
from jobdesk_app.remote.status import read_remote_task_status
```

## 真实 SSH 集成测试

默认跳过，需要设置环境变量：

```powershell
$env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "wcm"
pytest tests/integration/ -v
```

## 下一阶段 M7

M7 将实现 GUI（PySide6）：Servers/Projects/Tasks/Results 四个页面，后台 worker，仍不涉及自动轮询。
