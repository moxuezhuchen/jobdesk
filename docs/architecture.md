# JobDesk Architecture

## Current status and identity boundaries

This document describes the target source-level boundaries of the unreleased
JobDesk `0.8.0` architecture branch and records earlier acceptance and
production identities separately. The source version is not evidence of a
published artifact or deployment. The latest immutable released consumer
remains JobDesk `v0.7.10` at merge
`54f7735698f148371adb70397813c04ea569c245`, while failed or superseded tags
remain historical evidence. The immutable released producer is ConfFlow
`v2.1.6` at merge `45bfac11f721b2152eeff5ee26e50463fcc6f657`.
The four identities below must not be conflated (revalidate the live endpoint
before acceptance or promotion):

| Identity | Recorded value | Boundary |
|---|---|---|
| Shared source trees | JobDesk `C:\dft\tool\jobdesk` (`codex/gui-ux-remediation`, `154ee77b065cd71787418be312700c996bf01c57`); ConfFlow `/opt/ConfFlow` (`main`, `c6a4263bf3ec84669fd5279ec336b10ab2e18c9f`) | Dirty/shared development sources; not installed runtime |
| Isolated acceptance run | Candidate 3 for the released JobDesk `v0.7.10` / ConfFlow `v2.1.6` pair, run `jd0710-cf216-real-methane-candidate3-9c42f6a1` | **COMPLETED; APPROVED**. One atomic submit, one task, and one normally terminated real G16 optimization. |
| Released package evidence | JobDesk `v0.7.10` at merge `54f7735698f148371adb70397813c04ea569c245`, wheel SHA-256 `6e1c6b42f8cdbb939a57442e6b8b30b168c7bd6c5cf550cac958acd6e83992c3`; ConfFlow `v2.1.6` at merge `45bfac11f721b2152eeff5ee26e50463fcc6f657`, wheel SHA-256 `d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548` | Published immutable artifacts; no production switch |
| Configured production executable | `wsl` endpoint `/usr/local/bin/confflow` → `/opt/confflow-current` → `/opt/confflow-2.1.6-prod-venv/bin/confflow`, reporting ConfFlow `2.1.6` | Exact released producer with verified install provenance; stable `2.0.0` rollback retained |

The phase notes, compatibility records, and remediation evidence under
`docs/` are historical evidence. Their counters, hashes, commands, and
acceptance facts are intentionally unchanged and must not be read as current
product behavior or as release/promotion authorization. This includes the
older Phase 8/9 wizard and g16 notes, the Phase F owner-exception record, and
the compatibility-period records.

Candidate 3 used the released pair and consumed exactly one submit. Its control
trajectory reached `completed`; JobDesk downloaded the sole manifest-declared
artifact and same-run recovery evidence verified the manifest, summary, output,
and Gaussian log without resubmission. Production was then atomically promoted
to the exact released ConfFlow `v2.1.6` environment. CLI capability/control/
configuration smokes and JobDesk probe/configuration validation passed. The
promotion record and stable `2.0.0` rollback target are persisted.

## ConfFlow contract boundaries

The GUI has four working pages: Files, Workflow, Runs & Results, and Settings.
Workflow method presets are exposed through `WorkflowApplication`; their disk
adapter is an infrastructure detail.
The portable `WorkflowDocument`/codec/mapping path is dependency-free; the
optional `WorkflowSpec` facade may use producer Pydantic models for local
authoring compatibility, but those models are not a shared runtime contract.
JobDesk accepts the capability window `>=2.0,<3.0`; CI and the released pair
validate against ConfFlow `2.1.6` (the unchanged wire schemas reuse the
`v2.1.3` snapshot). Production is bound fail-closed to that exact clean released
`v2.1.6` producer identity.
The Phase F owner exception removed the legacy backend from the production
path; v1.5.3 and v1.4.6 remain historical release evidence only. This is a
capability window plus a producer-owned configuration contract, not an exact
shared Pydantic model pin.

An executable DAG must have one semantic terminal step. The OUTPUT node
visualizes that one result and does not aggregate independent branches; add a
final merge or calculation step before submitting a fan-out workflow.

A high-level map of the codebase. The intended audience is a new
contributor trying to locate where to make a change.

## Target layers for the unreleased 0.8.0 source candidate

```text
core                         standard library / pure data dependencies
  ↑
application                  use cases, immutable DTOs, owned ports
  ↑                ↖
gui / cli          infrastructure  adapters for SQLite, SSH/SFTP and ConfFlow
       ↖             ↑
          bootstrap             the only composition root
```

The dependency matrix is normative: `application` depends only on `core`;
`infrastructure` implements ports owned by `application`; GUI and CLI consume
only the public application API. Only `bootstrap` may import both application
and infrastructure to build `ApplicationContainer`. As a temporary v0.8 migration exception,
`gui/pages/runs_results_page.py` still imports `bootstrap` to assemble its
monitor and lifecycle collaborators; the static architecture test records
this explicit remaining boundary. Core does not depend on
application, infrastructure, GUI, or CLI. The old `services` and `remote`
packages are migration sources, not supported 0.8.0 public boundaries, and are
removed when this branch reaches acceptance.

One container owns the repository adapter, short-operation `SessionPool`, and
monitor registry. GUI shutdown and CLI `finally` paths close it idempotently.
The pool is still not the owner of long-lived monitor transports.

### Ordinary pooled sessions versus monitor transports

`infrastructure/remote/session_pool.py::SessionPool` owns one reusable, serialized SSH
session per server. Each `pool.lease(...)` is an exclusive, short-lived scope;
callers request either SSH-only (`need_sftp=False`) or SSH plus an SFTP channel
(`need_sftp=True`) and release the lease when the operation ends. The pool
closes detached clients after active leases return and on application
shutdown. It must not be held across a long-running watcher or a sequence of
unrelated operations.

The infrastructure monitor transport is different: each watcher owns a
long-lived transport for tailing `events.log` and probing declared workflow
state/statistics paths. Its `MonitorTransportProvider` explicitly must not
borrow a `SessionPool` lease, because a tail channel would starve ordinary
short operations. `application/runs_monitor.py` owns watcher identity and
lifecycle snapshots; `gui/run_monitor_qt.py` only bridges immutable events to
Qt. The watcher budget and reconnect/close state therefore remain separate
from ordinary pooled I/O.

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
* `WorkflowApplication` — application-facing preset operations; the disk-backed adapter loads user workflow presets from `<app_data_dir>/method_presets`, while built-in step presets live under `jobdesk_app.resources.step_presets`.

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

The presentation layer delegates submission to
`RunApplication.submit(...)`. The application use case coordinates
uploads and remote submission through ports and remains framework-free;
`MainWindow` does not perform I/O itself.

## Run lifecycle

JobDesk's local lifecycle orchestration is owned by `RunApplication`. The CLI
and GUI both call its immutable DTO-based API and never touch the database or
remote adapters directly. The remote workflow lifecycle remains owned by ConfFlow;
the two state machines are connected through the control protocol and typed
projections, not by shared database writes.

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

### State ownership

ConfFlow is authoritative for the remote aggregate state, revisions, event
ordering, checkpoints, idempotency, worker/launch tokens, and terminal
artifact manifests. JobDesk reads those producer-owned values through its
control client and monitor and never writes the producer's SQLite/state store.

JobDesk owns the local SQLite journal and projection: run/task records,
submit/delete and control-decision journal entries, accepted producer and
configuration provenance, handoff evidence, launcher reconciliation, and the
monotonic local task projection. Schema v7 adds the immutable
`run_configuration_bindings` row that binds the exact configuration digest,
producer-owned contract/schema identity, configured/resolved executable, and
canonical producer/executable provenance to one workflow run; schema v8 adds
the selected server identity to that binding. The ConfFlow control decision is
authoritative in the SQLite `operations` journal (`kind=confflow_control`),
where it is committed before task projection. `control_backend.json` is only
a rollback-compatible projection regenerated from that decision, never an
independent authority.

A failed submit, dropped SSH session, or external scheduler failure
ends up as one of `uncertain` / `failed` / `cancelled` in the task
state. The `confirm-submitted` and `abandon-submit` CLI / GUI actions
resolve `uncertain` tasks explicitly; recovery cannot silently take
over a lease (Schema v4).

## SQLite architecture

`%APPDATA%/JobDesk/runs/jobdesk.db` is the local source of truth for JobDesk
runs, tasks, journals, provenance, and projections. WAL mode allows concurrent
reads from CLI and GUI without manifest rewrites. It is not a second owner of
ConfFlow's remote aggregate state.

Schemas:

- **v1** — original per-task-only store
- **v2** — added the submit / delete operation journal
- **v3** — added trusted-workspace registry and delete-op-to-workspace bindings
- **v4** — added UTC submit-ownership leases
- **v5** — added `submit_activity_log` table for persisting Submit dialog activity (Phase 15C)
- **v6** — added `run_provenance` for accepted ConfFlow producer identity
- **v7** — added immutable `run_configuration_bindings` for accepted workflow
  configuration, contract/schema identity, executable identity, and producer
  provenance
- **v8** — added the explicit selected `server_id` to each immutable
  configuration binding and backfilled it from the parent run during migration

`infrastructure/persistence/sqlite_runs/` is split into `_schema`, `_paths`,
`_workspaces`, `_submit`, `_delete`, `_tasks`, `_tasks_helpers`, `_runs`,
`_operations`, `_operations_types`, `_legacy`, `_activity`, `_provenance`,
`_configuration_bindings`, and `_control_decisions`. The split is purely
organisational; all reads/writes flow through the adapter implementing
`RunRepositoryPort`. Control decisions commit the local journal,
provenance, and task projection atomically; `control_backend.json` remains a
byte-compatible projection for older readers.

## Method Preset Store

`WorkflowApplication` is the presentation-facing source of truth for user-saved
workflow compositions. A filesystem adapter stores them under
`<appdata>/method_presets/`; application callers do not receive the mutable
store. Each file loads as a `WorkflowSpec` via `WorkflowSpec.from_yaml()`.

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
front-end. JobDesk works without it. `WorkflowDocument`, `workflow_codec`,
`workflow_mapping`, and `workflow_schema_lint` keep the base install
dependency-free and lossless for saved documents. The compatibility
`WorkflowSpec` facade may use ConfFlow's Pydantic models when the `chem` extra
is installed, but those models are for local authoring compatibility rather
than a shared JobDesk/ConfFlow runtime contract. The page-level worker builds
a uniquely named local staging file (`workflow.<submission-id>.yaml`), which
is uploaded as `workflow.yaml` inside that submission's isolated remote
namespace, then:

1. Uploads the local XYZ inputs to the configured `remote_dir`.
2. Uploads the rendered `workflow.yaml` alongside them.
3. Submits the batch through the existing scheduler (`nohup setsid
   confflow …`). Initial launches do not pass `--resume`; an explicit
   retry/rerun reuses the same isolated namespace and adds `--resume`.

The local document/schema lint is advisory. Before a workflow run is created,
the ConfFlow infrastructure gateway resolves the configured producer's
`config contract --json` response and submits the exact YAML bytes to
`config validate --json --stdin`; the producer's canonical validator owns
semantic admission. `application/configuration_contract.py` stores the typed
admission while the gateway parses the frozen response ABI. The exact
contract/schema hash and executable/producer identities are
persisted in schema-v8 `run_configuration_bindings` and rechecked before
submission. Local and remote sides are coupled by capability, configuration,
control, and artifact contracts, not by an exact shared Pydantic model version.

A ConfFlow run is observed through the application monitor port polling the
remote `events.log` (DONE / RUNNING) **and** probing the SHA-256 content
digest/presence of the exact state + stats paths supplied by the run plan
(see `_CHECKPOINT_PROBE_SECONDS`). Mtime-only changes are ignored. A changed
snapshot fires a synthetic `DoneEvent` so the Runs page Progress column
updates between DONE lines; this remains observation, not producer-state
ownership.

## Where to make changes

| You want to… | Start here |
|---|---|
| Add a CLI subcommand | `src/jobdesk_app/cli.py` + the relevant application facade |
| Add a page / tab | `gui/main_window.py` (stacked widget) + `gui/pages/<name>_page.py` |
| Tweak the Submit dialog | `gui/dialogs/submit_dialog.py` (mode detection, payload build) + `gui/dialogs/workflow_builder_dialog.py` (preset editor) |
| Add a submit mode (kind) | `core/submit_payload.py` (`SubmitKind` literal) + the `RunApplication` submit use case |
| Tweak parser output | `core/parsers/{gaussian,orca}.py` + add a test in `tests/test_parsers.py` |
| Add a column to the runs-results table | `gui/pages/runs_results_page.py` + `_analysis_row` helper |
| Change the SQLite schema | `infrastructure/persistence/sqlite_runs/_schema.py` + add a migration in `_legacy.py` |
| Add a new server-side scheduler | implement the application scheduler port under `infrastructure/remote/` and wire it in `bootstrap` |

## Cross-cutting utilities

| Utility | Lives in |
|---|---|
| App-data paths (`%APPDATA%/JobDesk/`) | `app_paths.py` |
| File logger (`logs/jobdesk-YYYYMMDD.log`) | `app_logging.py` |
| `tr(text, language)` i18n | `gui/i18n.py` |
| Design tokens (Colors / Spacing / …) | `gui/design/tokens.py` |
| Button feedback (idle / pending / ok / error) | `gui/button_feedback.py` |
| Pre-commit public-tree gate | `scripts/check_public_tree.ps1` |
