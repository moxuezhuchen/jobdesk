# ConfFlow × JobDesk 修复执行 Plan（修订版 v9）

> 本版在 v8 基础上吸收 v5 双向审查报告，修复四项 P1（路径覆盖/列表页/Pool 模型/provenance 可复现）、四项 P2（PR 编号/exec 状态/常量/单职责）、一项 P3（死配置），并把 PR 编号集中到第 7 节的"PR-ID 映射表"，正文只用语义代号，避免一改 PR 顺序就要全篇改 PR id。
>
> **不进入本轮的问题**：monorepo 直接执行（先 RFC）、`NUL` 删除（只读验证 + 用户决定）。

## 0. 全局约束

- **不要触碰**：`/opt/g16/g16`、`/opt/g16/l1.exe`、`/opt/g16/bsd/g16.profile`。`install_mock_l1_wsl.py` 不得传 `--yes`。
- **WSL 写入需要用户授权**：agent 在没有用户明确指示前不得在 `/opt/ConfFlow` 执行 `git stash`、`git commit`、写文件等可能改工作树的操作。
- **不删 `NUL`**（只读验证；删除由用户手动决定）。
- 跨仓库契约变更必须 **producer 先 PR，再 consumer PR**；顺序不可逆。
- CI 必须全程绿：`tests/test_version_consistency.py`、`tests/test_confflow_validation_differential.py`、`ruff check`、`mypy`、`pytest tests -q -m 'not integration'`。
- 任何修改 `src/jobdesk_app/core/confflow_contract.py` 的 `MIN_VERSION` 之前必须同步 5 份镜像（pyproject / CI / README / 部署文档 / 离线校验）。
- **MONOREPO（R-MONO-A/B/C）必须补全远端部署方案后才能合**（不能进本轮验收）。
- **PR 编号使用规则**：本 plan 正文不内嵌 PR id，所有 PR 编号从第 7 节"PR-ID 映射表"集中查阅。若需要重排 PR 顺序，只改那张表，不改正文叙述。

---

## 1. 升级前基线（必须先记录）

```bash
# WSL Ubuntu-24.04
which confflow
confflow --version                                    # expect: 1.4.2
confflow --capabilities --json | jq '{schema_version, version, artifacts, has_commands: (.commands != null), has_build: (.build != null)}'
# expect: schema_version=2, version=1.4.2, artifacts=3 keys, has_commands=false, has_build=false

cd /opt/ConfFlow
git status --porcelain                                # 4 modified + 1 untracked
git log -1 --oneline                                  # expect: 1.4.2 release commit
git diff --stat                                       # 为下面 1.2 的 diff 分类做准备
```

**目标**：`schema_version=3`、`version=1.4.3`、5 个 artifacts、含 commands（含 `bash`）、含 build、producer 工作树干净（仅保留有意 commit 的改动）。

### 1.1 Producer 1.4.3 决策门（唯一正式路线）

- ✅ **正式路线**：`feature/phase2-workflow-state` merge → `main`，tag `v1.4.3`，构建并发布 wheel `confflow-1.4.3-py3-none-any.whl`。
- ❌ **非正式路线不成立**（v5 P1 修正）：不会有 `1.4.3rc` / `1.4.3.dev` 混入 stable release。

**producer release PR 启动前**（即 P-H2P 提交前）：用户必须在 chat 中确认走正式路线。

### 1.2 Producer dirty diff 分类流程

```bash
cd /opt/ConfFlow
git status --porcelain
git diff -- <path>
# 决策矩阵：
# A. 改动与本轮修复一致 → commit 到 feature/phase2-workflow-state
# B. 改动有价值但与本轮修复无关 → 单独 PR/branch 保留
# C. 改动是临时调试产物 → 用户手动 rm 或 stash 丢弃
# D. untracked 测试 fixture → 评估是 fixture 还是临时调试
```

**禁止 agent 自动 `git stash -u`**——除非用户在 chat 中明确说"stash 丢弃"。

---

## 2. 高严重度（必须本周期修复）

### R-H0 修复：`SubmitResult.warnings` 字段 + 最小传播（独立 PR，PR-ID 见 §7）

**P1 阻断**：v5 假设 `SubmitResult` 在 `run_coordinator.py`、`SubmitUseCase` 返回 `SubmitOutcome`、`RunRecord.payload_json` 存在——均不成立。

**真实接口**：

- `SubmitResult` 定义在 `src/jobdesk_app/core/submit.py`（**不是 run_coordinator.py**）
- `SubmitUseCase` 返回 `PreparedBatch`（**不是 SubmitOutcome**）
- `RunRecord` 定义在 `src/jobdesk_app/services/run_repository/_operations_types.py`，是 **dataclass**，没有 `payload_json` 字段；持久化走 `runs` 表列字段
- `RunCoordinator` 把 `JobSubmitter` 结果封装为 `RunOperationOutcome`（**已有该类型**），其含 `submit_results: list[SubmitResult]`
- `GUI` 通过 `combined.submit_results` 汇总 `result.warnings`

**修复方案**：

1. **`core/submit.py::SubmitResult` 加字段（v6 P1 修正）**：
   ```python
   from dataclasses import dataclass, field
   @dataclass
   class SubmitResult:
       # ... existing fields ...
       warnings: list[str] = field(default_factory=list)
   ```
   （**不是 BaseModel，不能用 Field()**，只能用 `field(default_factory=list)`）
2. **首版不生成具体业务 warning**：它只增加 `warnings` 字段和传播/展示能力。测试用人工构造的 `SubmitResult(warnings=[...])` 验证 CLI/GUI；producer build warning 在 P-H2C capability v3 落地后，由 `remote/submitter.py::JobSubmitter._preflight_capabilities(tasks, result)` 生成。
3. **`RunOperationOutcome`** 已有 `submit_results: list[SubmitResult]`，**无需新增字段**。
4. **CLI 传播（`cli.py::_cmd_run_submit`）**：保留现有真实入口 `_run_coordinator(...).submit(...)`，只在取得 `result` 后增加 warning 输出：
   ```python
   outcome = _run_coordinator(args, args.workspace).submit(
       args.run_id,
       resource_overrides=overrides or None,
   )
   if not outcome.submit_results:
       # 保留现有 error 分支
       ...
   result = outcome.submit_results[0]
   for warning in result.warnings:
       print(f"  WARNING: {warning}", file=sys.stderr)
   ```
5. **GUI 传播**：`combined` 是 `MainWindow._submit_payload()` 内部 `_run` 闭包产生的局部 `RunOperationOutcome`；在同一方法现有 `_done(outcome)` 回调中汇总，不新增虚构入口：
   ```python
   def _done(outcome):
       if outcome.errors:
           self.show_error(...)
           return
       warnings = [warning for result in outcome.submit_results for warning in result.warnings]
       if warnings:
           self.runs_page.set_submit_warnings(warnings)
       run_ids = [record.run_id for record in outcome.records]
       _show_submitted_runs(self, run_ids)
   ```
   `RunsResultsPage.set_submit_warnings(warnings)` 是首个 PR 新增的纯展示 API（黄色提示 + 详情），page 不直接访问 `combined`。

**持久化**：本轮**不**设计 `runs.submit_warnings` 字段（v5 假设 `RunRecord.payload_json` 不成立）。如需持久化，列为独立 R-H0b 设计任务，**出本轮范围**。

- **文件**：
  - `src/jobdesk_app/core/submit.py`（`SubmitResult` 加 `warnings: list[str]` 字段，**dataclass，`field(default_factory=list)`**）
  - `src/jobdesk_app/remote/submitter.py::JobSubmitter`（P-H2C 中使用真实签名 `_preflight_capabilities(tasks, result)`；`_preflight_tasks(tasks, result)` 保持现有参数顺序）
  - `src/jobdesk_app/services/run_coordinator.py`（现有 `_submit_record()` 把 `SubmitResult` 放入 `RunOperationOutcome.submit_results`，无需新增类型）
  - `src/jobdesk_app/cli.py::_cmd_run_submit`（CLI 遍历 `outcome.submit_results` 中的 `warnings` 写 stderr）
  - `src/jobdesk_app/gui/main_window.py::_submit_payload`（现有 `_done(outcome)` 回调汇总 warnings，通过 `self.runs_page.set_submit_warnings()` 传入）
  - `src/jobdesk_app/gui/pages/runs_results_page.py`（新增 `set_submit_warnings()` 纯展示 API）

### R-H1 修复：run_summary 损坏显式显示 Parse Error

- **新增** `load_summary_result(path) -> ParseResult`，其中 `ParseResult` 含 `state: ParseState`（`ok / missing / malformed`）和 `summary: ConfFlowSummary | None`。
- **保留** `load_summary(path) -> ConfFlowSummary` 作为 `_legacy_load_summary`，内部调用新函数；旧调用方不受影响。
- GUI 路由：`state.missing → "✗ Missing"`、`state.malformed → "⚠ Parse Error"`、`state.ok → "✓ Done"`。
- `format_summary()` 接收 `None` 时渲染为空占位，不崩。
- `ParseState` 区分 `ok`（字段完整/合理）和 `malformed`（结构损坏）；extra key 属于 `ok`（producer 可能加字段，backward compatible）。
- `tests/test_confflow_results.py` 新增：malformed JSON、空文件、不存在文件、含 `unexpected_key` 的合法 JSON；断言 state 分别为 `malformed / malformed / missing / ok`。

- **文件**：
  - `src/jobdesk_app/services/confflow_results.py:41-62`
  - `src/jobdesk_app/gui/pages/runs_results_page.py:1682-1703`

### R-H2 修复：capability contract 覆盖决定下载成败的全部工件 + Producer v1.4.3 升级

**Producer 端（`/opt/ConfFlow`）**：

1. `confflow/contract.py`：
   ```python
   CAPABILITY_SCHEMA_VERSION = 3
   RUN_REPORT_FILE = "{basename}.txt"
   RUN_MIN_XYZ_TEMPLATE = "{basename}min.xyz"
   # 必须与 `remote/submitter.py` / `remote/scheduler.py` / `remote/status.py` / `remote/confflow_probe.py` 中的实际命令调用点一致（由使用点驱动，而非手写清单）。
   REQUIRED_COMMANDS = ("bash", "nohup", "setsid", "xargs", "sha256sum", "mktemp", "base64")
   ```
   > **P2 修正**：原 `REQUIRED_COREUTILS` 漏掉 `bash`；字段已重命名为 `REQUIRED_COMMANDS` 以反映语义（包含非 coreutils 的 `bash`）。提交前必须 grep 一遍 producer 仓库的 `subprocess` / `os.system` / `ssh.run` 调用点确认清单完整。
2. **`confflow/__build__.py` 默认占位（v5 P1 修正）**：
   - **现状**：仓库当前不包含 `__build__.py`，且 `cli.py` 计划无条件 `from confflow.__build__ import COMMIT, DIRTY`；source checkout 中该文件不存在会直接 `ModuleNotFoundError`
   - **修复**：**提交** `confflow/__build__.py` 默认占位版（`COMMIT=None, DIRTY=None`）：
     ```python
     # confflow/__build__.py
     # Default placeholder. Override at wheel build time by setup.py cmdclass.
     COMMIT: str | None = None
     DIRTY: bool | None = None
     ```
   - 这样 source checkout 能正常 import；运行 probe 时 build 字段显示 `null` / `None`，warning 触发（由 consumer 端处理）
3. **`setup.py` build hook（v5 P1 修正）**：
   - `[tool.setuptools.build_meta]` **不是**注册 cmdclass 的入口；**必须**新建 `setup.py`（仓库当前没有）：
     ```python
     from setuptools import setup
     from setuptools.command.build_py import build_py as _build_py
     import subprocess
     from pathlib import Path

     ROOT = Path(__file__).resolve().parent

     class BuildPyWithProvenance(_build_py):
         def run(self):
             super().run()
             try:
                 commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
                 dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).decode().strip())
             except Exception:
                 commit, dirty = None, None
             # 写回 build_lib/confflow/__build__.py，不动源码树
             target = Path(self.build_lib) / "confflow" / "__build__.py"
             target.parent.mkdir(parents=True, exist_ok=True)   # v7 修正：补目录创建
             target.write_text(
                 f"# Auto-generated at build time\nCOMMIT = {commit!r}\nDIRTY = {dirty!r}\n"
             )

     setup(cmdclass={"build_py": BuildPyWithProvenance})
     ```
   - **关键**：build hook 只写 `self.build_lib/confflow/__build__.py`，**不**写回源码树，不会制造 dirty
   - 无 `.git` / editable install 时 `COMMIT=None, DIRTY=None`，与默认占位一致
4. **`confflow/cli.py::_CAPABILITY_PAYLOAD`**：
   ```python
   import shutil
   from confflow.__build__ import COMMIT, DIRTY   # 永远能 import（源码或 build_lib）

   payload = {
       "schema_version": 3,
       "version": __import__("confflow").__version__,
       "capabilities": {"workflow_state": True, "resume": True, "dag": True},
       "artifacts": {
           "run_summary": RUN_SUMMARY_FILE,
           "workflow_stats": WORKFLOW_STATS_FILE,
           "workflow_state": WORKFLOW_STATE_FILE,
           "run_report": RUN_REPORT_FILE,
           "min_xyz": RUN_MIN_XYZ_TEMPLATE,
       },
       "commands": {
           name: shutil.which(name) is not None
           for name in REQUIRED_COMMANDS
       },
       "build": {"commit": COMMIT, "dirty": DIRTY},
   }
   ```
5. `confflow/__init__.py`：`__version__ = "1.4.3"`（静态版本）。
6. `pyproject.toml`：
   ```toml
   [project]
   name = "confflow"
   version = "1.4.3"   # 静态
   ```
7. **测试**（必须覆盖两类，且 P1 修正可复现性）：
  - **source-import 测试**：`tests/test_cli.py::test_capability_payload_from_source_with_placeholder_build` — 直接 `import confflow.cli`，断言 `capabilities.build == {"commit": None, "dirty": None}`；验证源码树**使用已提交的占位 `__build__.py`**（COMMIT=None, DIRTY=None）可正常 import
  - **wheel-install 测试**：`tests/test_cli.py::test_capability_payload_from_wheel_with_real_build` — 安装 `confflow-1.4.3-py3-none-any.whl` 后跑 `confflow --capabilities --json`，断言 `capabilities.build.dirty == false` 且 `commit` 匹配 git HEAD
  - **P1 修正 - 复现性约束**：
    - wheel **必须在 producer 1.1 决策门 + 1.2 diff 分类** 都完成之后的 clean worktree 里构建；构建前预先断言 `git status --porcelain` 为空。
    - 构建产物落到 `dist/` 目录（必须 `gitignored`）；测试中不能用 `cd /opt/ConfFlow` + `pwd` 残留，必要时用独立临时目录构建。
    - 测试断言必须可在 CI 中重跑通过，不依赖本地残留物。
    - 测试断言中 `commit` 与 `dirty` 用占位比对待（commit 长度 7+ hex、dirty==false），不要硬编码具体 commit hash。
8. `tests/test_contract.py` & `tests/test_cli.py`：扩 fixture 覆盖 schema v3 + 5 artifacts + commands + build。
9. **依赖 1.1 决策门**：merge → `main`，tag `v1.4.3`，打 wheel 并部署到 WSL。

**Consumer 端（jobdesk）**：

1. `src/jobdesk_app/core/confflow_contract.py`：
   ```python
   CAPABILITY_SCHEMA_VERSION: int = 3
   MIN_VERSION: tuple[int, int, int] = (1, 4, 3)
   RUN_REPORT_FILE: str = "{basename}.txt"
   RUN_MIN_XYZ_TEMPLATE: str = "{basename}min.xyz"
   ```
2. **Consumer capability 完整闭环（`core/confflow_preflight.py` + `remote/confflow_probe.py` + `remote/submitter.py`）**：
  - **数据模型**：`ConfFlowCapabilities` 是 frozen dataclass（不是 Pydantic model），同时携带 `commands` 和 `build`：
    ```python
    @dataclass(frozen=True)
    class ConfFlowCapabilities:
        # ...现有 schema/version/capabilities/artifacts 字段...
        commands: dict[str, bool] | None = None
        build: dict[str, object] | None = None
    ```
  - **parser**：`parse_confflow_capabilities()` 从 capability JSON 的顶层读取两个对象并放入 dataclass；类型不正确时抛 `ValueError`，不能静默丢弃：
    ```python
    commands = _parse_commands(payload.get("commands"))
    build = _parse_build(payload.get("build"))
    # return ConfFlowCapabilities(..., commands=commands, build=build)
    ```
  - `_parse_commands()` 只接受 `dict[str, bool]`；`_parse_build()` 只接受 `commit: str | None` 与 `dirty: bool | None`。schema v3 缺少或类型错误的 `commands` 属于拒绝项；缺少/未知 `build` 属于 provenance warning。
  - **validator**：`validate_confflow_capabilities(capabilities, require_dag=...) -> None` 保持现有职责，只负责拒绝 schema/version/capabilities/artifacts/commands，不写 `SubmitResult`。
  - **remote probe**：真实函数由当前 `None` 返回改为返回已经解析并验证的 dataclass：
    ```python
    def probe_confflow_capabilities(
        ssh: Any,
        *,
        env_init_scripts: Iterable[str] = (),
        require_dag: bool = False,
    ) -> ConfFlowCapabilities:
        ...
        capabilities = parse_confflow_capabilities(response.stdout)
        validate_confflow_capabilities(capabilities, require_dag=require_dag)
        return capabilities
    ```
  - **JobSubmitter warning**：保持真实签名 `_preflight_capabilities(self, tasks, result)`，接收 probe 返回值后写现有 `result`：
    ```python
    def _preflight_capabilities(self, tasks: list[TaskRecord], result: SubmitResult) -> bool:
        # ...现有 workflow_tasks / try / except 结构...
        caps = probe_confflow_capabilities(
            self._ssh,
            env_init_scripts=self._env_init_scripts,
            require_dag=any(task.workflow_kind == "dag" for task in workflow_tasks),
        )
        build = caps.build or {}
        if build.get("dirty") is True or not build.get("commit"):
            result.warnings.append("producer build is dirty or provenance unknown")
        return True
    ```
  - warning 规则唯一化：`build is None`、`commit` 缺失/空值或 `dirty is True` 均 warning；`dirty is False` 且 commit 非空不 warning。拒绝项规则：
    - `schema_version == 3` → 接受；其他 schema → 拒绝
    - `version < MIN_VERSION` → 拒绝
    - `commands` 缺任一 7 项 → 拒绝（`bash` 必含）
3. **`src/jobdesk_app/services/program_adapters.py::result_templates`（保留 v5 列表形式）**：
   ```python
   result_templates: list[str] = [
       RUN_REPORT_FILE,                  # "{basename}.txt" 在 workspace 根
       RUN_MIN_XYZ_TEMPLATE,             # "{basename}min.xyz" 在 workspace 根
       f"{work_dir_token}/{RUN_SUMMARY_FILE}",          # "{basename}_confflow_work/run_summary.json"
       f"{work_dir_token}/{WORKFLOW_STATS_FILE}",       # "{basename}_confflow_work/workflow_stats.json"
       f"{work_dir_token}/{WORKFLOW_STATE_FILE}",       # "{basename}_confflow_work/.workflow_state.json"
   ]
   ```
4. **5 镜像同步**：
   - `pyproject.toml`：`confflow>=1.4.3,<2.0`
   - `README.md`：1.4.3
   - `tests/test_version_consistency.py`：5 份镜像全部 1.4.3
   - `docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md` → 改名或加横幅
   - `docs/confflow_dependency_decision.md`：同步到 1.4.3
5. `tests/test_version_consistency.py`：扩 fixture 覆盖 schema v3 + 5 字段。
6. `tests/test_confflow_preflight.py`：补充 schema v2/v4 拒绝 + 5 artifacts 缺一拒绝 + commands 缺一拒绝（任一 7 项缺失，包括 `bash`）。
7. `tests/test_program_adapters.py`：覆盖 `result_templates` list 结构 + 5 字段分目录路径。

### R-H3 修复：离线 YAML 校验不再抛 `TypeError`/`AttributeError`

```python
# src/jobdesk_app/core/_confflow_validation.py:91-131
# 现有签名：_validate_step_config(step, index) -> list[str]
# 现有调用方：errors.extend(_validate_step_config(step, i))
# 不改签名，仅在两个早退点用 return errors

def _validate_step_config(step, index):
    errors: list[str] = []
    if not isinstance(step, dict):
        errors.append(f"step {index + 1}: must be a mapping, got {type(step).__name__}")
        return errors
    if "params" in step:
        params = step["params"]
        if not isinstance(params, dict):
            errors.append(f"step {index + 1} params: must be a mapping, got {type(params).__name__}")
            return errors
        # 此后所有 .get() 调用都在已知 dict 上
        ...
    return errors
```

**验证**：
- `tests/test_confflow_validation_differential.py::EXPECTED_REJECTED_BY_BOTH` 新增：
  - `{"global": {}, "steps": [null]}`
  - `{"global": {}, "steps": [{"name": "x", "type": "calc", "params": "oops"}]}`
- 测试断言 **函数本身** 返回 `list[str]`（非空，被 `errors.extend` 后最终落 rejected），**不抛异常**。

---

## 3. 高/中合并项（H4+M10）：producer 工作树干净化 + 真实溯源

1. **Producer diff 分类**：见第 1.2 节（用户手动）。
2. **Producer `__build__.py` 路线**：默认占位提交 + `setup.py` cmdclass build hook（见 R-H2）。
3. **Producer commands 真实探测**：`shutil.which(name) is not None`（清除命名歧义）。
4. **Consumer build 校验**：`remote/submitter.py::JobSubmitter._preflight_capabilities(tasks, result)` 接收 `probe_confflow_capabilities(...)` 返回的 `caps`；`build` 缺失、`commit` 为空或 `dirty is True` 时写 `result.warnings`（依赖 P-H0）。
5. **Smoke 测试**（`tests/test_producer_dirt_state_warning.py`）：
  - 不设 assert
  - 读取 `capabilities.build`，记录到测试输出
  - `dirty` 时 `xfail(reason="producer build dirty")`，**不 fail**

---

## 4. 中严重度

### R-M1 修复：GUI 上传前 probe 走 RunCoordinator + 真复用 SessionPool

**v6 P1 阻断**：v6 把 `_clients` 当单个 `conn` 用；`app.py` shutdown 不存在。

**真实接口**：

- `RunCoordinator.__init__(service, *, server_lookup, ssh_factory, sftp_factory, close_clients=True, connect_clients=True, session_pool=None)` 已有可选 pool；这些参数不改名、不改位置
- `self._clients(server_id, server, need_sftp=False)` 返回 `(ssh, sftp)` **元组**（**不是单个 conn**）
- `RunCoordinator` 已有可选 `session_pool` 注入（**不需要新增构造参数**）
- GUI 入口是 `src/jobdesk_app/gui/app.py`；它已有 `app.aboutToQuit.connect(window.shutdown)`
- `FileTransferPage` 当前通过 `_build_service_factory()` 直接创建 SSH/SFTP，单纯给页面加一个 pool 参数不会自动复用连接
- `RunsResultsPage` 当前自行创建并在 `shutdown()` 关闭 `_session_pool`
- `SessionPool` 的真实关闭方法是 `close()`，且关闭所有权必须唯一

**修复方案**：

0. **目标模型与不做的事先写清楚**（P1 修正）：
  - **实现目标**：probe 与 upload 沿同一 `SessionPool` 租约，**串行**复用同一 SSH transport + 同一 SFTP channel；一次池内 lease 持有期间，read/write 共享同一 SFTP，调用结束释放。
  - **不实现**：同 server 多并发 read/write。当前 `_Entry.mutex` 已是 per-server 排他锁，允许多并发 lease 会破坏当前 invariant，留下不一致的 SFTP 状态。本轮继续以 "序列化复用" 为目标，不引入并发 SFTP channel 分裂。
  - **不持久保留 lease**：禁止 long-lived `lease` 跨多次操作；pooled factory 每次文件操作进入/退出 lease，读/写不会**永久**互相阻塞。
1. **只给 `RunCoordinator` 新增 probe 方法，不改其构造函数**：
   ```python
   def probe_capabilities(
       self,
       server_id: str,
       *,
       require_dag: bool = False,
   ) -> ConfFlowCapabilities:
       server = self._server_lookup(server_id)
       with self._clients(server_id, server, need_sftp=False) as (ssh, _):
           return probe_confflow_capabilities(
               ssh,
               env_init_scripts=list(server.env_init_scripts or []),
               require_dag=require_dag,
           )
   ```
   `_clients` 已经按 `session_pool.lease(server_id, server, need_sftp=...)` 工作；禁止另建 lease 逻辑。
2. **app 级唯一池**：`src/jobdesk_app/gui/app.py::main()` 构造 `SessionPool(create_ssh_client, create_sftp_client)`，以 `MainWindow(session_pool=app_session_pool)` 传入；`MainWindow` 保存为 `self._session_pool`，再注入 `FileTransferPage`、`RunsResultsPage` 以及它创建的每个 `RunCoordinator`。
3. **Files 页真正复用池，而不是只接收参数**：
  - 在 `services/session_pool.py` 增加 `pooled_sftp_factory(pool, server_id, server_config)`（或等价小适配器）。每次 factory 调用进入 `pool.lease(..., need_sftp=True)`，返回代理 SFTP；代理的 `close()` 只退出 lease，**不直接关闭底层 SSH/SFTP**。
  - `FileTransferPage._build_service_factory(server_id, server)` 在有共享池时返回上述 pooled factory；`FileTransferService` 对该路径使用 `persistent_session=False`，确保每次文件操作结束都会释放 lease。禁止把 lease 从页面连接时一直持有到页面关闭，否则 read/write 两个持久通道会相互阻塞。
  - 无 pool 的测试/兼容路径保留现有 `_ConnectedSFTP` factory 和 `persistent_session=True`。
  - `ConnectionsCoordinator.teardown()` 仍只调用 `FileTransferService.close()`；它不关闭共享 pool。
4. **Runs 页所有权**：`RunsResultsPage(..., session_pool=None)` 接收可选池。传入共享池时直接使用且 `shutdown()` 不关闭；仅兼容性自建池时设置 `_owns_session_pool=True` 并自行 `close()`。
5. **上传前 probe 走同一池**：`MainWindow._submit_payload()` 在 `_upload_prepared_batch()` 之前创建注入共享池的 coordinator；`FilesPage._submit_payload` 内 `_upload_prepared_batch(batch, payload, service, coordinator)` 调用 `coordinator.probe_capabilities(payload.server_id, require_dag=...)`，删除 `main_window.py::_preflight_batch_capabilities` 内部直接 `create_ssh_client()` / `ssh.close()` 路线。随后 `FileTransferService` 通过 pooled factory 复用同一底层 session 上传。
6. **唯一关闭所有权**：在 `MainWindow.shutdown()` 的幂等保护内，先停止各 page、再 `BackgroundWorker.wait_all()`，最后调用一次 `self._session_pool.close()`；`closeEvent()` 只调用 `shutdown()`。共享池不得由 `FileTransferPage`、`RunsResultsPage` 或 `RunCoordinator` 关闭。
7. **文档同步**：更新 `docs/architecture.md:34-36`，写明 GUI 通过 coordinator/service 层和 app-owned `SessionPool` 访问 remote；同时**显式标注**“probe 与 upload 共享池，但读写通过单一 SFTP 串行执行”，避免之后误以为是并发模型。

**验证**：
- `tests/test_run_coordinator.py::test_probe_capabilities_uses_session_pool`：断言 `(ssh, _)` 解包正确，并透传 `env_init_scripts` / `require_dag`
- `tests/test_file_transfer_page.py`：连续 probe + upload 只创建一个底层 SSH；pooled SFTP 的 `close()` 释放 lease，不关闭 pool；read/write 操作在序列上完成且无永久死锁（同时显式断言：并发触发不会触发两个 SFTP 同时被 lease）。
- `tests/test_runs_results_page.py`：注入共享池时 `shutdown()` 不关闭；自建池时关闭
- `tests/test_main_window.py`：断言无直接 `create_ssh_client` probe、同一 pool 注入 coordinator 和两页、`shutdown()` 等待 worker 后只关闭一次 pool

### R-M2 修复：两层并发资源乘法需有预算告警

**真实接口与唯一决策**：

- `_tasks_from_plan` 在 `services/run_service/_helpers.py`；`build_run_plan` 在 `core/run.py`
- `ServerConfig` 是 `config/schema.py` 中的 Pydantic `BaseModel`；`config/servers.py` 负责 YAML 加载
- `TaskRecord` 是 Pydantic `BaseModel`；仓库**不存在** `_PERSISTED_TASK_FIELDS`
- `JobSubmitter` 没有 `server_config` 参数；`_preflight_tasks(self, tasks, result)` 已有 `result`
- `max_cores` 只走一条真实链：`RunCoordinator._submit_record` → `RunService.submit_run` → `services/run_service/_submit.py::submit_run` → `JobSubmitter.__init__`

**ResourceBudget 定义位置**：`core/manifest.py`。该模块当前不导入 `core.run`，`core/run.py` 单向导入 `ResourceBudget` 不形成循环。

**修复方案**：

1. **`ResourceBudget` 数据结构**（`core/manifest.py`）：只持久化三个输入，派生值实时计算，避免存储不一致：
   ```python
   @dataclass(frozen=True)
   class ResourceBudget:
       jobdesk_max_parallel: int
       yaml_max_parallel_jobs: int
       cores_per_task: int

       @property
       def effective_slots(self) -> int:
           return self.jobdesk_max_parallel * self.yaml_max_parallel_jobs * self.cores_per_task

       def exceeds(self, server_max_cores: int | None, threshold: float = 0.8) -> bool:
           return bool(server_max_cores and self.effective_slots > server_max_cores * threshold)
   ```
2. **预算计算点覆盖所有 workflow 构建路径**：
   - 在 `services/submit_use_case.py` 增加纯 helper `_resource_budget(workflow_spec, jobdesk_max_parallel)`；从已经验证的 `WorkflowSpec.global_config` 读取 `max_parallel_jobs` / `cores_per_task`，缺省都按 `1`，用 `payload.max_parallel` 作为外层并发。
   - `_build_confflow_specs()` 的 `yaml_text` 分支必须保存 `parsed = WorkflowSpec.from_yaml(...)` 并计算 budget；表单分支直接使用 `WorkflowSpec.from_form(...)` 返回的 `spec` 计算。
   - `_build_dag_specs()` 使用其 `WorkflowSpec.from_form(...)` 结果计算；DAG steps 的覆盖不改变 global 并发字段。
   - `ConfFlowAdapter.build_spec(..., resource_budget=None)` 与 `build_dag_spec(..., resource_budget=None)` 接收并写入 `RunSpec.resource_budget`。Gaussian/ORCA 的 `RunSpec.resource_budget` 保持 `None`。
3. **对象链穿透与持久化必须覆盖 SQLite + TSV 两条路径**：
  - `RunSpec`、`RunTaskPlan`（均在 `core/run.py`）增加 `resource_budget: ResourceBudget | None = None`
  - `core/run.py::build_run_plan()` 构造每个 `RunTaskPlan` 时显式传 `resource_budget=spec.resource_budget`
  - `TaskRecord` 增加 `resource_budget: ResourceBudget | None = None`（Pydantic BaseModel）
  - `services/run_service/_helpers.py::_tasks_from_plan()` 显式传 `resource_budget=task.resource_budget`
  - **SQLite 路径**（`run_repository/_runs.py::_replace_tasks`）：已通过 `TaskRecord.model_dump(mode="json")` 自动写入 `tasks.payload_json`；`_load_tasks` 通过 `TaskRecord.model_validate` 自动读回。新增字段无须额外 API。
  - **TSV 路径**（`core/manifest.py`）：必须同步修改 `_MANIFEST_COLUMNS`、`_task_to_row()`、`_row_to_task()` 三处，新增 `resource_budget` JSON 列（结构：`{"jobdesk_max_parallel": N, "yaml_max_parallel_jobs": N, "cores_per_task": N}`）。`Manifest.read()` 按 header 兼容：当旧列不存在时，`resource_budget` 默认为 `None`。
  - **Manifest.read 兼容策略**：`_row_to_task()` 的 `values.get("resource_budget", "")` 直接走 `_parse_json_dict`，旧 manifest 缺列不会爆 KeyError。
  - **新增测试要求**：
    - `tests/test_resource_budget.py`：YAML text / 表单 ConfFlow / DAG 三条路径分别生成正确 budget；缺省 global 值为 1。
    - `tests/test_run_repository.py`：`_replace_tasks/_load_tasks` SQLite round-trip + `resource_budget` 字段保持。
    - `tests/test_manifest.py`（或现有 `test_manifest.py`）：写入新 TSV → `Manifest.read()` 完整还原；旧 TSV（缺 `resource_budget` 列）→ 加载为 `None`，不报错。
    - `core/run.py::build_run_plan`: `RunSpec.resource_budget` → 每个 `RunTaskPlan.resource_budget` 透传。
    - `max_cores=None` 不 warning；`64/50` 不 warning；`64/60`、`64/80` warning 且每次提交只追加一条。
    - coordinator → service → module submit → JobSubmitter 的 `max_cores=64` 透传测试。
  - **不修改列命名约定**：`_MANIFEST_COLUMNS` 顺序由该常量统一声明，追加列放在末尾（与 SQLite `schema_version` 升级一致）。
  - **不要求新增 manifest 文件版本**：保留 manifest 的未版本化兼容；新版本可以识别旧 TSV，但若兼容性破坏必须用 `Manifest.read()` 异常路径独立处理而非 `schema_version` 字段。
4. **`ServerConfig.max_cores` 与 GUI**：
   - `config/schema.py::ServerConfig` 增加 `max_cores: int | None = Field(default=None, ge=1)`；`config/servers.py` 沿现有 Pydantic 加载路径读写 `max_cores`
   - `gui/pages/settings_servers_page.py` 在现有 form 中增加 `QSpinBox`：范围 `0..1048576`，`0` 设置 `specialValueText("Not configured")`；加载 `None` 显示 0，保存 0 转回 `None`
   - 扩展 `tests/test_config_loader.py` 与 `tests/test_settings_servers_page.py`，覆盖 `64`、缺字段/0 sentinel round-trip 和负值拒绝；不引用不存在的 `ServerConfigDialog` / `settings.toml`
5. **`max_cores` 唯一透传链（不新增假的公共入口）**：
   - `RunCoordinator._submit_record()` 已取得 `server`；调用 `self.service.submit_run(..., max_cores=server.max_cores)`
   - `RunService.submit_run(run_id, ssh, sftp, ..., max_cores=None)` 以关键字参数传给模块函数 `_submit.submit_run(...)`
   - `services/run_service/_submit.py::submit_run(..., max_cores=None)` 构造 `JobSubmitter(..., max_cores=max_cores)`
   - `remote/submitter.py::JobSubmitter.__init__(..., *, tasks, ..., max_cores: int | None = None)` 保存 `self._max_cores`
   - 保持 `_preflight_tasks(self, tasks, result)` 的真实签名；在远程 dry-run 前读取 `task.resource_budget` 与 `self._max_cores`，命中阈值时向已有 `result.warnings` 追加**一次**汇总 warning。不传 `ServerConfig`，不使用 `_current_submit_result`
6. **决策矩阵与传播**：
   - `max_cores is None` → 不评估
   - 最大 `effective_slots > max_cores * 0.8` → 一条 warning；否则不 warning
   - CLI/GUI 沿 R-H0 的 `SubmitResult.warnings` 路径展示；不新增 flag
7. **测试**（`tests/test_resource_budget.py` + 相邻链路测试）：
   - YAML text、表单 ConfFlow、DAG 三条路径分别生成正确 budget；缺省 global 值按 1
   - `build_run_plan`: `RunSpec.resource_budget` → 每个 `RunTaskPlan.resource_budget`
   - `_tasks_from_plan` + repository `_replace_tasks/_load_tasks` round-trip
   - `max_cores=None` 不 warning；`64/50` 不 warning；`64/60`、`64/80` warning 且每次提交只追加一条
   - coordinator → service → module submit → JobSubmitter 的 `max_cores=64` 透传测试
### R-M3 修复：RunRecord 派生 workflow_kind

- `RunRecord` dataclass 加 `workflow_kind: WorkflowKind | None = None`（Python 字段，非 SQL 列）。
- `RunRepository._row_to_record()` 是唯一需要重写的派生函数（`load_run()` 与 `list_runs()` 都通过它），从任务表 `tasks.payload_json` 读取每个 task 的 `workflow_kind`，**单次连接一查询聚合**：
  - 全空 → `None`
  - 全部相同 → 该 `WorkflowKind`
  - 混合 → `None`
- `RunRepository.load_run()` / `list_runs()` 走同一条 helper（`_row_to_record`），避免 N+1；新 helper 接收 `_load_task_workflow_kinds(connection, run_id)` 子查询。
- `runs_results_page.py:1347,1458` 改用 `record.workflow_kind`：
  - `None` → 显示 "Unknown"（**不含字符串 fallback**）
  - 具值 → 显示对应 WorkflowKind
- `main_window.py` 的列表初始化（`refresh_run_list`）路径同样使用 `record.workflow_kind`，避免 “选择列表时是 None、详情页是正确值” 的不一致。
- **不迁移 SQL schema**。
- **不保留 fallback**。

**验证**：
- `tests/test_run_repository.py::test_run_record_workflow_kind_derived_from_tasks`（unit）
- `tests/test_run_repository.py::test_list_runs_populates_workflow_kind`（unit，覆盖 list 路径）
- `tests/test_gui_behavior/test_runs_page.py::test_runs_page_uses_workflow_kind`（integration，确保列表行也得到正确字段）

### R-M4 修复：work_dir 双所有权统一

- `WorkflowSpec.from_form()` 写入 `_wizard_metadata.work_dir`（新字段），**不再写 `global.work_dir`**。
- `to_yaml()` 不输出 `work_dir`。
- GUI SubmitDialog 增加只读标签："Work dir (locked by CLI: `<basename>_confflow_work`)".
- `tests/test_workflow_spec.py::test_workflow_yaml_omits_work_dir_global_key`。
- `tests/test_program_adapters.py::test_cli_work_dir_is_authoritative`。

### R-M5 修复：prerelease 接受策略明确

- **不使用** `ACCEPT_PRERELEASE` 布尔常量（v8 警告：单纯布尔无法表达"above-min 接受 / at-min 拒绝"）。
- 文档明确："`1.5.0-rc.1` 接受；`1.4.3-rc.1` 拒绝"。
- 在 `core/confflow_preflight.py` 顶部以常量形式固化策略：
  ```python
  # Prerelease 接受策略：与 `MIN_VERSION` 比较的核心元组相同时，prerelease 拒绝；
  # 高于此最低版本（major.minor.patch > MIN_VERSION）的 prerelease 接受。
  PRERELEASE_AT_MIN_REJECT = True
  PRERELEASE_ABOVE_MIN_ACCEPT = True
  ```
  并保留在 validator 注释中引用这两个常量，便于以后再调策略。
- `tests/test_confflow_preflight.py`：
  - `test_prerelease_above_min_accepted`：输入 `1.5.0-rc.1`，`MIN_VERSION=(1,4,3)` → 通过
  - `test_prerelease_at_min_rejected`：输入 `1.4.3-rc.1`，`MIN_VERSION=(1,4,3)` → 拒绝
  - `test_release_at_min_accepted`：输入 `1.4.3`，`MIN_VERSION=(1,4,3)` → 通过

### R-M6 修复：远端 commands 在 capability handshake 中校验

- Producer `cli.py` 在 `_CAPABILITY_PAYLOAD` 中 emit `commands`（已在 R-H2 中实现）。
- Consumer `confflow_preflight.py` 校验 7 项必须全 `True`（含 `bash`；已在 R-H2 中实现）。
- 缺任何一项 → fail closed with "missing commands: xxx"。

### R-M7 修复：module logger 落到 file handler

- `configure_file_logging` **不**挂 `"jobdesk"` 根 logger；改为配置 `"jobdesk_app"` 命名空间 logger（与子 logger `jobdesk_app.*` 共享同一 handler 链）：
  ```python
  _jobdesk_app_logger = logging.getLogger("jobdesk_app")
  _jobdesk_app_logger.setLevel(logging.INFO)
  if not _jobdesk_app_logger.handlers:
      _jobdesk_app_logger.addHandler(_file_handler)
  _jobdesk_app_logger.propagate = False
  ```
- **兼容性**：保留默认参数 `logger_name="jobdesk"`，但若传入 `"jobdesk"` 也桥接到 `"jobdesk_app"` 命名空间（避免双重 handler）。
- 验证：`tests/test_app_logging.py::test_submodule_logs_go_to_file`（覆盖 `jobdesk_app.services.run_monitor` 等子 logger 写入 `%APPDATA%\JobDesk\logs\jobdesk-YYYYMMDD.log`）。

### R-M8 修复：文档/实现漂移逐项对齐

- `wsl_distro: Ubuntu` → `Ubuntu-24.04`（`README.md`、`CONFFLOW_WSL_SINGLE_RUN.md`）
- `architecture.md:159` "mtime" → "SHA-256 digest of state + stats"
- `confflow_dependency_decision.md` `>=1.4.0,<2.0` → `>=1.4.2,<2.0`（后续 R-H2 升级时同步到 1.4.3）
- 1.3.0/1.4.0 部署文档加 archival banner

### R-M9 修复（只读 + 可选代码改动）：NUL 设备文件调查

**P2 修正**：`git ls-files ... -z | Select-String NUL` 会把整段 NUL 分隔输出当一个结果，误判为单一路径。

**正确命令**（按 NUL 分隔解析）：

```powershell
# PowerShell（只读，按 NUL 分隔解析）
git status --porcelain=v1 -z | ForEach-Object { $_.Split("`0") } | Where-Object { $_ -like '*NUL*' }
# 或更简洁：
git status --porcelain=v1 -z | % { $_ -split "`0" } | ? { $_ -match 'NUL' }
```

**已跟踪 / 未跟踪分开确认**：

```bash
git ls-files -z | tr '\0' '\n' | grep -F 'NUL'
git ls-files --others --exclude-standard -z | tr '\0' '\n' | grep -F 'NUL'
git ls-files --stage -z | tr '\0' '\n' | grep -F 'NUL'
```

**Win32 路径检查**：

```powershell
Test-Path -LiteralPath 'C:\dft\tool\jobdesk-dev\NUL' -PathType Any
Get-Item -LiteralPath 'C:\dft\tool\jobdesk-dev\NUL' -ErrorAction SilentlyContinue |
    Select-Object FullName, Mode, Attributes
```

**判定规则**：

- `git status --porcelain=v1 -z` 列出 NUL → 实际有该文件（注意：必须按 NUL 分隔逐项解析）
- `git ls-files` 不列 → 检查 PowerShell Get-Item；若返回设备属性（mode 含 device），Git 是被设备符号误导
- 已跟踪文件 objecttype 是 `blob`；未跟踪文件没有 object ID
- **`.gitignore` 加 `NUL` 只能隐藏状态，不能证明源头已解决**

**输出**：

- 调查报告 PR：写入 `.cursor/notes/nul-investigation.md`（或同等位置），由用户决定保留 / 删除 / 忽略。
- **代码改动（如 `.gitignore`）必须独立 PR**，由用户在调查中明确选择"忽略"后才执行。调查 PR 本身不写 `.gitignore`。
- **单职责原则**：调查 PR 与代码 PR 拆开后，`P-M9` 指向"调查 PR"，且该 PR 内只包含只读命令 + 文档结论。

**P-M9 拆分说明**：

- 调查 PR：仅 commit 调查报告（不破坏只读边界）。
- 代码 PR（仅在用户决定"忽略"时才创建）：在 `.gitignore` 增加 `NUL`，并提供一行幂等脚本确认 `git status` 干净。

---

## 5. 低严重度

### R-L1：linear / DAG adapter 合并

`build_spec()` / `build_dag_spec()` 抽 `_build(workflow_kind: WorkflowKind) -> RunSpec`。

### R-L2：results parser 保留 parse state（与 R-H1 同步 API）

- **新增** `load_step_progress_result(path) -> tuple[ParseState, ConfFlowStepProgress]`
- **保留** `load_step_progress(path) -> ConfFlowStepProgress`（旧入口，内部调用新函数）
- 迁移调用方策略：发现一个 → 改一个 → 跑测试

### R-L3：timestamp 统一 UTC

`datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()`。

### R-L4：monitor 显式声明写入行为

docstring 写明 "writes: events.log (touch), mktemp under remote scratch (cleanup)"。

---

## 6. Monorepo（R-MONO-A/B/C）——不进本轮验收

**缺失条件**（必须先回答）：

1. **远端 CLI 入口**：`confflow` 命令在 WSL 端如何引用 vendored 包？
2. **Linux 端依赖**：rdkit / numpy / numba 在 WSL 仍为外部依赖。
3. **过渡方案**：`JOBDESK_CONFFLOW_EXTERNAL=1` 指向旧路径。
4. **发布机制**：monorepo 后 producer 如何发布？

**RFC 框架**（独立文档）。

---

## 7. PR 拆分（修订版 v9）

**v6 P2 修正**：H0 与 H1 拆为独立 PR，**每 PR 单一职责**。

每 PR **单一职责**。producer 1.4.3 决策门（节 1.1）和 diff 分类（节 1.2）是**安全门**，不是 PR；R-M2、R-M3 在本轮不能拆为更细的 PR（合并后保留每 PR 单一职责边界）。

### 7.1 PR-ID 映射表（唯一编号来源）

执行者从这里取 PR id；正文与全局验收清单只用本表的语义代号（如 `P-H0`）。重排 PR 顺序时只改这一张表。

| PR-ID | 涉及项 | 内容 | 估计 LOC | 依赖 |
|---|---|---|---|---|
| P-H0 | R-H0 | `SubmitResult.warnings` 字段 + CLI/GUI 最小传播 | ~120 | 无 |
| P-H1 | R-H1 | `load_summary_result` / `load_step_progress_result` + 保留旧入口 | ~180 | 无 |
| P-H3 | R-H3 | `_validate_step_config` 早返回 + 保留签名 | ~80 | 无 |
| P-L1 | R-L1 | linear / DAG adapter 合并 | ~60 | 无 |
| P-M8 | R-M8 | 文档漂移（distro 名 / SHA-256 / 镜像版本 / 1.3.0-1.4.0 归档） | ~50 | 无 |
| P-H2P | R-H2 producer + H4+M10 | producer 1.4.3 + `__build__.py` + `setup.py` cmdclass + `commands` 字段 + build provenance | ~400 | 决策门 1.1 + 1.2 diff 分类 |
| P-H2C | R-H2 consumer + M6 | consumer `commands` / `build` 字段、parser、validator、probe、warning 闭环 | ~250 | P-H0, P-H2P |
| P-M4 | R-M4 | `work_dir` 单一所有权（CLI `-w` 主导） | ~80 | 无 |
| P-M5 | R-M5 | prerelease 策略（at-min 拒绝 / above-min 接受） | ~40 | P-H2C (`MIN_VERSION` 已升 1.4.3) |
| P-M1 | R-M1 | GUI probe 走 coordinator + 共享 `SessionPool` 复用 | ~200 | 无 |
| P-M2 | R-M2 | `ResourceBudget` 对象链穿透 + SQLite+TSV 双路径持久化 + `servers.yaml` GUI 入口 + warnings 联动 | ~280 | P-H0 |
| P-M3 | R-M3 | `RunRecord.workflow_kind` 派生（`load_run` + `list_runs` 同源） | ~100 | 无 |
| P-M7 | R-M7 | 命名空间 logger（`jobdesk_app.*` 落到文件） | ~80 | 无 |
| P-L2 | R-L2 | `load_step_progress_result` 新 API + 保留旧入口 | ~120 | P-H1 |
| P-L3L4 | R-L3 + R-L4 | UTC 时间戳 + monitor 写入声明 | ~50 | 无 |
| P-M9 | R-M9 | NUL 只读调查 + `.gitignore`（**仅当调查后用户明确选择"忽略"才执行**） | ~30 | 无 |

合计 16 个 PR。

### 7.2 PR-ID 命名规则

- 首位字母 P 标识 "plan PR"。
- 后续 `H` / `M` / `L` 复用 review plan 的严重度分类。
- 数字后缀 `P` / `C`（如 `P-H2P` / `P-H2C`）标识 producer / consumer 子 PR。
- `P-M9` 默认只做调查（README / 调查脚本），**代码改动**（`.gitignore`）必须分离为独立 PR，由用户在 P-M9 调查报告中明确选择后执行。

### 7.3 关键变更（v8 → v9）

- P-H0 只提供 `SubmitResult.warnings` 字段与传播能力；具体 build warning 延后到 P-H2C（capability v3 落地后）。
- P-H2P 明确 producer `commands` 字段含 `bash`（替代 v8 漏掉的 `coreutils`），且 wheel provenance 测试在 clean worktree / 临时目录中构建，可重跑。
- P-M1 显式声明 "probe + upload 共享池、**串行**复用同一 SFTP" 为目标；并发 SFTP channel 不在范围内。
- P-M2 显式声明 `ResourceBudget` 必须沿 SQLite + TSV 两条路径同步；不允许只更新 `payload_json`。
- P-M3 显式声明派生必须覆盖 `list_runs()` 与 `load_run()`（共用 `_row_to_record`），避免 N+1 与列表页不一致。
- P-M9 隔离代码改动：调查 PR 本身不写 `.gitignore`，后续 PR 由调查结论触发。
- 删除原 v8 表格中 "PR #5 已合并" 的矛盾表述（v8 既把 #5 列为"待办 R-M8", 文字又写已合并）。

---

## 8. 全局验收清单（修订版 v9）

**前置基线（必须先记录）**：

- [ ] WSL `/usr/local/bin/confflow` 当前是 1.4.2, schema v2, 3 artifacts
- [ ] producer 4 modified + 1 untracked 状态已记录（含每个文件的 `git diff --stat`）
- [ ] producer 版本镜像 lock 在 `tests/test_version_consistency.py`
- [ ] producer 1.4.3 决策门（正式 tag）已确认（**非正式不成立**）
- [ ] producer dirty diff 分类流程已走完（A/B/C/D 处置完成）

**P-H0（SubmitResult.warnings）完成后**：

- [ ] `core/submit.py::SubmitResult` 加 `warnings: list[str] = field(default_factory=list)`（dataclass，不是 BaseModel）
- [ ] `RunOperationOutcome.submit_results` 原样携带 warnings；本 PR 不生成 build/resource 业务 warning
- [ ] `cli.py::_cmd_run_submit` 保留 `_run_coordinator(...).submit(...)`，对人工构造的 warning 写 stderr
- [ ] `MainWindow._submit_payload()` 现有 `_done(outcome)` 汇总 warning，调用 `self.runs_page.set_submit_warnings()`
- [ ] `RunsResultsPage.set_submit_warnings()` 显示黄色提示和详情；无持久化
- [ ] **持久化约束**：本 PR 不修改 `runs` 表 schema（参见 R-H0 持久化约束）

**P-H1（run_summary result API）完成后**：

- [ ] `tests/test_confflow_results.py` 4 case 全绿（malformed / missing / ok / extra-key）

**P-H2P（producer 1.4.3 + build/commands/provenance）完成后**：

- [ ] `confflow/__build__.py` 默认占位版已提交（`COMMIT=None, DIRTY=None`）
- [ ] `setup.py` 新建，含 `cmdclass={"build_py": BuildPyWithProvenance}`；build hook 只写 `build_lib/confflow/__build__.py`，**不**写回源码树
- [ ] `cd /opt/ConfFlow && pytest tests/test_contract.py tests/test_cli.py` 全绿
- [ ] tests/test_cli.py 含 source-import 测试（`capabilities.build == {"commit": None, "dirty": None}`）
- [ ] tests/test_cli.py 含 wheel-install 测试（构建在工作树外的临时目录；`capabilities.build.dirty == false`，commit 长度 7+ hex 匹配 git HEAD）
- [ ] `confflow --version` → `1.4.3`
- [ ] `confflow --capabilities --json` 输出：
  - `schema_version: 3`
  - `version: 1.4.3`
  - `artifacts` 含 5 keys
  - `commands` 含 7 项（`bash, nohup, setsid, xargs, sha256sum, mktemp, base64`）且全 `True`
  - `build.commit` 为 git commit hash，`build.dirty` 为 `false`
- [ ] producer wheel `confflow-1.4.3-py3-none-any.whl` 已部署到 WSL
- [ ] `main` 上有 tag `v1.4.3`

**P-H2C（consumer mirror + M6 commands + build warning）完成后**：

- [ ] `ConfFlowCapabilities` dataclass 同时包含 `commands` / `build`；parser 对两者做严格类型解析
- [ ] `probe_confflow_capabilities(...) -> ConfFlowCapabilities` 在 validate 后返回同一对象
- [ ] `pytest tests/test_confflow_preflight.py`：schema v2/v4、缺 artifact、缺/假 commands 全部 reject（含 `bash` 缺失）
- [ ] build 缺失、commit 空值、dirty=true 分别由真实 `_preflight_capabilities(tasks, result)` 产生 warning；clean commit 不 warning
- [ ] `pytest tests/test_program_adapters.py`：`result_templates` 是 list，3 个 JSON 在 `{basename}_confflow_work/`，txt/min.xyz 在 workspace 根
- [ ] `tests/test_version_consistency.py` 5 镜像全部 1.4.3；prerelease/release/MIN_VERSION 三个边界测试全绿
- [ ] `pytest tests/test_confflow_validation_differential.py` 全绿（含 R-H3 case）

**P-M1（M1 真正复用 SessionPool）完成后**：

- [ ] `RunCoordinator.__init__` 签名保持不变；新增 `probe_capabilities(server_id, *, require_dag=False)` 并透传 server env scripts
- [ ] `gui/app.py` 构造唯一 `SessionPool`，同一实例注入 `MainWindow`、Files/Runs 页和所有 GUI coordinator
- [ ] pooled SFTP factory 每个文件操作进入/退出 lease；共享路径使用 `persistent_session=False`，probe + upload 只创建一个底层 SSH（**串行**复用同一 SFTP，无 read/write 永久死锁，但并发 lease 仍受 per-server 互斥锁约束）
- [ ] `_upload_prepared_batch` 通过 coordinator probe；GUI 不再直接调用 `create_ssh_client()`
- [ ] `RunsResultsPage` 只关闭兼容性自建 pool；Files 页、coordinator 均不关闭共享 pool
- [ ] `MainWindow.shutdown()` 先停止页面、等待 worker，最后且仅一次 `session_pool.close()`；`closeEvent()` 只委托 shutdown
- [ ] run coordinator、file transfer、runs page、main window 四组定向测试全绿
- [ ] `docs/architecture.md:34-36` 同步（**显式标注**："probe + upload 共享池，序列化复用同一 SFTP"）

**P-M2（M2 ResourceBudget + servers.yaml 入口）完成后**：

- [ ] `ResourceBudget` 在 `core/manifest.py` 持久化三个输入，`effective_slots` 为派生 property
- [ ] YAML text、表单 ConfFlow、DAG 三条 SubmitUseCase 路径都从已验证 WorkflowSpec 计算 budget；缺省 global 值为 1
- [ ] `ConfFlowAdapter` 写 `RunSpec.resource_budget`；`build_run_plan()` 显式传给每个 `RunTaskPlan`
- [ ] `_tasks_from_plan` 显式传 `resource_budget=task.resource_budget`；TaskRecord 字段通过 model_dump/model_validate 自动 round-trip
- [ ] **`core/manifest.py` 三处同步**：`_MANIFEST_COLUMNS`、`_task_to_row()`、`_row_to_task()` 同步新增 `resource_budget` JSON 列；旧 TSV 缺列时 `_row_to_task` 加载为 `None` 不报错
- [ ] `tests/test_manifest.py` 增加新 TSV round-trip + 旧 TSV 兼容加载测试
- [ ] `ServerConfig.max_cores: int | None = Field(default=None, ge=1)`；loader 覆盖 64、缺字段和负值拒绝
- [ ] Settings `QSpinBox` 以 0 / “Not configured” 表示 None，保存时 0 转 None；Qt round-trip 全绿
- [ ] `_submit_record` → `RunService.submit_run` → `_submit.submit_run` → `JobSubmitter(max_cores=...)` 唯一透传测试全绿
- [ ] `_preflight_tasks(self, tasks, result)` 签名不变；阈值命中每次 submit 只追加一条 warning
- [ ] `max_cores=None`、64/50、64/60、64/80 + repository round-trip + TSV round-trip 测试全绿
- [ ] GUI 沿 P-H0 warning API 显示黄色提示和详情

**P-M3（RunRecord 派生 workflow_kind）完成后**：

- [ ] `RunRecord.workflow_kind` dataclass 字段（Python 字段，非 SQL 列）
- [ ] `RunRepository._row_to_record()` 中读 tasks `payload_json` 聚合派生，`load_run()` 与 `list_runs()` 共用同一 helper（避免 N+1）
- [ ] `runs_results_page.py:1347,1458` 改用 `record.workflow_kind`
- [ ] `tests/test_run_repository.py::test_run_record_workflow_kind_derived_from_tasks` + `test_list_runs_populates_workflow_kind` 全绿
- [ ] `tests/test_gui_behavior/test_runs_page.py::test_runs_page_uses_workflow_kind` 全绿（列表、详情两条路径都校验）

**全局（producer 1.4.3 + consumer 全部 PR）完成后**：

- [ ] `pytest tests -q -m 'not integration'` 全绿
- [ ] `ruff check` + `mypy` 无新增告警
- [ ] `tests/test_app_logging.py::test_submodule_logs_go_to_file` 全绿

**真实任务闭环（integration / smoke，不在 CI 默认 gate）**：

- [ ] WSL 真实命令：`confflow <input.xyz> -c <workflow.yaml> -w <work_dir>`（不是 `confflow run <input_dir>`，后者不存在）
  - input.xyz / workflow.yaml / work_dir 由 `program_adapters.build_spec` 同一份代码生成
  - 由 `JobSubmitter.submit` 真实启动
- [ ] 5 个工件全部通过 `download_completed`（`run_service/_download.py`）下载成功：
  - `run_summary.json`、`workflow_stats.json`、`.workflow_state.json` 来自 `{basename}_confflow_work/` 子目录
  - `<basename>.txt`、`<basename>min.xyz` 来自远程 workspace 根
- [ ] Python 端 `load_summary_result` / `load_step_progress_result` 解析无 error（`ParseState.ok`）
- [ ] GUI 显示 "✓ Done"，artifact 计数正确
- [ ] `/opt/g16/g16` 与 `/opt/g16/l1.exe` 指纹未被污染（file 类型 + grep JOBDESK_MOCK = 0）
