# WSL Gaussian/ORCA Workflow Smoke

This smoke guide uses WSL as a local SSH/SFTP/nohup server. The Gaussian 16 and
ORCA water opt -> freq paths have been manually verified. It intentionally does
not validate Slurm, PBS, HPC modules, or cluster-specific behavior.

## Preconditions

- WSL SSH is reachable from Windows, for example:
  `ssh root@127.0.0.1 "hostname"`.
- The JobDesk `servers.yaml` file has a `wsl` server entry.
- The WSL server can run `g16`.
- `g16` is initialized for non-interactive jobs. In the verified setup,
  `/root/.bashrc` exports `g16root=/opt` and sources
  `/opt/g16/bsd/g16.profile`.
- (Optional, for ORCA smoke) ORCA is installed at `/opt/orca611/orca` and
  `/root/.bashrc` adds `/opt/orca611` to PATH.
- **ORCA visibility note**: A bare `ssh root@127.0.0.1 "which orca"` will fail
  because `.bashrc` exits early for non-interactive shells (`[ -z "$PS1" ] && return`).
  However, JobDesk's task runner sets `PS1` before sourcing `.bashrc`, so
  `$(type -P orca) {name} > {basename}.out` works in actual job execution. No
  `env_init_scripts` change needed.
- Run these commands from the JobDesk repository root:
  `C:\dft\tool\jobdesk`.

Example compatible server entry:

```yaml
servers:
  wsl:
    display_name: WSL Local
    host: 127.0.0.1
    port: 22
    username: root
    auth_method: key
    key_path: C:/Users/moxue/.ssh/id_rsa
    default_shell: bash
    env_init_scripts: []
    scheduler:
      type: nohup
      default_cpus: 4
      default_memory_mb: 4096
      default_walltime_minutes: 60
```

## Manual Smoke Steps

```powershell
cd C:\dft\tool\jobdesk

ssh root@127.0.0.1 "rm -rf /tmp/jobdesk_test; mkdir -p /tmp/jobdesk_test"

jobdesk files list-remote wsl /tmp/jobdesk_test
jobdesk files upload wsl examples\gaussian\water_opt.gjf /tmp/jobdesk_test/water_opt.gjf
jobdesk files preview wsl /tmp/jobdesk_test/water_opt.gjf

jobdesk workflow run . opt_freq --server wsl --remote-dir /tmp/jobdesk_test --files /tmp/jobdesk_test/water_opt.gjf
```

Record the printed `workflow_id`:

```powershell
$wf_id = "<workflow_id>"
jobdesk workflow status . $wf_id
jobdesk run list .
```

Find the `opt` run id, refresh it until it is remote-completed, then download
the output:

```powershell
$opt_run_id = "<opt_run_id>"
jobdesk run refresh . $opt_run_id
jobdesk run list .
jobdesk run download . $opt_run_id --patterns "*.log,*.out"
```

Advance the workflow. This extracts geometry from the downloaded opt log,
generates `water_opt_freq.gjf`, uploads it to WSL, and submits the `freq` run:

```powershell
jobdesk workflow advance . $wf_id
jobdesk workflow status . $wf_id
jobdesk run list .
```

Find the `freq` run id, refresh it until remote-completed, download the output,
and run a final advance to sync the workflow state:

```powershell
$freq_run_id = "<freq_run_id>"
jobdesk run refresh . $freq_run_id
jobdesk run list .
jobdesk run download . $freq_run_id --patterns "*.log,*.out"
jobdesk workflow advance . $wf_id
jobdesk workflow status . $wf_id
```

## Success Criteria

- `workflow status` ends with:
  - `opt: completed`
  - `freq: completed`
- `results/<opt_run_id>/water_opt/water_opt.log` exists and contains normal
  Gaussian termination.
- `results/<freq_run_id>/water_opt_freq/water_opt_freq.log` exists and contains
  normal Gaussian termination and frequency output.
- `.jobdesk/workflow_inputs/<workflow_id>/freq/water_opt_freq.gjf` exists and
  contains a `freq` route and O/H/H geometry.
- `workflow status` recent events include:
  - `workflow_started`
  - `step_started` for `opt`
  - `downstream_input_generated` for `freq`
  - `step_started` for `freq`

## Known CLI Details

- Use the existing server id `wsl`, not `wsl-local`, unless you add a separate
  `wsl-local` entry to `servers.yaml`.
- `jobdesk run download --patterns` accepts either one comma-separated string
  such as `"*.log,*.out"` or multiple arguments such as `"*.log" "*.out"`.
- `workflow status` reads the saved workflow state; it does not sync run state.
  Use `workflow advance` after downloading results to sync completed steps.
- `workflow advance` marks a step complete only after the underlying run is
  locally `downloaded` or `analyzed`.
- If WSL restarts and SSH is unavailable, start it in WSL with:
  `service ssh start`.

## ORCA opt -> freq Smoke

Built-in workflow `orca_opt_freq` uses
`$(type -P orca) {name} > {basename}.out` for both steps so that JobDesk can
download and parse output files, and ORCA receives an absolute executable path
when generated frequency inputs request parallel execution.

```powershell
ssh root@127.0.0.1 "rm -rf /tmp/jobdesk_test; mkdir -p /tmp/jobdesk_test"

jobdesk files upload wsl examples\orca\water_opt.inp /tmp/jobdesk_test/water_opt.inp

jobdesk workflow run . orca_opt_freq --server wsl --remote-dir /tmp/jobdesk_test --files /tmp/jobdesk_test/water_opt.inp

# Same refresh/download/advance cycle as Gaussian above.
# Success: workflow status shows opt: completed, freq: completed.
# Generated file: .jobdesk/workflow_inputs/<wf_id>/freq/water_opt_freq.inp contains "! freq" and O/H/H coords.
```

## Optional Integration Test

The real WSL workflow tests are skipped by default and only run when explicitly
enabled. For Gaussian:

```powershell
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
$env:JOBDESK_TEST_REAL_G16 = "1"
```

The test covers:

- prepare remote `/tmp/jobdesk_test`
- upload `examples/gaussian/water_opt.gjf`
- run `workflow run . opt_freq`
- wait for opt remote completion
- download opt logs
- advance to freq
- wait for freq remote completion
- download freq logs
- final `workflow advance`
- assert both workflow steps are completed

For ORCA:

```powershell
$env:JOBDESK_TEST_SSH_SERVER_ID = "wsl"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_orca_test"
$env:JOBDESK_TEST_REAL_ORCA = "1"
pytest tests/integration/test_real_workflow_orca_wsl.py -v --basetemp .pytest_tmp_real_orca_wsl_workflow
```

The ORCA test additionally confirms generation of `water_opt_freq.inp` and
downloads `.out` files for both steps.
