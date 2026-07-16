# ConfFlow 依赖决策

## 当前状态

### ConfFlow 在 JobDesk 中的角色

JobDesk 使用 ConfFlow 作为计算化学工作流引擎，主要有两个使用场景：

1. **远程执行** — 通过 SSH 在远程计算节点（WSL/Linux）上以 CLI 命令运行
2. **本地验证/解析** — 在 Windows GUI 进程中直接 import ConfFlow Python API

### 实际调用方式

JobDesk **不通过 subprocess 调用本地的 vendored ConfFlow**。ConfFlow 的执行路径如下：

```
用户点击 Submit
  → submit_use_case.SubmitUseCase.execute()
    → program_adapters.ConfFlowAdapter.build_spec()
      → 构建 command_template = "confflow {name} -c confflow.yaml -w work --resume"
  → SSH 传输 XYZ + confflow.yaml 到远程节点
  → 远程节点执行: bash -c 'nohup confflow ...'
  → SSH nohup 管道（带 --resume 断点续跑支持）
```

**关键发现**：JobDesk 构建的是**远程 SSH 命令字符串**，而不是本地 subprocess 调用。

### 关键文件

| 文件 | 作用 |
|---|---|
| `services/program_adapters.py` | `ConfFlowAdapter` 构建 `RunSpec`，含 `command_template` |
| `services/submit_use_case.py` | 构建 batch 和 `workflow.yaml`，调用 `ConfFlowAdapter` |
| `gui/pages/runs_results_page.py` | 检测 `command_template` 中的 "confflow"，解析结果 JSON |
| `services/confflow_results.py` | 解析 `run_summary.json` / `workflow_stats.json` |
| `gui/nodegraph/spec_bridge.py` | import `confflow.core.models` 做 Pydantic 验证 |
| `core/input_builder.py` | `preset_to_confflow_fields()` 生成 YAML 字段映射 |

### Vendored ConfFlow 的使用

ConfFlow 源码以 **vendored package** 形式存储在：

```
src/jobdesk_app/confflow/confflow/
```

`pyproject.toml` 的 `[project.optional-dependencies]` 说明：

```toml
chem = [
    "rdkit>=2023.9",
    # ConfFlow workflow engine is vendored under
    # ``src/jobdesk_app/confflow/`` (archived upstream tag v1.1.0-archived,
    # commit 758c53926d97fe0dc0b66610cb2854cb218f3c6d). Both the Windows GUI
    # and the Linux compute node import the same Pydantic models
    # (``jobdesk_app.confflow.confflow.core.models``) for workflow validation.
    # The remote node must ``pip install confflow`` at the same pinned commit.
]
```

**已经通过 import 使用的 API**：
- `confflow.core.models` — Pydantic 模型（`GlobalConfigModel`、`CalcConfigModel`）用于 YAML 验证
- `confflow.workflow.engine.run_workflow` — 工作流引擎（单元测试中大量使用）
- `confflow.calc.ChemTaskManager` — 计算任务管理
- `confflow.blocks.confgen.run_generation` — 构象生成
- `confflow.blocks.viz.report` — 报告生成
- `confflow.core.io` — XYZ 文件 I/O

## 选项分析

### 选项 A：保持现状（远程 CLI，通过 SSH 调用）

**实现**：ConfFlow 在远程节点通过 `nohup bash -c 'confflow ...'` 执行。

**优点**：
- 架构清晰：JobDesk 是 SSH 文件传输+命令调度器，ConfFlow 是黑盒远程工具
- 断点续跑通过 `--resume` 和 checkpoint 目录天然支持
- 不需要处理 ConfFlow 的本地平台兼容性（Windows 上运行 Gaussian 16/ORCA 没有意义）
- 远程节点独立维护 ConfFlow 版本

**缺点**：
- 测试需要 mock subprocess（已在 test_cli.py 中通过 `patch("confflow.cli.run_workflow")` 实现）
- 无法在本地 GUI 进程中直接调用 ConfFlow 引擎（但这本身也不合理——Gaussian 16 运行在远程）
- 版本同步依赖手动保证（pyproject.toml 注释中已注明 commit hash）

### 选项 B：改为 Python import（完全移除 CLI 调用）

**实现**：将 `command_template = "confflow ..."` 改为直接调用 `from confflow.workflow.engine import run_workflow`。

**问题**：**这个选项不适用**。原因：

1. ConfFlow 的计算后端（Gaussian 16、ORCA）必须运行在远程节点（WSL/Linux）
2. Windows 本地没有 Gaussian 16
3. 即使在 Linux 计算节点上，GUI 进程也不应该直接调用 `run_workflow`——那会阻塞 GUI 线程，且 SSH 连接断开会导致任务中断
4. 当前的 nohup+SSH 管道设计是正确的：后台运行 + 断点续跑

### 选项 C：混合模式（已实现）

**当前状态实际上就是混合模式**：

| 操作 | 调用方式 | 说明 |
|---|---|---|
| 远程工作流执行 | SSH CLI `confflow ...` | 通过 nohup 后台运行 |
| YAML 配置验证 | Python import `confflow.core.models` | Pydantic 模型 |
| 结果文件解析 | Python import `confflow.core.io` + JSON | 解析 `run_summary.json` |
| 单元测试 | Python import `confflow.workflow.engine` | 直接 mock 引擎函数 |

## 决策

### 推荐：选项 C（当前实现的正式确认）

**当前设计已经是最佳选择，无需改动。**

#### 理由

1. **执行必须在远程**：Gaussian 16 / ORCA 是 Linux/Win32 二进制，必须在远程节点运行。JobDesk 的设计（SSH 上传 → nohup 执行 → 下载结果）完全正确。

2. **Vendored API 已被充分利用**：项目已经在 `spec_bridge.py`、`input_builder.py`、`confflow_results.py` 等多处直接 import ConfFlow Python API，用于验证、解析和测试。这是合理的使用模式。

3. **测试可 mock**：通过 `patch("confflow.workflow.engine.run_workflow")` 可以完全 mock ConfFlow 引擎，单元测试覆盖良好（test_cli.py、test_engine.py、test_dag_engine.py 等）。

4. **部署简单**：远程节点只需 `pip install confflow==<commit>`，无需额外配置。

### 如果未来需要改进

如果未来发现以下痛点，可以考虑增强：

1. **版本锁定自动化**：当前依赖手动同步 `pyproject.toml` 中的 commit hash 和远程节点的 pip install。建议在 CI 中添加一致性检查。

2. **更细粒度的进度监控**：当前通过 tail `events.log` + checkpoint mtime 探测。如果需要实时 step 级别进度，可以考虑 WebSocket 或 polling ConfFlow 的 `workflow_stats.json`。

3. **本地 dry-run 验证**：可以在上传前用 vendored `run_workflow` 做本地 dry-run（只验证 YAML + 生成任务列表，不真正执行计算），目前 `confflow --dry-run` 由远程节点处理。

## 附录：Vendored ConfFlow API 参考

核心可导入模块（均通过 `jobdesk_app.confflow.confflow.` 访问）：

```python
from jobdesk_app.confflow.confflow.core.models import (
    GlobalConfigModel,
    CalcConfigModel,
    TaskContext,
)
from jobdesk_app.confflow.confflow.workflow.engine import run_workflow
from jobdesk_app.confflow.confflow.calc import ChemTaskManager
from jobdesk_app.confflow.confflow.blocks.confgen import run_generation
from jobdesk_app.confflow.confflow.blocks.refine import RefineOptions, process_xyz
from jobdesk_app.confflow.confflow.core.io import read_xyz_file, write_xyz_file
from jobdesk_app.confflow.confflow.core.utils import ConfFlowLogger, get_logger
from jobdesk_app.confflow.confflow.config.schema import ConfigSchema, merge_step_params
```

版本：`1.0.10`（对应 vendored commit `758c53926d97fe0dc0b66610cb2854cb218f3c6d`，tag `v1.1.0-archived`）
