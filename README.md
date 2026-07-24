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

Schema v5 is current. Schema v2 introduced the durable submit/delete operation
journal; schema v3 added an independent trusted-workspace registry and
delete-operation-to-workspace bindings; schema v4 added renewable submit
ownership leases (lease timestamps stored and compared in UTC); schema v5
adds a `submit_activity_log` table that persists SubmitPage activity
across restarts. Recovery takes over only ownerless legacy submissions or
submissions whose lease has expired. The v2-to-v3 migration seeds
workspace trust only from live run rows and leaves old delete operations
unbound; journal payloads are never treated as trust anchors. Back up the
complete SQLite file set before first opening an older database with this
version. Completed journal entries are retained for seven days; incomplete
entries are never automatically pruned.

New runs persist their workspace as an absolute anchor. Delete preparation
must match that live anchor; legacy rows without one require manual cleanup.

On first access, legacy `run.json` and `manifest.tsv` files under the runs directory are imported once. Legacy files are retained as read-only recovery inputs; new runs do not create them. Import failures are recorded in the database and do not prevent valid runs from loading.

For backup, close JobDesk and copy `jobdesk.db` together with any `jobdesk.db-wal` and `jobdesk.db-shm` files that are present. To restore, replace that complete set while JobDesk is closed. Do not copy only the main database while the application is running. See [TROUBLESHOOTING.md § Rolling back a failed schema upgrade] for upgrade recovery.

An `uncertain` task means a remote submit command may have started but JobDesk cannot prove whether it was accepted. Inspect the scheduler or remote process before resolving it. Use `confirm-submitted` (and `--job-id <task_id>=<job_id>` when known) only after confirming the remote job exists. `abandon-submit` makes the task eligible for submission again and can create a duplicate remote job if the original actually started.

SSH/SFTP connections are owned by `SessionPool`. A lease is exclusive per server, callers must release it promptly, and application shutdown closes the pool after active leases return. GUI objects do not own or share raw sessions directly.

## Development

```powershell
python -m ruff check .
python -m mypy src
python -m pip install -e ".[dev,chem]"  # required for workflow tests
python -m pytest tests -q --basetemp .pytest_tmp_dev -p no:cacheprovider
python -m build --outdir .build_dev
```

Real SSH/SFTP and ConfFlow integration tests are skipped unless the documented environment variables are set. See `docs/CONFFLOW_WSL_SINGLE_RUN.md` for the controlled real-environment test shape.

## ConfFlow integration

The ConfFlow workflow engine is an **optional** dependency. JobDesk's GUI
loads and runs without it; the wizard, `WorkflowSpec`, and `--resume`
submitter branches become available only after `pip install -e ".[chem]"`
on the same Python that runs JobDesk, and after the matching ConfFlow
wheel is installed on the remote Linux compute node. The current JobDesk
contract is `confflow>=1.4.2,<2.0`; CI validates against the 1.4.2 wheel. Versions must
match between Windows and Linux because the GUI imports the same Pydantic models
(`confflow.core.models.GlobalConfigModel` / `CalcConfigModel`) that the
remote `confflow` binary consumes.

The cross-repository contract is the **CLI capability JSON** only:
JobDesk never imports ConfFlow's contract module. ConfFlow 1.4.2 emits
`schema_version=2` and an `artifacts` block that names the on-disk
files JobDesk is allowed to discover (the run summary, workflow stats,
and workflow state files). JobDesk's `MIN_VERSION` / `MAX_EXCLUSIVE`
The required artifact names are `run_summary.json`, `workflow_stats.json`, and `.workflow_state.json`.
in `jobdesk_app.core.confflow_contract` is the structured source of
truth for the producer window; pyproject, CI, and this README are
mirrors.

```powershell
# Windows (JobDesk side)
# If the package index does not provide the chemistry build, install the
# approved wheel first (see docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md):
# python -m pip install /path/to/confflow-1.4.2-py3-none-any.whl
python -m pip install -e ".[chem]"
```

```bash
# Linux compute node: install the same approved ConfFlow 1.4.2 wheel.
# The offline wheel workflow is documented in
# docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md.
python -m pip install /path/to/confflow-1.4.2-py3-none-any.whl
```

### Submit page (Phase 14)

The Submit page (second tab in the GUI shell) is the unified submit
UI. It bundles what used to be the ConfFlow wizard + the InputBuilder
dialog into one inline widget, and adds first-class "Use as input"
hooks from the Files page (right-click → "Use as input → Submit").

Layout (top to bottom):

1. **Input source panel** — Local / Remote tabs. Pick `.xyz` /
   `.gjf` / `.inp` files via drag-drop, "Add files…", or "Add
   directory…" (recursive checkbox).
2. **Mode tabs** —
   - **Build input file**: Gaussian / ORCA input file builder
     (preset dropdown, method / basis / keywords / nproc / memory).
   - **Build workflow**: full ConfFlow workflow (method / basis
     validation, step list, work_dir, advanced options, live YAML
     preview).
3. **Action row** — server pill, max-parallel spinbox, **Submit** /
   **Create tasks only** / **Refresh preview**.
4. **Live preview** — `.gjf` / `.inp` body or `workflow.yaml`.
5. **Activity log** — last 50 status messages, persisted to SQLite so
   they survive application restarts (schema v5).

Right-click on any row in the Files page's Local or Remote table
to push it to the Submit page as an input. The page is the single
entry point for "the user wants to submit this"; the page-level
worker callback (in `MainWindow`) handles uploads + the
`RunCoordinator.create_and_submit` call.

On accept the Submit page stages `workflow.yaml` and each input in a unique
remote submission namespace. Before launch, JobDesk requires the remote
ConfFlow capability schema 2 with a compatible `>=1.4.2,<2.0` version, the
declared `artifacts` block matching the consumer contract field-by-field, and
runs the exact per-task command with `--dry-run`. Only a successful preflight
may start the batch through the existing `nohup setsid` scheduler.

### SSH-disconnect resilience

`nohup` and ConfFlow resume solve different failures. `nohup` keeps an already
running process alive when the SSH control connection drops; an initial launch
does not use `--resume`. If a workflow process later stops or fails, an explicit
JobDesk retry reuses that run's original isolated namespace and adds exactly one
`--resume`, allowing ConfFlow to continue from its persisted state. The watcher
reconnects to `events.log` and synchronizes only the exact declared
workflow state and workflow stats paths (sourced from
`jobdesk_app.core.confflow_contract`).

### Auto-sync progress

`services/run_monitor.py` polls the remote `events.log` for `DONE` /
`RUNNING` lines, and additionally probes `workflow_stats.json` mtime once
per loop iteration. A change there fires a synthetic DoneEvent that
triggers an immediate refresh of the Runs page **Progress** column so step
progress (`done: confgen, preopt; current: opt`) updates between DONE
lines.

## Safety Notes

- Remote deletion is restricted to JobDesk-declared run directories and protected roots are rejected.
- Declared result paths are validated before download.
- Scheduler resource settings are validated before remote submission.
- Parsed scientific results are convenience signals only. They do not prove structural correctness, energy ordering, or scientific conclusions.

## License

JobDesk is licensed under the Apache License 2.0. See `LICENSE`.
