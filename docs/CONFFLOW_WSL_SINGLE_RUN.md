# ConfFlow Batch Execution in JobDesk

JobDesk submits ConfFlow as one run containing one task per molecule. ConfFlow
owns its internal conformer generation and calculation steps; JobDesk monitors
those processes, downloads declared artifacts, and displays per-molecule
summaries.

## Execution Architecture

ConfFlow executes on the remote WSL server via SSH/SFTP. JobDesk does NOT
run a local WSL backend or polling supervisor:

1. **Submission**: JobDesk uses SSH/SFTP to upload inputs and invoke
   `confflow ...` on the remote server with `nohup setsid` to detach from
   the SSH session.
2. **Progress Observation**: JobDesk downloads and parses `run_summary.json`
   (final results) and `.workflow_state.json` (atomic checkpoint state).
3. **Recovery**: `confflow ... --resume` is the explicit resume entry point.
   JobDesk does NOT auto-retry failed tasks.
4. **State Files**: Both `workflow_stats.json` (per-step progress) and
   `.workflow_state.json` (atomic workflow checkpoint) are downloaded when
   available. These files are written atomically by ConfFlow; partial writes
   are handled gracefully.

## GUI Flow

1. Open **Files** and connect to server `wsl`.
2. Select one or more `.xyz` input files from one pane (local or remote).
   Non-XYZ files in the selection are ignored. Selecting XYZ from both panes
   simultaneously is rejected with a visible error.
3. Click **Run ConfFlow**.
   - If a `.yaml`/`.yml` is selected in the remote pane alongside remote XYZ,
     it is reused directly.
   - Otherwise, a file dialog opens to choose a local YAML configuration.
4. Review the confirmation dialog showing molecule count, YAML source, remote
   directory, and max parallel.
5. Confirm submission. JobDesk uploads required inputs (local XYZ and/or local
   YAML), creates one run with one task per molecule, and opens **Runs**.
6. The Runs page refreshes active jobs, downloads ConfFlow outputs on
   completion, and displays a per-molecule summary table.

The displayed summary confirms task execution and structured-output parsing
only. It is not a scientific validation of structures, energies, conformer
ranking, or downstream decisions.

Downloaded outputs per molecule include:

- `<name>.txt`
- `<name>min.xyz`
- `<name>_confflow_work/run_summary.json`
- `<name>_confflow_work/workflow_stats.json`
- `<name>_confflow_work/.workflow_state.json` (v1.3.0+ atomic checkpoint)

The `.workflow_state.json` file provides the most reliable view of workflow
progress because it is atomically written after each step completes. It contains
step statuses (`pending`, `submitted`, `completed`, `failed`), timestamps, and
the `final_status` field indicating whether the workflow succeeded or failed.

## Concurrency

The `max_parallel` value from Settings (shown on Files page) controls how many
ConfFlow processes execute simultaneously. With `max_parallel = 4` and 10
molecules, at most 4 run at the same time via `xargs -P`.

## Results Display

When a ConfFlow batch run is selected in Runs/Results, the lower panel shows a
table with columns: Molecule, Status, Conformers (in→out), Duration, Steps.

- **✓ Done**: summary parsed successfully.
- **✗ Missing**: task directory exists but no `run_summary.json` found.
- **⚠ Parse Error**: summary file exists but could not be parsed.

## Workflow State and Recovery

ConfFlow v1.3.0+ writes atomic workflow state to `.workflow_state.json` after each
step completes. This file is the authoritative source for workflow progress and
enables reliable resume:

- **Atomic Writes**: State is written to a temp file and renamed, ensuring the
  file is always in a valid state.
- **Step Statuses**: Each step has `status` (`pending`, `submitted`, `completed`,
  `failed`), `submitted_at`, `completed_at`, `output_xyz`, and `fail_count`.
- **Explicit Resume**: Run `confflow ... --resume` to continue from the last
  completed step. JobDesk does not auto-retry failed tasks.
- **Partial State Handling**: If `.workflow_state.json` is missing or incomplete,
  JobDesk degrades gracefully and treats the workflow as not started.

## Safety and Recovery

- Unknown SSH host keys are rejected by default. For a trusted new WSL SSH
  endpoint, explicitly enable `trust_on_first_use` once in server settings,
  connect to store the key, and then disable the option again.
- **Cancel** sends a remote termination request before a task is marked
  cancelled. A cancellation error leaves the task active and reports the
  failure.
- Recursive remote deletion is restricted to JobDesk-owned task directories;
  arbitrary user directories are not accepted as delete roots.
- Automatic refresh and download are always enabled. Completed tasks are
  automatically downloaded without requiring manual intervention.

## WSL Prerequisites

- `wsl` exists in `%APPDATA%\JobDesk\servers.yaml`, includes
  `wsl_distro: Ubuntu`, and connects over SSH.
- The JobDesk task shell can find `confflow` and the calculation program used
  by the YAML.
- For the provided integration test, Gaussian is available at `/opt/g16/g16`.

### g16 Environment Variables

Gaussian (g16) environment setup uses **explicit exports**, NOT `source /opt/g16/bsd/g16.profile`:

```
export g16root=/opt
export GAUSS_EXEDIR=/opt/g16/bsd:/opt/g16
export PATH=/opt/g16/bsd:/opt/g16:$PATH
export GAUSS_SCRDIR=/opt/g16/scratch
```

Sourcing `g16.profile` is avoided because it triggers `set -u` PERLLIB unbound
errors in the test harness environment. The explicit exports provide the same
functionality.

**Note**: `Gau-*.inp` files generated by Gaussian are used by the calculation
process and should not be cleaned up while a job is running.

## Optional Real Batch Test

Run from PowerShell in the JobDesk repository:

```powershell
$env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
$env:JOBDESK_TEST_REAL_CONFFLOW = "1"
pytest tests\integration\test_real_confflow_wsl.py -v
```

The test executes two small molecules through ConfFlow with `max_parallel=2`,
so it is skipped unless `JOBDESK_TEST_REAL_CONFFLOW=1` is explicitly set.
Its cleanup guard only removes a generated child directory beneath the
configured `JOBDESK_TEST_REMOTE_TMP_DIR`; set that variable to a dedicated
test directory such as `/tmp/jobdesk_test`, not `/tmp`.
