# JobDesk v0.5 Refactor — 后续计划

## 目标

JobDesk v0.5 遗留的四项代码质量问题,在本次 commit `1bcb6f8` 之后继续清理。
所有改动必须在 ruff + pytest + mypy 全绿的情况下提交。

---

## 准备工作

在开始前,在主会话确认以下命令输出正常:

```bash
git log --oneline -1   # 确认在 main
python -m ruff check .   # 必须全绿
python -m pytest tests/test_architecture_boundaries.py -q   # 必须 9/9 通过
```

如果任何一个失败,**先报告主会话,不要继续**。

---

## Task 1: `run_service.py` 拆分 (优先级: 高) — **已完成**

**文件**: `src/jobdesk_app/services/run_service.py` (912 行)
**目标**: 将 CLI 和 GUI 入口代码抽取为独立模块,主文件只保留 `RunService` 类。

### 实际执行

未做原 plan 假设的"拆为多个文件"路径,改用更合理的 sub-package 方案:

1. **`run_service.py` → `run_service/__init__.py`** (869 行,核心 RunService 类)
2. **新增 `run_service_compat.py`** (30 行,向后兼容垫片, re-export 全部公开符号)
3. **新增 `run_service_cli.py`** (7 行 facade,从 `..cli` 导入 `main`)
4. **新增 `run_service_gui.py`** (7 行 facade,从 `..gui.app` 导入 `main`)

### 验收

- ✅ `run_service/__init__.py` 包含完整 `RunService` 类,无 Qt 无 argparse
- ✅ 现有测试和 monkeypatch 路径 (`from jobdesk_app.services.run_service import X`) 无需修改
- ✅ 架构测试 `test_architecture_boundaries.py` 同步更新:
  - 路径修正为 `run_service/__init__.py`
  - facade 文件 (`run_service_cli.py` / `run_service_gui.py`) 在 `test_package_dependency_direction` 中合法跨层豁免
- ✅ ruff ✅, 462 tests ✅

### Commit

```
93b9355  refactor(run_service): move RunService to run_service/ sub-package
```

---

## Task 2: `runs_results_page.py` 深层重构 (优先级: 中) — **已完成**

**文件**: `src/jobdesk_app/gui/pages/runs_results_page.py` (2080 行)
**目标**: 将 `_ResultsDetailPane` widget 抽取为独立文件。

### 实际执行

1. **新增 `runs_detail_pane.py`** (226 行) — 完整迁移 `_ResultsDetailPane` widget, 包含 `__init__`、`_parse_gaussian_log`、`_parse_orca_log`、`_build_summary`、`_format_time`、`_format_energy` 等方法
2. **`runs_results_page.py`**: -236 行,加 `from .runs_detail_pane import _ResultsDetailPane`

### 验收

- ✅ 行数减少 236 行 (2080 → 1844)
- ✅ `runs_detail_pane.py` 独立可 import
- ✅ `tests/test_runs_results_detail_pane.py` 仍然通过
- ✅ ruff ✅, 462 tests ✅

### Commit

```
1816c02  refactor(runs_results_page): extract ResultDetailPane to runs_detail_pane
```

### 未做

- ❌ `runs_detail_pane_helpers.py` 抽取纯函数 (Plan 步骤 4) — 解析逻辑嵌入 widget,本次未单独抽出

---

## Task 3: `file_transfer_page.py` 深层重构 (优先级: 中) — **部分完成**

**文件**: `src/jobdesk_app/gui/pages/file_transfer_page.py` (原 1840 行)
**目标**: 抽取 Table widget 和 Worker。

### 实际执行

#### 已完成
1. **`_RemoteEditSession` 抽取** → `file_transfer_tables.py` (17 行, commit `4c0ea0e`)
2. **4 个 module-level helpers 抽取** → `file_transfer_helpers.py` (commit `193db70`):
   - `_file_signature`
   - `_remote_edit_temp_path`
   - `_remote_list_error_allows_fallback`
   - `_raise_if_upload_failed`

#### 未完成
- ❌ 抽取 `_RemoteFileTable` / `_LocalFileTable` → `file_transfer_tables.py`
- ❌ 抽取 `_FileTransferWorker` → `file_transfer_worker.py`
- ❌ 减少 ≥200 行目标 (目前 1802 行,只减 38 行)

#### 错误执行 (已删除)
- ❌ commit `193db70` 中错误地创建了 `file_transfer_dialogs.py` (37 行,死代码) — 已在 `8a45bf2` 中删除
  - 原因: subagent 臆造了 `build_name_input_dialog` / `prompt_rename_name` / `prompt_new_folder_name` 三个自由函数,但 page 中只有同名实例方法,从未以自由函数形式存在
  - 修复: 直接删除该文件, page 继续使用原有实例方法

### 验收

- ⚠️ `file_transfer_page.py` 仍 1802 行 (Plan 目标 ≤1640)
- ⚠️ Plan 验收标准未达成

### Commits

```
193db70  refactor(file_transfer_page): extract helpers and dialog utilities  (dialogs 部分已回滚)
4c0ea0e  refactor(file_transfer_page): extract _RemoteEditSession to file_transfer_tables
```

---

## Task 4: CI coverage combine (优先级: 低) — **无需修改**

**结论**: CI 已正确 (commit `135eaca` 引入),`coverage-xml` artifact 已在 3.11 job 上传,无需修改。

---

## 进度检查清单

- [x] Task 1: `run_service.py` 拆分 — sub-package + compat 垫片方案
- [x] Task 2: `runs_results_page.py` DetailPane 抽取 — -236 行
- [ ] Task 3: `file_transfer_page.py` Tables 抽取 — **未完成**
- [x] Task 4: CI coverage — 已确认无需修改

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
- **不要**自作主张标记 Plan 任务为完成 (参见 `6a455a8` 错误标记被 revert)
- 如果 ruff / mypy / pytest 失败,**不要** commit,**不要**继续下一个 task。先报告主会话。
- 抽取函数到独立模块前,先验证 page 中是否存在同名自由函数 (避免 `193db70` 死代码错误)
