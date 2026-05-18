# JobDesk Examples

Example projects live under `examples/`.

## shell_basic

Single profile, single shell script task.

Run command:

```yaml
command: "bash {entry_name}"
```

## mixed_profiles_fake

Two fake profiles, `g16` and `orca`, both running shell scripts. This example is
useful for validating mixed-profile batch behavior without real Gaussian or ORCA
installations.

## shared_files

Demonstrates `upload.shared_files` and `{shared_dir_abs}`.

The task script receives the shared file path as an argument:

```yaml
command: "bash {entry_name} {shared_dir_abs}/basis.dat"
```

