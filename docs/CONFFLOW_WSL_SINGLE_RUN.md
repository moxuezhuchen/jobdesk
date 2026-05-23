# ConfFlow Batch Execution in JobDesk

JobDesk submits ConfFlow as one run containing one task per molecule. ConfFlow
owns its internal conformer generation and calculation steps; JobDesk monitors
those processes, downloads declared artifacts, and displays per-molecule
summaries.

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

## Safety and Recovery

- Unknown SSH host keys are rejected by default. For a trusted new WSL SSH
  endpoint, explicitly enable `trust_on_first_use` once in server settings,
  connect to store the key, and then disable the option again.
- **Cancel** sends a remote termination request before a task is marked
  cancelled. A cancellation error leaves the task active and reports the
  failure.
- Recursive remote deletion is restricted to JobDesk-owned task directories;
  arbitrary user directories are not accepted as delete roots.
- Automatic refresh and download are enabled by default and can be disabled in
  settings.

## WSL Prerequisites

- `wsl` exists in `%APPDATA%\JobDesk\servers.yaml`, includes
  `wsl_distro: Ubuntu`, and connects over SSH.
- The JobDesk task shell can find `confflow` and the calculation program used
  by the YAML.
- For the provided integration test, Gaussian is available at `/opt/g16/g16`.

## Optional Real Batch Test

Run from PowerShell in the JobDesk repository:

```powershell
$env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
$env:JOBDESK_TEST_REAL_CONFFLOW = "1"
pytest tests\integration\test_real_confflow_wsl.py -v --basetemp .pytest_tmp_real_confflow_wsl
```

The test executes two small molecules through ConfFlow with `max_parallel=2`,
so it is skipped unless `JOBDESK_TEST_REAL_CONFFLOW=1` is explicitly set.
Its cleanup guard only removes a generated child directory beneath the
configured `JOBDESK_TEST_REMOTE_TMP_DIR`; set that variable to a dedicated
test directory such as `/tmp/jobdesk_test`, not `/tmp`.
