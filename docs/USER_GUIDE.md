# JobDesk User Guide

JobDesk is a Windows 11 local workbench for managing batch scientific computing
tasks on remote Linux servers through SSH.

It does not understand Gaussian, ORCA, xTB, ConfFlow, or any other program
semantics. It discovers files, uploads them, runs your command templates, checks
status files, downloads results, and applies generic regex extraction rules.

## Basic Workflow

1. Configure `servers.yaml`.
2. Open a project containing `project.yaml`.
3. Configure runtime bindings for every execution profile.
4. Scan inputs.
5. Create a batch.
6. Upload files.
7. Submit jobs.
8. Refresh status.
9. Download completed results.
10. Analyze downloaded results.
11. Review `final_results.tsv`, `job_status.tsv`, and `failures.tsv`.

## Important Files

Project files:

```text
project/
  project.yaml
  inputs/
  results/
  .jobdesk/
    batches/
      <batch_id>/
        batch.json
        manifest.tsv
        failures.tsv
```

Machine-local files:

```text
%APPDATA%/JobDesk/
  servers.yaml
  runtime_bindings.yaml
  logs/
```

## Batch Recovery

Created batches are listed from `.jobdesk/batches/`.

The GUI Tasks page automatically selects the latest batch when a project is
opened. Batch execution uses the frozen `server_id`, `remote_work_dir`, and
`max_parallel` stored in `manifest.tsv`, not the current runtime binding.

## Result Review

The Results page can show:

- `enriched_results`
- `final_results.tsv`
- `failures.tsv`
- `group_summary.tsv`
- `job_status.tsv`

`enriched_results` joins `final_results.tsv` with manifest metadata such as
`execution_profile`, `discovery_name`, `server_id`, and `status`.

## CLI

After installation, the same workflow service is available as `jobdesk`:

```powershell
jobdesk scan <project>
jobdesk preflight <project> --servers-yaml <servers.yaml>
jobdesk list-batches <project>
jobdesk create-batch <project> --servers-yaml <servers.yaml>
jobdesk upload <project> <batch_id> --servers-yaml <servers.yaml>
jobdesk submit <project> <batch_id> --servers-yaml <servers.yaml>
jobdesk refresh <project> <batch_id> --servers-yaml <servers.yaml>
jobdesk download <project> <batch_id> --servers-yaml <servers.yaml>
jobdesk analyze <project> <batch_id>
```

Remote cleanup is deliberately separate and dry-run friendly:

```powershell
jobdesk cleanup-remote <project> <batch_id> --servers-yaml <servers.yaml> --dry-run
```

Without `--dry-run`, cleanup only targets frozen batch directories of the form
`{remote_work_dir}/{batch_id}` from `manifest.tsv`.
