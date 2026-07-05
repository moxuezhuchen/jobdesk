# JobDesk + ConfFlow Migration Notes

This document captures the structural move of the [ConfFlow](https://example) workflow
engine into the **JobDesk** desktop application monorepo. It is a record of how
the codebase was reshaped from a standalone pip-installable project into the
`jobdesk_app.workflow` sub-package and what to watch for when working across the
boundary.

## TL;DR

* ConfFlow used to live in `confflow/{blocks,calc,config,core,shared,workflow}`.
* It now lives in `jobdesk_app/workflow/{blocks,calc,config,core,shared,workflow}`.
* All absolute imports `from confflow.X import …` were rewritten to
  `from jobdesk_app.workflow.X import …`.
* Internal relative imports `from ..X` were rewritten to `from .X` because the
  helper modules now sit beside each other inside `workflow/`.
* Python entry points are now `jobdesk`, `jobdesk-gui`, `jobdesk workflow …`.
* The `confflow-agent` daemon remains a stand-alone CLI so it can run on a
  remote cluster without GUI dependencies.

## Directory map

| Old path                                       | New path                                           |
| ---------------------------------------------- | -------------------------------------------------- |
| `confflow/blocks/*.py`                         | `jobdesk_app/workflow/blocks/*.py`                 |
| `confflow/calc/*.py`                           | `jobdesk_app/workflow/calc/*.py`                   |
| `confflow/config/*.py`                         | `jobdesk_app/workflow/config/*.py`                 |
| `confflow/core/*.py`                           | `jobdesk_app/workflow/core/*.py`                   |
| `confflow/shared/*.py`                         | `jobdesk_app/workflow/shared/*.py`                 |
| `confflow/workflow/{engine,confts,cli,...}.py` | `jobdesk_app/workflow/{engine,confts,cli,...}.py`  |
| `confflow/agent/*.py`                          | `jobdesk_app/agent/*.py`                           |
| `confflow/__init__.py`                         | (dropped — JobDesk owns the namespace)             |
| `confflow/cli.py`                              | `jobdesk_app/cli/__init__.py` (top-level `jobdesk`) |

## Import rules

After the move the helper modules in `jobdesk_app/workflow/` may use only
`from .X import Y` for sibling packages (`calc`, `blocks`, `config`, `core`,
`shared`). Anything outside `workflow/` uses the absolute path
`from jobdesk_app.workflow.X import Y` (e.g. `agent/runner.py`).

## Files page submission

The legacy `RunCoordinator` + `ConfFlowAdapter` direct-submit path was
removed in Stage 5. The Files page **Run ConfFlow** button now exclusively
forwards work to `confflow-agent` on the remote server:

```text
JobDesk GUI  ──SSH/SFTP──►  confflow-agent daemon  ──subprocess──►  Gaussian/ORCA
```

Closing the GUI no longer kills in-flight calculations. To follow job progress
back to a different machine, re-open JobDesk, click "View Agent Jobs", pick
the server and the job id.

## Wizard YAML → Agent

Stage 4 introduced a declarative form-based workflow builder. Submitting a
wizard-built configuration goes through the same agent path:

1. Wizard writes the YAML to a temporary file.
2. Files page picks up the wizard's stash and uploads `wizard_<id>.yaml` to
   `remote_dir`.
3. `AgentBridge.submit_job()` enqueues the input molecule + the YAML as one
   agent job.

The wizard is only a *form -> YAML* translator; it never executes workflows
itself. Layer-1 (form-level) and layer-2 (runtime model) validations run on
every keystroke so the user cannot save an invalid YAML to disk.

## Test layout

| Old path                       | New path                                  |
| ------------------------------ | ----------------------------------------- |
| `tests/test_confflow_*.py`     | `tests/test_workflow_*.py`                |
| `tests/test_confflow_cli.py`   | `tests/test_workflow_cli.py`              |
| `tests/integration/test_*`     | unchanged                                  |
| `confflow/agent/tests/*`       | merged into top-level `tests/`             |

New tests added in Stage 4:

* `tests/test_workflow_builder.py` — form state ⇄ YAML round-trip, validation.
* `tests/test_workflow_cli.py` — `jobdesk workflow build|check|presets`.
* `tests/test_workflow_page.py` — wizard PySide6 page (headless).

## Compatibility shims

There are **no** compatibility shims. Code that imports
`from confflow import …` will fail. If you maintain a tool that depends on
ConfFlow being a stand-alone project, see `docs/archive/CONFFLOW_WSL_SINGLE_RUN.md`
for instructions on installing the legacy 0.x line.
