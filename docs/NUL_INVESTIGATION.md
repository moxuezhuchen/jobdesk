# NUL 伪影调查（P-M9）

## 症状

`git status` 在 Win32 仓库根下显示一行：

```
?? NUL
```

但 `Test-Path -LiteralPath 'C:\dft\tool\jobdesk-dev\NUL'` 返回 `False`。

## 权威诊断命令（按 NUL 分隔逐项解析）

NUL 是 Win32 保留设备名（`\\.\NUL`），Git for Windows 在某些版本下会
把它当作一个"未跟踪条目"列入 `git status`，但物理上不存在这一文件。

```powershell
# 1. 工作树状态（按 NUL 分隔（`0）逐项解析，避开 PowerShell 把整段输出当一个结果）
git status --porcelain=v1 -z | ForEach-Object { $_.Split("`0") } | Where-Object { $_ -like '*NUL*' }

# 2. 已跟踪文件
git ls-files -z | ForEach-Object { $_.Split("`0") } | Where-Object { $_ -match 'NUL' }

# 3. 未跟踪文件（排除已忽略）
git ls-files --others --exclude-standard -z | ForEach-Object { $_.Split("`0") } | Where-Object { $_ -match 'NUL' }

# 4. 已暂存文件
git ls-files --stage -z | ForEach-Object { $_.Split("`0") } | Where-Object { $_ -match 'NUL' }

# 5. Win32 真实路径检查
Test-Path -LiteralPath 'C:\dft\tool\jobdesk-dev\NUL' -PathType Any
Get-Item -LiteralPath 'C:\dft\tool\jobdesk-dev\NUL' -ErrorAction SilentlyContinue |
    Select-Object FullName, Mode, Attributes
```

## 本仓库诊断结果（2026-07-25）

| `#` | 命令 | 结果 |
|---|---|---|
| 1 | `git status --porcelain=v1 -z` | 1 条 `?? NUL` |
| 2 | `git ls-files` | 空 |
| 3 | `git ls-files --others --exclude-standard` | `NUL` |
| 4 | `git ls-files --stage` | 空 |
| 5 | `Test-Path C:\dft\tool\jobdesk-dev\NUL` | `False` |

## 判定

- **Git 状态**：NUL 是单条未跟踪条目，无对应 blob、无对应索引条目。
- **文件系统**：物理上不存在 `C:\dft\tool\jobdesk-dev\NUL`（亦非符号链接）。
- **本质**：Git for Windows 在 Win32 仓库根下把 `NUL` 设备伪影当作"未跟踪路径"报出。
  这是工具层面的伪影，不影响实际工作树内容。

## 推荐处置

### 默认：**不写 `.gitignore`**

- `.gitignore` 加 `NUL` 只能隐藏状态，不能证明源头已解决。
- 加完之后 `git status` 不再显示，但伪影仍在（Git 仍会扫描它）。
- 这违背 plan R-M9 的"调查后用户明确选择忽略才执行"原则。

### 如果用户明确选择"忽略"

```text
# .gitignore
NUL
```

仅作用：让 `git status` / `git ls-files --others` 不再列出 `NUL`。
**注意**：每次在新环境首次检出时仍可能临时出现，直到 pre-commit hook
或 `.gitignore` 把它过滤掉。

### 真正根治（不可行）

- Win32 保留设备名属于内核层，无法在用户态"删除"。
- Git for Windows 上游目前仍未彻底修复该误报。
- 唯一"消除"方式：当 Git 扫描到保留设备名时**不**把它当作路径报告。
  跟踪上游 issue，等待修复。

## 调查脚本

`scripts/investigate_nul_artifact.py`（本调查的脚本化入口）：

```bash
python scripts/investigate_nul_artifact.py
```

输出 5 个命令的逐项结果，结构化便于 CI / 巡检。
