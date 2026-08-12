# JobDesk Architecture

## Current candidate boundary (2026-08-12)

The isolated candidate is `6122ecc` (base `e4d8f74`); the paired ConfFlow
candidate is `0037c04` (base `6981935`). Neither candidate is published or
configured as a production endpoint. The dirty shared checkout at
`C:\dft\tool\jobdesk-dev` remains historical/user-owned state at `89d232a`.

The current workflow path is:

```text
GUI -> WorkflowDocument / WorkflowCodec
    -> explicit migration policy + bounded structural lint
    -> per-server config-contract resolver
    -> remote canonical dry-run
    -> control submission and provenance-bound run projection
```

`WorkflowDocument`, `WorkflowCodec`, and `WorkflowMigrationPort` preserve
unknown saved fields and do not import Qt or producer Python models. The
compatibility facade may use a producer validator when available, but remote
canonical validation is authoritative. A config contract records the producer
schema/version/hash and binds the result to the server and immutable
executable identity before upload. Stable v2.0.0 may use only its explicit,
identity-pinned compatibility fallback because that release predates the
additive `config contract --json` command.

Release publication, side-by-side acceptance, real-launcher acceptance, and
promotion remain independent gates; this candidate has not switched any
production endpoint.

## ConfFlow contract update (2026-07-28)

The GUI has four working pages: Files, Workflow, Runs & Results, and Settings.
Workflow method presets are supplied by `jobdesk_app.services.method_presets`,
while the editable local `WorkflowSpec` and the remote `confflow` CLI form a
two-part contract. JobDesk accepts ConfFlow in the compatibility window
`>=2.0,<3.0`; control submission is pinned to the exact clean `v2.0.0`
provenance. The Phase F owner exception removed the legacy backend from the
production path; v1.5.3 and v1.4.6 remain historical release evidence only.
This is a capability window, not an exact shared model pin.

An executable DAG must have one semantic terminal step. The OUTPUT node
visualizes that one result and does not aggregate independent branches; add a
final merge or calculation step before submitting a fan-out workflow.

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

> **P-M1 (R-M1) — serialised reuse, not concurrency.** The
> `SessionPool` owned by `MainWindow` is shared by the Files page and
> the Runs page (and each `RunCoordinator` they create).  When the
> GUI runs a ConfFlow capability probe before upload, the probe
> borrows the same SSH transport that the subsequent upload will
> reuse.  Each `pool.lease(...)` returns both an SSH and an SFTP
> handle and they are released together when the lease exits — read
> and write therefore serialise through one SFTP channel rather
> than racing two.  Long-lived leases held across multiple
> operations are intentionally not supported: every file operation
> enters and exits its own lease.

## 4-page GUI shell

`gui/main_window.py` wires a `QStackedWidget` of four pages (Phase 14):

| Page | Module | Role |
|---|---|---|
| Files | `gui/pages/file_transfer_page.py` | SSH/SFTP browser. Right-click "Use as input → Submit" opens the SubmitDialog. Primary [Submit] button on the action row opens the dialog with the currently selected sources. |
| Workflow | `gui/pages/workflow_page.py` | Sidebar view of method presets (built-in + user). Lets the user browse, save, and dispatch presets. The SubmitDialog is opened separately when the user is ready to submit. |
| Runs & Results | `gui/pages/runs_results_page.py` | Run list, per-task status, parsed preview, ResultDetailPane |
| Settings | `gui/pages/settings_servers_page.py` | `servers.yaml` editor + GUI preferences |

The Submit page (Phase 2) is now split across three modules:

* `gui/dialogs/submit_dialog.py` — modal that produces a `SubmitPayload`. Auto-detects Single vs Workflow mode from the selected input files.
* `gui/dialogs/workflow_builder_dialog.py` — modal that hosts `WorkflowGraphEditor` for editing a single preset.
* `services/method_presets.py` — disk-backed `MethodPresetStore` that loads user workflow presets from `<app_data_dir>/method_presets`; built-in step presets live under `jobdesk_app.resources.step_presets`.

```
InputSourcePanel  ──+──►  SubmitDialog  ──►  SubmitPayload  ──►  SubmitUseCase  ──►  PreparedBatch
WorkflowGraphEditor ─┘    (modal)             (dataclass)            (pure logic)
```

* `InputSourcePanel` — tabbed local/remote picker; `add_local_paths`,
  `add_remote_paths`, drag-drop, `sources_changed(list[InputSource])`.
* `WorkflowGraphEditor` — node-graph editor driving the Submit preview and
  payload (`to_workflow_spec(...)` / `from_workflow_spec(...)`). Replaces
  the Phase 14A `CalculationWidget` / `WorkflowWidget` / `InputBuilderWidget`,
  which were retired in Phase 10.6.

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
- **v4** — added UTC submit-ownership leases
- **v5** — added `submit_activity_log` table for persisting Submit dialog activity (Phase 15C)

`services/run_repository/` is split into `_schema`, `_paths`,
`_workspaces`, `_leases`, `_submit`, `_delete`, `_tasks`, `_runs`,
`_operations`, `_legacy`, `_operations_types`, `_activity`. The split is purely
organisational; all reads / writes still flow through
`RunRepository` (the package's `__init__.py`).

## Method Preset Store

`services/method_presets.py::MethodPresetStore` is the source of truth for
user-saved workflow compositions under `<appdata>/method_presets/`. Each file
loads as a `WorkflowSpec` via `WorkflowSpec.from_yaml()`. The historical class
name remains for compatibility, but it no longer exposes bundled workflows.

`StepPresetStore` owns reusable single-step fragments. It combines bundled
entries from `jobdesk_app.resources.step_presets` with optional user entries
under `<appdata>/step_presets/`; user fragments take precedence by name. Step
presets deliberately omit workflow-global fields and graph edges.

Save path: `MethodPresetStore.save_user(name, spec)` writes
`spec.to_yaml()` to `<user_dir>/<name>.yaml` via
`core/atomic_write.atomic_write_text`. Renames go through temp+move;
deletes are unconditional `unlink`.

The Workflow page and Submit dialog use these stores so saved compositions and
reusable steps follow the same persistence rules.

## ConfFlow integration

The Submit page's "Build workflow" tab is the optional ConfFlow
front-end. JobDesk works without it. When installed
(`pip install -e ".[chem]"`), `SubmitUseCase` produces a
`WorkflowSpec` that round-trips through ConfFlow's Pydantic models
plus a uniquely named local staging file (`workflow.<submission-id>.yaml`),
which is uploaded as `workflow.yaml` inside that submission's isolated remote
namespace. The
page-level worker callback then:

1. Uploads the local XYZ inputs to the configured `remote_dir`.
2. Uploads the rendered `workflow.yaml` alongside them.
3. Submits the batch through the existing scheduler (`nohup setsid
   confflow …`). Initial launches do not pass `--resume`; an explicit
   retry/rerun reuses the same isolated namespace and adds `--resume`.

The local model validates authoring inputs, while the remote CLI owns execution
and artifact production. They are coupled by the capability and file contracts,
not by an exact shared Pydantic model version.

A ConfFlow run is observed via `services/run_monitor.py` polling the
remote `events.log` (DONE / RUNNING) **and** probing the SHA-256 digest
of state + stats files once per iteration (see
`_CHECKPOINT_PROBE_SECONDS` in `run_monitor.py`). The latter fires a
synthetic DoneEvent so the Runs page Progress column updates
between DONE lines.

## Where to make changes

| You want to… | Start here |
|---|---|
| Add a CLI subcommand | `src/jobdesk_app/cli.py` + `services/run_coordinator.py` |
| Add a page / tab | `gui/main_window.py` (stacked widget) + `gui/pages/<name>_page.py` |
| Tweak the Submit dialog | `gui/dialogs/submit_dialog.py` (mode detection, payload build) + `gui/dialogs/workflow_builder_dialog.py` (preset editor) |
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
