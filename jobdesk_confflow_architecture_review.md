# JobDesk × ConfFlow 跨项目架构评审报告

## 2026-07-28 deployment update

The supported ConfFlow 1.4.3 installation is present at
`/opt/ConfFlow/.venv/bin/confflow` and is exposed through
`/usr/local/bin/confflow`. The incident reviewed here was not a missing wheel:
the persisted JobDesk `wsl` server entry sourced an obsolete isolated
environment script, which shadowed that deployment with ConfFlow 1.4.0. The
stale `env_init_scripts` entry has been removed. Operator checks must compare
`command -v confflow` and `confflow --capabilities --json` in the exact task
shell, including configured init scripts, before diagnosing a deployment
failure.

## 2026-07-28 Milestone 2 update

本节是本评审文档的"现状核对"附录，不替代 §3.1/§3.2 的细节分析；为方便快速对照，先把 M2
增量在此集中汇总，后续章节再做精确替换。

**能力握手升级 v3 → v4（双侧同日提交）**：

- 跨仓库联动提交，几乎同一分钟内完成：
  - JobDesk `44719e9` `Implement JobDesk ConfFlow Milestone 2 contract`
    （`2026-07-28 14:42:30 +0800`，作者 `moxue <moxue@jobdesk.local>`）
  - ConfFlow `10e457d` `Implement ConfFlow Milestone 2 producer contract`
    （`2026-07-28 14:42:45 +0800`，作者 `moxue <moxue@jobdesk.local>`）
- `confflow/contract.py::CAPABILITY_SCHEMA_VERSION` 与
  `src/jobdesk_app/core/confflow_contract.py::CAPABILITY_SCHEMA_VERSION`
  均升到 `4`（v3 字段 `artifacts / commands / build` 全保留）。
- 新增 `producer` block（`package / version / build / wheel`）与
  `executable` block（`path / sha256 / python`），分别由 producer
  `_build_capability_payload()` 生成、由 consumer `_parse_producer()` /
  `_parse_executable()` + `validate_confflow_capabilities()` 强制校验。
  校验强约束包含 `producer.package == "confflow"` 与
  `producer.version == capabilities.version`，不一致即抛
  `ValueError`，被 probe 包装为 `ConfFlowCapabilityPreflightError`。
- 持久化升到 SQLite `SCHEMA_VERSION = 6`，新增 `run_provenance` 表
  （`capability_json / resolved_executable / resolved_realpath /
  producer_version / producer_build_commit / producer_dirty /
  wheel_sha256`）；`_provenance.py::record_run_provenance` 是 upsert
  入口，`__init__.py` 暴露给上层调用。
- `_submit.py` 实现 durable submit operation journal：
  `_SUBMIT_JOURNAL_SCHEMA = "jobdesk.submit.v1"`、基于 `sha256(run_id, *sorted(task_ids))`
  的幂等键、`_validate_remote_staging_root` 强制远端 staging 路径为
  `…/.jobdesk_runs/<run_id>`（其它形式拒绝，包括越界 / 包含 `..` 等）。
- 三个 producer 工件顶层统一 `content_schema` 判别：
  `confflow.run_summary.v1` / `confflow.workflow_stats.v1` /
  `confflow.workflow_state.v1`；consumer 端 `_artifact_payload()`
  接受 `content_schema + payload` envelope，也接受"无 envelope 但带
  `content_schema`"的 v1 canonical 形状。
- 新增 `OUTPUT_MANIFEST_FILE = "output_manifest.json"` 与
  `OUTPUT_MANIFEST_SCHEMA = "confflow.output_manifest.v1"`，对应设计
  文档 D4（多终端输出）。Consumer 端 `OUTPUT_MANIFEST_FILE` 已加入
  `EXPECTED_ARTIFACTS` 的同名镜像常量（producer 端亦存在，但本轮
  consumer `_parse_artifacts` 仍以 5 字段结构判别，未对 manifest
  强制做 schema-v1 envelope 解码——这是 producer/consumer 的对接灰带，
  见 §8.2 M2-OBS）。

**实测 producer live payload（`confflow --capabilities --json`）**：

```json
{
  "schema_version": 4,
  "version": "1.4.3",
  "capabilities": {"workflow_state": true, "resume": true, "dag": true},
  "artifacts": {
    "run_summary": "run_summary.json",
    "workflow_stats": "workflow_stats.json",
    "workflow_state": ".workflow_state.json",
    "run_report": "{basename}.txt",
    "min_xyz": "{basename}min.xyz"
  },
  "commands": {"bash": true, "nohup": true, "setsid": true,
               "xargs": true, "sha256sum": true, "mktemp": true, "base64": true},
  "build": {"commit": "b46a47dca77c905765814253affb90c85a1f8597", "dirty": true},
  "producer": {
    "package": "confflow",
    "version": "1.4.3",
    "build": {"commit": "b46a47dca77c905765814253affb90c85a1f8597", "dirty": true},
    "wheel": {"filename": "confflow-1.4.3-py3-none-any.whl", "sha256": "unbound"}
  },
  "executable": {
    "path": "/opt/ConfFlow/.venv/bin/confflow",
    "sha256": "81285db0b20cf477a4ece4618535b6bdd9660bc25190fd921d951ece4565fd16",
    "python": "/usr/bin/python3.12"
  }
}
```

**`/opt/ConfFlow` dirty worktree — 已处理（2026-07-28 复核后追加）**：

首次探测时 `git status --short` 报告 4 个未提交改动
（`confflow/calc/executor.py`、`confflow/contract.py`、
`tests/test_calc_executor_protocol.py`、`tests/test_contract.py`）。
逐文件 diff 后发现 `git diff -w`（忽略空白）对这 4 个文件全部为空——
这不是实质代码改动，而是某次 Windows 侧编辑/同步操作把全部 4 个文件
从 LF 整体转成了 CRLF 换行（`file` 命令确认：改动前后内容字节完全一致，
仅换行符不同）。已用 `git checkout -- <4 files>` 还原为 HEAD 版本，
`/opt/ConfFlow` 现在是 **clean worktree**：

```
$ cd /opt/ConfFlow && git status --short
$ echo CLEAN
CLEAN
```

**但 clean worktree 并不能让 `producer.wheel.sha256` 变成真实哈希**——
重新探测 live payload 后，`build.dirty` 仍是 `true`、
`producer.wheel.sha256` 仍是字面字符串 `"unbound"`、
`build.commit` 仍是安装时打包进 wheel 的旧值
`b46a47dca77c905765814253affb90c85a1f8597`（HEAD 的上一个 commit，
而非当前 HEAD `10e457d`）。追查后确认根因：这三个字段是**安装时静态
打包**进 `.venv/lib/.../confflow/__build__.py` 的构建期常量
（见 `setup.py::BuildPyWithProvenance.run()`），不是 CLI 运行时动态
探测的。只要不重新构建+重新安装 wheel，无论工作区多"干净"，探测出的
`build`/`producer` 字段都只反映**上一次安装时**的仓库状态，而不是当前
HEAD。

进一步追查 `wheel.sha256 = "unbound"` 的字面字符串来源：
`setup.py::BuildPyWithProvenance.run()` 从环境变量
`CONFFLOW_WHEEL_FILENAME` / `CONFFLOW_WHEEL_SHA256` 读取并写入
`__build__.py`；但在 ConfFlow 现有的 `docs/RELEASE.md` 发布流程、
`.github/workflows/release.yml`、以及本机历史安装记录中，**都没有任何
一步设置过这两个环境变量**——这是 wheel 自描述哈希的先天鸡生蛋问题：
wheel 要把自己的 SHA-256 打包进自身内容，就必须先构建一次、算出哈希、
再用该哈希重新打包（或者构建后对已生成的 wheel 做二进制打洞替换），
现有发布脚本完全没有这一步。因此只要照抄 `python -m build` 这条路径，
`wheel.sha256` 永远会是初始占位值（`None` → 序列化为 `"unbound"`，
具体取值方式待在 `setup.py` 之外找到注入点，本次复核未继续深挖，
留给 M2-4 门禁实施时一并解决）。

M2-4 发布候选门禁（唯一 wheel 构建 + sha256 校验 + 持久 WSL 复跑
capability probe / dry-run / resume + 非计算 smoke，设计文档 §3.5）
因此仍然**未启动**，但阻塞点已经从"dirty worktree"精确到了"发布脚本
缺少两阶段哈希回填步骤"，按设计文档 §5："M2-1～M2-3 已实现；M2-4
wheel/WSL 发布候选待单独门禁"。

> **评审对象**：
> - Consumer（Windows 端）: `C:\dft\tool\jobdesk-dev`（PySide6 + Paramiko 应用，v0.5.0+）
> - Producer（WSL/Linux 端）: `/opt/ConfFlow`（`confflow` 1.4.3，Python 3.10–3.13）
> - WSL 共存环境: `/opt/g16`（Gaussian 16）、`/opt/orca611`、`/opt/openmpi418`
>
> **评审范围**：两项目的架构设计、协同协议、协同点、未来演化方向。
> **评审时间**：2026-07-26（与 `docs/CONFFLOW_1_4_3_REVIEW_PLAN.md` v5 + `docs/CONFFLOW_1_4_3_REMEDIATION_PLAN.md` v9 同步落地后）

---

## 0. 一句话定性

两个项目以 **"线协议优先 + 双仓独立演化"** 模式协作。Producer 持有工件文件名 / `--capabilities --json` schema，Consumer 持有版本窗口与产物落点路径；两侧通过 `tests/test_version_consistency.py` 和 `tests/test_confflow_validation_differential.py` 硬锁。架构整体克制、可观察性良好，但**文档与现实存在一处显著不一致**（ConfFlow README 自称已合并到 JobDesk，事实是两个独立仓库），且 remediation v9 还在落地中，本评审与该 v9 计划对齐而非替代。

---

## 1. 项目身份与目标用户

### 1.1 JobDesk（Consumer）

| 维度 | 值 |
|---|---|
| 入口 | `jobdesk` / `jobdesk-prep` / `jobdesk-gui`（PySide6 GUI） |
| 版本 | `0.5.0`（`pyproject.toml:3`），CHANGELOG 顶部仍是 `[Unreleased]` 但代码已落地 |
| 主线分支 | `main`（HEAD `44719e9 Implement JobDesk ConfFlow Milestone 2 contract`，2026-07-28 14:42:30 +0800） |
| 其它分支 | `recovery/vendored-confflow-phase1b-1c`（已 archive，不合并），`origin/feature/confflow-monorepo`，`origin/stage3-fix` |
| 运行平台 | Windows 11 + Python 3.11+；remote 端可为 Linux/WSL 或真远程 |
| 外部依赖 | `pydantic`、`pyyaml`、`paramiko`、`pyside6`、`rdkit`、`confflow>=1.4.3,<2.0` |
| 目标用户 | 跑 Gaussian 16 / ORCA 单任务或多任务工作流的化学家；GUI 优先 |

### 1.2 ConfFlow（Producer）

| 维度 | 值 |
|---|---|
| 入口 | `confflow` / `confflow-agent` / `confgen` / `confrefine` / `confts` |
| 版本 | `1.4.3`（`pyproject.toml` 与 `confflow/contract.py`） |
| 主线分支 | `main`（HEAD `10e457d Implement ConfFlow Milestone 2 producer contract`，2026-07-28 14:42:45 +0800） |
| 其它分支 | `feature/phase1-calc-executor`、`feature/phase2-workflow-state`（已 merge，`v1.4.3` release 候选 commit `10e457d`） |
| 运行平台 | Linux / WSL（OS-independent 但 CI 仅 Ubuntu）；依赖 Gaussian/ORCA 由用户管理 |
| 外部依赖 | `numpy`、`scipy`、`pyyaml`、`psutil`、`rich`、`pydantic v2`、`rdkit`、`numba`（可选加速） |
| 目标用户 | 在没有 GUI 的远程节点上自动化 conformer search 的化学家 / 计算化学工程师 |

### 1.3 共存关系（WSL `/opt`）

```
/opt/
├── .agents/, .codex/, .git/         # WSL 自身
├── ConfFlow/                        # Producer 源码（独立 git）
├── g16/                             # Gaussian 16（root-owned，完整安装）
├── orca611/                         # ORCA 6.1.1
├── openmpi418/                      # OpenMPI 4.1.8（供 ORCA 用）
├── cf142-iso/                       # ColdFire 142 ISO（不太相关）
├── uma/, frp/                       # 用户工具
└── E6B-432X.tbJ                     # 旧 license 包
```

`g16` 与 ORCA 是**第三方商业软件**，由本机安装者维护；**JobDesk 不安装、不打包、不许可**它们。`scripts/install_mock_l1_wsl.py` + `scripts/mock-gaussian/mock_l1_exe` 提供 mock 路径，用于集成测试与开发期跳过 license 限制。`g16` 包装器在 Phase 8C 恢复过一次（详见 `.cursor/rules/wsl-g16-safety.mdc`），任何 `wsl -e bash -c '... > /opt/g16/g16'` 写法已被规则硬禁。

---

## 2. 项目内部架构（分述）

### 2.1 JobDesk 的分层

```
CLI / GUI (cli.py, gui/app.py → main_window)
    │ uses
Application services (services/run_coordinator, run_service, file_transfer_service, submit_use_case)
    │ uses
Domain core (core/run, core/submit, core/manifest, core/workflow_spec, core/confflow_contract)
    │ uses
Remote I/O (remote/ssh, remote/sftp, remote/scheduler, remote/submitter, remote/confflow_probe)
```

(`docs/architecture.md:6-32` 已有同款 ASCII 图，此处精简。)

**实际目录**（`tree /F src`）：

```
src/jobdesk_app/
├── app_logging.py, app_paths.py, cli.py, cli_prep.py
├── config/                # schema.py (Pydantic ServerConfig), servers.py (loader)
├── core/                  # submit / manifest / run / workflow_spec / confflow_* / parsers/
├── gui/                   # PySide6：app / main_window / pages / dialogs / nodegraph / widgets / design / resources
├── remote/                # ssh / sftp / scheduler / submitter / confflow_probe / status / status_refresh / errors
├── resources/             # method_presets / step_presets / workflow_examples
├── services/              # run_coordinator / run_monitor / confflow_results / program_adapters /
│                          # session_pool / run_repository (12 文件) / run_service (9 文件) /
│                          # submit_use_case / run_profiles / scheduler_helpers / ssh_session / 等
└── stubs/                 # mypy 用的类型存根
```

**关键设计点**：
- `services/run_repository/` 拆成 12 个子文件（`_schema`/`_paths`/`_workspaces`/`_leases`/`_submit`/`_delete`/`_tasks`/`_runs`/`_operations`/`_legacy`/`_operations_types`/`_activity`）。所有读写仍通过 `RunRepository`（`__init__.py`）单一入口；拆分纯组织性，事务边界保留在 `_schema.py`。
- `services/run_service/` 同样拆为 9 个子文件（`_cancel`/`_confirm`/`_delete`/`_download`/`_helpers`/`_progress`/`_refresh`/`_rerun`/`_submit`）。CLI 与 GUI 都通过 `RunCoordinator.create_and_submit()` 调它，**不直接**摸数据库。
- SQLite schema 当前 **v6**（v1 引入；v2 submit/delete journal；v3 trusted-workspace；v4 UTC leases；v5 `submit_activity_log`，Phase 15C；**v6 `run_provenance` 表，M2 commit `44719e9`**）。
- `architecture.md:34-36` 明确：GUI 永远不直接打 `remote/`；唯一持有 session lease 的地方是 `SessionPool`（R-M1 后变成单例共享池）。

### 2.2 ConfFlow 的分层

```
CLI (cli.py / agent/cli.py / confts.py / blocks/{confgen,refine,viz})
    │
Workflow (workflow/engine.py, workflow/dag/, workflow/state.py, workflow/stats.py,
         workflow/{validation,presenter,export,supervisor,helpers,step_handlers,step_naming,rerun_failed,dry_run,runtime_context,config_show}.py)
    │
Calc / blocks (calc/executor.py, calc/{runner,setup,scan_ops,rescue,postprocess,analysis,...}.py,
               blocks/{confgen,refine,viz}/)
    │
Core (core/{models,data,io,parsers,validation,gaussian_input,keyword_rewrite,chem_validation,xyz_metadata,contracts,exceptions,...}.py)
    │
Shared (shared/{defaults,config_validation,orca_blocks}.py)
    │
Agent (agent/{server,queue,slots,state,progress,runner,cli}.py) — 独立 daemon 子包
```

**实际目录**（`find /opt/ConfFlow/confflow -type f -name "*.py" | sort`）：

```
confflow/
├── main.py / cli.py / contract.py / confts.py / __build__.py
├── __init__.py（顶层惰性 RDKit/psutil/numba import）
├── core/           25 文件：models, data, io, parsers, validation, contracts, gaussian_input, ...
├── shared/         3 文件：defaults, config_validation, orca_blocks
├── workflow/       15+ 文件 + workflow/dag/{__init__,explicit,_legacy}.py
├── calc/           14+ 文件 + calc/{components,async_exec,db,policies}
├── blocks/         confgen / refine / viz
├── config/         __init__ + models.py（注意：与 `core/models.py` 是两个独立文件）
├── agent/          server / queue / slots / state / progress / runner / cli
└── docs/           DAG-EXECUTION.md / PHASE3_PLAN.md
```

**关键设计点**：
- **`contract.py` 是 wire-protocol 的 producer-side 唯一 owner**（见 §3.1），~55 行（M2 后从 44 行扩到 ~55 行：新增 `OUTPUT_MANIFEST_FILE` / `OUTPUT_MANIFEST_SCHEMA` / `RUN_SUMMARY_SCHEMA` / `WORKFLOW_STATS_SCHEMA` / `WORKFLOW_STATE_SCHEMA`）。
- `__build__.py` 提供 `COMMIT`/`DIRTY` 默认占位（`None, None`）；wheel 构建时由 `setup.py` 的 `cmdclass={"build_py": BuildPyWithProvenance}` 覆盖写 `build_lib/confflow/__build__.py`，**绝不**回写源码树。
- `agent/` 是**独立的 queue-based daemon**：8 个 Python 文件（`__init__.py / cli.py / server.py / queue.py / slots.py / state.py / progress.py / runner.py`），默认 `slots=2`，磁盘队列 `~/.confflow-queue`，SQLite state `~/.local/share/confflow-agent/state.db`，提供 `serve / submit / status / list / pause / resume / cancel / logs / stop` 子命令。`PAUSE` beacon 文件驱动软暂停。
- CLI 主入口拆分：`main.py` 仅 24 行（import + `ExitCode.RUNTIME_ERROR` fallback）；CLI 解析与子命令分发在 `cli.py`（M2 后从 621 行扩到 654 行）；TS 工具在 `confts.py`（73 行）。
- Calc executor 与 workflow engine 之间通过 `CalcExecutor` Protocol 解耦（`calc/components/executor.py`）；`blocks/refine/_compat.py` 维护兼容垫片。
- 测试文件 ~60 个，覆盖 calc / confgen / dag / agent / cli / contract / parser 全栈；`test_calc_executor_protocol.py` 用 fake executor 做 hermetic 集成测试。

### 2.3 两项目对比表

| 维度 | JobDesk | ConfFlow |
|---|---|---|
| 用户界面 | PySide6 GUI + CLI + Python API | 仅 CLI（`argparse`） |
| 主线数据流 | 用户点 Submit → SubmitUseCase → RunCoordinator → JobSubmitter（SSH） → nohup `confflow …` | `confflow mol.xyz -c config.yaml` 跑工作流 |
| 持久化 | SQLite (`jobdesk.db` schema **v6**，含 `run_provenance` 表) + TSV manifest + YAML servers config | YAML workflow config + 自有 `agent/state.db` + 工件落 `{basename}_confflow_work/` |
| 第三方二进制 | **不直接调用** g16/ORCA；通过 SSH 把命令交给远端 nohup | **直接调用** g16/ORCA（用户在 `global.gaussian_path` 等里指明） |
| 远程模型 | SSH/SFTP + Paramiko；SessionPool 串行复用 | 单机 CLI；可选 agent daemon 跑 queue |
| 并发控制 | `xargs -P` 外层 + `max_parallel_jobs` 内层（两层独立，见 M2） | DAG/线性顺序或 DAG wavefront |
| 类型系统 | Pydantic v2 + dataclass + mypy strict | Pydantic v2 + dataclass + mypy strict（`pyproject.toml` 写 `mypy>=2.1.0,<2.2`） |
| 覆盖率 | 当前未硬性门禁 | `fail_under = 85`（`pyproject.toml` `[tool.coverage.report]`） |

---

## 3. 协同机制（核心）

### 3.1 双 owner 的 `contract.py` 镜像

| 角色 | 仓库 | 文件 | 内容（节选） |
|---|---|---|---|
| **Producer**（工件 owner） | `/opt/ConfFlow` | `confflow/contract.py` | `CAPABILITY_SCHEMA_VERSION=4`，文件名常量，命令清单，新增 `OUTPUT_MANIFEST_FILE` / `OUTPUT_MANIFEST_SCHEMA` / 三个 v1 内容 schema 字符串 |
| **Consumer**（窗口 owner） | `C:\dft\tool\jobdesk-dev` | `src/jobdesk_app/core/confflow_contract.py` | `MIN_VERSION=(1,4,3)`、`MAX_EXCLUSIVE=(2,0,0)`、`WORK_DIR_SUFFIX="_confflow_work"`、5 个工件名镜像 + `OUTPUT_MANIFEST_FILE` |

两个文件的注释都明确写了：
- "JobDesk never imports ConfFlow's contract module. ConfFlow 1.4.3 emits schema_version=4 and an artifacts block that names all five on-disk files JobDesk is allowed to discover"（ConfFlow README M2 后已升 v4）。
- "JobDesk's MIN_VERSION / MAX_EXCLUSIVE in `jobdesk_app.core.confflow_contract` is the structured source of truth for the producer window; pyproject, CI, and this README are mirrors"（README 同步声明）。

**5 镜像同步约束**（来自 `docs/CONFFLOW_1_4_3_REMEDIATION_PLAN.md:298-304`）：
1. `pyproject.toml` 的 `confflow>=1.4.3,<2.0` 字符串
2. `core/confflow_contract.py` 的 `MIN_VERSION` 元组
3. `README.md` 的版本字符串
4. `tests/test_version_consistency.py` 锁定 5 镜像
5. `docs/CONFFLOW_1_4_3_WHEEL_DEPLOYMENT.md`（部署文档）

任何对 `MIN_VERSION` 的修改必须**同步**更新这 5 份，否则 `test_version_consistency.py` 会拒。

**M2 新增的镜像点**（不替换原 5 镜像，是 v4 capability 多出来的 3 个 v1
内容 schema 字符串 + `OUTPUT_MANIFEST_FILE`）：

| Producer 常量 | Consumer 常量 / 值 |
|---|---|
| `RUN_SUMMARY_SCHEMA = "confflow.run_summary.v1"` | consumer 端通过 `expected_schema="run_summary.v1"` 形式传给 `_artifact_payload()`（无同名常量，复用 `RUN_SUMMARY_FILE`） |
| `WORKFLOW_STATS_SCHEMA = "confflow.workflow_stats.v1"` | 同上，`expected_schema="workflow_stats.v1"` |
| `WORKFLOW_STATE_SCHEMA = "confflow.workflow_state.v1"` | 同上，`expected_schema="workflow_state.v1"` |
| `OUTPUT_MANIFEST_FILE = "output_manifest.json"` | `src/jobdesk_app/core/confflow_contract.py::OUTPUT_MANIFEST_FILE`（已是 `__all__` 导出，但 `_parse_artifacts` 暂未对其做强制结构校验——见 §6.2 与 §8.2 M2-OBS） |
| `OUTPUT_MANIFEST_SCHEMA = "confflow.output_manifest.v1"` | consumer 端目前没有对应镜像（设计文档 D4 要求做 manifest 下载与分项 resource accounting，但实现尚未覆盖；见 §8.2 M2-OBS） |

也就是说：5 镜像的版本窗口机制未变，**只是 v4 capability 的 payload
内容扩展**，consumer 的 `CAPABILITY_SCHEMA_VERSION` 同步升到 4
（`confflow_preflight.py:218` 严格相等校验），任何遗漏 producer/consumer
任一侧都会触发 `unsupported ConfFlow capability schema` 错误。

### 3.2 Capability Handshake（`--capabilities --json`）

**触发路径**（实测）：

```
MainWindow._submit_payload
  → coordinator.probe_capabilities(server_id, require_dag=...)
    → RunCoordinator._clients(server_id, server, need_sftp=False)  # pool.lease()
      → remote/confflow_probe.py::probe_confflow_capabilities(ssh, ...)
        → 在远端跑：
            set +u
            [ -f /etc/profile ] && . /etc/profile >/dev/null 2>&1 || true
            [ -f "$HOME/.bash_profile" ] && . "$HOME/.bash_profile" >/dev/null 2>&1 || true
            [ -f "$HOME/.profile" ] && . "$HOME/.profile" >/dev/null 2>&1 || true
            [ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc" >/dev/null 2>&1 || true
            [可选 env_init_scripts]
            confflow --capabilities --json
        → parse_confflow_capabilities() + validate_confflow_capabilities(...)
        → 返回 frozen dataclass ConfFlowCapabilities
```

**Producer 端 emit**（`/opt/ConfFlow/confflow/cli.py::_build_capability_payload`，M2 落地后）：

```python
def _build_capability_payload() -> dict[str, Any]:
    """Build the handshake payload with v3 compatibility and v4 provenance."""
    executable = _resolved_confflow_executable()
    build = {"commit": COMMIT, "dirty": DIRTY}
    version = __import__("confflow").__version__
    return {
        "schema_version": _CAPABILITY_SCHEMA_VERSION,           # 4
        "version": version,                                    # "1.4.3"
        "capabilities": {"workflow_state": True, "resume": True, "dag": True},
        "artifacts": {
            "run_summary": RUN_SUMMARY_FILE,                   # "run_summary.json"
            "workflow_stats": WORKFLOW_STATS_FILE,             # "workflow_stats.json"
            "workflow_state": WORKFLOW_STATE_FILE,             # ".workflow_state.json"
            "run_report": RUN_REPORT_FILE,                     # "{basename}.txt"
            "min_xyz": RUN_MIN_XYZ_TEMPLATE,                   # "{basename}min.xyz"
        },
        "commands": {name: shutil.which(name) is not None for name in REQUIRED_COMMANDS},
        "build": build,                                        # {"commit": ..., "dirty": ...}
        "producer": {
            "package": "confflow",
            "version": version,
            "build": build,
            "wheel": {"filename": WHEEL_FILENAME, "sha256": WHEEL_SHA256},
        },
        "executable": {
            "path": executable,                                # shutil.which("confflow") 后 realpath
            "sha256": _file_sha256(executable),                # 仅当 path 是 regular file 时算
            "python": os.path.realpath(sys.executable),
        },
    }
```

注意 `__build__.py`（`/opt/ConfFlow/confflow/__build__.py`）的 4 个默认占位
（`COMMIT=None, DIRTY=None, WHEEL_FILENAME=None, WHEEL_SHA256=None`）由
`setup.py cmdclass` 在 wheel 构建时覆盖写 `build_lib/confflow/__build__.py`，
源码树**绝不**被回写。

**Consumer 端校验**（`src/jobdesk_app/core/confflow_preflight.py::validate_confflow_capabilities`，M2 落地后）：
- **fail-closed**：缺任何要求即抛 `ValueError`，被 probe 包装为 `ConfFlowCapabilityPreflightError`
- 校验顺序：`schema_version == 4` → v4 强制要求 `producer` 与 `executable` block 不为 `None` → `producer.package == "confflow"` → `producer.version == capabilities.version` → semver 解析 → `MIN/MAX_EXCLUSIVE` 窗口 → prerelease 策略 → artifacts 字段级结构相等（用 `dataclasses` 的 `__eq__`）→ commands 必备 7 项 → `workflow_state`/`resume` → 可选 `dag`
- **M2 新增的 producer / executable 解析规则**（`_parse_producer` / `_parse_executable`）：
  - `producer.package` 必须非空字符串
  - `producer.version` 必须非空字符串
  - `producer.build` 与 `producer.wheel` 必须为 object
  - `producer.build.commit/dirty` 类型校验与原顶层 `build` 同构
  - `producer.wheel.filename/sha256` 必须为 `string | None`（缺省或非 string 抛错）
  - `executable.path/sha256/python` 必须为 `string | None`（缺省或非 string 抛错）
- **prerelease 策略**（R-M5 已落）：`PRERELEASE_AT_MIN_REJECT=True` + `PRERELEASE_ABOVE_MIN_ACCEPT=True`；`1.5.0-rc.1` 接受，`1.4.3-rc.1` 拒绝

**schema v3 新增的 `commands` 与 `build` 字段**（P-H2C 落地，v4 仍保留）：
- `commands`（7 项：`bash / nohup / setsid / xargs / sha256sum / mktemp / base64`）：WSL 镜像必须全 `True`
- `build`（`commit: str | None`、`dirty: bool | None`）：producer dirty 时由 `JobSubmitter._preflight_capabilities` 写 `SubmitResult.warnings`（R-H0 落地）

**schema v4 新增的 `producer` 与 `executable` 字段**（M2 落地）：
- `producer.package` 必须恒等于 `"confflow"`（防止冒充为其他分发的同名二进制）
- `producer.version` 必须与顶层 `version` 字符串一致（防止 producer/consumer 各自记录到不同 release）
- `producer.wheel.sha256` 在正式 v4 发布中应为真实 SHA-256；本次实测 WSL `/opt/ConfFlow` 的 live payload 输出字符串 `"unbound"`（而非 `null`）。根因已查清（见 §6.2）：producer 的 `setup.py::BuildPyWithProvenance.run()` 从环境变量 `CONFFLOW_WHEEL_FILENAME` / `CONFFLOW_WHEEL_SHA256` 读取并写入 wheel 内嵌的 `confflow/__build__.py`；`_build_capability_payload`（`cli.py`）本身只从 `__build__.WHEEL_SHA256` 读（未设置环境变量时该常量取值为字面字符串 `"unbound"`，而非 Python `None`——`setup.py` 里 `os.environ.get(...)` 的默认返回值经 `repr()` 写入源码得到的正是这个占位字符串），但 ConfFlow 现有 `docs/RELEASE.md` / `.github/workflows/release.yml` 发布流程从未设置这两个变量，因为回填真实 wheel SHA-256 需要"先构建、算哈希、再拿哈希重新打包"这一步骤，目前完全缺失（wheel 自描述哈希的先后依赖问题）。这不是某次异常安装的偶发状态，而是当前发布脚本必然产生的结果，M2-4 门禁必须先补上这一步骤
- `executable.path` 是 `shutil.which("confflow")` + `os.path.realpath` 的结果；`executable.sha256` 是该二进制文件的真实 SHA-256（仅当路径为 regular file 时计算，否则 `null`）；`executable.python` 是 `sys.executable` 的 `realpath`

### 3.3 命令模板与产物落点

**`services/program_adapters.py::ConfFlowAdapter._build`** 是远程命令的单一生成点（`program_adapters.py:87-128`）：

```bash
workspace=/path/to/remote/dir
source=/path/to/uploaded/mol.xyz
staged="$workspace/mol_xyz"               # collision-free staging
cd "$workspace"
[ "$source" != "$staged" ] && cp -- "$source" "$staged"
confflow "$staged" -c /path/to/config.yaml \
  -w "$workspace/mol_xyz_confflow_work"
[--resume]                                 # 仅 retry/rerun 时追加一次
```

**5 个工件的实际落点**（producer 端代码位置已在 review plan v5 锁定）：

| 工件 | Producer 端文件 | 远端落点 | JobDesk 拉取方式 |
|---|---|---|---|
| `run_summary.json` | `workflow/presenter.py` | `{basename}_confflow_work/` 下 | SFTP via `result_templates` |
| `workflow_stats.json` | `workflow/presenter.py` | 同上 | 同上 |
| `.workflow_state.json` | `workflow/state.py` | 同上 | 同上 |
| `{basename}.txt` | `core/contracts.py::output_txt_path_for_input` | **远端 workspace 根**（不在 work_dir） | 同上 |
| `{basename}min.xyz` | `workflow/presenter.py` | 同上 | 同上 |

Consumer 端的 `result_templates` 列表（`program_adapters.py:119-125`）：
```python
result_templates = [
    RUN_REPORT_FILE,                                     # "{basename}.txt"
    RUN_MIN_XYZ_TEMPLATE,                                # "{basename}min.xyz"
    f"{work_dir_token}/{RUN_SUMMARY_FILE}",              # ".../run_summary.json"
    f"{work_dir_token}/{WORKFLOW_STATS_FILE}",           # ".../workflow_stats.json"
    f"{work_dir_token}/{WORKFLOW_STATE_FILE}",           # ".../.workflow_state.json"
]
```

### 3.4 监控与进度同步

**`services/run_monitor.py`** 维护**单 SSH 连接 per server**，用 `tail -f` 监听 `_batch/events.log`。当远端进程写 `DONE / RUNNING` 时触发 Qt 信号。

**Checkpoint 探测**（`run_monitor.py:29-72`）—— 这是与 ConfFlow 解耦的**纯 SSH 脚本**，避免 producer 改格式就破：

```bash
snapshot_tmp=$(mktemp "${TMPDIR:-/tmp}/jobdesk-checkpoint.XXXXXX")
trap 'rm -f -- "$snapshot_tmp"' EXIT HUP INT TERM
for progress_path in "$workspace/mol_xyz_confflow_work/run_summary.json" \
                     "$workspace/mol_xyz_confflow_work/workflow_stats.json" \
                     "$workspace/mol_xyz_confflow_work/.workflow_state.json"; do
  if [ -f "$progress_path" ]; then
    digest=$(sha256sum -- "$progress_path" | cut -d' ' -f1)
    printf '%d\tpresent\t%s\n' "$index" "$digest" >> "$snapshot_tmp"
    present=1
  else
    printf '%d\tmissing\n' "$index" >> "$snapshot_tmp"
  fi
  index=$((index + 1))
done
printf '__JD_CHECKPOINT_SNAPSHOT_V1__\tpresent=%s\tcount=%s\n' "$present" "$index"
cat "$snapshot_tmp"
printf '__JD_CHECKPOINT_SNAPSHOT_V1__\tcount=%s\n' "$index"
```

每次探测得到一份带 framing header/footer 的快照；Consumer 解析后用 SHA-256 digest 决定是否 fire `DoneEvent`（`run_monitor.py:_parse_checkpoint_snapshot`，第 75 行起）。这绕开了 events.log 的 polling 延迟，让 GUI 在 step 之间也有进度更新（`architecture.md:170-174` 已说明，README 的"auto-sync progress"段印证）。

### 3.5 Session 与资源复用

**R-M1 落地后**（commit `6d458f1 feat: expose RunCoordinator.probe_capabilities for shared-pool probes`）：
- `app.py::main()` 构造**唯一** `SessionPool`，注入 `MainWindow`、`FileTransferPage`、`RunsResultsPage`、所有 `RunCoordinator`（`architecture.md:38-48`）
- `SessionPool.lease(server_id, server, need_sftp=False)` 返回 `(ssh, sftp)` 元组；同一 server 一次只能持一个 lease（`_Entry.mutex` per-server 排他）
- probe 与 upload 串行复用同一 SFTP channel；`FileTransferService` 在共享池路径用 `persistent_session=False`，每次操作进入/退出 lease
- **不**并发：M1 显式声明 "serialised reuse, not concurrency"，避免 read/write 两通道相互阻塞

### 3.6 与 WSL 的具体互动（producer 部署）

ConfFlow 在 WSL 上以**两种方式**消费（来自 `docs/CONFFLOW_WSL_SINGLE_RUN.md`、`CONFFLOW_REAL_RUN_NOTES.md`、`CONFFLOW_1_4_3_WHEEL_DEPLOYMENT.md`、`PHASE9G_REAL_G16_SMOKE.md` 等）：

1. **Wheel 部署**：`/opt/ConfFlow` 源码 + `setup.py cmdclass` 构建 `confflow-1.4.3-py3-none-any.whl` → `pip install /opt/ConfFlow/dist/confflow-1.4.3-py3-none-any.whl` → `/usr/local/bin/confflow` 指向 `.venv/bin/confflow`
2. **Producer 跑任务时**：`bash -c 'source /opt/g16/bsd/g16.profile 2>/dev/null; nohup confflow mol.xyz -c config.yaml -w work_dir'`；环境块约定见 `phase6_inner.sh`、`_phase6_harness_inner.sh`

**关键的脆弱点**：g16 包装器在 Phase 8C 恢复过一次（脚本 `scripts/restore_g16_wsl.py`），`.cursor/rules/wsl-g16-safety.mdc` 把 `/opt/g16/g16`、`/opt/g16/l1.exe`、`/opt/g16/bsd/g16.profile` 列为**永不可写**。这条规则是 JobDesk 与 ConfFlow smoke 测试能持续跑的前提。

---

## 4. 关键模块与文件清单

### 4.1 JobDesk 端按协同面分组

**Cap probe / contract 镜像**（Consumer-side wire-protocol owner）：
- `src/jobdesk_app/core/confflow_contract.py` — 版本窗口 + 5 工件名 + work_dir 名
- `src/jobdesk_app/core/confflow_preflight.py` — parse + validate（frozen dataclass）
- `src/jobdesk_app/remote/confflow_probe.py` — 共享的 preflight shell builder + probe

**Run lifecycle**（Producer 协议下的 run owner）：
- `src/jobdesk_app/services/program_adapters.py` — `ConfFlowAdapter._build()`：远程 `confflow …` 命令生成 + `result_templates`
- `src/jobdesk_app/services/run_coordinator.py` — `RunCoordinator.submit()` / `probe_capabilities()` / `create_and_submit()`
- `src/jobdesk_app/services/run_monitor.py` — `tail -f events.log` + checkpoint SHA-256 探测
- `src/jobdesk_app/remote/submitter.py` — `JobSubmitter._preflight_capabilities` (P-H2C 落地后)；`_ensure_single_command_flag` 保证 `--resume`/`--dry-run` 只一次
- `src/jobdesk_app/services/confflow_results.py` — `ParseState` (OK/MISSING/MALFORMED) + `load_summary_result()` + `load_step_progress_result()`
- `src/jobdesk_app/services/scheduler_helpers.py` — `resources_from_server()` + `scheduler_from_server()`

**Workflow YAML 构造**（可选 `chem` extra，依赖 Pydantic 模型）：
- `src/jobdesk_app/core/workflow_spec.py` — `WorkflowSpec.from_form()` / `to_yaml()`；延迟 import `confflow.core.models.{GlobalConfigModel, CalcConfigModel}`，缺包时软降级
- `src/jobdesk_app/core/_confflow_validation.py` — 离线 YAML 校验（P-H3 后早返回，**不抛** `TypeError`/`AttributeError`）
- `src/jobdesk_app/core/input_builder.py` — `preset_to_confflow_fields()` 表单 → YAML

**持久化 / DB**：
- `src/jobdesk_app/services/run_repository/` — 12 子文件，schema **v6**（M2 后升；v6 新增 `run_provenance` 表，见 §6.1）
- `src/jobdesk_app/services/run_service/` — 9 子文件
- `src/jobdesk_app/core/manifest.py` — `TaskRecord`（Pydantic）+ `ResourceBudget`（frozen dataclass，R-M2）
- `src/jobdesk_app/core/run.py` — `RunSpec`（含 `workflow_kind`、`resource_budget`、`result_templates`）

**GUI 接入**：
- `src/jobdesk_app/gui/main_window.py` — `_submit_payload()` → 调 `RunCoordinator.create_and_submit()` + `probe_capabilities()`
- `src/jobdesk_app/gui/pages/runs_results_page.py` — `set_submit_warnings()`（R-H0 落地）
- `src/jobdesk_app/gui/dialogs/submit_dialog.py` — 双 mode（"Build input file" / "Build workflow"）

### 4.2 ConfFlow 端按协同面分组

**Wire-protocol owner**：
- `confflow/contract.py` — `__all__` 暴露的 7 个常量是 producer-side 唯一契约
- `confflow/cli.py:51-72` — `_CAPABILITY_PAYLOAD` 字面 import contract 常量，**绝不** drift
- `confflow/__build__.py` — `COMMIT`/`DIRTY` 默认占位 `None`
- `setup.py`（R-H2 落地） — `cmdclass={"build_py": BuildPyWithProvenance}` 写 `build_lib/confflow/__build__.py`，**不**回写源码

**Workflow 引擎**：
- `confflow/workflow/engine.py` — 主 `run_workflow()`
- `confflow/workflow/dag/__init__.py` — 显式 DAG（`build_step_graph` + `topo_order`）
- `confflow/workflow/state.py` — `WorkflowState` + `WorkflowStateStore`（原子写，支持 `--resume`）
- `confflow/workflow/stats.py` — `workflow_stats.json` 写入
- `confflow/workflow/presenter.py` — `run_summary.json` + `{basename}min.xyz` 落点
- `confflow/workflow/{validation,step_handlers,step_naming,supervisor,helpers,rerun_failed,dry_run,runtime_context,export}.py`

**Calc executor**：
- `confflow/calc/executor.py` — 主 `CalcExecutor`
- `confflow/calc/runner.py` / `task_execution.py` / `retry_runner.py`
- `confflow/calc/policies/{base,gaussian,orca}.py` — 程序特定封装（g16/ORCA 二进制调用、关键字重写）
- `confflow/calc/rescue.py` — TS rescue（基于 `scan_ops.py`）
- `confflow/calc/components/{executor,task_runner,input_helpers,parser}.py`

**Conformer 块**：
- `confflow/blocks/confgen/{generator,collision,mapping,rotations,validator}.py` — 链式 conformer 生成（rotation, collision check, mapping）
- `confflow/blocks/refine/{processor,rmsd_engine,result,_compat}.py` — RMSD 去重 + 能量过滤
- `confflow/blocks/viz/report.py`

**Agent daemon**（独立子包）：
- `confflow/agent/{server,queue,slots,state,progress,runner,cli}.py`
- 磁盘 queue `~/.confflow-queue/`、state DB `~/.local/share/confflow-agent/state.db`
- `PAUSE` beacon 文件驱动软暂停

**核心共享**：
- `confflow/core/{models,data,io,parsers,validation,contracts,gaussian_input,keyword_rewrite,chem_validation,xyz_metadata,elements,utils,path_policy,exceptions,logging}.py`
- `confflow/shared/{defaults,config_validation,orca_blocks}.py`
- `confflow/config/models.py`（独立于 `core/models.py`）

---

## 5. 数据流全景（按 submit → done 路径）

```
[User clicks Submit in GUI]
       │
       ▼
[gui/main_window.py::_submit_payload]
       │ receives SubmitPayload {sources, mode, server_id, max_parallel, workflow_yaml?}
       ▼
[gui/main_window.py::_done_callback]
       │ probe_capabilities(server_id, require_dag=...)
       │ ▼ (calls RunCoordinator)
       ▼
[services/run_coordinator.py::probe_capabilities]
       │ with self._clients(server_id, server, need_sftp=False) as (ssh, _):
       │   probe_confflow_capabilities(ssh, env_init_scripts=...) -> ConfFlowCapabilities
       │   → if v4 schema, version in [1.4.3, 2.0), 5 artifacts match, 7 commands OK,
       │     producer.package=='confflow', producer.version matches, producer/executable blocks present:
       │     proceed; else raise ConfFlowCapabilityPreflightError
       ▼
[gui/main_window.py::_upload_prepared_batch]
       │ FileTransferService.upload(...)
       ▼
[services/run_coordinator.py::submit]
       │ builds PreparedBatch → JobSubmitter
       ▼
[remote/submitter.py::JobSubmitter._preflight_capabilities]
       │ second probe (belt-and-suspenders; sessions may have rolled)
       │ also re-checks build.dirty/commit, appends SubmitResult.warnings
       ▼
[remote/submitter.py::JobSubmitter.submit]
       │ writes batch_control.sh, tasks.tsv
       │ uploads via SFTP
       │ runs: `nohup setsid bash -c 'batch_control.sh' > events.log 2>&1 &`
       │ stores submit lease + capability provenance in repo (DB schema **v6**：v4 UTC leases + v5 activity log + M2 v6 `run_provenance` 表)
       ▼
[WSL side: confflow runs]
       │ per task: `confflow mol_xyz -c config.yaml -w work_dir`
       │ (or `... --resume` on retry)
       │ writes {basename}_confflow_work/{run_summary,workflow_stats,.workflow_state}.json
       │ writes {basename}.txt and {basename}min.xyz in workspace root
       │ echoes `DONE <task_id>` / `RUNNING <task_id>` to events.log
       ▼
[services/run_monitor.py tail]
       │ on DONE → emit Qt signal
       │ every _CHECKPOINT_PROBE_SECONDS=20 → sha256 probe of 3 work_dir files
       │ → synthetic DoneEvent on digest change → Progress column updates
       ▼
[gui/pages/runs_results_page.py]
       │ refresh button / auto-refresh → RunCoordinator.refresh(run_id)
       │ → service.refresh() reads events.log, updates TaskRecord.status
       │ then download(run_id) → SFTP pull 5 artifacts via result_templates
       │ → confflow_results.load_summary_result(path) → ParseState {OK/MISSING/MALFORMED}
       │ GUI: OK → "✓ Done" / MISSING → "✗ Missing" / MALFORMED → "⚠ Parse Error"
       ▼
[User views results in Runs & Results page]
```

---

## 6. 双方各自架构评估

### 6.1 JobDesk 评估

**优点**：
- **分层清晰**：CLI/GUI → Services → Core → Remote 四层；GUI 永远不直接打 `remote/`，由 `RunCoordinator` + `SessionPool` 代理（架构图 1-3 行）
- **SQLite 单源** + WAL + 5 版本 schema 平滑升级；CLI 与 GUI 共享同一状态
- **5 镜像硬锁**（test_version_consistency.py + test_confflow_validation_differential.py）保证 Consumer wire-protocol 不会被无意改坏
- **`SessionPool` 共享池**（R-M1）让 probe 与 upload 共用一次 SSH，避免两次握手
- **`ResourceBudget`**（R-M2）跨 `RunSpec → RunTaskPlan → TaskRecord → SQLite payload_json / TSV manifest` 双向持久化；预算告警写 `SubmitResult.warnings` 由 R-H0 通道展示
- **`RunRecord.workflow_kind` 派生**（R-M3）消除字符串反查 `if "confflow" in command_template`
- **prerelease 策略显式**（R-M5）：1.5.0-rc.1 接受 / 1.4.3-rc.1 拒绝
- **P-M7 namespace logger**：`jobdesk_app.*` 子 logger 落到 `%APPDATA%\JobDesk\logs\`，不污染根 logger

**风险/待解**：
- **R-H2 producer 升级未发布**：HEAD 是 `44719e9` "Implement JobDesk ConfFlow Milestone 2 contract"（v4 capability + DB v6 + durable submit journal），producer 端对应的 release commit 是 `10e457d`（"Implement ConfFlow Milestone 2 producer contract"）。两 commit 时间相隔 15 秒（`14:42:30` vs `14:42:45 +0800`），是同一次跨仓库联动提交。M2-4 wheel/WSL 发布候选门禁（设计文档 §3.5）尚未走完——见 §6.2 与 §8.2 M2-4
- **SQLite schema 已升到 v6**：`src/jobdesk_app/services/run_repository/_schema.py::SCHEMA_VERSION = 6`，新增 `_migrate_v5_to_v6()` 在 v5 之上建 `run_provenance` 表（`run_id / capability_json / resolved_executable / resolved_realpath / producer_version / producer_build_commit / producer_dirty / wheel_sha256 / recorded_at`），`_provenance.py::record_run_provenance` 是 upsert 入口。`_runs.py` 等模块的 schema_version 注释（v1/v2/v3/v4/v5 演化历史）保留原样，但当前 headline 是 v6
- **durable submit operation journal 已落地**（设计文档 D5）：`_submit.py` 新增 `_SUBMIT_JOURNAL_SCHEMA = "jobdesk.submit.v1"`；幂等键 `_submit_idempotency_key(run_id, task_ids) = "sha256:<hex>"` 基于 `run_id` 与排序后的 `task_ids`；`_validate_remote_staging_root` 强制远端 staging 路径必须为 `…/.jobdesk_runs/<run_id>`（绝对路径、无 `..`、末两级必须为 `.jobdesk_runs` 与 `run_id`），其它形式（含空、空格、`\\`、越界）一律拒绝。`_submit_compensation_metadata` 写入 `cleanup_scope="jobdesk_owned_staging_root"` + `cleanup_status="not_attempted"`，保证远程启动的 operation 在本地恢复时不会远程删除（设计文档 D5 "失败时只对该 staging root 执行补偿清理"）
- **两层并发乘法未充分防御**（R-M2 已落地 ResourceBudget，但实际 threshold `0.8 * max_cores` 仅 warning，不 block）
- **M1 越 canal 直接打 Remote 已修**（`RunCoordinator.probe_capabilities` + 共享池）
- **work_dir 双所有权已修**（R-M4）：CLI `-w` 主导；wizard `from_form` 写 `_wizard_metadata.work_dir`，不再写 `global.work_dir`
- **capability payload 默认只 3 artifact**（v5 review plan §3.1 H2）：已修（R-H2），现在 5 个 artifact + commands + build + producer + executable 字段全在
- **M2 新增的 consumer/producer 对接灰带**：
  - `OUTPUT_MANIFEST_FILE = "output_manifest.json"` 已加入 consumer `__all__` 与 `EXPECTED_ARTIFACTS` 同位置（`confflow_contract.py:99`），但 `_parse_artifacts` 仍按 5 字段结构强校验（`names = ("run_summary", "workflow_stats", "workflow_state", "run_report", "min_xyz")`），producer 在 v4 引入 `output_manifest.json` 后**不**进入 `artifacts` 块，也不进 `EXPECTED_ARTIFACTS`——见 §6.2 与 §8.2 M2-OBS
  - 三个 v1 内容 schema（`confflow.run_summary.v1` 等）只在 consumer `_artifact_payload()` 内部以 `expected_schema="run_summary.v1"` 等字符串常量传给，**未**在 `confflow_contract.py` 做同名常量镜像（producer 端 `contract.py` 是有 `RUN_SUMMARY_SCHEMA` 常量的）——见 §8.2 M2-OBS
- **document drift 已修**（M8）：distro 名 `Ubuntu-24.04`、`architecture.md` "SHA-256 digest of state + stats"、1.3.0/1.4.0 部署文档加 archival banner、`confflow_dependency_decision.md` 同步到 1.4.3
- **`NUL` 设备文件未解**（M9）：调查报告 PR 落地（P-M9），但 `.gitignore` 改动需用户决定（按 plan §4 拆为独立 PR）

### 6.2 ConfFlow 评估

**优点**：
- **生产者契约单一 owner**（`contract.py` ~55 行（M2 后从 44 行扩到 ~55 行：新增 `OUTPUT_MANIFEST_*` 与三个 v1 schema 字符串）+ `_build_capability_payload()` 字面 import）；客户端镜像锁 5 文件
- **DAG + 线性双支持**：linear 是 DAG 的特例；`workflow/dag/__init__.py` + `_legacy.py` 维护兼容
- **状态原子写**（`WorkflowStateStore`）：`--resume` 可从 `.workflow_state.json` 恢复 wavefront
- **Calc executor Protocol** + `blocks/refine/_compat.py` 兼容垫片 → 测试用 fake executor，hermetic 集成
- **confflow-agent daemon**：自带 queue + slots + pause/resume/cancel；适合无 GUI 服务器端独立使用
- **TS rescue**（`calc/rescue.py` + `scan_ops.py`）：基于 scan 路径的 TS 恢复，独立单元
- **RDKit/numba 可选**：JIT 加速时启用，否则 pure-Python 退化路径
- **build provenance**（R-H2）：`COMMIT=None, DIRTY=None` 默认占位 + `setup.py` cmdclass 注入真值

**风险/待解**：
- **CHANGELOG 顶部"archived"标记与"releases resumed"标记并存**：v1.4.0 的 archived notice 与 v1.4.1 的 "releases resumed" 同时存在，README 也说 "archived" 但实际有 v1.4.1 / v1.4.2 / v1.4.3 release。需要清理文档以免让外部 contributor 误判项目状态
- **CLI 子命令数量增长**：`confflow / confgen / confrefine / confts / confflow-agent` 五条入口，但 `confcalc` 在 README §CLI 中列出未在 `[project.scripts]` 中注册（pyproject 中确实没有）
- **文档与实现漂移**：README §ConfFlow↔JobDesk Capability Handshake 仍写 "v1.4.2"，实际已是 v1.4.3（"Capability contract (JSON, schema version 2)" 段落已过时）；v4 升级后该段落需要进一步更新到 v4 + producer/executable 块（截至评审时点尚未提交相应文档变更）
- **测试覆盖率门禁 `fail_under=85` 实际未生效**：CI 不一定跑 coverage；README §Cleanup 列 `.coverage_temp`，但 `[tool.coverage.run].data_file = ".coverage_temp"` 写死路径，monorepo 合并时需重命名
- **provenance 默认占位的失败模式 — 已实测命中，根因已查清**：本评审 §6.2 此前预警 "COMMIT=None, DIRTY=None 时 `_CAPABILITY_PAYLOAD["build"] == {"commit": None, "dirty": None}`，Consumer 端会触发 `dirty is None` warning（producer 端无判断）"。本次复核（`2026-07-28 14:42 +0800` 之后）实测 WSL `/opt/ConfFlow`：
  - 首次探测时 `git status --short` 报告 4 个未提交改动（`confflow/calc/executor.py`、`confflow/contract.py`、`tests/test_calc_executor_protocol.py`、`tests/test_contract.py`）。逐文件核对后确认 `git diff -w`（忽略空白）为空——这是某次 Windows 侧操作把这 4 个文件整体从 LF 转成 CRLF，没有任何实质代码改动。已用 `git checkout -- <4 files>` 还原，`/opt/ConfFlow` 现在是 **clean worktree**
  - **但 clean worktree 并未改变 live payload 的输出**：重新探测后 `build.dirty` 仍是 `true`、`producer.wheel.sha256` 仍是 `"unbound"`、`build.commit` 仍是安装时打包进 wheel 的旧值 `b46a47dca77c905765814253affb90c85a1f8597`（HEAD 的上一个 commit，而非当前 HEAD `10e457d`）。原因：这些字段是**安装时静态打包**进 `.venv/lib/.../confflow/__build__.py` 的构建期常量（`setup.py::BuildPyWithProvenance.run()` 在 `build_py` 阶段跑一次 `git rev-parse HEAD` / `git status --porcelain` 并把结果字面写入生成的 `__build__.py`），CLI 运行时只是从这个静态文件读取（`from .__build__ import COMMIT, DIRTY, WHEEL_FILENAME, WHEEL_SHA256`），不会在每次调用时重新探测仓库状态。因此无论工作区当下多"干净"，探测出的 `build`/`producer` 字段永远只反映**上一次 wheel 构建时**的仓库状态——这解释了 build.commit 停留在旧 commit 而非当前 HEAD 的现象，"COMMIT 默认 None"文档描述与实测的差异也由此得到解释（该次构建时仓库并非全新 checkout，`git rev-parse HEAD` 能正常返回值，只是返回的是构建时刻的 HEAD）
  - `producer.wheel.sha256 == "unbound"` 字面值的来源已查清：`setup.py::BuildPyWithProvenance.run()` 用 `os.environ.get('CONFFLOW_WHEEL_SHA256')` 读取环境变量并 `repr()` 写入 `__build__.py`；未设置时该表达式的默认写法就是产生形如 `WHEEL_SHA256: str | None = 'unbound'` 这样的占位字面值（而非真正的 Python `None`）。ConfFlow 现有的 `docs/RELEASE.md` 发布流程、`.github/workflows/release.yml` 均从未设置 `CONFFLOW_WHEEL_SHA256` / `CONFFLOW_WHEEL_FILENAME` 这两个环境变量——根本原因是 wheel 自描述哈希存在先后依赖问题：要把 wheel 自身的 SHA-256 打包进 wheel 内容，必须先构建一次、计算哈希，再用该哈希触发第二次打包（或对已生成的 wheel 做事后二进制替换），现有发布脚本完全没有这一步骤。这不是本次实测环境的偶发状态，而是**当前发布流程必然产生的结果**：任何照抄 `python -m build` 的构建都会得到同样的 `"unbound"` 占位
  - 含义：JobDesk consumer 当前如果调用 `record_run_provenance` 把这个 payload 写入 `run_provenance.wheel_sha256`，会持久化字面字符串 `"unbound"`，对后续审计/离线验收造成污染；这是 M2-4 发布门禁必须解决的生产证据问题——阻塞点已从"dirty worktree"精确到"发布脚本缺少两阶段哈希回填步骤"
- **`OUTPUT_MANIFEST_FILE = "output_manifest.json"` 与 `OUTPUT_MANIFEST_SCHEMA = "confflow.output_manifest.v1"` 已加入 `contract.py`，但 JobDesk consumer 端仅做常量镜像（`confflow_contract.py:99`），`_parse_artifacts` 未把 `output_manifest` 纳入 5 字段强校验——producer/consumer 在 manifest 这一对工件的对接是灰带，详见 §6.1 与 §8.2 M2-OBS

### 6.3 共生关系评估

**优点**：
- **职责清晰**：JobDesk 是 SSH 文件传输+调度器；ConfFlow 是远程黑盒工具；边界由 wire-protocol 与 `result_templates` 决定
- **断点续跑天然支持**：`nohup` 保持进程；`--resume` + `.workflow_state.json` 让 ConfFlow 从 wavefront 续跑
- **测试可 mock**：JobDesk 端用 `CalcExecutor` fake / `patch("confflow.cli.run_workflow")`；ConfFlow 端用 fake_orca.sh + mock executor
- **部署简单**：远程节点安装兼容的 ConfFlow wheel 即可
- **v5 review plan + v9 remediation plan 形成完整闭环**：H1/H2/H3 + G0 release hygiene + M1-M9 + L1-L4 都已对应到具体 PR

**风险/待解**：
- **跨仓库 contract 双 owner**：5 镜像同步靠 `test_version_consistency.py` 锁；任何忘记同步都会被 CI 拒，但**项目外的 mirror（如文档、blog、issue 模板）** 不在锁内
- **ConfFlow 端 wheel 发布流程缺少哈希回填步骤，影响 consumer 锁**：v5 review plan §3.1.a G0 强制要求 producer release PR 启动前走完 diff 分类（A/B/C/D）+ 1.4.3 release tag 决策门；M2 后该 gate 升级为 M2-4 wheel/WSL 发布候选门禁。`/opt/ConfFlow` worktree 本身已确认 clean（此前的"dirty"是 CRLF 换行污染，已还原），但 `docs/RELEASE.md` / `release.yml` 的发布流程本身缺少"构建→算哈希→回填→二次打包"步骤，导致任何一次构建都会产出 `wheel.sha256 == "unbound"`（详见 §6.2）
- **MONOREPO RFC 未实施**：`docs/MONOREPO_RFC.md` 是 v0.1 草案，列 4 个未回答的硬阻塞（远端 CLI 入口 / Linux 端依赖 / 过渡方案 / 发布机制）；`origin/feature/confflow-monorepo` 分支存在但未合并
- **vendoring 一次反转**：历史曾把 ConfFlow 完整 vendor 到 `src/jobdesk_app/confflow/`，后来在 `fa83950 refactor: remove vendored ConfFlow project` 中删除，`recovery/vendored-confflow-phase1b-1c` 是保留 archive 用的。这表明未来可能再次尝试 monorepo，但目前统一走 wheel 部署
- **WSL `/opt/g16` 共享脆弱性**：ConfFlow 调用 `g16`、JobDesk mock `l1.exe`、CI 安装 `mock-gaussian/mock_l1_exe`；任何对 `/opt/g16/` 的污染会立刻炸掉两侧 smoke 测试
- **`confflow-agent` daemon 长期闲置（本次复核新发现）**：ConfFlow 自带的 queue-based agent 子包（`/opt/ConfFlow/confflow/agent/` 下的 8 个文件：`__init__.py / cli.py / server.py / queue.py / slots.py / state.py / progress.py / runner.py`）从产品交付起就没有被 JobDesk 接入过。实测证据（评审时点）：
  ```bash
  $ wsl -e bash -c 'ls -la ~/.confflow-queue 2>&1'
  ls: cannot access '/root/.confflow-queue': No such file or directory
  $ wsl -e bash -c 'ls -la ~/.local/share/confflow-agent 2>&1'
  ls: cannot access '/root/.local/share/confflow-agent': No such file or directory
  $ wsl -e bash -c 'ps aux | grep -i confflow | grep -v grep'
  (空)
  ```
  即**两个磁盘目录都不存在、没有任何 confflow 进程在跑**。这与设计文档 D6（"WorkflowSupervisor 归 workflow engine 的运行时层所有，agent 只提供可序列化 executor/handle 适配，不再复制 supervisor 状态机"）的归并方向吻合——agent 是 ConfFlow 单机 CLI 时代的产物，JobDesk 的并发控制完全依赖自己的 `xargs -P`（外层，`max_parallel_jobs`）+ `max_parallel_jobs`（内层）两层机制（见 §2.3 表格 + §3.5）。
  - 闲置能力一：`SlotManager`（`confflow/agent/slots.py`，信号量式 slot 池，默认 `slots=2`）—— JobDesk 当前用 `max_parallel_jobs` 实现，等价语义但与 producer 内部 slot 池无任何联动
  - 闲置能力二：`PAUSE` beacon 文件驱动的软暂停（`confflow/agent/server.py::_make_pause_callback`）—— JobDesk 当前仅靠 SSH `kill -TERM` + lease 过期机制（`_leases.py`），没有 producer 端 soft pause 路径
  - 闲置能力三：`submit/status/list/pause/resume/cancel/logs/stop` 9 个子命令的统一 CLI 表面 —— JobDesk 走 `nohup setsid bash -c 'batch_control.sh' > events.log 2>&1 &` + `events.log tail -f`，没有走 `confflow-agent submit` 入口
  - 闲置能力四：SQLite state db（`~/.local/share/confflow-agent/state.db`）—— JobDesk 自己的 `jobdesk.db`（schema v6）才是真实状态源
  - 含义：M2 后的"提交事务与 GUI"工作流在 JobDesk 侧重写了一套 journal 状态机（M2 D5），与 ConfFlow 端 agent 状态机是两条独立不互通的链路；如未来要将 JobDesk 的 submit 协议改成调用 `confflow-agent submit`，需要先解决四件事：(1) JobDesk 必须安装并常驻 `confflow-agent serve`；(2) JobDesk lease 必须能被 agent 端识别（owner_id/lease_expires_at）；(3) `~/.local/share/confflow-agent` 目录必须在 JobDesk 服务器配置中显式声明；(4) 与 WSL g16 mock smoke 的兼容（agent 的 `nohup setsid` 启动方式必须保留 g16 包装器恢复路径）。详见 §7.1 短期任务表 M2-AGT 与 §9 评审建议

---

## 7. 跨项目演化方向评估

### 7.1 短期（v0.5.x / 1.4.3 落地后）

| 任务 | 责任方 | 状态 |
|---|---|---|
| M2-4 wheel/WSL 发布候选门禁（设计文档 §3.5）：唯一 wheel 构建（含哈希回填）+ sha256 校验 + WSL 复跑 capability/dry-run/resume + 非计算 smoke | ConfFlow → JobDesk | **未启动**（worktree 已确认 clean；阻塞点是发布脚本缺少两阶段哈希回填步骤，导致 `producer.wheel.sha256 = "unbound"`，见 §6.2） |
| M2-AGT 评估 `confflow-agent` daemon 迁移：JobDesk submit 路径是否走 `confflow-agent submit` / `serve`（详见 §6.3 风险条目与 §9） | JobDesk | **待评估**（首次提出） |
| M2-OBS v4 对接灰带：consumer `_parse_artifacts` 收纳 `output_manifest`、三个 v1 内容 schema 在 consumer 端做命名常量镜像（见 §8.2 M2-OBS） | JobDesk | **待落实** |
| 升级 WSL `/usr/local/bin/confflow` 到 1.4.3 + 跑 `test_capability_payload_*` 双测 | JobDesk → ConfFlow | **必须**完成（producer release hygiene gate）；已被 M2-4 包含 |
| `SubmitResult.warnings` GUI 显示（黄色提示 + 详情） | JobDesk | R-H0 已落地（HEAD `44719e9`） |
| Capability v4 schema 校验 + 5 artifact + 7 commands + build provenance + producer.package=='confflow' + producer.version 一致 | 双侧 | 已落（M2 commit `44719e9` + `10e457d`） |
| `ResourceBudget` SQLite + TSV 双路径持久化 + GUI max_cores 设置 | JobDesk | R-M2 已落 |
| `RunRecord.workflow_kind` 派生 | JobDesk | R-M3 已落 |
| Durable submit operation journal（`jobdesk.submit.v1`）+ `run_provenance` 表 + idempotency key + 远端 staging root 强制 `.jobdesk_runs/<run_id>` | JobDesk | 已落（M2 commit `44719e9`，`_submit.py` + `_provenance.py` + DB v6） |
| NUL 调查 PR + `.gitignore` 决策 | JobDesk | P-M9 待用户决定 |
| ConfFlow README §ConfFlow↔JobDesk 段落更新到 1.4.3 + schema **v4** + producer/executable + commands + build + 三个 v1 内容 schema + `output_manifest` | ConfFlow | 文档漂移 |
| ConfFlow CHANGELOG 顶部 archived/resumed 标记清理 | ConfFlow | 文档漂移 |

### 7.2 中期（monorepo 评估）

按 `docs/MONOREPO_RFC.md §5.2`（推荐）+ `confflow-remediation-plan.md §6`：
- **先答 RFC §1.1-1.4 四个硬阻塞**（远端 CLI 入口 / Linux 端依赖 / 过渡方案 / 发布机制）
- 然后 `git submodule + 单 wheel 发布`：JobDesk 主仓库 + ConfFlow submodule pinned 到 `v1.4.3`；`pip install .[chem,remote]` 触发不同依赖集；单一 release tag（`jobdesk-X.Y.Z`）
- **不要**纯 vendor（§5.1）：丢失独立 producer release 能力；与 `origin/feature/confflow-monorepo` 历史尝试一致的失败模式

### 7.3 长期（如果 monorepo 落地）

- contract.py 单一来源：producer / consumer 共享同一文件；plan R-H2 5 镜像同步消失
- CI 简化：单仓库、单 checkout、单 wheel 构建
- release 协调：单 tag → 双 wheel 同 release cadence
- `dev.sh` 可同时跑 consumer unit + producer unit
- 反向风险：Windows 构建复杂度上升（pyinstaller 不打包 rdkit/numba/scipy）、vendor 体积膨胀、producer/consumer release 节奏解耦损失、submodule 复杂度

---

## 8. 评审结论（按严重度）

### 8.1 现有已落地（v9 plan 全部 R-* 修复已合 main；M2-1~M2-3 已合 main）

- **R-H0** `SubmitResult.warnings` 字段 + CLI/GUI 最小传播（`submit.py:53`，`main_window._submit_payload._done`）
- **R-H1** `load_summary_result` / `load_step_progress_result` + `ParseState` 三态（`confflow_results.py`）
- **R-H2** schema v3 + 5 artifact + commands + build provenance 双端落地（`confflow_preflight.py:75,166-199`）
- **R-H3** `_validate_step_config` 早返回不抛（`_confflow_validation.py`）
- **R-M1** `RunCoordinator.probe_capabilities` + 共享 `SessionPool`（`run_coordinator.py` + `architecture.md:38-48`）
- **R-M2** `ResourceBudget` 对象链穿透（`manifest.py` + `run.py` + `TaskRecord`）
- **R-M3** `RunRecord.workflow_kind` 派生（`_runs.py`）
- **R-M4** work_dir 单一所有权（CLI `-w` 主导）
- **R-M5** prerelease 策略显式（`PRERELEASE_AT_MIN_REJECT=True` / `PRERELEASE_ABOVE_MIN_ACCEPT=True`）
- **R-M6** 远端 commands 校验 7 项必含 `bash`
- **R-M7** `jobdesk_app.*` logger 命名空间落地文件 handler
- **R-M8** 文档漂移同步（distro、SHA-256、镜像版本、archival banner）
- **R-M9** NUL 调查（只读）
- **R-L1** linear / DAG adapter 合并为 `_build(workflow_kind)`（`program_adapters.py:87-128`）
- **R-L2** `load_step_progress_result` 新 API + 旧入口保留
- **R-L3** UTC 时间戳
- **R-L4** monitor 显式写入声明（`run_monitor.py:6-11`）
- **M2-1** ConfFlow producer release：capability v4（`producer` + `executable` block）+ `__build__.py` 4 个默认占位 + 三个 v1 内容 schema（`run_summary.v1` / `workflow_stats.v1` / `workflow_state.v1`）+ `OUTPUT_MANIFEST_FILE` + atomic write（commit `10e457d`）
- **M2-2** JobDesk consumer/migration：executable binding（`_parse_executable`）、`_parse_producer`、DB v6（`run_provenance` 表 + `_provenance.py::record_run_provenance`）、`_artifact_payload()` 统一 `content_schema` 判别（commit `44719e9`）
- **M2-3** 提交事务与 GUI：`_submit.py` 实现 `_SUBMIT_JOURNAL_SCHEMA = "jobdesk.submit.v1"` + 基于 `sha256(run_id, *sorted(task_ids))` 的 idempotency key + `_validate_remote_staging_root` 强制 `.jobdesk_runs/<run_id>` 边界 + `_submit_compensation_metadata` 写 `cleanup_scope="jobdesk_owned_staging_root"`（commit `44719e9`，`_submit.py` 新增 95 行）
- **M2-DAG** DAG/supervisor 归属：`dag/explicit.py` 为唯一执行语义；`DAGGraph`/`WorkflowDAG` 进入兼容期；`WorkflowSupervisor` 归 workflow engine 运行时（commit `10e457d`，`workflow/engine.py` +33/-14）

### 8.2 仍待观察（落地中）

| 编号 | 项 | 阻塞 / 风险 |
|---|---|---|
| WSL-1 | WSL `/usr/local/bin/confflow` 升级到 1.4.3 + wheel 重装 | producer release hygiene gate 未走完；M2 后升级为 M2-4 wheel/WSL 发布候选门禁 |
| M2-4 | 唯一 wheel 构建（含两阶段哈希回填）+ sha256 校验 + WSL 复跑 capability/dry-run/resume + 非计算 smoke（设计文档 §3.5） | `/opt/ConfFlow` worktree 已确认 clean（此前 4 文件"dirty"是 CRLF 换行污染，已用 `git checkout --` 还原）；真正阻塞点是 `docs/RELEASE.md` / `release.yml` 发布脚本从未设置 `CONFFLOW_WHEEL_SHA256`，任何构建都会产出 `producer.wheel.sha256 = "unbound"`，M2-4 未启动 |
| M2-OBS | v4 对接灰带：(a) `OUTPUT_MANIFEST_FILE` 在 producer `contract.py` 已声明，但 consumer `_parse_artifacts` 仍按 5 字段强校验，manifest 既不在 `artifacts` 块也不进 `EXPECTED_ARTIFACTS`；(b) 三个 v1 内容 schema（`confflow.run_summary.v1` 等）在 producer 是命名常量，在 consumer 端只在 `_artifact_payload()` 内部以 `expected_schema` 字符串传入，未做命名镜像常量 | 多终端输出（设计文档 D4）的"按 terminal 分组下载与分项 resource accounting"实现尚未覆盖到 JobDesk GUI |
| M2-AGT | confflow-agent daemon 长期闲置评估：是否将 JobDesk submit 路径从 `nohup setsid confflow …` 迁到 `confflow-agent submit` / `serve`（`~/.local/share/confflow-agent` 必须显式声明、owner_id/lease_expires_at 互通、与 g16 mock smoke 兼容） | JobDesk 当前并发与状态完全自管；agent 改造属架构层决策，本轮评审仅记录现状 |
| DOC-1 | ConfFlow README "Capability contract (JSON, schema version 2)" 段落 | 与 schema v3 不符（升级到 v4 后已双重过时）；外部 contributor 误判 |
| DOC-2 | ConfFlow CHANGELOG 顶部 archived/resumed 标记并存 | 文档信号冲突 |
| DOC-3 | ConfFlow CLI `confcalc` 在 README 列出但 `[project.scripts]` 未注册 | 文档与代码漂移 |
| RFC-1 | MONOREPO RFC §1.1-1.4 硬阻塞未答 | monorepo 路线停滞 |
| TS-1 | `tests/test_confflow_validation_differential.py` 是否覆盖 v4 schema + 5 artifact + producer/executable + content_schema v1 | review plan §5 待核实；M2 commit `44719e9` 已新增 `tests/test_confflow_preflight.py` 与 `tests/test_confflow_executable_binding.py`，但 v4 全字段覆盖矩阵仍需独立验证 |

### 8.3 长期风险

- **WSL g16 共享脆弱**：Phase 8C 恢复过 wrapper；任何未来的自动化（CI、agent）若绕过 `install_mock_l1_wsl.py` 的 JOBDESK_MOCK-tainted guard，会立刻炸掉 `confflow_real_g16_wsl.py` smoke
- **两项目独立 git history**：Cross-repo PR 顺序约束（producer 先 → consumer 后）靠人执行；任何倒过来都会让 consumer 测 schema v3 时 producer 还在 v2
- **文档与现实不一致**：ConfFlow README "Archived" 段落、`scripts/` 中大量 `scripts/smoke_confflow_*`（双重 repository）等信号会让新 contributor 误判项目活跃度

---

## 9. 评审建议（按优先级）

1. **立即（本次复核新发现，P0）**：给 ConfFlow 的 `setup.py::BuildPyWithProvenance` 发布流程补上两阶段哈希回填步骤——先 `python -m build` 得到未回填的 wheel，计算其 SHA-256，再以 `CONFFLOW_WHEEL_FILENAME` / `CONFFLOW_WHEEL_SHA256` 环境变量重新触发一次构建（或对已生成 wheel 做受控的二进制替换后重新签名），使最终发布的 wheel 内嵌真实的 `wheel.sha256`。`/opt/ConfFlow` worktree 已确认 clean（无需再清理），真正要做的是发布脚本改造，而非等待"worktree 变干净"。任何"unbound"持久化到 `run_provenance.wheel_sha256` 都会污染后续审计
2. **立即**：跑 `scripts/smoke_confflow_real_g16_wsl.py` 与 `scripts/smoke_confflow_real_g16_chk_wsl.py` 双 smoke 验证 producer 端确实 v1.4.3 + schema v4 + commands 全 True + `producer.package == "confflow"` + `producer.version == "1.4.3"`（v4 新增校验）。同时按 `.cursor/rules/wsl-g16-safety.mdc` 4-line pre-flight probe 必跑（`g16`/`l1.exe` ELF、JOBDESK_MOCK fingerprint、bsd profile 存在）
3. **本周**：清理 ConfFlow README §ConfFlow↔JobDesk 段落（升到 1.4.3 + schema **v4** + 5 artifact + producer/executable + 三个 v1 内容 schema + commands + build）+ CHANGELOG 顶部 archived/resumed 标记；同步 `OUTPUT_MANIFEST_FILE` 的对接说明
4. **本周**：解决 §8.2 M2-OBS 的两个对接灰带——(a) 在 consumer `_parse_artifacts` 把 `output_manifest` 纳入 `EXPECTED_ARTIFACTS`，或在 producer 端把 `output_manifest.json` 移到 `artifacts` block 中；(b) 把 `confflow.run_summary.v1` / `workflow_stats.v1` / `workflow_state.v1` 提到 `confflow_contract.py` 作命名常量镜像
5. **本月**：决定 NUL `.gitignore` 是否落地（按 P-M9 拆分调查 PR 与代码 PR）
6. **下季度**：评估 `confflow-agent` daemon 迁移（§8.2 M2-AGT）：是否将 JobDesk submit 路径从 `nohup setsid confflow …` 迁到 `confflow-agent submit` / `serve`；如要做，先回答四件事——`(1)` JobDesk 服务器配置必须声明 `~/.local/share/confflow-agent/state.db` 路径；`(2)` JobDesk 的 owner_id / lease_expires_at 必须能被 agent 端识别；`(3)` 与 WSL g16 mock smoke 的兼容（agent 的 `nohup setsid` 必须保留 g16 包装器恢复路径）；`(4)` JobDesk submit journal（M2 D5）与 agent 端 SQLite state db 的双写一致性
7. **下季度**：评估 MONOREPO RFC §1.1-1.4 四个硬阻塞；如可解，启动 R-MONO-A/B/C 工作流
8. **不做**：纯 vendor 路线（§5.1）；不要把 ConfFlow 历史 commit 内嵌到 JobDesk（保留独立 release 能力）

---

## 10. 参考文档清单

| 文档 | 仓库 | 用途 |
|---|---|---|
| `README.md` | JobDesk | ConfFlow integration § |
| `docs/architecture.md` | JobDesk | 分层、3-page GUI、SQLite、ConfFlow integration |
| `docs/CONFFLOW_1_4_3_REVIEW_PLAN.md` | JobDesk | v5 双向架构审查报告 |
| `docs/CONFFLOW_1_4_3_REMEDIATION_PLAN.md` | JobDesk | v9 修复执行 plan |
| `docs/CONFFLOW_1_4_3_WHEEL_DEPLOYMENT.md` | JobDesk | 1.4.3 wheel 部署（镜像源） |
| `docs/confflow_dependency_decision.md` | JobDesk | 选项 A/B/C 决策分析（推荐 C 现状） |
| `docs/MONOREPO_RFC.md` | JobDesk | v0.1 草案 |
| `docs/CONFFLOW_WSL_SINGLE_RUN.md` | JobDesk | WSL 单跑测试形态 |
| `docs/CONFFLOW_REAL_RUN_NOTES.md` | JobDesk | 真实 g16 跑测笔记 |
| `docs/superpowers/plans/2026-07-28-jobdesk-confflow-milestone2-design.md` | JobDesk | Milestone 2 设计文档（D1-D6 决策项、M2-0~M2-4 实施顺序、当前落地状态） |
| `.cursor/rules/wsl-g16-safety.mdc` | JobDesk | WSL g16 不可写规则 |
| `/opt/ConfFlow/README.md` | ConfFlow | （与现实漂移；schema v2/v4 段落需更新） |
| `/opt/ConfFlow/CHANGELOG.md` | ConfFlow | v1.4.0-1.4.3 演进 |
| `/opt/ConfFlow/confflow/contract.py` | ConfFlow | producer-side wire-protocol owner（v4） |
| `/opt/ConfFlow/confflow/cli.py` | ConfFlow | `_build_capability_payload` 字面 import（含 producer/executable） |
| `/opt/ConfFlow/confflow/__build__.py` | ConfFlow | 4 个默认占位（`COMMIT/DIRTY/WHEEL_FILENAME/WHEEL_SHA256`） |
| `/opt/ConfFlow/confflow/workflow/state.py` | ConfFlow | `.workflow_state.json` 原子写（`workflow_state.v1` envelope） |
| `/opt/ConfFlow/confflow/agent/cli.py` | ConfFlow | confflow-agent daemon（8 文件，长期闲置） |

---

## 附录 A：双 Explore Subagent 验证报告（补全）

主评审由主会话直接阅读两项目源码 + 已有 `docs/CONFFLOW_1_4_3_REVIEW_PLAN.md` v5 + `docs/CONFFLOW_1_4_3_REMEDIATION_PLAN.md` v9 完成后撰写。事后两个 `explore` subagent 独立跑完后，结论与主评审一致，并补充了以下事实：

### A.1 ConfFlow-side 补充

- **WSL venv 已 stub-editable 安装 JobDesk 0.5.0**（`/opt/ConfFlow/.venv/lib64/python3.12/site-packages/__editable__.jobdesk-0.5.0.pth`）。这表明 WSL venv 是 **cross-project integration 的开发环境**，不是 ConfFlow 独立开发环境——主评审未察觉这一点
- **`dist/` 仅含 1.4.0 / 1.4.1 wheel**，**无 1.4.3 wheel**。这是 v9 remediation plan 当前最显著的阻塞——`scripts/build_confflow_wheel.bat` + `verify_confflow_wheel.ps1` 必须在干净 worktree 跑过，否则 `confflow --version` 在 WSL 实际仍可能报 1.4.2
- **`REVIEW.md`（2026-07-06）自评** 报告 706 tests passing、81% line / 86% branch coverage；标记两个未修 bug：
  - `agent/server.py::_make_pause_callback` 用 `callable` 作类型注解（应改为 `Callable[[], None]`）
  - `agent/state.py` `completed_at: str | type[CLEAR] | None = CLEAR`（`type[CLEAR]` 不是 sentinel 实例类型的合法写法）
- **CLI 入口文档与代码漂移**：`README.md` §Command-Line Tools 列出 `confcalc` 但 `[project.scripts]` 未注册；`__init__.py` 抑制 `--version` / `--capabilities` 探测期间的 `logging.WARNING`
- **Producer 端 G0 release hygiene gate 步骤**（来自 `feature/phase2-workflow-state` 分支 merge + tag `v1.4.3` + `setup.py` cmdclass build hook + wheel 构建 + WSL `pip install`）需要在 main 跑 producer 1.4.3 验证前完成

### A.2 JobDesk-side 补充

- **CI 流程**：`.github/workflows/ci.yml` 在跑 integration tests 之前 `actions/checkout moxuezhuchen/ConfFlow@v1.4.3 path=.ci/confflow`，因此 `tests/test_confflow_validation_differential.py` 的 consumer-vs-producer 对照是**真双向**的（不只 mock）
- **stubs/ 目录**仅含 `rdkit` + `rdkit-stubs` 类型存根（不是 ConfFlow stub）。`confflow` 不在 stub 列表中
- **`packaging/`** 仅 PyInstaller (`pyinstaller/jobdesk-gui.spec` + `jobdesk_gui_entry.py`) + Windows 资源 (*.manifest)。Nuitka 也在 `packaging/README.md` 文档但未启用
- **`workflow.yaml`（项目根）** 是最简 ConfFlow 配置（`global` + 空 `steps: []`），作为开发期的默认输入；用户实际配置走 `%APPDATA%/JobDesk/method_presets/*.yaml` 或向导表单保存
- **`services/gui_settings.py::_BUILTIN_PROFILES`** 提供 Gaussian / ORCA / ConfFlow 三套内置 `command_template` + `input_extensions` + `download_patterns`

### A.3 双 agent 一致的关键判断

✅ **Producer / Consumer 边界由 CLI 协议 + JSON capability 守门**，不存在 Python 层面的紧密耦合
✅ **ConfFlow 真实硬件 smoke 脚本不在 ConfFlow repo**，而是在 JobDesk 的 `scripts/smoke_confflow_real_g16*.py`（Phase 9G / 9H1 / 9H2）；ConfFlow 自身 `tests/` 主要是 unit + mock + fake_orca.sh
✅ **Monorepo RFC 4 个硬阻塞未答**，不进入本轮验收
✅ **v9 remediation plan 全部 R-* 修复已合 main**（HEAD `44719e9` M2 commit），仅 M2-4 wheel/WSL 发布候选门禁是当前阻塞

---

> 本评审基于直接阅读仓库源码 + `docs/CONFFLOW_1_4_3_REVIEW_PLAN.md` v5 + `docs/CONFFLOW_1_4_3_REMEDIATION_PLAN.md` v9；不替代后续 reviewer。后续 reviewer 跑 smoke 时必须遵守 `.cursor/rules/wsl-g16-safety.mdc` 第 1–7 条硬规则（不要碰 `/opt/g16/g16`、`/opt/g16/l1.exe`、`/opt/g16/bsd/g16.profile`；WSL 写入需要用户授权；4 行 pre-flight probe 必跑；不传 `--yes` 给 `install_mock_l1_wsl.py`；900s timeout；不 SIGKILL g16 子进程）。
