# JobDesk 架构决策记录

> 本文档记录 JobDesk 项目的所有已确认决策，作为后续实现基线。  
> 最后更新：2026-05-11

---

## 已确认决策

### 决策 1：Windows 11 本地主程序

JobDesk 主程序运行在 Windows 11 本地。本地保存项目配置、输入文件、结果和 GUI。Python 运行环境为原生 Windows，不依赖 WSL。

### 决策 2：远程 Linux 服务器只是执行端

远程 Linux 服务器仅作为计算执行端。所有控制逻辑、状态管理、结果分析均在本地完成。服务器上不要求安装 JobDesk 软件。

### 决策 3：不按计算程序类型设计

JobDesk 不内置 Gaussian / ORCA / xTB 等特定程序的专用模块。所有程序差异通过 YAML 配置文件表达。schema 中不硬编码任何程序名称或专用字段。

### 决策 4：默认直接提交命令

用户配置中的 `command` 字段直接作为远程执行命令。不强制要求用户创建 `run.sh` 包装脚本。对于简单任务，直接写 `g16 {input_name}` 即可。

### 决策 5：服务器无常驻 agent

远程服务器上不安装任何 JobDesk 守护进程、systemd 服务或常驻 agent。所有远程操作通过 SSH / SFTP 一次性完成。后台并行通过 `nohup` + `xargs -P` 等标准工具实现。

### 决策 6：max_parallel 采用方案 B

一次提交整批任务，服务器端使用一次性后台批处理过程，始终维持最多 N 个并行任务。已入队的任务在 GUI 关闭或 SSH 断开后仍继续自动补位并跑完。

### 决策 7：当前 Batch 内任务后台自动补位并跑完

已入队的当前 Batch 任务，GUI 关闭或 SSH 断开后后台批处理继续自动补位直至所有任务完成。这不意味着服务器安装了常驻执行器。

### 决策 8：GUI 关闭后不自动接纳新任务

GUI 关闭后新出现的、后来新增的、尚未进入当前 Batch 的任务不会被自动接纳或提交。仅已入队任务继续。

### 决策 9：输出下载手动触发

v0.2 中刷新状态不会自动下载输出文件。用户通过"下载"按钮手动拉回结果。避免大文件在用户只想查看状态时意外占用带宽和磁盘。

### 决策 10：重跑失败任务创建新 Batch

失败任务重跑默认创建新 Batch，保留原始失败批次与失败记录。v0.2 不把"同一 Batch 内覆盖重跑"作为默认行为。

### 决策 11：servers.yaml 为全局用户级配置

服务器配置是用户级资源，不属于单个项目。推荐存放位置为 `%APPDATA%\JobDesk\servers.yaml`。`project.yaml` 只引用 `server_id`，不保存服务器连接信息。

### 决策 12：GUI 使用 PySide6

采用 Python + PySide6 实现 GUI，适合表格、树、日志、进度条等桌面应用需求。与 Python 后端共享代码最直接。v0.2 不引入 Web 前端或 Electron / Tauri。

### 决策 13：Manifest 为 Batch 权威清单

Manifest 记录一次提交的明确事实：哪些任务属于本 Batch、每个任务的输入路径、远程目录、执行命令、当前状态。所有后续流程（上传、提交、下载、恢复、分析、重跑）都应消费 Manifest。

### 决策 14：Batch 自身结果文件为权威源

每个 Batch 自己目录下的 `manifest.tsv`、`batch.json`、`failures.tsv`、`final_results.tsv`、`group_summary.tsv` 为该 Batch 的权威源。`results/aggregate/` 下的全局合并表仅为派生聚合视图，不可作为恢复或历史权威源。

### 决策 15：v0.2 默认生成轻量任务状态标记文件

JobDesk 在提交任务时自动生成包装脚本 `.jobdesk_run.sh`，并在任务执行过程中写入 `.jobdesk_status`、`.jobdesk_exit_code`、`.jobdesk_submit.log` 等轻量标记文件，提升状态判定的可靠性。

---

## 相关文档

- [JOBDESK_PLAN.md](JOBDESK_PLAN.md) — 完整项目规划文档
