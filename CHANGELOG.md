# Changelog

## Unreleased

- **ConfFlow integration complete (Stage 1–5).**
  - Stage 1: stand-alone `confflow-agent` daemon + systemd unit + install script.
  - Stage 1.5: resume re-enqueues; PAUSE interrupts running subprocess via
    `<step_dir>/STOP` beacon.
  - Stage 2: monolithic `confflow/` package flat-moved into the JobDesk
    monorepo under `jobdesk_app.workflow`. All `from confflow.X` imports
    rewritten. See `MIGRATION.md`.
  - Stage 3: JobDesk Files page "Run ConfFlow" forwards via SSH/SFTP to
    `confflow-agent`. New "View Agent Jobs" view pollls daemon and exposes
    pause / resume / cancel / download for any agent job id.
  - Stage 4: declarative **Workflow Builder** wizard (PySide6 form-based).
    CLI `jobdesk workflow build|check|presets`. New
    `jobdesk_app.workflow.{schema,builder}` modules.
  - Stage 5: removed legacy `_run_confflow` / `ConfFlowAdapter` direct-submit
    path; the Files page now exclusively uses AgentBridge.
- **Docs**: added `MIGRATION.md` (ConfFlow → JobDesk) and `AGENT_SETUP.md`
  (confflow-agent lifecycle).
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
