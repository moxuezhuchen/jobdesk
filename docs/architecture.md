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

`gui/main_window.py` wires a `QStackedWidget` of four pages (Phase 14):

| Page | Module | Role |
|---|---|---|
| Files | `gui/pages/file_transfer_page.py` | SSH/SFTP browser. Right-click "Use as input → Submit" / "Send to ConfFlow → Submit" pushes paths to the Submit page. |
| Submit | `gui/pages/submit_page.py` | Unified submit UI: input picker, mode tabs (Build input file / Build workflow), Submit / Create-tasks-only buttons, live preview, activity log. |
| Runs & Results | `gui/pages/runs_results_page.py` | Run list, per-task status, parsed preview, ResultDetailPane |
| Settings | `gui/pages/settings_servers_page.py` | `servers.yaml` editor + GUI preferences |

The Submit page (Phase 14) replaces the legacy ConfFlow wizard and
InputBuilder dialog. It embeds four reusable widgets from
`gui/widgets/`:

```
InputSourcePanel   ──+──►  SubmitPage  ──►  SubmitUseCase  ──►  PreparedBatch
CalculationWidget  ──┤                       (pure logic)
WorkflowWidget     ──┤
InputBuilderWidget ──┘
```

* `InputSourcePanel` — tabbed local/remote picker; `add_local_paths`,
  `add_remote_paths`, drag-drop, `sources_changed(list[InputSource])`.
* `CalculationWidget` — method/basis/charge/multiplicity/nproc/memory
  form with validation hints and a recent-presets MRU strip.
* `WorkflowWidget` — workflow steps + work_dir + advanced options +
  YAML preview; `build_spec(calc)` produces a `WorkflowSpec`.
* `InputBuilderWidget` — Gaussian / ORCA input file renderer
  (`build_content()` / `build_content_to()`).

The page-level worker callback (in `MainWindow`) handles the I/O:
uploads `local_paths` to `remote_targets`, then calls
`RunCoordinator.create_and_submit(spec, local_dir=...)`. The use case
is intentionally framework-free.

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

The Submit page's "Build workflow" tab is the optional ConfFlow
front-end. JobDesk works without it. When installed
(`pip install -e ".[chem]"`), `SubmitUseCase` produces a
`WorkflowSpec` that round-trips through ConfFlow's Pydantic models
plus a `workflow.yaml` written next to the first XYZ input. The
page-level worker callback then:

1. Uploads the local XYZ inputs to the configured `remote_dir`.
2. Uploads the rendered `workflow.yaml` alongside them.
3. Submits the batch through the existing scheduler (`nohup setsid
   confflow … --resume`).

The Submit page and the remote `confflow` binary must import the same
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
| Tweak the Submit page | `gui/pages/submit_page.py` (layout / signals) or `gui/widgets/{input_source_panel,calculation_widget,workflow_widget,input_builder_widget}.py` (embedded widgets) |
| Add a submit mode (kind) | `core/submit_payload.py` (`SubmitKind` literal) + `services/submit_use_case.py` (`_build_*_specs`) |
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