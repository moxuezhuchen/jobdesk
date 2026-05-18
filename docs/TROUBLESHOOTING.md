# JobDesk Troubleshooting

## SSH Connection Fails

Check:

- `servers.yaml` path
- `server_id`
- host, port, username
- SSH key path
- server network access

Run:

```powershell
pytest tests/integration/test_real_ssh.py -v
```

## SFTP Upload Fails

Check:

- remote directory permissions
- remote path uses POSIX `/`
- `remote_work_dir` is writable
- local files selected by upload rules exist

Look at:

```text
.jobdesk/batches/<batch_id>/failures.tsv
```

## Submit Does Nothing

If there are no `uploaded` tasks, JobDesk records a no-op submit failure. Upload
the batch first or refresh the manifest state.

## Tasks Stay Running

Refresh reads remote `.jobdesk_status` and `.jobdesk_exit_code` files. Check:

```text
<remote_job_dir>/.jobdesk_submit.log
<remote_job_dir>/.jobdesk_status
<remote_job_dir>/.jobdesk_exit_code
```

## Download Fails

Check:

- `download.patterns`
- remote result files exist
- local results directory is writable

Partial failures should not stop other tasks.

## Analyze Finds No Results

Check:

- files were downloaded under `results/<batch_id>/<task_id>/`
- `extract.results[].source_glob`
- regex named group `(?P<value>...)`

## Batch Looks Corrupted

`manifest.tsv` and `batch.json` are written with same-directory temporary files
and atomic replace. If a file is still corrupted, `load_batch()` should report
which file failed.

## Remote Cleanup

Use `cleanup-remote --dry-run` first. JobDesk builds cleanup targets only from
the batch manifest and refuses unsafe `batch_id` or `remote_work_dir` values.
It does not clean arbitrary remote directories.
