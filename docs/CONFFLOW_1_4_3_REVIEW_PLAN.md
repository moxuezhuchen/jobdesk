# ConfFlow × JobDesk 双向架构审查报告（修订版 v5）

> 本版修正 v4 的 3 处 P2 元数据错误（标题 / 摘要 / 优先级表 / 工作树快照）。
> v1–v4 中所有问题列表保留，仅修正事实错误，不删减内容。

---

## 0. 摘要（一句话定性）

`/opt/ConfFlow`（Producer，独立 git 仓库，当前 `feature/phase2-workflow-state` 分支，working tree 不干净）和 `C:\dft\tool\jobdesk-dev`（Consumer，Windows 端 PySide6 + Paramiko 应用）通过 **SSH 隧道 + `confflow --capabilities --json` 协议** 进行跨仓库协作。两条契约线（Producer 持有文件名，Consumer 持有版本窗口）都通过 `tests/test_version_consistency.py` 与 `tests/test_confflow_validation_differential.py` 硬锁。这是**"线协议优先、双仓独立演化"**的模式，整体架构合理，但存在进度感知静默失败、capability 遗漏工件、schema drift 无覆盖等可观察风险。

---

## 1. 跨仓库工作目录与事实快照

| 维度 | ConfFlow（Producer） | JobDesk（Consumer） |
|---|---|---|
| 仓库根 | `/opt/ConfFlow` | `C:\dft\tool\jobdesk-dev` |
| 当前版本 | `1.4.2`（`confflow.__version__`） | `0.5.0`（`pyproject.toml`） |
| 当前分支 | `feature/phase2-workflow-state` | `main` |
| 工作树状态 | **不干净**：4 个 modified + 1 个 untracked | **不干净**：4 个 tracked + `NUL` + 2 个未跟踪 plan（`confflow-review-plan.md`、`confflow-remediation-plan.md`） |
| 入口 | `confflow / confgen / confrefine / confts / confflow-agent` | `jobdesk / jobdesk-prep / jobdesk-gui` |
| 依赖 | numpy / scipy / pyyaml / psutil / rich / pydantic v2 / rdkit / numba | pydantic / pyyaml / paramiko / pyside6 + `confflow>=1.4.2,<2.0` |
| 部署 | WSL wheel 或 `pip install /opt/ConfFlow` | Windows 11 + Python 3.11+ + Paramiko SSH |

---

## 2. 协同协议表面

### 2.1 Producer 端定义的契约常量

```python
__all__ = [
    "CAPABILITY_SCHEMA_VERSION",   # 2
    "RUN_SUMMARY_FILE",            # "run_summary.json"
    "WORKFLOW_STATS_FILE",         # "workflow_stats.json"
    "WORKFLOW_STATE_FILE",         # ".workflow_state.json"
]
```

Producer 在 emit 时字面 import 这些常量（`/opt/ConfFlow/confflow/cli.py:51-65`），不会漂移。

### 2.2 Consumer 端镜像常量

```python
CAPABILITY_SCHEMA_VERSION: int = 2
RUN_SUMMARY_FILE: str = "run_summary.json"
WORKFLOW_STATS_FILE: str = "workflow_stats.json"
WORKFLOW_STATE_FILE: str = ".workflow_state.json"
MIN_VERSION: tuple[int, int, int] = (1, 4, 2)
MAX_EXCLUSIVE: tuple[int, int, int] = (2, 0, 0)
```

### 2.3 容易误判的耦合点

1. **JobDesk 不携带 confflow 源码**，`pyproject.toml` 是外部 pip 依赖，不存在源码级耦合。
2. **生产代码存在可选、延迟的源码级依赖**：`workflow_spec.py` 用 `try: from confflow.core.models import … / except ImportError: return None` 做延迟 import；安装 `[chem]` extra 时 import 成功并使用 confflow 数据类，缺包时软降级到 `None`。**唯一真正的"镜像"import** 在 `tests/test_confflow_validation_differential.py`（测试代码，不进生产路径）。
3. **字符串 `"confflow"` 在多处是 `WorkflowKind` enum 值**（`run.py:40`），不是模块耦合。

---

## 3. 架构问题（按严重度）

### 3.1 高严重度（H）

**H1. `run_summary.json` 损坏被静默折算为成功**

`src/jobdesk_app/services/confflow_results.py:41-62` 中 `load_summary()` 对 JSON 解析失败返回全零对象。GUI 依赖异常路径显示 `⚠ Parse Error`，但异常被吞，UI 显示 `✓ Done`、`0→0`、`0.0 s`。

**H2. capability handshake 不覆盖决定下载成败的工件**

> v2 中"待核实"标注与正文"已确认"互相矛盾；本版统一为"已确认"。

Contract 只列出 3 个 JSON（`run_summary.json / workflow_stats.json / .workflow_state.json`），但 Producer 实际产出 5 个工件。**5 个工件的最终落点路径**已确认：

| 工件 | Producer 端代码位置 | 落点 |
|---|---|---|
| `run_summary.json` | `confflow/workflow/presenter.py` | `{basename}_confflow_work/` 下 |
| `workflow_stats.json` | `confflow/workflow/presenter.py` | `{basename}_confflow_work/` 下 |
| `.workflow_state.json` | `confflow/workflow/state.py` | `{basename}_confflow_work/` 下 |
| `<basename>.txt` | `confflow/core/contracts.py::output_txt_path_for_input` | **原始输入所在目录**（远程 workspace 根） |
| `<basename>min.xyz` | `confflow/workflow/presenter.py` | **原始输入所在目录**（远程 workspace 根） |

`<basename>.txt` 与 `<basename>min.xyz` 在原始输入所在目录，不在 run 目录。Producer 端未在 capability 暴露这 5 字段全部，是真实缺陷。

**H3. 离线 YAML 校验器对非法输入可能抛 `TypeError`/`AttributeError`**

`_validate_step_config()` 假设 step 是 mapping，遇到 `steps: [null]` 或 `params: "not-a-map"` 时可能不返回错误列表，而是 Python 异常。`SubmitUseCase` 只捕获 `ConfFlowUnavailableError` 和 `ValueError`，会升级为 GUI 崩溃。

### 3.1.a Release Hygiene Gate（G0）

> G0 不是产品缺陷，是合并 producer release PR 之前必须跨越的执行门。**H 严重度统计 = 3 项（H1、H2、H3）**；G0 单独在 §4.3 优先级表列出。

**G0. Producer 工作树不干净影响 line-protocol（H4+M10 合并后的 release hygiene gate）**

`/opt/ConfFlow` 处于 `feature/phase2-workflow-state` 分支，`confflow/contract.py` 是 modified 状态。由于 producer 在 emit 时字面 import contract 常量，dirty 工作树不会进入已安装的 wheel；但若 `git stash` 不执行，producer 端任何基于当前 HEAD 的测试都无法代表 1.4.2 release。

**触发条件**：

- 在 producer release PR 启动前，必须先完成 1.4.3 决策门（正式 tag）与 1.2 dirty diff 分类（A/B/C/D 处置）。未满足则禁止开始 PR。
- 在 producer release PR 期间的工作树必须保持 `git status --porcelain` 为空；build 输出（`dist/`、`build/`）必须 `gitignored`。

---

### 3.2 中严重度（M）

**M1. GUI 越过 RunCoordinator 直接打 Remote**

`gui/main_window.py:515-540` 上传前 probe 直接调用 `create_ssh_client()`，而 `docs/architecture.md:34-36` 声称"GUI never talks directly to remote"。同一提交多一次 SSH 连接，且未来 SessionPool 优化无法复用。

**M2. 两层并发资源乘法放大**

外层 `JobDesk max_parallel`（`xargs -P`）与内层 `YAML global.max_parallel_jobs` 完全独立。4 × 4 = 16 个并发计算单元可能超 CPU 上限。需联合资源预算告警。

**M3. RunRecord 缺少 workflow_kind 结构性字段**

`RemoteSubmitter` 用 `if "confflow" in command_template` 反查（`remote/submitter.py:47-50`、`runs_results_page.py:1347,1458`）。TaskRecord 已有 `workflow_kind` 字段（`core/manifest.py`），但结果页只拿到 RunRecord；当前 RunRecord 没有 `workflow_kind`，UI 只能字符串反查。需通过 TaskRecord 派生，详见修复 plan。

**M4. `global.work_dir` 与 CLI `-w` 双重所有权**

`WorkflowSpec.from_form()` 写入 `global.work_dir`，但 `program_adapters.py:42-49` 无条件传 `-w "$workspace/<basename>_confflow_work"`。用户看到的字段不生效。

**M5. prerelease 接受策略不明确**

`core/confflow_preflight.py:136-139` 接受 `1.9.0-rc.1`；需明确是否有意。

**M6. 远端 shell 环境需求不在 handshake 中**

`bash / nohup / setsid / xargs / sha256sum / mktemp` 等不在 capability 中，部署到最小 Linux 镜像时下游才报错。

**M7. Module logger 不会落到 JobDesk 文件 logs**

`app_logging.py` 将 handler 挂在 `"jobdesk"` logger，但 `run_monitor.py:28` 等用 `logging.getLogger(__name__)`，对应 `jobdesk_app.services.run_monitor` 等子 logger。这些不会向 `"jobdesk"` 传播，`%APPDATA%\JobDesk\logs\jobdesk-YYYYMMDD.log` 收不到警告。

**M8. 文档与实现漂移**

- `wsl_distro: Ubuntu` → 应为 `Ubuntu-24.04`
- `architecture.md:159` "mtime" → 实际是 SHA-256 digest
- `confflow_dependency_decision.md` 仍写 `>=1.4.0,<2.0`，当前是 `>=1.4.2,<2.0`
- 旧 1.3.0/1.4.0 部署文档无 archival banner

**M9. `NUL` 设备文件**

`git status` 报 `?? NUL`。Windows 保留设备名，需确认源头（只读验证，不删除）。

**M10. （已合并至 G0）**

> 原 v1 M10 与 H4 是同一问题；v5 重构后这部分内容已归入 §3.1.a "Release Hygiene Gate"。M10 在本版中保留为占位以避免编号偏移，但内容已与 G0 合并。

---

### 3.3 低严重度（L）

**L1. linear / DAG adapter 复制粘贴**

**L2. results parser 静默折算**

**L3. timestamp 本地时区**

**L4. monitor "只读 probe" 仍写远端**

---

## 4. 审查结论

### 4.1 架构评估

**优点**：Producer / Consumer 边界克制、双 owner 划分合理、CI 镜像锁有效、提交恢复设计成熟、路径安全选择面清晰。

**主要风险**：进度静默失败（H1）、capability 遗漏工件（H2）、YAML 校验器异常升级（H3）、producer 工作树 dirty（G0 release hygiene gate）、GUI 越 canal 直接打 Remote（M1）、两层并发乘法失控（M2）。

### 4.2 Monorepo 路线（v1 结论超出证据，本版重写）

> v1 第 4.2 节将 monorepo 定为"最终最优路径"，但"是否转 monorepo"本身是待确认问题，结论不应超出证据。本版结论为：双仓现状有真实成本，monorepo 是值得探索的方向，但**需先完成 RFC 评估**，不能在本轮修复中直接实施。

**双仓当前真实成本**：
- 跨仓库 `contract.py` 镜像（producer 端 + consumer 端各一份）
- CI 需要分别 checkout 两个仓库并协调 wheel 安装

**monorepo 探索前提**：
- 必须有远端 CLI 入口兼容方案（`command_template` 仍调用 `confflow` 命令，不能仅靠 vendored Python import）
- 必须有 Linux 端依赖路径（rdkit / numpy / numba 仍在 WSL，不在 Windows）
- 必须有过渡方案（`JOBDESK_CONFFLOW_EXTERNAL=1` 等双路径）
- 必须有 producer 发布机制替代（目前 `/opt/ConfFlow` 的 pip install 是发布载体）

**结论**：monorepo 方向值得开 RFC，不在本轮修复验收范围内。若执行，R-MONO-A/B/C 必须补全远端部署方案。

### 4.3 修复优先级建议

| 优先级 | 门 / 问题 | 理由 |
|---|---|---|
| G0（producer release PR 启动前） | producer dirty diff 分类决策（A/B/C/D 处置）+ 1.4.3 release tag 决策门 | dirty branch + 非正式 tag 会冒充 stable release，consumer 的 `MIN_VERSION=(1,4,3)` 与 prerelease 策略会拒；未走流程前 producer release PR 不开始 |
| P1（本 sprint） | H1、H2、H3 | 静默失败 / 产物缺失 / 崩溃 |
| P2（次 sprint） | M1、M4、M7 | 架构约束 / 越 canal / logging |
| P3（后续） | M2、M3、M5、M6、M8、M9、L1–L4 | 功能完善 |

> **门 vs 缺陷**：G0（release hygiene gate）属于执行前置门，不计入"H 严重度统计"。H 严重度仍然为 H1、H2、H3 三项；H4（producer dirty）单独列为 G0。这样梳理避免把"门"与"缺陷"混在同一统计中。

---

## 5. 待核实事实（仍未锁定）

1. **`basename.txt` / `basename.min.xyz` 在 producer 源码中的具体 template string**（应在 `core/contracts.py::output_txt_path_for_input` 中可查，但提交时仍需确认）。
2. **producer `1.4.3` 是否已在规划**（`feature/phase2-workflow-state` 是否会 merge 并 tag）。
3. **Capability cache**：`probe` 耗时较长（30 s timeout），同一 submit 两次 probe 是否可合并为一次。

> v2 中"`<basename>.txt` / `<basename>min.xyz` 是 producer 产出"已确认，本版从待核实移除。

---

## 6. frontmatter 修正

```yaml
todos:
  - id: review-jobdesk
    content: 通过 explore subagent 读 JobDesk 架构并汇总主要风险
    status: completed
  - id: review-confflow
    content: 读 WSL /opt/ConfFlow 架构并汇总 contract / workflow / calc 设计
    status: completed
  - id: synthesize-findings
    content: 汇总两个仓库协同点、跨仓库合同、严重度问题与改进建议
    status: completed
```
