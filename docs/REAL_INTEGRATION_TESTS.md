# JobDesk Real Integration Tests

This document explains how to run JobDesk tests against a real Linux SSH server.

The real tests are skipped by default. They run only when the required
environment variables are set.

## Required Environment

PowerShell:

```powershell
$env:JOBDESK_TEST_SERVERS_YAML = "$env:APPDATA\JobDesk\servers.yaml"
$env:JOBDESK_TEST_SSH_SERVER_ID = "your_server_id"
$env:JOBDESK_TEST_REMOTE_TMP_DIR = "/tmp/jobdesk_test"
```

`JOBDESK_TEST_SERVERS_YAML` must point to a valid JobDesk `servers.yaml`.

`JOBDESK_TEST_SSH_SERVER_ID` must be a key in that file.

`JOBDESK_TEST_REMOTE_TMP_DIR` must be a writable remote POSIX directory. Tests
create subdirectories under it and remove those subdirectories at the end.

## Optional Multi-Server Environment

PowerShell:

```powershell
$env:JOBDESK_TEST_SERVER_ID_A = "server_a"
$env:JOBDESK_TEST_SERVER_ID_B = "server_b"
```

If these are not set, the full lifecycle test uses `JOBDESK_TEST_SSH_SERVER_ID`
for both fake execution profiles. This still validates mixed-profile lifecycle
behavior on one real server.

If both are set, they must both exist in `servers.yaml`. The lifecycle test then
uses profile `g16` on server A and profile `orca` on server B.

## Run Commands

Run all real integration tests:

```powershell
pytest tests/integration/ -v
```

Run only the full workflow lifecycle test:

```powershell
pytest tests/integration/test_real_lifecycle.py -v
```

Run normal local tests:

```powershell
pytest -q
```

Without the environment variables, integration tests should be skipped and the
local suite should remain green.

## What `test_real_lifecycle.py` Covers

The test creates a temporary project with:

- two fake execution profiles: `g16` and `orca`
- two task discoveries
- one shared file: `shared/basis.dat`
- shell scripts as task entry files
- result extraction from `result.out`

The test runs:

```text
scan_inputs
create_batch
upload_tasks
submit_batch
refresh_batch until remote_completed
download_completed
analyze_batch
```

It verifies:

- task files upload through real SFTP
- shared files upload through real SFTP
- submit uses the real remote batch control scripts
- refresh observes remote completion
- download retrieves result files
- analyze extracts two result values
- temporary remote directories are cleaned up

## Server Requirements

The server must support:

- SSH key login
- SFTP
- `bash`
- `nohup`
- `xargs`
- write access under `JOBDESK_TEST_REMOTE_TMP_DIR`

No Gaussian, ORCA, xTB, ConfFlow, Slurm, or PBS installation is required for
these tests.

## Safety

The lifecycle test creates a unique remote directory:

```text
{JOBDESK_TEST_REMOTE_TMP_DIR}/jobdesk_lifecycle_<random>
```

Cleanup removes only that unique directory.

Do not set `JOBDESK_TEST_REMOTE_TMP_DIR` to a sensitive directory. Use a scratch
location such as:

```text
/tmp/jobdesk_test
```

