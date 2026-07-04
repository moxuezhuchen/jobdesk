# JobDesk

JobDesk is a Windows-first desktop and CLI tool for managing single scientific-computing jobs over SSH/SFTP. It helps prepare Gaussian and ORCA inputs, submit jobs to a remote machine or local WSL environment, monitor status, download outputs, and preview parsed results.

JobDesk is currently a preview project. It is suitable for source review and controlled local use, but not yet a stable public package release.

## Scope

- Submit, monitor, cancel, refresh, download, and retry single-task Gaussian/ORCA runs.
- Submit one or more `.xyz` inputs through the ConfFlow integration and display per-molecule execution summaries.
- Manage remote files through SSH/SFTP with guarded deletion boundaries.
- Keep multi-step workflow orchestration outside JobDesk's public user interface.

## Requirements

- Windows 11
- Python 3.11 or newer
- SSH access to a configured remote machine or WSL environment

## Install From Source

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
jobdesk-gui
```

## Server Configuration

JobDesk stores server configuration in `%APPDATA%\JobDesk\servers.yaml` by default.

```yaml
servers:
  wsl:
    display_name: WSL Local
    host: 127.0.0.1
    port: 22
    username: root
    auth_method: key
    key_path: C:/Users/me/.ssh/id_rsa
    wsl_distro: Ubuntu
    trust_on_first_use: false
    env_init_scripts: []
    ssh_access:
      config_alias: wsl
      proxy_command: ""
      proxy_jump: ""
    external_tools:
      terminal_provider: windows_terminal
      ssh_alias: wsl
      putty_session: ""
      terminal_path: ""
    scheduler:
      type: nohup
      default_cpus: 4
      default_memory_mb: 4096
      default_walltime_minutes: 60
```

Unknown SSH host keys are rejected by default. Enable `trust_on_first_use` only for a trusted first connection, then disable it after the host key has been saved.

JobDesk does not store SSH passwords and does not pass passwords on command lines. Use key-based authentication or an external SSH configuration.

## CLI Examples

```powershell
jobdesk files list-remote <server_id> <remote_path>
jobdesk files upload <server_id> <local_path> <remote_path>
jobdesk files download <server_id> <remote_path> <local_path>
jobdesk files preview <server_id> <remote_path>

jobdesk run create <workspace> --server <id> --remote-dir <path> --command "g16 {name}" --files <f1> <f2>
jobdesk run submit <workspace> <run_id>
jobdesk run refresh <workspace> <run_id>
jobdesk run download <workspace> <run_id> --patterns "*.log" "*.out"
jobdesk run cancel <workspace> <run_id>
jobdesk run retry <workspace> <run_id>
jobdesk run recover <workspace>
jobdesk run confirm-submitted <workspace> <run_id> --tasks <task_id> --job-id <task_id>=<job_id>
jobdesk run abandon-submit <workspace> <run_id> --tasks <task_id>
```

## Run Database

JobDesk stores run and task state in `%APPDATA%/JobDesk/runs/jobdesk.db` by default using SQLite. WAL mode and transactional updates allow the GUI and CLI to share state without rewriting manifest files.

Schema v4 is current. Schema v2 introduced the durable submit/delete operation
journal; schema v3 added an independent trusted-workspace registry and
delete-operation-to-workspace bindings; schema v4 adds renewable submit
ownership leases. Lease timestamps are stored and compared in UTC,
and recovery takes over only ownerless legacy submissions or submissions whose
lease has expired. The v2-to-v3 migration seeds workspace trust only from live
run rows and leaves old delete operations unbound; journal payloads are never
treated as trust anchors. Back up the complete SQLite file set before first
opening an older database with this version. Completed journal entries are
retained for seven days; incomplete entries are never automatically pruned.

New runs persist their workspace as an absolute anchor. Delete preparation
must match that live anchor; legacy rows without one require manual cleanup.

On first access, legacy `run.json` and `manifest.tsv` files under the runs directory are imported once. Legacy files are retained as read-only recovery inputs; new runs do not create them. Import failures are recorded in the database and do not prevent valid runs from loading.

For backup, close JobDesk and copy `jobdesk.db` together with any `jobdesk.db-wal` and `jobdesk.db-shm` files that are present. To restore, replace that complete set while JobDesk is closed. Do not copy only the main database while the application is running.

An `uncertain` task means a remote submit command may have started but JobDesk cannot prove whether it was accepted. Inspect the scheduler or remote process before resolving it. Use `confirm-submitted` (and `--job-id <task_id>=<job_id>` when known) only after confirming the remote job exists. `abandon-submit` makes the task eligible for submission again and can create a duplicate remote job if the original actually started.

SSH/SFTP connections are owned by `SessionPool`. A lease is exclusive per server, callers must release it promptly, and application shutdown closes the pool after active leases return. GUI objects do not own or share raw sessions directly.

## Development

```powershell
python -m ruff check .
python -m mypy src
python -m pytest tests -q --basetemp .pytest_tmp_dev -p no:cacheprovider
python -m build --outdir .build_dev
```

Real SSH/SFTP and ConfFlow integration tests are skipped unless the documented environment variables are set. See `docs/CONFFLOW_WSL_SINGLE_RUN.md` for the controlled real-environment test shape.

## Safety Notes

- Remote deletion is restricted to JobDesk-declared run directories and protected roots are rejected.
- Declared result paths are validated before download.
- Scheduler resource settings are validated before remote submission.
- Parsed scientific results are convenience signals only. They do not prove structural correctness, energy ordering, or scientific conclusions.

## License

JobDesk is licensed under the Apache License 2.0. See `LICENSE`.
