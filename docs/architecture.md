# JobDesk Architecture

A high-level map of the codebase. The intended audience is a new
contributor trying to locate where to make a change.

## Layers

```
                    ┌──────────────────────────────────────────┐
                    │   CLI (cli.py, cli_prep.py)              │
                    │   GUI (gui/app.py → main_window)         │
                    └──────────────────┬───────────────────────┘
                                       │ uses
                    ┌──────────────────▼───────────────────────┐
                    │   Application services                   │
                    │   (services/run_coordinator,             │
                    │    services/run_service,                 │
                    │    services/file_transfer_service, ...)  │
                    └──────────────────┬───────────────────────┘
                                       │ uses
                    ┌──────────────────▼───────────────────────┐
                    │   Domain core                            │
                    │   (core/run, core/submit,                │
                    │    core/manifest, core/parsers/, ...)    │
                    └──────────────────┬───────────────────────┘
                                       │ uses
                    ┌──────────────────▼───────────────────────┐
                    │   Remote I/O                             │
                    │   (remote/ssh, remote/sftp,              │
                    │    remote/scheduler, remote/submitter)   │
                    └──────────────────────────────────────────┘
```

The GUI never talks directly to `remote/`; everything routes through
`services/run_coordinator.RunCoordinator`, which is the only place
that holds session leases via `SessionPool`.

## 3-page GUI shell

`gui/main_window.py` wires a `QStackedWidget` of three pages:

| Page | Module | Role |
|---|---|---|
| Files | `gui/pages/file_transfer_page.py` | SSH/SFTP browser + per-row "Run" action |
| Runs & Results | `gui/pages/runs_results_page.py` | Run list, per-task status, parsed preview, ResultDetailPane |
| Settings | `gui/pages/settings_servers_page.py` | `servers.yaml` editor + GUI preferences |

A user can open the ConfFlow wizard from the Runs page (`Run ConfFlow`
button → `gui/dialogs/confflow_wizard_dialog.py`). The wizard is a
self-contained three-step `QWizard`:

```
XyzPage      ─►  CalcPage     ─►  WorkflowPage   ─►  accept()
  add files       fill fields       pick steps
                                       + YAML preview
```

## Run lifecycle

The lifecycle of a single run is owned by `RunService` and
`RunCoordinator`. The CLI and GUI both call into them and never
touch the database directly.

```
create     →  submit  →   running   →  download  →   analyzed
  │           │            │              │              │
  ▼           ▼            ▼              ▼              ▼
 DB row     submitter    RunMonitor     SFTP pull     core/parsers/
 + manifest  → remote     polls events  files         Gaussian/ORCA
 + lease      nohup       .log +         → results/   → analysis.tsv
              setsid      workflow_      <task_id>/   + detail pane
                          stats.json     + manifest                render
                                          update
```

A failed submit, dropped SSH session, or external scheduler failure
ends up as one of `uncertain` / `failed` / `cancelled` in the task
state. The `confirm-submitted` and `abandon-submit` CLI / GUI actions
resolve `uncertain` tasks explicitly; recovery cannot silently take
over a lease (Schema v4).

## SQLite architecture

`%APPDATA%/JobDesk/runs/jobdesk.db` is the single source of truth
for runs and tasks. WAL mode allows concurrent reads from CLI and
GUI without manifest rewrites.

Schemas:

- **v1** — original per-task-only store
- **v2** — added the submit / delete operation journal
- **v3** — added trusted-workspace registry and delete-op-to-workspace bindings
- **v4** — added UTC submit-ownership leases (current)

`services/run_repository/` is split into `_schema`, `_paths`,
`_workspaces`, `_leases`, `_submit`, `_delete`, `_tasks`, `_runs`,
`_operations`, `_legacy`, `_operations_types`. The split is purely
organisational; all reads / writes still flow through
`RunRepository` (the package's `__init__.py`).

## ConfFlow integration

The wizard is optional; JobDesk works without it. When installed
(`pip install -e ".[chem]"`), the wizard produces a `WorkflowSpec`
that round-trips through ConfFlow's Pydantic models. On accept:

1. `workflow.yaml` is written next to the first XYZ input.
2. Both files are uploaded to the configured remote.
3. A `nohup setsid confflow … --resume` batch is submitted through
   the existing scheduler.

The wizard and the remote `confflow` binary must import the same
Pydantic model version (`pyproject.toml` pins this).

A ConfFlow run is observed via `services/run_monitor.py` polling the
remote `events.log` (DONE / RUNNING) **and** probing
`workflow_stats.json` mtime once per iteration. The latter fires a
synthetic DoneEvent so the Runs page Progress column updates
between DONE lines.

## Where to make changes

| You want to… | Start here |
|---|---|
| Add a CLI subcommand | `src/jobdesk_app/cli.py` + `services/run_coordinator.py` |
| Add a page / tab | `gui/main_window.py` (stacked widget) + `gui/pages/<name>_page.py` |
| Add a wizard step | `gui/dialogs/confflow_wizard_dialog.py` (mirror `_XyzPage` / `_CalcPage` / `_WorkflowPage` pattern) |
| Tweak parser output | `core/parsers/{gaussian,orca}.py` + add a test in `tests/test_parsers.py` |
| Add a column to the runs-results table | `gui/pages/runs_results_page.py` + `_analysis_row` helper |
| Change the SQLite schema | `services/run_repository/_schema.py` + add a migration in `_legacy.py` |
| Add a new server-side scheduler | `remote/scheduler.py` + `services/scheduler_helpers.py` |

## Cross-cutting utilities

| Utility | Lives in |
|---|---|
| App-data paths (`%APPDATA%/JobDesk/`) | `app_paths.py` |
| File logger (`logs/jobdesk-YYYYMMDD.log`) | `app_logging.py` |
| `tr(text, language)` i18n | `gui/i18n.py` |
| Design tokens (Colors / Spacing / …) | `gui/design/tokens.py` |
| Button feedback (idle / pending / ok / error) | `gui/button_feedback.py` |
| Pre-commit public-tree gate | `scripts/check_public_tree.ps1` |