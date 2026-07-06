# Windows E2E Checklist — P0 Tests

This document covers the manual and automated tests required to validate a
ConfFlow deployment on a real Windows 10/11 machine with WSL2 and an HPC
server. All integration tests are **opt-in** via environment variables and are
normally skipped in CI.

---

## 1. Prerequisites

| Requirement | Version / Notes |
|---|---|
| Python | **3.11+** (run `python --version` to confirm) |
| Windows | 10 or 11 with WSL2 installed (`wsl --list` should show a distro) |
| SSH key to HPC | `~/.ssh/id_rsa` (or configured key) accessible from WSL |
| confflow-agent on HPC | Binary installed and the daemon running (`systemctl status confflow-agent` or equivalent) |
| JobDesk source | Cloned from `jobdesk-staging`, `pip install -e ".[dev]"` run in the venv |

Check WSL2 is working:

```powershell
wsl --list --verbose
# Expected output includes a distro name with State=Running
```

Check SSH key is accessible from WSL:

```bash
ssh -o BatchMode=yes <hpc_username>@<hpc_host> echo ok
# Expected: prints "ok" with no password prompt
```

Check confflow-agent is running on HPC:

```bash
ssh <hpc_username>@<hpc_host> "systemctl is-active confflow-agent"
# Expected: "active"
```

---

## 2. Test Environment Setup — Integration Test Env Vars

Set these **four** environment variables before running `pytest` on the
integration tests. They are read by `tests/integration/test_agent_e2e.py`.

| Variable | Value |
|---|---|
| `JOBDESK_TEST_SERVERS_YAML` | Absolute path to a `servers.yaml` on your local machine that contains the HPC server entry |
| `JOBDESK_TEST_SSH_SERVER_ID` | The `server_id` key in that `servers.yaml` (e.g. `hpc`) |
| `JOBDESK_TEST_REMOTE_TMP_DIR` | A writable temporary directory on the HPC (e.g. `/tmp/<username>/jobdesk_tests`) |
| `JOBDESK_TEST_CONFFLOW_YAML` | Path to a minimal `confflow.yaml` on the HPC (see example below) |
| `JOBDESK_TEST_REAL_AGENT=1` | Opts in to the real-agent tests (without this they skip) |

**Example `servers.yaml` (must be valid YAML):**

```yaml
servers:
  hpc:
    display_name: HPC Cluster
    host: hpc.example.com
    port: 22
    username: <your_username>
    auth_method: key
    key_path: C:/Users/<you>/.ssh/id_rsa   # Windows path; or /home/<you>/.ssh/id_rsa in WSL
    trust_on_first_use: false
    env_init_scripts: []
    ssh_access:
      config_alias: ""
      proxy_command: ""
      proxy_jump: ""
    external_tools:
      terminal_provider: windows_terminal
      ssh_alias: ""
      putty_session: ""
      terminal_path: ""
    scheduler: {}
```

**Minimal `confflow.yaml` for testing:**

```yaml
global:
  charge: 0
  multiplicity: 1
steps:
  - type: sp
    program: g16
    params:
      keyword: HF/3-21G
```

Create the remote tmp directory on HPC:

```bash
ssh <hpc_username>@<hpc_host> "mkdir -p /tmp/<username>/jobdesk_tests"
```

Set the env vars in PowerShell before running pytest:

```powershell
$env:JOBDESK_TEST_SERVERS_YAML = "C:\path\to\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "hpc"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/<username>/jobdesk_tests"
$env:JOBDESK_TEST_CONFFLOW_YAML = "/home/<username>/confflow.yaml"
$env:JOBDESK_TEST_REAL_AGENT = "1"
```

---

## 3. Skipped WSL Tests — Manual Verification Procedures

Three tests in `tests/test_remote_ssh.py` are skipped in CI because they use
`CREATE_NO_WINDOW` (a win32-only flag). They cover WSL bootstrap logic.
Run them manually on a Windows machine by removing the `@pytest.mark.skipif`
decorators, or run pytest with `--runxfail` after removing the skip markers.

### 3.1 `test_connect_starts_configured_wsl_distro_before_ssh`

**What it tests:** When connecting to a server that has `wsl_distro` set, the
SSH client wrapper must first spawn `wsl.exe -d <distro> -- true` to wake the
distro before attempting the Paramiko SSH connect.

**How to run:**
1. Open PowerShell in the repo root.
2. Run the test directly:

```powershell
python -m pytest tests/test_remote_ssh.py::TestWSLBootstrap::test_connect_starts_configured_wsl_distro_before_ssh -v
```

**Expected output:**

```
tests/test_remote_ssh.py ... PASSED
```

**Pass criteria:**
- `subprocess.run` is called exactly **once** with `["wsl.exe", "-d", "Ubuntu", "--", "true"]`
- `paramiko.SSHClient.connect` is called exactly **once**
- No additional `wsl.exe` invocations occur

**What failure looks like:** `run_wsl` not called — the distro never boots and
SSH connects to the Windows host's own SSH daemon (if running) instead of
forwarding into WSL.

---

### 3.2 `test_wsl_bootstrap_failure_is_rate_limited_during_cooldown`

**What it tests:** If `wsl.exe` fails (non-zero exit), the failure must enter
the cooldown mechanism so that repeated calls within the cooldown window do not
re-spawn `wsl.exe`. This prevents hammering a broken WSL install.

**How to run:**

```powershell
python -m pytest tests/test_remote_ssh.py::TestWSLBootstrap::test_wsl_bootstrap_failure_is_rate_limited_during_cooldown -v
```

**Expected output:**

```
tests/test_remote_ssh.py ... PASSED
```

**Pass criteria:**
- `subprocess.run` is called exactly **once** even though `_start_wsl_if_configured()` is called twice
- The second call raises `SSHConnectionError` (expected) but does **not** spawn `wsl.exe` again

**What failure looks like:** `run_wsl` called twice — the second failure is
re-spawning `wsl.exe` instead of being suppressed by the cooldown.

---

### 3.3 `test_wsl_bootstrap_first_attempt_is_not_suppressed_by_low_monotonic_clock`

**What it tests:** A freshly-started process has `time.monotonic()` close to zero.
The cooldown mechanism must not incorrectly suppress the **first ever** WSL
boot attempt just because the monotonic clock starts below the cooldown threshold.

**How to run:**

```powershell
python -m pytest tests/test_remote_ssh.py::TestWSLBootstrap::test_wsl_bootstrap_first_attempt_is_not_suppressed_by_low_monotonic_clock -v
```

**Expected output:**

```
tests/test_remote_ssh.py ... PASSED
```

**Pass criteria:**
- `subprocess.run` is called exactly **once**
- The call succeeds (does not raise `CalledProcessError`)

**What failure looks like:** `run_wsl` not called — the low `monotonic()` value
is being treated as a "recent failure in cooldown" instead of as "no prior history".
The WSL distro never boots and the SSH connection fails or connects to the wrong host.

---

## 4. Manual Smoke Test — Workflow Run CLI

Once the SessionPool fix (Task 1) is complete and the CLI `run` subcommand is
implemented, verify the end-to-end CLI workflow on Windows:

```powershell
# 1. Navigate to a project directory with a confflow.yaml
cd C:\path\to\project

# 2. Create a run (dry run — no real submission)
python -m jobdesk_app run create . `
    --server hpc `
    --remote-dir /tmp/<username>/smoke_test `
    --command "confflow run confflow.yaml"

# Expected: prints "created run <run_id>" with no error

# 3. List runs
python -m jobdesk_app run list C:\path\to\project

# Expected: shows the run with server "hpc" and the remote dir

# 4. Upload a file via the files subcommand
python -m jobdesk_app files upload hpc .\mol.xyz /tmp/<username>/smoke_test/mol.xyz

# Expected: prints "uploaded mol.xyz" or "skipped (same size)" if already present
```

**Pass criteria for all four commands:** exit code `0`, no Python traceback,
output contains the expected confirmation string.

---

## 5. Integration Test Run

### Set up the environment

```powershell
# In PowerShell, from the repo root:
$env:JOBDESK_TEST_SERVERS_YAML = "C:\Users\<you>\AppData\Roaming\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "hpc"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/<username>/jobdesk_tests"
$env:JOBDESK_TEST_CONFFLOW_YAML = "/home/<username>/confflow.yaml"
$env:JOBDESK_TEST_REAL_AGENT = "1"
```

### Run all integration tests

```powershell
python -m pytest tests/integration/test_agent_e2e.py -v
```

### Run only agent lifecycle tests (fastest smoke)

```powershell
python -m pytest tests/integration/test_agent_e2e.py::test_agent_submit_and_poll -v
```

### Expected results (clean environment)

| Test | Expected | Notes |
|---|---|---|
| `test_agent_install_and_start` | SKIPPED or PASSED | Skipped if agent already installed |
| `test_agent_submit_and_poll` | PASSED | Poll interval: 60 × 1 s timeout |
| `test_agent_pause_resume_cancel` | PASSED | Full lifecycle; ~2–5 minutes |
| `test_agent_status` | PASSED | Fast; no submission needed |

### Tear-down

Remove the test directory on HPC after all tests pass:

```bash
ssh <hpc_username>@<hpc_host> "rm -rf /tmp/<username>/jobdesk_tests"
```
