# Plan: 让 JobDesk 真正服务计算化学家

> **Historical plan (2026-05):** Workflow-related sections below (multi-step orchestration, `jobdesk workflow` CLI) are superseded. JobDesk now delegates workflow orchestration to ConfFlow. The remaining analysis/input/viewer features may still be relevant.

## 目标

把 JobDesk 从"通用 SSH 批量提交工具"升级为"计算化学家的工作台"，
重点解决 HPC 集群可用性、Gaussian/ORCA 输出解读、多步工作流自动化。

## 设计原则

- **保持小核心**：Slurm/Gaussian 等领域知识用插件式 adapter，不污染 core 层
- **兼容现有架构**：新功能挂在 RunService 旁，不动 manifest / lifecycle FSM
- **可分阶段交付**：每个 Phase 独立可用，不强制升级

---

## Phase A：调度器集成（P0，HPC 解锁）

### A1. Scheduler adapter 抽象

新增 `core/scheduler.py`：

```python
class SchedulerAdapter(Protocol):
    def submit(self, ssh, remote_script: str, resources: ResourceSpec) -> str:
        """Submit job, return scheduler-assigned job_id."""
    def poll(self, ssh, job_id: str) -> JobState:
        """Poll job state: pending/running/completed/failed/cancelled."""
    def cancel(self, ssh, job_id: str) -> None: ...
    def stdout_path(self, job_id: str) -> str: ...
```

`ResourceSpec`: `nodes`, `cpus_per_task`, `memory_mb`, `walltime_minutes`,
`partition`, `account`, `gpus`.

### A2. 内置三个 adapter

- `NohupScheduler`：现有行为，保持兼容
- `SlurmScheduler`：`sbatch` 提交 + `squeue` 轮询 + `scancel` 取消
- `PBSScheduler`：`qsub` + `qstat` + `qdel`

### A3. ServerConfig 扩展

```yaml
servers:
  hpc1:
    host: ...
    scheduler:
      type: slurm
      default_partition: cpu
      default_account: my_account
      default_walltime: "24:00:00"
```

无 `scheduler` 字段时默认 nohup（向后兼容）。

### A4. JobSubmitter 改造

- 不再硬编码 `nohup ... &`，改为生成 scheduler-specific 提交脚本（含 `#SBATCH` 头）
- 状态轮询走 adapter.poll() 而非读 `.jobdesk_status` 文件
- `.jobdesk_status` 仍保留作为 fallback（任务侧脚本写入）

### A5. 资源参数 UI / CLI

- GUI Run controls 新增"资源"折叠面板（cpus、memory、walltime、partition）
- CLI `jobdesk run create` 新增 `--cpus 8 --memory 16G --walltime 24h --partition cpu`

**交付物：** 在装有 Slurm 的服务器上能 `sbatch` 一组任务，并实时看到队列状态（PD/R/CG）。

**工作量：** 约 2 周（含测试集群上的真实验证）。

---

## Phase B：Gaussian/ORCA 输出专业解析（P0）

### B1. 输出解析器抽象

`core/parsers/`:
- `gaussian.py`: `parse_gaussian_log(path) -> GaussianResult`
- `orca.py`: `parse_orca_out(path) -> OrcaResult`

提取字段（Gaussian）：
```python
@dataclass
class GaussianResult:
    converged: bool                     # Stationary point found
    scf_energies: list[float]           # 每个 SCF Done
    final_energy_au: float | None
    zpe_au: float | None
    enthalpy_au: float | None           # H corrected
    gibbs_au: float | None              # G corrected
    thermo_temperature_k: float | None
    frequencies_cm1: list[float]        # 全部频率
    imaginary_freq_count: int           # 虚频数（TS 应为 1）
    final_xyz: str | None               # 最后一帧坐标
    nbo_charges: dict[int, float] | None
    error_termination: str | None       # Link 9999、negative curvature 等
    walltime_seconds: float | None
    cpu_time_seconds: float | None
```

### B2. 内置 ExtractProfile

`services/analysis_profiles.py` 增加预设：
- `gaussian_opt_freq`：能量、ZPE、H、G、频率、虚频数、几何
- `gaussian_sp`：单点能量
- `orca_opt_freq`：同上 ORCA 版
- `orca_dlpno_ccsd_t`：CCSD(T) 能量

用户在 Results 页一键选用，不必手写正则。

### B3. 错误码识别

`core/errors_gaussian.py`:
```python
GAUSSIAN_ERRORS = {
    "convergence": ["Convergence failure", "Convergence criterion not met"],
    "negative_curvature": ["Negative curvature in"],
    "link9999": ["l9999.exe", "Erroneous write"],
    "bad_input": ["Z-matrix not found", "Atomic number out of range"],
    "scratch_full": ["No space left on device"],
    "memory": ["Out of memory", "galloc:"],
}

def diagnose(log_path: Path) -> ErrorDiagnosis | None: ...
```

### B4. 智能重试

`RunService.retry_with_strategy(run_id, strategy)`:
- `convergence` → 增加 `SCF=(MaxCycle=512)` 后重跑
- `negative_curvature` → 加 `Opt=CalcFC` 后重跑
- `memory` → walltime/memory ×2 后重跑（结合 Phase A）

**交付物：** Results 页打开任意一个 g16 log，自动展示 E、ZPE、H、G、虚频数、收敛状态、错误诊断。

**工作量：** 约 1 周。

---

## Phase C：自动化（P1）

### C1. 自动状态轮询

- GUI Runs 页可选"Auto-refresh every N seconds"开关
- 后台 QTimer + worker 线程轮询，避免 UI 卡顿
- 每个 server 独立轮询周期，避免连接抖动放大

### C2. 自动下载

- 任务变 `remote_completed` 后立即触发 download（按 run 配置的 patterns）
- 下载完成后立即触发 analyze（如果 run 有关联 ExtractProfile）
- 整链路：completed → downloaded → analyzed 全自动

### C3. 完成通知

- Windows 系统托盘通知（`QSystemTrayIcon`）
- 可选 webhook（POST 到用户配置的 URL，含 run_id + 状态摘要）
- 可选 SMTP（user 配置 servers.yaml 旁的 `notifications.yaml`）

**交付物：** 用户提交一组任务后可以直接关掉电脑显示器，回来时所有结果已自动解析完成。

**工作量：** 约 1 周。

---

## Phase D：工作流链（P1）

### D1. WorkflowSpec 数据模型

```python
@dataclass(frozen=True)
class WorkflowStep:
    name: str                              # "opt", "freq", "sp"
    command_template: str
    depends_on: list[str] = field(default_factory=list)
    input_from: str | None = None          # 上游 step 的 final_xyz
    extract_profile: str | None = None
    on_failure: Literal["stop", "continue", "retry"] = "stop"

@dataclass(frozen=True)
class WorkflowSpec:
    steps: list[WorkflowStep]
    sources: list[RunSource]
```

### D2. 内置工作流模板

- `opt_freq_sp`：B3LYP opt → freq → DLPNO-CCSD(T) sp
- `crest_xtb_dft`：CREST 构象搜索 → xtb 筛选 → DFT 优化
- `irc_then_opt`：IRC → 两端 opt

模板存在 `~/.config/jobdesk/workflows/*.yaml`，用户可自定义。

### D3. 几何传递

WorkflowStep 之间通过 `parsers.gaussian.GaussianResult.final_xyz` 自动注入下游输入：
- 上游产 `xxx_opt.log` → 解析最后坐标 → 写入 `xxx_freq.gjf`
- 路由行用上游声明的 method/basis 替换

### D4. 工作流执行器

`services/workflow_runner.py`:
- 拓扑排序 steps
- 每个 step 创建一个 Run（复用 RunService）
- 监听上游 Run 的 `analyzed` 状态触发下游
- 失败处理按 `on_failure` 策略

**交付物：** 一行命令 `jobdesk workflow run opt_freq_sp --files mol1.gjf mol2.gjf` 自动跑完三步。

**工作量：** 约 2 周。

---

## Phase E：跨 run 比较与导出（P2）

### E1. Compare view

GUI Results 页新增 "Compare runs" 模式：
- 多选若干 run → 表格展示
- 列：task_id、method（从 command_template 解析）、final_E、ZPE、G、imag_count
- 行排序：按 G 升序自动给出 ranking
- 自动相对值（最低能量为 0，其他显示 ΔG kcal/mol）

### E2. 导出

- TSV / CSV / Excel 导出
- 可选 markdown 表（贴 PR / 论文用）
- JSON 导出（机器可读）

### E3. 简单可视化

`gui/charts/`：
- 用 PySide6 内置 `QtCharts` 画 IRC profile（横坐标反应坐标、纵坐标能量）
- 构象 ensemble 的能量直方图
- 不做 3D molecular viewer（让用户用 Avogadro/ChemCraft）

**工作量：** 约 1 周。

---

## Phase F：输入文件生成（P2）

### F1. xyz → gjf 模板

`core/input_builder.py`:
```python
def build_gjf(
    xyz_path: Path,
    method: str,              # "B3LYP/6-31G(d)"
    job_keywords: list[str],  # ["opt", "freq"]
    charge: int = 0,
    multiplicity: int = 1,
    nproc: int = 8,
    mem: str = "16GB",
    title: str = "",
    output_path: Path | None = None,
) -> Path
```

### F2. 简易方法库

`presets/gaussian_methods.yaml`：
```yaml
- name: "DFT-D3"
  route: "B3LYP/6-31G(d) EmpiricalDispersion=GD3BJ"
- name: "DLPNO-CCSD(T)/cc-pVTZ"  # ORCA
  route: "! DLPNO-CCSD(T) cc-pVTZ cc-pVTZ/C"
```

### F3. CLI / GUI 入口

- CLI: `jobdesk input build mol.xyz --method "B3LYP/6-31G(d) opt freq" --charge 0 --mult 1`
- GUI: Files 页右键 `.xyz` 文件 → "Generate Gaussian input..."

**工作量：** 约 1 周。

---

## Phase G：xyz/SMILES 入口与可视化（P3）

### G1. SMILES → 3D xyz

依赖 `rdkit`（可选 dev dep）：
- `rdkit-pypi`
- `Chem.MolFromSmiles → AddHs → EmbedMolecule → MMFFOptimizeMolecule → 写 xyz`

### G2. method/basis 库扩展

支持自定义集合（DFT-D4、SMD 溶剂、TDDFT、各种 functional 系列）。

### G3. 集成第三方查看器

- 右键 xyz / log → "Open in Avogadro" / "Open in GaussView" / "Open in IboView"
- 配置工具路径在 Settings 页

**工作量：** 约 1 周。

---

## 实施顺序与依赖

```
Phase A (调度器) ──┐
                   ├──→ Phase D (工作流链)
Phase B (解析器) ──┤        │
                   │        └──→ Phase E (比较/导出)
Phase C (自动化) ──┘
                                 Phase F (输入生成) ←─┐
                                                       │── Phase G (SMILES/可视化)
```

A 和 B 可并行（不同模块）；C 依赖 A（轮询走 scheduler.poll）和 B（自动 analyze 用解析器）；
D 依赖 A+B+C；E 依赖 B；F/G 独立。

---

## 风险与决策点

| 风险 | 应对 |
|---|---|
| Slurm 各家集群参数差异大（partition、account、QoS）| ServerConfig 提供 raw `extra_directives: list[str]` 直通 |
| Gaussian 解析依赖输出格式版本（g09 vs g16）| 加版本检测；解析失败 fallback 到通用正则 |
| 自动下载在大文件时阻塞网络 | 串行队列 + 全局 max parallel transfers + 用户可暂停 |
| 工作流链中途失败的恢复语义 | run.json 增 workflow_id 字段；workflow_runner 可 resume |
| RDKit 是大依赖（>100MB）| 列为可选 dep，用户不装时关闭 SMILES 入口 |
| 错误自动重试可能掩盖真问题 | 默认关闭，用户显式启用；每次重试在 manifest 留痕 |

---

## 完成标准（按 Phase）

每个 Phase 单独可交付，标准如下：

- **A：** 提交一组任务到真实 Slurm 集群，能看到 squeue 状态变化，cancel 生效。
- **B：** 任意 g16 log 拖进 Results 页，立刻显示能量/频率/收敛/错误诊断；解析正确率 >95%。
- **C：** 提交后无需点击任何按钮，结果文件自动出现在 `results/<run_id>/`，分析结果展示完成。
- **D：** `jobdesk workflow run opt_freq_sp --files a.xyz b.xyz` 跑完三步，结果一并解析。
- **E：** 选若干 run 一键导出 CSV，包含 ΔG ranking。
- **F：** `jobdesk input build mol.xyz --method "..."` 产出可直接 g16 提交的 gjf。
- **G：** 输入框粘贴 SMILES `c1ccccc1` 后一键得到 benzene 的 gjf。

---

## 不做的功能（明确收敛范围）

- 3D 分子查看器（让用户用 Avogadro / GaussView / ChemCraft）
- 数据库后端（manifest.tsv 已经够用，引入数据库是过度设计）
- Web UI（桌面 GUI 已经覆盖；如果未来要远程访问再说）
- 多用户协作（这是单机工具）
- 直接调用 Gaussian / ORCA Python API（仅作为 SSH 命令的客户端）
