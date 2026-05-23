# ConfFlow Single-Run Validation in WSL

JobDesk submits ConfFlow as one remote task. ConfFlow owns its internal
conformer generation and calculation steps; JobDesk monitors that process,
downloads declared artifacts, and displays `run_summary.json`.

## GUI Flow

1. Open **Files** and connect to server `wsl`.
2. Select one local or remote `.xyz` input file.
3. Click **Run ConfFlow** and select a local `.yaml` or `.yml` ConfFlow configuration.
4. Confirm submission. JobDesk uploads required inputs, creates one run, and opens **Runs**.
5. Leave the Runs page open. It refreshes active jobs, downloads ConfFlow outputs on completion, and displays the summary.

Downloaded outputs include `<name>.txt`, `<name>min.xyz`,
`<name>_confflow_work/run_summary.json`, and
`<name>_confflow_work/workflow_stats.json`.

## WSL Prerequisites

- `wsl` exists in `%APPDATA%\JobDesk\servers.yaml` and connects over SSH.
- The JobDesk task shell can find `confflow` and the calculation program used by the YAML.
- For the provided integration test, Gaussian is available at `/opt/g16/g16`.

## Optional Real Test

Run from PowerShell in the JobDesk repository:

```powershell
$env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
$env:JOBDESK_TEST_REAL_CONFFLOW = "1"
pytest tests\integration\test_real_confflow_wsl.py -v --basetemp .pytest_tmp_real_confflow_wsl
```

The test executes a water Gaussian optimization through ConfFlow, so it is
skipped unless `JOBDESK_TEST_REAL_CONFFLOW=1` is explicitly set.
