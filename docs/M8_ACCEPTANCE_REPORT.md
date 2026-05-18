# JobDesk M8 真实验收报告

> 日期：2026-05-11  
> 版本：v0.2.0-dev  
> 状态：**M8 真实验收未完成 — 缺少真实 Linux 服务器**

---

## 1. 测试环境

| 项目 | 值 |
|------|-----|
| Windows 版本 | Windows 11 |
| Python 版本 | 3.13.13 |
| PySide6 | 已安装可导入 |
| paramiko | 已安装 |
| SSH 环境变量 | **未配置** |
| servers.yaml | **不存在**（`%APPDATA%/JobDesk/servers.yaml`） |
| 真实 Linux 服务器 | **未连接** |

## 2. 阻塞项

以下三项全部缺失，导致真实验收无法进行：

| 阻塞项 | 状态 | 说明 |
|--------|------|------|
| `JOBDESK_TEST_SSH_SERVER_ID` | 未设置 | 需设置为 servers.yaml 中的 server_id |
| `JOBDESK_TEST_SERVERS_YAML` | 未设置 | 需指向含真实服务器信息的 servers.yaml |
| `JOBDESK_TEST_REMOTE_TMP_DIR` | 未设置 | 需指向远程可写临时目录，如 `/tmp/jobdesk_test` |
| `%APPDATA%/JobDesk/servers.yaml` | 不存在 | GUI 的 Servers 页和 Open Project 需要此文件 |

## 3. 已通过的项目（mock/fixture 层面）

### 3.1 pytest

**247 passed, 4 skipped, 0 failed**

| 模块 | 测试数 | 状态 |
|------|--------|------|
| config (schema/loader/servers) | 29 | ✅ |
| core (lifecycle/models/batch/manifest/template) | 36 | ✅ |
| analyzer/grouping/outputs | 29 | ✅ |
| SSH mock | 13 | ✅ |
| server_info mock | 7 | ✅ |
| remote_status mock | 7 | ✅ |
| SFTP mock | 30 | ✅ |
| submitter mock | 30 | ✅ |
| status_refresh mock | 17 | ✅ |
| overwrite/dryrun | 11 | ✅ |
| services (project/batch/workflow) | 20 | ✅ |
| GUI imports/state | 10 | ✅ |
| 真实集成测试 | 4 | **skip** |

### 3.2 GUI 模块

- 全部 4 页面 + MainWindow + Worker + Session 可正常 import
- 10 个 GUI import/state 测试通过
- 用户已手工确认窗口实际可打开

### 3.3 fake 项目配置

`fake_project_real/` 已创建，包含：
- 5 个任务（grpA_001, grpA_002, grpB_001, grpB_fail, grpB_002）
- 2 个 group（grpA, grpB）
- 1 个失败任务（grpB_fail: exit 1）
- max_parallel = 2
- 每个任务 4-6 秒
- extract: energy (float)
- download patterns: result.out, task.log

## 4. 集成测试结果

| 测试文件 | 结果 | 原因 |
|----------|------|------|
| `tests/integration/test_real_ssh.py` | **SKIPPED** | 环境变量未设置 |
| `tests/integration/test_real_sftp.py` | **SKIPPED** | 环境变量未设置 |
| `tests/integration/test_real_submitter.py` | **SKIPPED** | 环境变量未设置 |

### 如何启用集成测试（需用户手动操作）

```powershell
# 1. 创建 servers.yaml
New-Item -ItemType Directory -Force -Path "$env:APPDATA\JobDesk"
# 编辑 %APPDATA%/JobDesk/servers.yaml

# 2. 设置环境变量
$env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "your_server_id"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"

# 3. 运行集成测试
pytest tests/integration/ -v

# 4. 运行完整测试套件
pytest tests/ -v
```

## 5. 待真实验证的场景（需真实服务器）

| # | 场景 | mock 层状态 | 需真实验证 |
|---|------|-----------|----------|
| 1 | 真实 SSH 连接与 echo | ✅ mock tests | 需服务器 |
| 2 | 真实 SFTP 上传下载 roundtrip | ✅ mock tests | 需服务器 |
| 3 | 真实远程提交与 nohup 启动 | ✅ mock tests | 需服务器 |
| 4 | 真实 max_parallel=2 限制并行 | ✅ batch_control.sh 生成正确 | 需观察远程进程 |
| 5 | GUI 关闭后后台继续 | ✅ nohup + xargs -P 设计 | 需关闭 GUI 后检查进程 |
| 6 | 真实状态恢复（Refresh） | ✅ mock tests | 需读取远程标记文件 |
| 7 | 真实手动下载 | ✅ mock tests | 需 SFTP 拉取文件 |
| 8 | 真实本地分析 | ✅ fixture tests | 需下载后执行 |
| 9 | 真实失败任务记录 | ✅ mock tests | 需远程任务 exit 1 |
| 10 | GUI 重启恢复 | ✅ Manifest 持久化 | 需重启 GUI 验证 |

## 6. 发现的问题与修复

无。本轮未能进行真实验收，未暴露需要修复的问题。

## 7. 从 fake_project 配置暴露出的小问题

`fake_project_real/project.yaml` 中 `submit.command: "bash {input_name}"` 要求每个 .in 文件是可执行的 bash 脚本。这需要：
- 上传后远程执行 `chmod +x`（当前 JobSubmitter 只 chmod 内部控制脚本）
- 或者将 command 改为 `bash {input_name}` 方式执行（已采用）

## 8. 手动验收流程待执行清单

当真实服务器就绪后，按以下步骤操作：

```powershell
# 1. 编辑 project.yaml，替换 %%SERVER_ID%% 和 %%REMOTE_WORK_DIR%%
# 2. 启动 GUI
python -m jobdesk_app.gui.app

# 3. GUI 操作序列：
#    Projects → Open Project → 选 fake_project_real
#    Tasks → Scan Inputs → Create Batch
#    Dry-run Upload → Upload
#    Dry-run Submit → Submit
#    **关闭 GUI**（在任务完成前）
#    等待 30 秒
#    重新启动 GUI
#    Projects → Open Project → 选 fake_project_real
#    Tasks → 选原 Batch → Refresh
#    检查任务状态变化
#    **不点 Download，确认本地无结果文件**
#    Download
#    **确认结果文件出现**
#    Analyze
#    Results → 查看各表

# 4. 运行集成测试
$env:JOBDESK_TEST_SERVERS_YAML = "..."
$env:JOBDESK_TEST_SSH_SERVER_ID = "..."
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "..."
pytest tests/integration/ -v
```

## 9. 最终判定

**M8 真实验收未完成。**

原因：缺少真实可 SSH 的 Linux 服务器，以下必要项目全部无法执行：

1. `JOBDESK_TEST_SSH_SERVER_ID` 未设置
2. `JOBDESK_TEST_SERVERS_YAML` 未设置
3. `JOBDESK_TEST_REMOTE_TMP_DIR` 未设置
4. `%APPDATA%/JobDesk/servers.yaml` 不存在
5. 4 个真实集成测试全部 skip

Mock/fixture 层覆盖率：247 tests passed（涵盖所有纯逻辑路径）。
真实端到端验证：**未执行**。
