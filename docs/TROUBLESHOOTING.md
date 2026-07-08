# JobDesk Troubleshooting

## SQLite run database

Run state is stored in `%APPDATA%/JobDesk/runs/jobdesk.db` by default. The sibling `jobdesk.db-wal` and `jobdesk.db-shm` files are normal while JobDesk is open.

- Back up or restore the database only while the GUI and CLI are closed. Copy or replace all three files as one set when WAL/SHM files exist.
- Do not edit the database with a SQLite browser while JobDesk is running.
- Legacy `<runs-dir>/<run_id>/run.json` and `manifest.tsv` files are imported once and retained. They are recovery inputs, not writable state for new runs.
- If a legacy record is absent after migration, inspect the `migration_errors` table: `sqlite3 "%APPDATA%/JobDesk/runs/jobdesk.db" "select legacy_path, message from migration_errors;"`.
- Before manual repair, make a complete database backup. Removing `jobdesk.db` creates a new empty database on next launch; it is not a repair operation.

### Interrupted operations and `uncertain` tasks

Run `jobdesk run recover <workspace>` after an application or machine crash. Recovery replays durable submit/delete journal entries. It also quarantines legacy `submitting` rows that predate a matching journal as `uncertain`; merely opening the database does not perform this state change. Recovery is idempotent, so it is safe to invoke again after another interruption.

`uncertain` does not mean failed. It means the remote command may have started but no durable acceptance result is available. Check the scheduler queue/history or remote process first:

```powershell
jobdesk run confirm-submitted <workspace> <run_id> --tasks <task_id> --job-id <task_id>=<job_id>
```

If the remote job definitely does not exist, return the task to `uploaded` with:

```powershell
jobdesk run abandon-submit <workspace> <run_id> --tasks <task_id>
```

Do not abandon an unverified task: submitting it again can launch a duplicate remote calculation.

Schema v5 is current. Schema v2 introduced the submit/delete operation journal,
schema v3 added the trusted-workspace registry and independent delete-operation
workspace bindings, schema v4 added renewable submit ownership leases, and
schema v5 adds a `submit_activity_log` table for persisting SubmitPage
activity. Lease timestamps use UTC; recovery skips a live lease and may acquire
only an ownerless legacy operation or an expired lease. Before upgrading from
an older JobDesk version, close all JobDesk processes and copy `jobdesk.db`,
`jobdesk.db-wal`, and `jobdesk.db-shm` as one backup set. Completed operations
are retained for seven days during recovery cleanup; incomplete operations are
retained until successfully replayed.

### Rolling back a failed schema upgrade

JobDesk upgrades the database in place from schema v4 to v5 on first open.
If a migration fails (e.g. disk full, antivirus lock, schema corruption),
JobDesk aborts startup and leaves the database untouched. To roll back:

1. Close all JobDesk processes.
2. Restore the backup set: `jobdesk.db`, `jobdesk.db-wal`, `jobdesk.db-shm`
   (all three if present, into `%APPDATA%/JobDesk/runs/`).
3. Reinstall the previous JobDesk version.
4. Verify with:

```powershell
jobdesk run list <workspace>
```

It should return runs from before the upgrade.

Do not manually edit schema columns. If the backup set is incomplete
(only `jobdesk.db` without `-wal`/`-shm`), the database may be inconsistent
and the rollback will fail; in that case the v2/v3 legacy import path
(`run.json` + `manifest.tsv`) still contains the source-of-truth and
JobDesk can reimport it into a fresh database.

To capture a snapshot before a forced migration:

1. Stop the GUI.
2. Run `jobdesk run list <workspace>` on a separate process; this opens the
   DB read-only and forces a clean checkpoint.
3. Copy the three-file set to a dated backup directory.

SSH and SFTP clients are owned by `SessionPool`, not by GUI pages. Each per-server lease is serialized and must be released; shutdown prevents new leases and closes sessions after active work returns.

## SSH 连接失败

检查：
- Settings 页服务器配置（host, port, username, key_path）
- SSH 密钥文件是否存在且权限正确
- 服务器网络是否可达

验证：
```powershell
pytest tests/integration/test_real_ssh.py -v
```

## SFTP 上传失败

检查：
- 远端目录是否有写权限
- 路径是否使用 POSIX `/` 格式
- 本地文件是否存在

## 提交后无反应

确认：
- Files 页已连接到正确的服务器
- 选择了远端文件
- 命令模板正确（如 `g16 {name}`）

## 任务一直显示"运行中"

手动检查远端状态文件：
```text
<remote_dir>/.jobdesk_runs/<run_id>/<task_id>/.jobdesk_status
<remote_dir>/.jobdesk_runs/<run_id>/<task_id>/.jobdesk_exit_code
<remote_dir>/.jobdesk_runs/<run_id>/<task_id>/.jobdesk_submit.log
```

可能原因：
- 任务仍在运行（正常）
- 任务崩溃但未写 status 文件 → 右键"刷新状态"手动检测
- SSH monitor 断连 → 重启应用或手动刷新

## 下载失败

检查：
- Settings 页下载模式配置（Gaussian: `*.log,*.chk`）
- 远端是否生成了对应输出文件
- 本地 workspace 目录是否有写权限

状态保持 `remote_completed` 时可再次右键刷新重试。

## 结果分析为空

确认：
- 文件已下载到 `<workspace>/results/<run_id>/<task_id>/`
- 文件内容包含可识别的能量行（如 `SCF Done` 或 `HF=`）

## 应用关闭时卡住

正常退出应在 1-2 秒内完成。如果卡住：
- 可能是后台 SSH 操作未完成
- 强制关闭不会丢失数据（manifest 是原子写入的）

## Monitor 不自动更新状态

确认：
- Run 处于 `running` 或 `submitted` 状态
- 已切换到 Runs 页（monitor 在页面激活时启动）
- 服务器 SSH 连接正常
- 新提交的任务使用了最新版本的 run 脚本（旧 run 不会写 events.log）
