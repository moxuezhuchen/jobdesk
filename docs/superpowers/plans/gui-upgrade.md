# GUI 升级重构计划

将 Phase A–G 新增的后端功能全部暴露到 GUI，使用户无需 CLI 即可完成所有操作。

## 现状

| 页面 | 文件 | 行数 | 当前职责 |
|------|------|------|----------|
| Files | file_transfer_page.py | 1263 | 本地/远端文件浏览、上传下载、创建 Run 并提交 |
| Runs | runs_page.py | 438 | Run 列表、刷新状态、下载结果、自动轮询 |
| Results | results_page.py | 227 | 加载 TSV 结果表格、诊断信息 |
| Servers | servers_page.py | 106 | 服务器配置 CRUD |
| Settings | settings_page.py | 252 | 语言/主题/字号 |

## 需要暴露的新功能

| 后端模块 | 需要的 GUI 入口 |
|----------|----------------|
| `core/input_builder.py` | XYZ→GJF/INP 生成器对话框 |
| `core/viewer.py` | 右键"在外部查看器中打开"菜单 |
| `services/comparison.py` | 跨 Run 能量对比面板 + CSV 导出按钮 |
| `services/workflow_service.py` | 工作流启动/监控面板 |
| `remote/scheduler.py` | Servers 页增加调度器配置区 |
| `services/analysis_profiles.py` | Results 页增加 Profile 选择器 + 一键分析 |

## 改动方案（5 步）

### Step 1: Results 页重写 — 分析 Profile + 对比 + 导出

**目标：** Results 页不再依赖旧 ProjectContext，改为基于 RunService + AnalysisProfileStore。

- 顶部：Run 选择器（multi-select QListWidget）+ Profile 下拉框 + "分析" 按钮
- 中部：结果表格（现有 QTableWidget 复用）
- 底部工具栏：
  - "对比选中 Runs" → 调用 `compare_runs()`，显示对比表格 + ΔE 列
  - "导出 CSV" → `export_csv()` + QFileDialog
  - "导出 Markdown" → `export_markdown()` + 复制到剪贴板
- 删除对 `current_project_context` 的依赖
- 大约 200 行重写

### Step 2: Files 页 — 右键菜单增加"输入文件生成"和"在查看器中打开"

**目标：** 对选中的 .xyz/.gjf/.log 文件提供快捷操作。

远端右键菜单增加：
- "Generate GJF from XYZ…" — 先下载 .xyz 到 tmp，打开 InputBuilderDialog（Step 3），生成后上传回远端
- "Open in Viewer" → 子菜单列出 `list_available_viewers()` 结果 → 先下载到本地 tmp 再 `open_in_viewer()`

本地右键菜单增加：
- "Generate GJF from XYZ…" — 直接打开 InputBuilderDialog
- "Open in Viewer" → 同上

变动约 40 行（菜单条目 + 2 个调度方法）。

### Step 3: InputBuilderDialog — 独立对话框

新文件 `gui/dialogs/input_builder_dialog.py`

- QDialog，包含：
  - XYZ 文件路径 (QLineEdit + Browse)
  - Preset 下拉框（`list_presets()` 填充）
  - 手动模式：method/basis、keywords、charge、mult、nproc、mem
  - Gaussian / ORCA 切换 (QRadioButton)
  - 输出路径选择
  - 预览 (QTextEdit, readonly)
  - "Generate" 按钮 → 调用 `build_gjf()` / `build_inp()`
- 被 Files 页右键菜单和将来的 Workflow 面板共同调用
- 约 150 行

### Step 4: Runs 页 — 工作流面板 + 调度器显示

Runs 页上方增加一行工具栏：
- "New Workflow" 按钮 → WorkflowDialog（选择 built-in workflow 或自定义 steps）
- 表格增加一列 "scheduler" 显示使用的调度器类型
- 右键菜单增加 "Analyze Run"（调用 `RunService.analyze_run()`，跳转到 Results 页）

WorkflowDialog（新文件 `gui/dialogs/workflow_dialog.py`）：
- 选择 built-in workflow (opt_freq, opt_freq_sp)
- 选择初始输入文件 (从远端文件列表中选)
- 启动 → 创建 WorkflowRun + advance + submit

约 120 行新增。

### Step 5: Servers 页 — 调度器配置

Servers 页编辑表单增加：
- "Scheduler" 下拉框（nohup / slurm / pbs）
- "Partition" / "Queue" 文本框
- "Default nproc" / "Default memory" 输入框
- 保存时写入 `ServerConfig.scheduler` + `ServerConfig.default_resources`

约 50 行扩展。

## 文件变动总览

| 操作 | 文件 | 预估行数 |
|------|------|----------|
| 重写 | `gui/pages/results_page.py` | ~200 |
| 修改 | `gui/pages/file_transfer_page.py` | +40 |
| 新建 | `gui/dialogs/__init__.py` | 0 |
| 新建 | `gui/dialogs/input_builder_dialog.py` | ~150 |
| 新建 | `gui/dialogs/workflow_dialog.py` | ~120 |
| 修改 | `gui/pages/runs_page.py` | +50 |
| 修改 | `gui/pages/servers_page.py` | +50 |
| 修改 | `gui/main_window.py` | +5 (传入 results_page 引用) |
| 修改 | `gui/i18n.py` | +30 (新增翻译 key) |

**总计：** ~645 行新增/重写

## 执行顺序

```
Step 1 (Results 重写) → Step 2 (Files 右键) → Step 3 (InputBuilder 对话框)
                                                        ↓
                                              Step 4 (Workflow 面板)
                                                        ↓
                                              Step 5 (Servers 调度器)
```

Step 1 无依赖，先做。Step 2-3 联动。Step 4-5 独立。

## 设计原则

1. **最小侵入** — 不改变现有 page 的初始化签名，新功能通过添加方法/菜单项接入
2. **延迟导入** — 对话框模块在用户点击时才 import，不影响启动速度
3. **后端零改动** — 所有新 GUI 代码只调用已有 service/core API
4. **测试** — 对话框的 pure-logic 部分（preset 填充、验证）抽取为可测函数
