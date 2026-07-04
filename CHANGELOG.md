# Changelog

## Unreleased

- Schema v4 is current: v2 introduced the replayable submit/delete operation journal, v3 added trusted-workspace registry and delete-operation bindings, and v4 added UTC submit ownership leases so recovery cannot take over a live submission. Completed operations are retained for seven days, and legacy orphan `submitting` tasks are recovered into `uncertain`.
- Added explicit recovery commands for confirming or abandoning uncertain submissions and a shared, GUI-independent SSH/SFTP `SessionPool` ownership model.
- Replaced writable per-run JSON/TSV manifests with transactional SQLite persistence in the JobDesk runs directory, including one-time legacy import and migration diagnostics.
- Added a shared run coordinator for CLI and GUI lifecycle operations and separated the run monitor service from its Qt adapter.
- Added architecture-boundary, concurrent persistence, migration, coordinator, and GUI delegation regression coverage.
- Prepared the repository for public source preview under Apache License 2.0.
- Consolidated the GUI around single-task execution and ConfFlow batch submission.
- Added guarded remote cancellation, explicit SSH host-key trust configuration, and restricted recursive remote deletion.
- Hardened run persistence, result download diagnostics, task identity generation, scheduler validation, and XYZ validation.
- Added GUI button feedback polish and strengthened GUI behavior coverage.
- Included GUI resource assets in distributable packages and strengthened CI quality gates.
