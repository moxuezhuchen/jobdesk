# JobDesk Configuration Reference

## project.yaml

`project.yaml` describes project rules. It should not contain `server_id` or
`remote_work_dir`.

Required top-level fields:

```yaml
project_id: my-project
project:
  name: My Project
local_paths:
  input_dir: ./inputs
  result_dir: ./results
task_discoveries: []
execution_profiles: {}
```

## task_discoveries

Each rule discovers task packages.

```yaml
task_discoveries:
  - name: shell_jobs
    mode: flat_single
    entry_glob: "*.sh"
    task_id_prefix: shell_
    execution_profile: shell
```

Supported modes:

- `flat_single`
- `grouped_by_stem`
- `directory`

## execution_profiles

Each profile defines the command template and defaults.

```yaml
execution_profiles:
  shell:
    label: Shell
    command: "bash {entry_name}"
    defaults:
      max_parallel: 4
```

Supported command variables include:

- `{task_id}`
- `{entry_name}`
- `{entry_stem}`
- `{input_name}`
- `{stem}`
- `{job_dir}`
- `{batch_id}`
- `{shared_dir}`
- `{shared_dir_abs}`

JobDesk shell-quotes variable values. Do not put template variables inside your
own quotes.

Recommended:

```yaml
command: "bash {entry_name}"
```

Avoid:

```yaml
command: "bash '{entry_name}'"
```

## upload.task_files

```yaml
upload:
  task_files:
    include: ["*.sh"]
    exclude: []
    require_entry_file: true
    on_missing: error
```

## upload.shared_files

```yaml
upload:
  shared_files:
    base_dir: shared
    include: ["basis.dat"]
    exclude: []
    target_subdir: _shared
    on_missing: error
```

Shared files are frozen into `batch.json` at batch creation time.

## runtime_bindings.yaml

Runtime bindings are machine-local.

```yaml
bindings:
  my-project:
    shell:
      server_id: srv1
      remote_work_dir: /tmp/jobdesk/my-project
      max_parallel: 4
```

Changing runtime bindings does not affect already-created batches.

