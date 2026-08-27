# JobDesk

JobDesk is a Windows-first desktop and CLI tool for managing single scientific-computing jobs over SSH/SFTP. It helps prepare Gaussian and ORCA inputs, submit jobs to a remote machine or local WSL environment, monitor status, download outputs, and preview parsed results.

JobDesk is currently a preview project with an isolated `0.7.7` source
candidate. The released JobDesk package is `v0.7.5`; the failed immutable
`v0.7.6` tag did not create a release. The current published
producer is ConfFlow `v2.1.6`, which this consumer candidate binds exactly.
Publication does not by itself promote either package to production.

## Documentation and identity status

The phase notes, compatibility records, and remediation evidence under `docs/`
are historical records. In particular, `docs/PHASE*.md`, the compatibility
records, and older planning/evidence files may describe
superseded backends or compatibility periods. Keep their counters, hashes,
commands, and acceptance facts unchanged; they are not current release,
endpoint, or promotion status. Current product boundaries are maintained in
this file and [docs/architecture.md](docs/architecture.md).

The four identities below are deliberately separate (recorded during the
remediation and release closeout; revalidate the live endpoint before any
acceptance or promotion action):

| Identity | Recorded value | Meaning |
|---|---|---|
| Shared source trees | JobDesk `C:\dft\tool\jobdesk` (`codex/gui-ux-remediation`, `154ee77b065cd71787418be312700c996bf01c57`); ConfFlow `/opt/ConfFlow` (`main`, `c6a4263bf3ec84669fd5279ec336b10ab2e18c9f`) | Shared/dirty development sources, not runtime identity |
| Isolated implementation candidate | JobDesk `0.7.7` release-runtime fix based on failed immutable tag `v0.7.6`; no producer source change | Candidate code under review; no release or endpoint switch |
| Released package evidence | JobDesk `v0.7.5` at merge `df8cd1c42cb423456ae4677d6964e1ec832bbfcc` (wheel SHA-256 `cf10c91843ed59a4fe41fe9d44f71cdb5ef033ca6a375f1598ffe1837164d3fa`); current producer ConfFlow `v2.1.6` at merge `45bfac11f721b2152eeff5ee26e50463fcc6f657` (wheel SHA-256 `d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548`) | Published immutable artifacts; neither publication is a production switch |
| Configured production executable | `wsl` `/usr/local/bin/confflow` → currently observed `/opt/ConfFlow/.venv/bin/confflow`, reporting ConfFlow `2.0.0` | Protected runtime identity; the released `2.1.6` executable has not been promoted |

The `0.7.7` value above identifies an isolated source candidate only. The
published `v0.7.5` and current producer `v2.1.6`,
and the configured production executable remain separate identities;
production is still on ConfFlow `2.0.0` until a separately authorized
endpoint switch and post-switch smoke pass.

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
    wsl_distro: Ubuntu-24.04
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

JobDesk stores its local run/task state in `%APPDATA%/JobDesk/runs/jobdesk.db` by default using SQLite. WAL mode and transactional updates allow the GUI and CLI to share local state without rewriting manifest files. This database is not ConfFlow's remote state store.

Schema v8 is current. Schema v2 introduced the durable submit/delete operation
journal; schema v3 added an independent trusted-workspace registry and
delete-operation-to-workspace bindings; schema v4 added renewable submit
ownership leases (lease timestamps stored and compared in UTC); schema v5
adds a `submit_activity_log` table that persists SubmitPage activity; schema
v6 adds the `run_provenance` table for ConfFlow producer identity across
restarts; schema v7 adds immutable accepted-configuration bindings for
workflow runs; and schema v8 adds the explicit selected server identity.
The ConfFlow control decision is authoritative in the SQLite `operations`
journal (kind `confflow_control`); `control_backend.json` is only a
rollback-compatible projection that can be regenerated and is never a second
authority. Recovery takes over only ownerless legacy submissions or
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

For ordinary SSH/SFTP work, `SessionPool` owns one reusable session per server. Each short-lived lease is exclusive, may request SSH-only or SSH+SFTP (`need_sftp=False/True`), and must be released promptly; application shutdown closes the pool after active leases return. The long-lived `RunMonitor` watcher has a separate monitor transport owner and must not borrow a `SessionPool` lease. GUI objects receive application snapshots/events rather than owning pooled sessions.

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
loads and runs without it. A base install can open and preserve workflow
documents and perform advisory structural lint; the optional `chem` extra
enables producer-model authoring conveniences and the local dry-run path. The
remote compute node still needs the configured producer executable. The current
JobDesk contract is `confflow>=2.0,<3.0`; CI and the published pair validate
against ConfFlow `2.1.6`; the historical `2.1.3` pair remains archived.
Versions do not need to match between Windows and
Linux through shared Pydantic imports: JobDesk resolves the configured
executable's producer-owned workflow configuration contract and sends the exact
YAML bytes to its canonical validator. The configured production executable
remains ConfFlow `2.0.0` until separately promoted.
The Phase F owner exception removed the legacy backend from the production
path. The provenance-verified v1.4.6 rollback remains historical evidence only
and never authorizes a current control run.

The cross-repository runtime contracts are the **CLI capability JSON**, the
producer-owned configuration-contract/validation JSON, and the declared
control/artifact schemas. JobDesk never imports ConfFlow's contract module.
The current capability payload uses `schema_version=4` and carries
producer/executable provenance plus an artifacts block naming the six on-disk
files JobDesk may discover: `run_summary.json`, `workflow_stats.json`,
`.workflow_state.json`, `output_manifest.json`, `{basename}.txt`, and
`{basename}min.xyz`. JobDesk first validates `output_manifest.json` and then
accepts only the relative paths it declares.

For workflow configuration, `application/configuration_contract.py` holds the
typed admission value objects; `remote/confflow_config_contract.py` parses the
frozen producer response ABI; and
`services/ssh_configuration_contract_client.py` runs `config contract --json`
and `config validate --json --stdin` on the configured executable. The
checked-in `resources/config_contracts/stable_2_0_0.py` is an exact fallback
only for the approved stable producer when the remote contract command is
unavailable. Local `core/workflow_document.py`, `workflow_codec.py`,
`workflow_mapping.py`, and `workflow_schema_lint.py` preserve/edit documents
and provide advisory structural checks; they do not replace remote semantic
validation.

The required remote commands are `bash`, `nohup`, `setsid`, `xargs`,
`sha256sum`, `mktemp`, and `base64`; the build, producer, and executable blocks
report commit, wheel, interpreter, and executable provenance.
JobDesk's `MIN_VERSION` / `MAX_EXCLUSIVE` in
`jobdesk_app.core.confflow_contract` is the structured source of truth for the
producer window; pyproject, CI, and this README are mirrors.

```powershell
# Windows (JobDesk side)
# If the package index does not provide the chemistry build, install the
# approved wheel first (see docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md):
# python -m pip install /path/to/confflow-2.1.6-py3-none-any.whl
python -m pip install -e ".[chem]"
```

```bash
# Linux compute node: install the same approved ConfFlow 2.1.6 wheel.
# The offline wheel workflow is documented in
# docs/CONFFLOW_1_4_2_WHEEL_DEPLOYMENT.md.
python -m pip install /path/to/confflow-2.1.6-py3-none-any.whl
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
   they survive application restarts (schema v8).

Right-click on any row in the Files page's Local or Remote table
to push it to the Submit page as an input. The page is the single
entry point for "the user wants to submit this"; the page-level
worker callback (in `MainWindow`) handles uploads + the
`RunCoordinator.create_and_submit` call.

On accept the Submit page stages `workflow.yaml` and each input in a unique
remote submission namespace. Before launch, JobDesk requires the remote
ConfFlow capability schema 4 with a compatible `>=2.0,<3.0` version, resolves
and rechecks the producer-owned configuration contract, validates the exact
configuration bytes remotely, requires the declared `artifacts` block to
match field-by-field, and runs the exact per-task command with `--dry-run`.
The accepted contract, configuration digest, producer provenance, and
server/executable identities are bound immutably to the workflow run in
schema v7/v8 (`run_configuration_bindings`; v8 adds the selected server
identity).
Only a successful admission and preflight may start the batch through the
existing `nohup setsid` scheduler.

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

`services/run_monitor.py` owns a bounded, long-lived watcher transport and
polls the remote `events.log` for `DONE` / `RUNNING` lines. It also probes the
declared workflow state/statistics paths by SHA-256 content digest (mtime-only
changes are ignored). A content or presence change fires a synthetic
`DoneEvent` that triggers an immediate refresh of the Runs page **Progress**
column so step progress (`done: confgen, preopt; current: opt`) updates
between DONE lines.

## Safety Notes

- Remote deletion is restricted to JobDesk-declared run directories and protected roots are rejected.
- Declared result paths are validated before download.
- Scheduler resource settings are validated before remote submission.
- Parsed scientific results are convenience signals only. They do not prove structural correctness, energy ordering, or scientific conclusions.

## License

JobDesk is licensed under the Apache License 2.0. See `LICENSE`.
