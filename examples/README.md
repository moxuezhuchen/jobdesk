# Examples

Minimal input files for quick-start usage with JobDesk.

## Gaussian

```powershell
# Create a run and submit
jobdesk run create . --server wcm --remote-dir /scratch/user/water --command "g16 {name}" --files examples/gaussian/water_opt.gjf
jobdesk run submit . <run_id>
```

## ORCA

```powershell
jobdesk run create . --server wcm --remote-dir /scratch/user/water --command "orca {name} > {basename}.out" --files examples/orca/water_opt.inp
jobdesk run submit . <run_id>
```

## Workflow (opt → freq → sp)

```powershell
jobdesk workflow list
jobdesk workflow run . opt_freq_sp --server wcm --remote-dir /scratch/user/water --files examples/gaussian/water_opt.gjf
```

## Per-run resource overrides

```powershell
jobdesk run submit . <run_id> --cpus 8 --mem-mb 16000 --walltime 720 --partition normal
```
