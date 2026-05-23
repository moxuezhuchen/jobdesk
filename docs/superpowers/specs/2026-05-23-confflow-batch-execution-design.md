# ConfFlow Batch Execution and Profile Clarity Design

## Goal

Extend the existing JobDesk-managed ConfFlow invocation from one molecule per
run to a practical batch workflow: the user can submit multiple molecule
inputs with one shared ConfFlow configuration and control how many independent
ConfFlow processes run concurrently. At the same time, the Settings page must
show the built-in ConfFlow profile for existing users and accurately explain
how ConfFlow result downloads are determined.

## Product Boundary

JobDesk owns:

- Selection and transfer of `.xyz` input files and one shared YAML config.
- Creation of one JobDesk run containing one independent task per molecule.
- Remote concurrency control through the existing `max_parallel` setting.
- Monitoring, declared-output download, and presentation of downloaded
  ConfFlow summaries.

ConfFlow owns:

- YAML-defined scientific workflow steps.
- Any Gaussian, ORCA, or other program execution triggered internally.
- Resume, checkpoints, internal retry semantics, and scientific result logic.

JobDesk does not expose or model ConfFlow's internal steps as JobDesk workflow
steps.

## Chosen Execution Model

One user action creates one JobDesk run with multiple ConfFlow tasks.

Example selection:

```text
mol1.xyz
mol2.xyz
mol3.xyz
mol4.xyz
confflow.yaml
max_parallel = 4
```

Resulting task commands:

```bash
confflow mol1.xyz -c confflow.yaml -w mol1_confflow_work
confflow mol2.xyz -c confflow.yaml -w mol2_confflow_work
confflow mol3.xyz -c confflow.yaml -w mol3_confflow_work
confflow mol4.xyz -c confflow.yaml -w mol4_confflow_work
```

All four tasks belong to one run and use the existing remote batch control
mechanism. With `max_parallel = 4`, at most four ConfFlow processes execute at
the same time. This is process-level concurrency; it does not alter internal
parallelism configured inside a ConfFlow YAML.

## Input Selection

### Molecule Inputs

The Files page accepts one or more `.xyz` files from exactly one source pane:

- Multiple local `.xyz` selections are uploaded before submission.
- Multiple remote `.xyz` selections are reused in place.
- Non-XYZ selections in either pane do not block a valid XYZ selection in the
  active source pane.
- Selecting XYZ inputs in both panes simultaneously is rejected with a visible
  message so the destination and transfer rules remain unambiguous.

### YAML Configuration

Each batch uses exactly one `.yaml` or `.yml` configuration:

- If the user selects a remote YAML file with the remote XYZ inputs, JobDesk
  reuses that remote file and does not upload a replacement.
- Otherwise JobDesk asks the user for one local YAML file and uploads it to the
  displayed remote directory before submission.
- Selecting multiple remote YAML files is rejected with a visible error.
- A local XYZ batch can use only a locally chosen YAML for the initial
  implementation; mixed local-input and remote-config submission is excluded
  to avoid an implicit cross-pane transfer workflow.

The Files page confirms the selected molecule count, selected YAML source,
remote directory, and current `max_parallel` before submission.

## Run and Artifact Contract

`ConfFlowAdapter` changes from a single-source helper to a batch-capable
adapter. It receives a list of XYZ remote paths, one YAML remote path, and the
selected `max_parallel`; it returns a `RunSpec` with one source per XYZ and one
shared supporting source for the YAML.

Each task declares its own output paths:

```text
{basename}.txt
{basename}min.xyz
{basename}_confflow_work/run_summary.json
{basename}_confflow_work/workflow_stats.json
```

The existing manifest field `remote_result_files` remains the authoritative
contract for ConfFlow downloads. `RunService.download_completed()` already
prefers these declared paths over extension matching, so nested JSON summaries
remain reliable for every molecule in the batch.

## Software Profiles and Settings Clarity

### Built-In Profile Migration

The Settings page must show a ConfFlow row even when a user already has a
saved `software_profiles` mapping that predates ConfFlow support.

Loading settings merges missing built-in profile names into existing profiles:

- Existing user entries and modified field values remain unchanged.
- Missing `ConfFlow` is added from the current built-in default.
- Missing future built-in profiles can use the same merge behavior.
- Explicit deletion of a built-in profile is not treated as permanent
  suppression in this iteration; built-ins reappear on load because the table
  represents supported execution types.

### Displayed Download Field

The ConfFlow row continues to display typical output patterns:

```text
*.txt,*min.xyz,*/run_summary.json,*/workflow_stats.json
```

For ConfFlow this column is descriptive, not the task download authority.
The Settings page adds visible explanatory text:

```text
ConfFlow downloads are managed from declared task outputs; shown patterns
describe the default artifacts.
```

User edits to ConfFlow's displayed patterns do not override the four
structured result paths declared by `ConfFlowAdapter`. Gaussian and ORCA keep
their existing pattern-based download behavior when a task does not declare
specific outputs.

## Runs and Results Presentation

The existing Runs page remains the entry point for monitoring a submitted
batch. The run row summarizes overall task state. When a ConfFlow batch is
selected:

- The result view discovers downloaded `run_summary.json` files under each
  task directory.
- It provides per-molecule status and summary availability.
- Selecting or expanding a molecule exposes its parsed ConfFlow summary.
- A task whose declared outputs cannot be downloaded is visibly failed or
  incomplete without hiding successful molecule results in the same run.

No cross-molecule scientific aggregation is required in this iteration.

## Failure Handling

- No selected XYZ: show a visible ConfFlow input error.
- XYZ selected in both local and remote panes: show a visible ambiguity error.
- More than one remote YAML selected: show a visible YAML selection error.
- Remote YAML selected with local XYZ: reject with guidance to choose a local
  YAML or upload the inputs first.
- Upload failure before submission: do not create or submit the run; present
  the transfer error.
- Individual task execution failure: retain the batch and show failed task
  status alongside any successful tasks.
- Individual output download failure: keep downloaded outputs from other tasks
  and expose which task did not satisfy its declared artifact contract.

## Files and Responsibilities

- `src/jobdesk_app/services/gui_settings.py`: define built-in profiles once and
  merge missing built-ins into persisted user profile mappings.
- `src/jobdesk_app/gui/pages/settings_servers_page.py`: show the ConfFlow
  profile and add the declared-output clarification text.
- `src/jobdesk_app/services/program_adapters.py`: construct a multi-source
  `RunSpec` for one shared YAML and per-molecule declared artifacts.
- `src/jobdesk_app/gui/pages/file_transfer_page.py`: validate multi-selection,
  resolve local versus remote YAML, upload local inputs/configuration, use
  `max_parallel`, and submit one batch run.
- `src/jobdesk_app/gui/pages/runs_results_page.py`: present multiple downloaded
  ConfFlow summaries in one run.
- `tests/test_gui_settings.py`, `tests/test_settings_servers_page.py`,
  `tests/test_program_adapters.py`, `tests/test_file_transfer_page_helpers.py`,
  and `tests/test_gui_behavior.py`: unit and GUI regression coverage.
- `tests/integration/test_real_confflow_wsl.py`: optional real WSL batch smoke
  using two small molecules and `max_parallel = 2`.
- `docs/CONFFLOW_WSL_SINGLE_RUN.md`: replace the single-run user flow with the
  batch-capable GUI validation procedure.

## Verification

Automated verification must cover:

1. Existing profile mappings receive a missing ConfFlow built-in without
   overwriting customized Gaussian or ORCA values.
2. Settings UI exposes ConfFlow and explains declared outputs.
3. Adapter output contains multiple tasks, one shared YAML support file,
   declared result artifacts per molecule, and the selected `max_parallel`.
4. Files-page helpers accept multiple XYZ from one pane, support a remote
   YAML, reject ambiguous/mixed cases visibly, and reuse the current maximum
   parallel value.
5. Runs/Results renders more than one ConfFlow summary from one batch.
6. Existing single-molecule behavior remains valid as the one-item batch case.
7. Optional real WSL smoke runs two inexpensive inputs concurrently through
   ConfFlow, downloads declared outputs, and observes both tasks complete.

Final validation includes focused pytest, full `pytest tests -q` with a
repo-local `--basetemp`, `ruff check` on modified modules/tests, `git diff
--check`, and an offscreen GUI construction and navigation check.
