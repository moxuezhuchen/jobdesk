"""JobDesk CLI — run + files commands powered by RunService.

This module previously lived at the top level as ``jobdesk_app/cli.py``; it
moved to ``jobdesk_app/cli/__init__.py`` so the ``jobdesk_app.cli`` package
can host subcommand modules (``agent_cmd``, ``workflow_cmd``). The
``pyproject.toml`` entry point ``jobdesk = "jobdesk_app.cli:main"`` continues
to work because ``main`` is re-exported below.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config.servers import load_servers
from ..core.file_transfer import OverwritePolicy
from ..core.run import RunMode, RunSource, RunSpec
from ..core.transfer import TransferStatus
from ..services.file_transfer_service import FileTransferService
from ..services.job_id_overrides import JobIdOverridesError, parse_job_id_overrides
from ..services.run_coordinator import RunCoordinator
from ..services.run_service import RunService
from ..services.ssh_session import ConnectedSFTP, create_sftp_client, create_ssh_client


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


# ---- parser ---------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobdesk")
    parser.add_argument("--servers-yaml", type=Path, default=None)
    sub = parser.add_subparsers(dest="command")

    # -- run subcommand group --
    run = sub.add_parser("run", help="Manage runs")
    run_sub = run.add_subparsers(dest="run_command", required=True)

    cr = run_sub.add_parser("create")
    cr.add_argument("workspace", type=Path)
    cr.add_argument("--server", required=True)
    cr.add_argument("--remote-dir", required=True)
    cr.add_argument("--command", required=True)
    cr.add_argument("--files", nargs="+", default=[])
    cr.add_argument("--dirs", nargs="+", default=[])
    cr.add_argument("--mode", default="selected_files", choices=[m.value for m in RunMode])
    cr.add_argument("--max-parallel", type=int, default=4)
    cr.set_defaults(func=_cmd_run_create)

    for name, func in [
        ("list", _cmd_run_list),
        ("submit", _cmd_run_submit),
        ("refresh", _cmd_run_refresh),
        ("cancel", _cmd_run_cancel),
        ("delete", _cmd_run_delete),
        ("retry", _cmd_run_retry),
        ("rerun", _cmd_run_rerun),
    ]:
        p = run_sub.add_parser(name)
        p.add_argument("workspace", type=Path)
        if name != "list":
            p.add_argument("run_id")
        if name == "submit":
            p.add_argument("--cpus", type=int, default=None)
            p.add_argument("--mem-mb", type=int, default=None)
            p.add_argument("--walltime", type=int, default=None)
            p.add_argument("--partition", default=None)
        p.set_defaults(func=func)

    dl = run_sub.add_parser("download")
    dl.add_argument("workspace", type=Path)
    dl.add_argument("run_id")
    dl.add_argument("--patterns", nargs="+", default=["*.log"],
                    help="Output file patterns (comma-separated within each arg; commas in filenames not supported)")
    dl.set_defaults(func=_cmd_run_download)

    for name, func in (
        ("confirm-submitted", _cmd_run_confirm_submitted),
        ("abandon-submit", _cmd_run_abandon_submit),
    ):
        recovery = run_sub.add_parser(name)
        recovery.add_argument("workspace", type=Path)
        recovery.add_argument("run_id")
        recovery.add_argument("--tasks", nargs="+", required=True)
        if name == "confirm-submitted":
            recovery.add_argument("--job-id", action="append", default=[])
        recovery.set_defaults(func=func)

    recover = run_sub.add_parser("recover")
    recover.add_argument("workspace", type=Path)
    recover.set_defaults(func=_cmd_run_recover_operations)

    # -- compare subcommand --
    cmp = sub.add_parser("compare", help="Compare results across runs")
    cmp.add_argument("workspace", type=Path)
    cmp.add_argument("run_ids", nargs="+")
    cmp.add_argument("--field", default="scf_energy")
    cmp.add_argument("--profile", default="gaussian_opt_freq")
    cmp.add_argument("--output", type=Path, default=None)
    cmp.add_argument("--format", choices=["csv", "markdown"], default="csv")
    cmp.set_defaults(func=_cmd_compare)

    # -- files subcommand group --
    files = sub.add_parser("files", help="Remote file operations")
    files_sub = files.add_subparsers(dest="files_command", required=True)

    lr = files_sub.add_parser("list-remote")
    lr.add_argument("server_id")
    lr.add_argument("remote_path")
    lr.set_defaults(func=_cmd_files_list_remote)

    up = files_sub.add_parser("upload")
    up.add_argument("server_id")
    up.add_argument("local_path", type=Path)
    up.add_argument("remote_path")
    up.add_argument("--overwrite", action="store_true", help="Overwrite remote files that differ")
    up.add_argument("--dry-run", action="store_true", help="Report planned actions without transferring")
    up.set_defaults(func=_cmd_files_upload)

    dn = files_sub.add_parser("download")
    dn.add_argument("server_id")
    dn.add_argument("remote_path")
    dn.add_argument("local_path", type=Path)
    dn.add_argument("--overwrite", action="store_true", help="Overwrite local files that differ")
    dn.add_argument("--dry-run", action="store_true", help="Report planned actions without transferring")
    dn.set_defaults(func=_cmd_files_download)

    mk = files_sub.add_parser("mkdir")
    mk.add_argument("server_id")
    mk.add_argument("remote_path")
    mk.set_defaults(func=_cmd_files_mkdir)

    pv = files_sub.add_parser("preview")
    pv.add_argument("server_id")
    pv.add_argument("remote_path")
    pv.set_defaults(func=_cmd_files_preview)

    # -- workflow subcommand group --
    from .workflow_cmd import add_parser as _workflow_add_parser
    _workflow_add_parser(sub)

    # -- agent subcommand group --
    from .agent_cmd import _build_agent_parser as _agent_build_parser

    agent_p = sub.add_parser(
        "agent",
        help="Manage ConfFlow agents on remote servers",
        description="Bridge to jobdesk_app.cli.agent_cmd subcommands.",
    )
    agent_p.add_argument(
        "agent_args",
        nargs=argparse.REMAINDER,
        help="Subcommand and arguments forwarded to jobdesk_app.cli.agent_cmd",
    )
    agent_p.set_defaults(func=_cmd_agent_dispatch)

    return parser


# ---- agent dispatcher -----------------------------------------------------


def _cmd_agent_dispatch(args) -> int:
    from .agent_cmd import _build_agent_parser

    sub = _build_agent_parser()
    sub_args = sub.parse_args(args.agent_args)
    return sub_args.func(sub_args)


# ---- run commands ---------------------------------------------------------


def _cmd_run_create(args) -> int:
    sources = [RunSource(path=f, is_dir=False) for f in args.files]
    sources += [RunSource(path=d, is_dir=True) for d in args.dirs]
    spec = RunSpec(
        server_id=args.server,
        remote_dir=args.remote_dir,
        command_template=args.command,
        max_parallel=args.max_parallel,
        mode=RunMode(args.mode),
        sources=sources,
    )
    outcome = _run_coordinator(args, args.workspace).create_run(spec)
    if outcome.errors:
        print(outcome.errors[0])
        return 2
    record = outcome.records[0]
    print(f"created run {record.run_id}: {record.status_summary}")
    return 0


def _cmd_run_list(args) -> int:
    service = RunService(args.workspace)
    runs = service.list_runs()
    for error in service.migration_errors():
        print(
            f"WARNING: legacy run import failed: {error.legacy_path}: {error.message}",
            file=sys.stderr,
        )
    if not runs:
        print("No runs")
        return 0
    for r in runs:
        print(f"{r.run_id}\t{r.server_id}\t{r.remote_dir}\t{r.mode}\t{r.status_summary}")
    return 0


def _cmd_run_submit(args) -> int:
    overrides = {}
    if getattr(args, "cpus", None) is not None:
        overrides["cpus"] = args.cpus
    if getattr(args, "mem_mb", None) is not None:
        overrides["memory_mb"] = args.mem_mb
    if getattr(args, "walltime", None) is not None:
        overrides["walltime_minutes"] = args.walltime
    if getattr(args, "partition", None) is not None:
        overrides["partition"] = args.partition
    for key in ("cpus", "memory_mb", "walltime_minutes"):
        if key in overrides and int(overrides[key]) < 1:
            print(
                f"scheduler {key} must be >= 1: {overrides[key]}",
                file=sys.stderr,
            )
            return 2
    outcome = _run_coordinator(args, args.workspace).submit(
        args.run_id,
        resource_overrides=overrides or None,
    )
    if not outcome.submit_results:
        for error in outcome.errors:
            print(f"  ERROR: {error}")
        return 2
    result = outcome.submit_results[0]
    print(f"submitted={result.submitted_task_count}, errors={len(result.errors)}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    return 0 if not result.errors else 2


def _cmd_run_refresh(args) -> int:
    outcome = _run_coordinator(args, args.workspace).refresh(args.run_id)
    if outcome.refresh_result is None:
        for error in outcome.errors:
            print(f"  ERROR: {error}")
        return 2
    result = outcome.refresh_result
    print(f"changed={result.changed_count}, warnings={len(result.warnings)}")
    return 0


def _cmd_run_download(args) -> int:
    patterns = [p.strip() for arg in args.patterns for p in arg.split(",") if p.strip()]
    outcome = _run_coordinator(args, args.workspace).download(args.run_id, patterns)
    if outcome.errors and not outcome.failures:
        for error in outcome.errors:
            print(f"  ERROR: {error}")
        return 2
    records = outcome.transfer_records
    failures = outcome.failures
    transferred = sum(1 for r in records if r.status == TransferStatus.transferred)
    print(f"downloaded={transferred}, failures={len(failures)}")
    return 0 if not failures else 2


def _cmd_run_cancel(args) -> int:
    outcome = _run_coordinator(args, args.workspace).cancel(args.run_id)
    changed = outcome.changed_count
    errors = outcome.errors
    print(f"cancelled {changed} task(s)")
    for error in errors:
        print(f"  ERROR: {error}")
    return 0 if not errors else 2


def _cmd_run_delete(args) -> int:
    outcome = _run_coordinator(args, args.workspace).delete(args.run_id)
    if outcome.errors:
        print(outcome.errors[0])
        return 2
    print(f"deleted run {args.run_id}")
    return 0


def _cmd_run_retry(args) -> int:
    outcome = _run_coordinator(args, args.workspace).retry_failed(args.run_id)
    if outcome.errors:
        print(outcome.errors[0])
        return 2
    changed = outcome.changed_count
    if changed == 0:
        print("No failed tasks to retry")
        return 0
    print(f"reset {changed} failed task(s) to uploaded, run `jobdesk run submit` to resubmit")
    return 0


def _cmd_run_rerun(args) -> int:
    outcome = _run_coordinator(args, args.workspace).rerun(args.run_id)
    if outcome.errors:
        print(outcome.errors[0])
        return 2
    changed = outcome.changed_count
    print(f"reset {changed} task(s) to uploaded, run `jobdesk run submit` to resubmit")
    return 0


def _cmd_run_confirm_submitted(args) -> int:
    try:
        remote_job_ids = parse_job_id_overrides(args.job_id, args.tasks)
    except JobIdOverridesError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    outcome = _run_coordinator(args, args.workspace).confirm_submitted(
        args.run_id, args.tasks, remote_job_ids or None
    )
    return _print_recovery_outcome("confirmed", "task(s)", outcome)


def _cmd_run_abandon_submit(args) -> int:
    outcome = _run_coordinator(args, args.workspace).abandon_submit(
        args.run_id, args.tasks
    )
    return _print_recovery_outcome("abandoned", "task(s)", outcome)


def _cmd_run_recover_operations(args) -> int:
    outcome = _run_coordinator(args, args.workspace).recover_operations(
        include_legacy_imports=True
    )
    return _print_recovery_outcome("recovered", "operation(s)", outcome)


def _print_recovery_outcome(action: str, noun: str, outcome) -> int:
    print(f"{action} {outcome.changed_count} {noun}")
    for error in outcome.errors:
        print(f"  ERROR: {error}")
    return 0 if not outcome.errors else 2


def _cmd_compare(args) -> int:
    from ..services.comparison import compare_runs, export_csv, export_markdown
    comparison = compare_runs(args.workspace, args.run_ids, args.field, args.profile)
    if not comparison.rows:
        print("No results found for the specified runs and profile.")
        return 2
    if args.format == "markdown":
        output = export_markdown(comparison)
    else:
        output = export_csv(comparison, args.output)
    if args.output and args.format == "csv":
        print(f"Exported {len(comparison.rows)} rows to {args.output}")
    else:
        print(output)
    return 0


# ---- files commands -------------------------------------------------------


def _file_transfer_service(args, server_id: str) -> FileTransferService:
    server = _get_server_by_id(args, server_id)

    def factory():
        ssh = create_ssh_client(server)
        ssh.connect()
        sftp = create_sftp_client(ssh)
        return ConnectedSFTP(ssh, sftp)

    return FileTransferService(factory)


def _cmd_files_list_remote(args) -> int:
    entries = _file_transfer_service(args, args.server_id).list_remote(args.remote_path)
    for entry in entries:
        kind = "dir" if entry.is_dir else "file"
        size = "" if entry.size_bytes is None else str(entry.size_bytes)
        print(f"{kind}\t{size}\t{entry.permissions}\t{entry.path}")
    return 0


def _cmd_files_upload(args) -> int:
    policy = OverwritePolicy.overwrite if args.overwrite else OverwritePolicy.skip_same_size
    records = _file_transfer_service(args, args.server_id).upload_path(
        args.local_path, args.remote_path, policy, dry_run=args.dry_run
    )
    if not isinstance(records, list):
        records = [records]
    failures = sum(1 for r in records if r.status == TransferStatus.failed)
    print(f"upload: records={len(records)}, failures={failures}")
    return 0 if failures == 0 else 2


def _cmd_files_download(args) -> int:
    policy = OverwritePolicy.overwrite if args.overwrite else OverwritePolicy.skip_same_size
    records = _file_transfer_service(args, args.server_id).download_path(
        args.remote_path, args.local_path, policy, dry_run=args.dry_run
    )
    if not isinstance(records, list):
        records = [records]
    failures = sum(1 for r in records if r.status == TransferStatus.failed)
    print(f"download: records={len(records)}, failures={failures}")
    return 0 if failures == 0 else 2


def _cmd_files_mkdir(args) -> int:
    _file_transfer_service(args, args.server_id).mkdir_remote(args.remote_path)
    print(f"created {args.remote_path}")
    return 0


def _cmd_files_preview(args) -> int:
    print(_file_transfer_service(args, args.server_id).preview_remote_text(args.remote_path), end="")
    return 0


# ---- helpers --------------------------------------------------------------


def _get_server(args):
    """Get server config for the run being operated on."""
    record = RunService(args.workspace).load_run(args.run_id)
    return _get_server_by_id(args, record.server_id)


def _run_coordinator(args, workspace: Path) -> RunCoordinator:
    return RunCoordinator(
        RunService(workspace),
        server_lookup=lambda server_id: _get_server_by_id(args, server_id),
        ssh_factory=create_ssh_client,
        sftp_factory=create_sftp_client,
    )


def _get_server_by_id(args, server_id: str):
    servers = load_servers(args.servers_yaml).servers if args.servers_yaml else load_servers().servers
    if server_id not in servers:
        raise SystemExit(f"server not found: {server_id}")
    return servers[server_id]


if __name__ == "__main__":
    raise SystemExit(main())