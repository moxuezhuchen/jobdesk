# ConfFlow 1.3.0 Wheel 构建与部署指南

## 概述

JobDesk 的 `chem` extra 依赖 ConfFlow >= 1.3.0。由于计算化学 ConfFlow v1.3.0 未发布 PyPI（PyPI 上的同名包是另一个 YAML 配置库），需从上游源码构建 wheel 并交付。

**上游源码位置**：`C:\dft\tool\ConfFlow`（或对应的 Windows 路径）

## 前提条件

- Windows + Python 3.11+
- PowerShell 或 CMD
- `git` 已配置（提交者身份）
- 上游 ConfFlow 源码已克隆至 `C:\dft\tool\ConfFlow`

---

## 第一步：获取上游 ConfFlow v1.3.0 源码

如果还没有上游源码，请先克隆：

```powershell
# 在 Windows 原生路径执行（不要在 WSL /mnt/c 下）
cd C:\dft\tool
git clone https://github.com/moxuezhuchen/ConfFlow.git
cd ConfFlow
git checkout v1.3.0
```

**确认版本**：

```powershell
type C:\dft\tool\ConfFlow\pyproject.toml | findstr "version"
# 预期输出：version = "1.3.0"
```

---

## 第二步：构建 Wheel

> **注意**：此步骤必须在 Windows 原生文件系统执行，**不要**在 WSL `/mnt/c` 下。

### 2.1 打开 PowerShell

```powershell
cd C:\dft\tool\ConfFlow
```

### 2.2 安装构建依赖（首次需要）

```powershell
py -m pip install build wheel
```

### 2.3 构建 Wheel

```powershell
py -m build --wheel --outdir C:\dft\tool\confflow-dist
```

**预期输出**：

```
* Creating isolated environment ... done
* Installing packages ... done
  Running command ... done
Successfully built confflow
Stored wheel in C:\dft\tool\confflow-dist\
```

生成文件：`C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl`

> **注意**：wheel 文件名包含正确的版本号 `1.3.0`，满足 `confflow>=1.3.0` 要求。

---

## 第三步：验证 Wheel 内容（可选）

```powershell
py -m zipfile -l C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl
```

确认输出包含：
- `confflow/__init__.py`
- `confflow/config/loader.py`
- `confflow/workflow/engine.py`
- `confflow/core/models.py`
- 等等

---

## 第四步：在干净环境中安装

### 4.1 创建干净虚拟环境（推荐）

```powershell
py -m venv C:\dft\tool\jobdesk-venv
C:\dft\tool\jobdesk-venv\Scripts\activate
```

### 4.2 安装顺序（必须严格遵守）

```powershell
# 1. 先安装本地构建的 confflow wheel（满足 >=1.3.0 要求）
py -m pip install C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl

# 2. 再安装 JobDesk（editable 模式，带 chem extra）
py -m pip install -e "C:\dft\tool\jobdesk-dev[chem]"
```

**安装顺序关键点**：

- `pip install -e ".[chem]"` 会读取 `C:\dft\tool\jobdesk-dev\pyproject.toml`
- `pyproject.toml` 声明 `confflow>=1.3.0` 作为依赖
- 如果先执行第 2 步，pip 会尝试从 PyPI 安装 confflow 并失败（PyPI 上没有计算化学版）
- 先执行第 1 步安装本地 wheel 后，pip 检测到已满足版本要求，不会重复安装

### 4.3 验证安装

```powershell
# 验证 confflow 版本
py -c "import confflow; print(confflow.__version__)"
# 预期输出：1.3.0

# 验证 JobDesk 可导入
py -c "import jobdesk_app; print('JobDesk OK')"

# 验证 chem extra 依赖
py -c "from rdkit import Chem; print('RDKit OK')"
```

---

## 第五步：运行测试验证

```powershell
cd C:\dft\tool\jobdesk-dev

# 运行 Phase 3 相关测试
py -m pytest tests\test_confflow_results.py tests\test_run_monitor_checkpoint.py tests\test_workflow_spec.py -v

# 运行全量单元测试（跳过集成测试）
py -m pytest tests\ -m "not integration" -v
```

**预期结果**：
- `test_confflow_results.py`：全部通过（包括新增的 `load_workflow_state_progress` 测试）
- `test_run_monitor_checkpoint.py`：全部通过（包括状态文件探针测试）
- `test_workflow_spec.py`：部分通过（需要 confflow 的测试会运行，不是跳过）

---

## 常见问题

### Q: wheel 构建失败，提示 "Permission denied"

确保在 Windows 原生路径（如 `C:\dft\...`）操作，不要在 WSL `/mnt/c` 下。如果已经在原生路径但仍失败，以管理员身份运行 PowerShell。

### Q: `py -m pip` 找不到

确保 Python 启动器已安装。在 PowerShell 中运行：

```powershell
py --version
py -m pip --version
```

如果 `py` 不可用，使用完整路径：

```powershell
C:\Path\To\Python\python.exe -m pip install ...
```

### Q: 安装顺序是否可以调换？

**不能**。必须先安装 confflow wheel，再安装 JobDesk。否则 JobDesk 的 `pyproject.toml` 会尝试从 PyPI 安装 confflow 并失败。

### Q: 上游源码在哪里？

上游 ConfFlow v1.3.0 源码应在 `C:\dft\tool\ConfFlow`（Windows 路径）或 `/opt/ConfFlow`（WSL 路径）。如果是克隆的新仓库：

```powershell
cd C:\dft\tool
git clone https://github.com/moxuezhuchen/ConfFlow.git
cd ConfFlow
git checkout v1.3.0
```

### Q: 可以从 vendored 目录构建 wheel 吗？

**不能**。vendored 目录是 v1.0.10，不包含 `.workflow_state.json` 支持。必须使用上游 v1.3.0 源码。

---

## 文件清单

| 文件 | 路径 | 用途 |
|------|------|------|
| confflow wheel | `C:\dft\tool\confflow-dist\confflow-1.3.0-py3-none-any.whl` | 可分发包 |
| 上游源码 | `C:\dft\tool\ConfFlow\` | 构建原料 |
| JobDesk 源码 | `C:\dft\tool\jobdesk-dev\` | 开发源码 |

---

## 下一步

vendored subtree 删除闸门通过后，将 vendored 目录替换为 PyPI 安装（删除 `src/jobdesk_app/confflow/` 并移除 `pyproject.toml` 中 vendored 相关配置）。届时 `chem` extra 会直接从 PyPI（如果发布）或从本地 wheel 安装真正的 confflow。
