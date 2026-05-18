# JobDesk v1.0 RC Checklist

This checklist freezes the v1.0 release candidate scope around the tested local
workbench lifecycle.

## Scope

In scope:

- Windows 11 local GUI and CLI workbench.
- SSH/SFTP direct remote execution on Linux servers.
- `project.yaml` with `task_discoveries`, `execution_profiles`, upload rules,
  shared files, download patterns, and regex extraction.
- Machine-local `servers.yaml` and `runtime_bindings.yaml`.
- Batch lifecycle:
  `scan -> create_batch -> upload -> submit -> refresh -> download -> analyze`.
- Mixed-profile batches and shared files.
- Batch recovery from `.jobdesk/batches/<batch_id>`.

Out of scope for v1.0:

- Slurm/PBS adapters.
- Workflow DAGs.
- Program-specific Gaussian/ORCA/xTB semantics.
- SQLite storage.
- Remote cleanup UI.

## Required Local Checks

Run from the repository root:

```powershell
pytest -q --basetemp .\tmp_pytest_full
```

Expected:

```text
369 passed, 5 skipped
```

The skip count is expected when real integration environment variables are not
set globally.

## Required Real Backend Checks

Set the real test environment:

```powershell
New-Item -ItemType Directory -Force C:\tmp\jobdesk_temp | Out-Null
$env:TEMP = "C:\tmp\jobdesk_temp"
$env:TMP = "C:\tmp\jobdesk_temp"
$env:JOBDESK_TEST_SERVERS_YAML = "C:\Users\moxue\AppData\Roaming\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "814new"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
```

Run:

```powershell
pytest tests\integration\test_real_ssh.py `
       tests\integration\test_real_sftp.py `
       tests\integration\test_real_submitter.py `
       tests\integration\test_real_lifecycle.py `
       -q --basetemp C:\tmp\jobdesk_pytest_real_all
```

Expected:

```text
5 passed
```

## Manual GUI Smoke

Use the GUI against the configured `814new` server:

1. Start JobDesk with `scripts/jobdesk_gui.ps1` or `jobdesk-gui`.
2. Open or create a project.
3. Confirm Projects page shows `project_id`, execution profiles, and binding
   status.
4. Run Tasks page `Preflight`.
5. Run `Scan Inputs`.
6. Create a batch.
7. Upload.
8. Submit.
9. Refresh until tasks are completed.
10. Download.
11. Analyze.
12. Reopen the GUI and confirm the latest batch is selected.
13. Open Results page and inspect `enriched_results`, `job_status.tsv`, and
    `failures.tsv`.

## RC Blockers

Treat these as release blockers:

- Manifest or batch metadata corruption.
- Frozen `server_id` / `remote_work_dir` ignored after batch creation.
- Upload/download failures preventing other tasks from continuing.
- Submit re-runs that duplicate already submitted tasks.
- GUI cannot recover the latest batch after reopen.
- Real lifecycle integration fails on `814new`.

## Known Non-Blockers

- `.pytest_cache` permission warnings in the current workspace.
- Real integration tests skip unless environment variables are set.
- No PyInstaller executable yet; launcher script and entry points are enough for
  this RC.
