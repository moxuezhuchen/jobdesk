# JobDesk v1.0 RC Report

## Current Status

JobDesk has passed local automated verification and real SSH backend validation
for the core lifecycle.

The project is ready for v1.0 RC bug bash, with development focused on fixes,
documentation, and packaging polish rather than new workflow features.

## Verified Capabilities

- Project config loading with the current schema.
- Multiple task discoveries.
- TaskPackage to TaskRecord batch creation.
- Frozen manifest runtime fields.
- Upload rules and shared files.
- Real SSH connection to server `814new`.
- Real SFTP upload/download.
- Real remote submitter execution.
- Real workflow lifecycle:
  `scan -> create_batch -> upload -> submit -> refresh -> download -> analyze`.
- Batch list/load/latest recovery.
- Failures written to `failures.tsv`.
- Results enrichment in GUI helpers.
- CLI entry points for local workflow operations.

## Latest Verification

Local:

```text
pytest -q --basetemp .\tmp_pytest_full
369 passed, 5 skipped, 1 warning
```

Real backend:

```text
pytest tests\integration\test_real_ssh.py tests\integration\test_real_sftp.py tests\integration\test_real_submitter.py tests\integration\test_real_lifecycle.py -q --basetemp C:\tmp\jobdesk_pytest_real_all
5 passed
```

## Notes

- `5 skipped` in the local run is expected without real backend environment
  variables.
- The pytest cache warning is caused by workspace `.pytest_cache` permissions and
  does not affect test results.
- Real backend validation used:
  - `servers.yaml`: `C:\Users\moxue\AppData\Roaming\JobDesk\servers.yaml`
  - `server_id`: `814new`
  - remote temp root: `/tmp/jobdesk_test`

## Changes Made During RC Prep

- Added `.gitignore` rules for Python caches, test caches, local fake projects,
  and one-off validation scripts.
- Fixed Windows compatibility in `tests/integration/test_real_submitter.py` by
  closing the temporary file before `Manifest.write()` performs atomic replace.
- Added a Tasks page batch header showing `batch_id`, task count, execution
  profiles, server ids, shared file count, and manifest path.

## Recommended RC Bug Bash

1. Run the automated checks from `docs/V1_RC_CHECKLIST.md`.
2. Perform the manual GUI smoke checklist.
3. File only blocker or polish issues.
4. Do not add new compute backends or workflow features during RC.
