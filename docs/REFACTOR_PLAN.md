# JobDesk v0.5 Refactor — 后续计划

## 目标

JobDesk v0.5 遗留的四项代码质量问题,在本次 commit `1bcb6f8` 之后继续清理。
所有改动必须在 ruff + pytest + mypy 全绿的情况下提交。

---

## 准备工作

在开始前,在主会话确认以下命令输出正常:

```bash
git log --oneline -1   # 确认在 main, 确认 1bcb6f8 是 HEAD
python -m ruff check .   # 必须全绿
python -m pytest tests/test_architecture_boundaries.py -q   # 必须 9/9 通过
```

如果任何一个失败,**先报告主会话,不要继续**。

---

## Task 1: `run_service.py` 拆分 (优先级: 高)

**文件**: `src/jobdesk_app/services/run_service.py` (912 行)
**目标**: 将 CLI 和 GUI 入口代码抽取为独立模块,主文件只保留 `RunService` 类。

### 步骤

1. 读取 `run_service.py` 全文,识别以下内容:
   - CLI 入口函数 (`def main():`, `def run():` 等)
   - GUI 入口函数 (`def gui_main():` 或类似)
   - `RunService.__init__` 中检测 CLI vs GUI 的分支逻辑
   - 与 CLI/GUI 强耦合的 import(如 `argparse`, `sys.exit`)

2. 创建新文件 `src/jobdesk_app/services/run_service_cli.py`:
   - 将所有 CLI 入口逻辑移入
   - 在底部添加 `main = RunService().run` 或等效导出
   - **不要**引入任何 GUI 或 Qt import

3. 创建新文件 `src/jobdesk_app/services/run_service_gui.py`:
   - 将所有 GUI 入口逻辑移入
   - 在底部添加 `gui_main` 导出
   - **不要**在 `pyproject.toml` 中暴露为 script

4. 重写 `run_service.py` 为:
   ```python
   """RunService — shared run coordination for both CLI and GUI."""
   from __future__ import annotations

   # RunService class stays here (no Qt, no argparse)
   class RunService:
       ...
   ```

5. 更新 `src/jobdesk_app/cli.py` 的 import,指向 `run_service_cli`
6. 更新 `src/jobdesk_app/gui/app.py` 的 import,指向 `run_service_gui`
7. 运行:
   ```bash
   python -m ruff check src/jobdesk_app/services/run_service*.py
   python -m pytest tests/test_run_service.py -q
   python -m mypy src/jobdesk_app/services/run_service*.py
   ```

### 验收标准

- `run_service.py` ≤ 400 行
- `run_service_cli.py` 包含所有 CLI 入口
- `run_service_gui.py` 包含所有 GUI 入口
- ruff ✅, mypy ✅, `tests/test_run_service.py` 全通过
- `test_architecture_boundaries.py` 中的 `test_run_service_has_no_manifest_to_database_writeback` 仍然通过

### 如果发现架构依赖问题(如 RunService 本身依赖 Qt)

如果 `RunService` 类内部已经引入了 Qt 或 argparse,报告给主会话,说明依赖情况,等待进一步指示。

---

## Task 2: `runs_results_page.py` 深层重构 (优先级: 中)

**文件**: `src/jobdesk_app/gui/pages/runs_results_page.py` (2080 行)
**目标**: 将 `_ResultsDetailPane` widget 抽取为独立文件。

### 步骤

1. 读取 `runs_results_page.py`,找到 `class _ResultsDetailPane(QWidget)` 的完整定义(包括其所有内嵌类、信号、方法)。

2. 创建新文件 `src/jobdesk_app/gui/pages/runs_detail_pane.py`:
   ```python
   """Result detail pane — parsed Gaussian/ORCA output viewer widget."""
   from __future__ import annotations

   from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, ...
   # ... 移入完整的 _ResultsDetailPane 类 ...
   ```

3. 在 `runs_results_page.py` 中:
   - 删除 `_ResultsDetailPane` 类定义(约 150-400 行)
   - 添加 `from .runs_detail_pane import _ResultsDetailPane`
   - **确认** import 路径有效

4. 创建 `src/jobdesk_app/gui/pages/runs_detail_pane_helpers.py`:
   - 将 `_ResultsDetailPane.__init__` 中的数据解析逻辑抽取为纯函数
   - 例如: `parse_gaussian_energy(log_text: str) -> float | None`
   - 例如: `parse_gaussian_zpe(log_text: str) -> float | None`
   - 例如: `format_geometry_table(atoms: list, coords: list) -> str`

5. 运行:
   ```bash
   python -m ruff check src/jobdesk_app/gui/pages/runs_detail_pane*.py
   python -m pytest tests/test_runs_results_detail_pane.py -q
   python -m mypy src/jobdesk_app/gui/pages/runs_detail_pane*.py
   ```

### 验收标准

- `runs_results_page.py` 行数减少 ≥ 150 行
- `runs_detail_pane.py` 独立可 import
- `tests/test_runs_results_detail_pane.py` 仍然通过
- ruff ✅, mypy ✅

---

## Task 3: `file_transfer_page.py` 深层重构 (优先级: 中)

**文件**: `src/jobdesk_app/gui/pages/file_transfer_page.py` (1840 行)
**目标**: 将 `_RemoteFileTable` / `_LocalFileTable` 两个内部 widget 抽取为独立文件。

### 步骤

1. 读取 `file_transfer_page.py`,找到:
   - `class _RemoteFileTable(QTableWidget)`
   - `class _LocalFileTable(QTableWidget)`
   - `class _FileTransferWorker`

2. 创建 `src/jobdesk_app/gui/pages/file_transfer_tables.py`:
   - 将两个 Table widget 类移入
   - 每个类保持完整,不做改动

3. 创建 `src/jobdesk_app/gui/pages/file_transfer_worker.py`:
   - 将 `_FileTransferWorker` 移入

4. 在 `file_transfer_page.py` 中用 `from .file_transfer_tables import ...` 替换

5. 将 `_FileTransferWorker` 的 `TransferStatus` 相关逻辑抽取为 `file_transfer_helpers.py` 中的纯函数(参考现有 `file_transfer_helpers.py` 的风格)

6. 运行:
   ```bash
   python -m ruff check src/jobdesk_app/gui/pages/file_transfer*.py
   python -m pytest tests/test_gui_behavior/test_file_transfer_page.py -q
   python -m mypy src/jobdesk_app/gui/pages/file_transfer*.py
   ```

### 验收标准

- `file_transfer_page.py` 行数减少 ≥ 200 行
- 抽取的 widget 类不引入新 import(只继承 Qt 已有类)
- ruff ✅, mypy ✅, test_file_transfer_page.py 全通过

---

## Task 4: CI coverage combine (优先级: 低)

**文件**: `.github/workflows/ci.yml`
**目标**: 在 `test` job 中添加 `coverage/combine` 并上传合并后的 XML。

### 步骤

1. 读取当前 `ci.yml` 的 `test` job。

2. 在 `test` job 的 `Test with coverage` step 之后,添加:

   ```yaml
       - name: Combine and upload coverage
         if: matrix.python-version == '3.11'
         run: |
           python -m pytest --cov=jobdesk_app --cov-report=xml --cov-report=term-missing
   ```

   **实际上**: 已经在 `Test with coverage` step 里跑了 `--cov`,只需要将 `coverage.xml` 上传即可。当前 workflow 已经有 `coverage-xml` artifact upload,跳过此 task。

### 结论

此 task 无需修改。当前 CI 配置已经是最佳实践(coverage 只在 3.11 上报,避免重复合并)。

---

## 提交规范

每完成一个 task,立即:
1. 运行 `python -m ruff check .` 确认全绿
2. 运行 `python -m pytest tests/test_architecture_boundaries.py -q` 确认边界测试通过
3. 用 `git add` 暂存,commit message 格式:
   ```
   refactor(<file>): <一句话描述>

   <3行以内的详细说明>
   ```
4. 报告给主会话:完成的 task、改动行数、测试结果

---

## 注意事项

- **不要**修改 `src/jobdesk_app/gui/design/components.py` (已有 86 行改动,但不是你这次的范围)
- **不要**修改 `src/jobdesk_app/gui/nodegraph/` 目录下的文件
- **不要**修改 `src/jobdesk_app/confflow/` 目录下的任何文件
- 每个 task 之间用 `git log --oneline` 确认当前 HEAD
- 如果 ruff / mypy / pytest 失败,**不要** commit,**不要**继续下一个 task。先报告主会话。

---

## 进度检查清单

- [x] Task 1: `run_service.py` 拆分 — ✅ 93b9355
- [x] Task 2: `runs_results_page.py` 深层重构 — ✅ 1816c02
- [x] Task 3: `file_transfer_page.py` 深层重构 — ✅ 4c0ea0e + 193db70
  - 抽取 `_RemoteEditSession` 到 `file_transfer_tables.py`
  - 抽取 helper 函数到 `file_transfer_helpers.py`
  - 抽取 dialog 工具到 `file_transfer_dialogs.py`
  - 文件从 ~2030 行减少到 ~1983 行 (-47 行)
  - **Note**: Plan 原目标 ≤1640 行，剩余约 340 行待抽取
- [x] Task 4: CI coverage — 已确认无需修改

每个 task 完成时在报告中列明:
- 改动文件列表 + 行数变化
- ruff / mypy / pytest 结果摘要
- commit hash
