# JobDesk v0.6 — 后续修复计划 (Post-Refactor)

> 基于 v0.5.0 重构完成后的清理与收尾工作

## Completed Work

| Phase | Task | Status |
|-------|------|--------|
| Phase 1 | SessionPool acquire() context manager | ✅ |
| Phase 2 | Protocol definitions (SSH/SFTP/Scheduler) | ✅ |
| Phase 3 | run_service.py split (9 modules) | ✅ |
| Phase 4 | workflow_page.py split (3 new modules) | ✅ |
| Phase 5 | AppConfig unified configuration | ✅ |
| Phase 6 | run_repository consolidation (16→12 files) | ✅ |
| Phase 7 | Test suite documentation (README + MOCK_STRATEGY) | ✅ |
| Phase 8 | Type annotations + mypy.ini | ✅ |
| Phase 9 | file_transfer_page.py split (Task 1-5) | ✅ |
| Phase 10 | NodeGraph DAG support (10.1-10.5) | ✅ |

### workflow_page Split Details

```
gui/pages/workflow_page/
├── __init__.py              882 lines (main class)
├── _form_builder.py         410 lines (UI widget construction)
├── _preview.py              275 lines (flow diagram + YAML preview)
├── _state.py                 82 lines (WorkflowDraft + YAML helpers)
└── workflow_page_helpers.py  24 lines (step detail formatting)
```

### run_repository Structure (12 files)

```
services/run_repository/
├── __init__.py              494 lines (RunRepository class)
├── _activity.py              59 lines
├── _delete.py               387 lines
├── _legacy.py               122 lines
├── _operations.py           206 lines
├── _operations_types.py      52 lines
├── _paths.py                40 lines
├── _runs.py                204 lines
├── _schema.py              194 lines
├── _submit.py              690 lines (largest, but cohesive)
├── _tasks.py                52 lines
├── _tasks_helpers.py        36 lines
└── _workspaces.py           60 lines
```

---

## 待完成任务

### High Priority

#### Task A: REFACTOR_PLAN_V2.md Task 6 — file_transfer_page 收尾

**状态**: Task 1-5 已完成，Task 6 未执行

**待办**:
- [ ] 删除 18 个 thin alias 方法
- [ ] 给 `ConnectionsCoordinator` 添加 `set_server()` 方法
- [ ] 消除 `_connections._service` 等直接赋值
- [ ] 最终行数验证

**验收**:
```bash
python -m ruff check .
python -m pytest tests/test_architecture_boundaries.py tests/test_gui_behavior/test_file_transfer_page.py -q
# 预期: file_transfer_page.py ≤ 1512 行, 462 tests 全绿
```

---

#### Task B: 清理遗留代码

**B.1 删除 Phase 10.6 遗留的 legacy widget**
```python
# 待删除文件
src/jobdesk_app/gui/widgets/calculation_widget.py
src/jobdesk_app/gui/widgets/input_builder_widget.py
src/jobdesk_app/gui/widgets/workflow_widget.py
```

**B.2 清理 gitignore**
```bash
# 添加 Gau-*.inp (g16 临时文件)
echo "Gau-*.inp" >> .gitignore
```

**B.3 清理未使用的 test 文件**
```bash
# 查找孤立测试
pytest tests/ --collect-only
# 删除确实不再引用的测试
```

---

#### Task C: 国际化完善

**目标**: 提取硬编码中文字符串

**范围**:
- `src/jobdesk_app/gui/pages/*.py` — 页面标签
- `src/jobdesk_app/gui/widgets/*.py` — 组件文本
- `src/jobdesk_app/gui/dialogs/*.py` — 对话框

**步骤**:
1. 用 `grep -rn "[:\"]" src/jobdesk_app/gui/ | grep -v "\.py:"` 查找中文
2. 提取到 `gui/i18n.py`
3. 替换为 `_(...)` 调用

---

### Medium Priority

#### Task D: ConfFlow 依赖管理

**问题**: `src/jobdesk_app/confflow/confflow/` 是 git subtree，版本管理困难

**方案选择**:

| 方案 | 优点 | 缺点 |
|------|------|------|
| D1: pip install from git | 简单 | 需维护版本 tag |
| D2: 独立 pypi 包 | 专业 | 需要单独发布流程 |
| D3: 保持 subtree | 现状 | 版本同步困难 |

**推荐**: D1 + `pyproject.toml` dependency

---

#### Task E: 依赖版本锁定

**目标**: `requirements.txt` 锁定所有直接依赖版本

```bash
# 安装 pip-tools
pip install pip-tools

# 生成锁文件
pip-compile pyproject.toml --output-file requirements.txt

# 验证
pip-sync requirements.txt
```

---

#### Task F: CI/CD 完善

**目标**: GitHub Actions 自动化

```yaml
# .github/workflows/
# ├── test.yml        # pytest on PR
# ├── lint.yml        # ruff + mypy
# └── smoke.yml       # smoke tests (manual trigger)
```

---

### Low Priority (Future)

#### Task G: 文档完善

- [ ] `docs/USER_GUIDE.md` 更新 nodegraph 截图
- [ ] `docs/ARCHITECTURE.md` — 系统架构图
- [ ] API 文档 (Sphinx / MkDocs)

#### Task H: 性能优化

- [ ] `run_repository` 缓存策略优化
- [ ] GUI 启动时间优化
- [ ] 大文件传输进度优化

#### Task I: 测试覆盖率提升

- [ ] 覆盖率目标: core modules > 80%
- [ ] 属性测试 (hypothesis) 用于核心算法
- [ ] 模糊测试用于文件解析

---

## 执行顺序

```
1. Task A (收尾)     — 完成 REFACTOR_PLAN_V2.md
2. Task B (清理)     — 快速清理，无风险
3. Task C (i18n)     — 中等工作量
4. Task D (依赖)     — 决策后执行
5. Task E (锁定)     — 可选，看需求
6. Task F (CI/CD)   — 提升开发效率
7. Task G-I (未来)  — 视情况
```

---

## 风险与注意事项

1. **Task A**: 必须保持所有 coordinator 功能不变
2. **Task B**: 删除文件前确认无引用
3. **Task C**: i18n 不要破坏现有逻辑
4. **Task D**: ConfFlow 是核心依赖，慎之又慎

---

## 成功标准

- `file_transfer_page.py` ≤ 1512 行
- 所有 pytest 测试全绿
- ruff / mypy 全绿
- 无遗留未使用的代码/文件
- 依赖版本可复现
