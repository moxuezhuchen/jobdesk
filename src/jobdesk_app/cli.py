"""JobDesk CLI presentation adapter backed by public application facades."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bootstrap import (
    JobIdOverridesError,
    create_application,
    parse_job_id_overrides,
)
from .core.file_transfer import OverwritePolicy
from .core.run import RunMode, RunSource, RunSpec
from .core.transfer import TransferStatus


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1

    # The CLI is a presentation adapter.  Bootstrap owns construction of the
    # concrete application graph, while this boundary owns its lifetime.  Keep
    # the container on the parsed namespace so commands can migrate to the
    # public facades without growing new infrastructure imports.
    application = create_application(
        getattr(args, "workspace", None),
        servers_path=getattr(args, "servers_yaml", None),
    )
    args.application = application
    try:
        return args.func(args)
    finally:
        application.close()


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
    dl.add_argument(
        "--patterns",
        nargs="+",
        default=["*.log"],
        help="Output file patterns (comma-separated within each arg; commas in filenames not supported)",
    )
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

    verify_rollback = run_sub.add_parser(
        "verify-rollback",
        help="Fail closed unless every ConfFlow JSON projection matches SQLite authority",
    )
    verify_rollback.add_argument("workspace", type=Path)
    verify_rollback.set_defaults(func=_cmd_run_verify_rollback)

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

    return parser


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
    outcome = args.application.runs.create(spec)
    if outcome.failures:
        print(outcome.failures[0].display_text)
        return 2
    assert outcome.value is not None
    record = outcome.value.summary
    print(f"created run {record.run_id}: {record.status_summary}")
    return 0


def _cmd_run_list(args) -> int:
    runs = args.application.runs.list_runs()
    for error in args.application.runs.migration_failures():
        print(
            f"WARNING: legacy run import failed: {error.display_text}",
            file=sys.stderr,
        )
    if not runs:
        print("No runs")
        return 0
    for r in runs:
        details = args.application.runs.get_run(r.run_id)
        print(f"{r.run_id}\t{r.server_id}\t{r.remote_dir}\t{details.mode}\t{r.status_summary}")
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
    outcome = args.application.runs.submit_existing(args.run_id, resource_overrides=overrides or None)
    if outcome.value is None:
        _print_application_failures(outcome)
        return 2
    result = outcome.value
    print(f"submitted={result.changed_count}, errors={len(outcome.failures)}")
    _print_application_failures(outcome)
    # P-H0 (R-H0): surface advisory warnings to stderr so CI / shell
    # users can see producer build / resource budget advisories
    # without going through the GUI.
    for w in result.warnings:
        print(f"  WARNING: {w}", file=sys.stderr)
    return 0 if not outcome.failures else 2


def _cmd_run_refresh(args) -> int:
    outcome = args.application.runs.refresh(args.run_id)
    if outcome.value is None:
        _print_application_failures(outcome)
        return 2
    print(f"changed={outcome.value.changed_count}, warnings={len(outcome.value.warnings)}")
    return 0


def _cmd_run_download(args) -> int:
    patterns = [p.strip() for arg in args.patterns for p in arg.split(",") if p.strip()]
    outcome = args.application.runs.download(args.run_id, tuple(patterns))
    if outcome.value is None:
        _print_application_failures(outcome)
        return 2
    failures = len(outcome.failures)
    print(f"downloaded={len(outcome.value.local_paths)}, failures={failures}")
    return 0 if failures == 0 else 2


def _cmd_run_cancel(args) -> int:
    outcome = args.application.runs.cancel(args.run_id)
    changed = outcome.value.changed_count if outcome.value is not None else 0
    print(f"cancelled {changed} task(s)")
    _print_application_failures(outcome)
    return 0 if not outcome.failures else 2


def _cmd_run_delete(args) -> int:
    outcome = args.application.runs.delete(args.run_id)
    if outcome.failures:
        print(outcome.failures[0].display_text)
        return 2
    print(f"deleted run {args.run_id}")
    return 0


def _cmd_run_retry(args) -> int:
    outcome = args.application.runs.prepare_retry_failed(args.run_id)
    if outcome.failures:
        print(outcome.failures[0].display_text)
        return 2
    changed = outcome.value.changed_count if outcome.value is not None else 0
    if changed == 0:
        print("No failed tasks to retry")
        return 0
    print(f"reset {changed} failed task(s) to uploaded, run `jobdesk run submit` to resubmit")
    return 0


def _cmd_run_rerun(args) -> int:
    outcome = args.application.runs.prepare_rerun(args.run_id)
    if outcome.failures:
        print(outcome.failures[0].display_text)
        return 2
    changed = outcome.value.changed_count if outcome.value is not None else 0
    print(f"reset {changed} task(s) to uploaded, run `jobdesk run submit` to resubmit")
    return 0


def _cmd_run_confirm_submitted(args) -> int:
    try:
        remote_job_ids = parse_job_id_overrides(args.job_id, args.tasks)
    except JobIdOverridesError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    outcome = args.application.runs.resolve_uncertain(
        args.run_id,
        tuple(args.tasks),
        action="confirm",
        remote_job_ids=remote_job_ids or None,
    )
    return _print_recovery_outcome("confirmed", "task(s)", outcome)


def _cmd_run_abandon_submit(args) -> int:
    outcome = args.application.runs.resolve_uncertain(args.run_id, tuple(args.tasks), action="abandon")
    return _print_recovery_outcome("abandoned", "task(s)", outcome)


def _cmd_run_recover_operations(args) -> int:
    outcome = args.application.runs.recover(include_legacy_imports=True)
    return _print_recovery_outcome("recovered", "operation(s)", outcome)


def _cmd_run_verify_rollback(args) -> int:
    """Check the pre-JD2b JSON compatibility boundary before a rollback."""
    outcome = args.application.runs.verify_rollback()
    if outcome.failures:
        for failure in outcome.failures:
            for line in failure.display_text.splitlines():
                print(f"ERROR: {line}", file=sys.stderr)
        return 2
    print("rollback ready: all ConfFlow JSON projections match SQLite authority")
    return 0


def _print_recovery_outcome(action: str, noun: str, outcome) -> int:
    changed_count = outcome.value.changed_count if outcome.value is not None else 0
    print(f"{action} {changed_count} {noun}")
    _print_application_failures(outcome)
    return 0 if not outcome.failures else 2


def _print_application_failures(outcome) -> None:
    for failure in outcome.failures:
        print(f"  ERROR: {failure.display_text}")


def _cmd_compare(args) -> int:
    from .application.comparison import export_csv, export_markdown

    outcome = args.application.runs.compare(
        args.workspace,
        tuple(args.run_ids),
        args.field,
        args.profile,
    )
    if outcome.value is None:
        _print_application_failures(outcome)
        return 2
    comparison = outcome.value
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


def _cmd_files_list_remote(args) -> int:
    outcome = args.application.files.list_remote(args.server_id, args.remote_path)
    if outcome.value is None:
        _print_application_failures(outcome)
        return 2
    entries = outcome.value
    for entry in entries:
        kind = "dir" if entry.is_dir else "file"
        size = "" if entry.size_bytes is None else str(entry.size_bytes)
        print(f"{kind}\t{size}\t{entry.permissions}\t{entry.path}")
    return 0


def _cmd_files_upload(args) -> int:
    policy = OverwritePolicy.overwrite if args.overwrite else OverwritePolicy.skip_same_size
    outcome = args.application.files.upload(
        args.server_id,
        str(args.local_path),
        args.remote_path,
        policy=policy.value,
        dry_run=args.dry_run,
    )
    records = outcome.value.records if outcome.value is not None else ()
    failures = sum(1 for r in records if r.status == TransferStatus.failed.value)
    failures += len(outcome.failures)
    print(f"upload: records={len(records)}, failures={failures}")
    return 0 if failures == 0 else 2


def _cmd_files_download(args) -> int:
    policy = OverwritePolicy.overwrite if args.overwrite else OverwritePolicy.skip_same_size
    outcome = args.application.files.download(
        args.server_id,
        args.remote_path,
        str(args.local_path),
        policy=policy.value,
        dry_run=args.dry_run,
    )
    records = outcome.value.records if outcome.value is not None else ()
    failures = sum(1 for r in records if r.status == TransferStatus.failed.value)
    failures += len(outcome.failures)
    print(f"download: records={len(records)}, failures={failures}")
    return 0 if failures == 0 else 2


def _cmd_files_mkdir(args) -> int:
    outcome = args.application.files.mkdir(args.server_id, args.remote_path)
    if outcome.failures:
        _print_application_failures(outcome)
        return 2
    print(f"created {args.remote_path}")
    return 0


def _cmd_files_preview(args) -> int:
    outcome = args.application.files.preview_text(args.server_id, args.remote_path)
    if outcome.value is None:
        _print_application_failures(outcome)
        return 2
    print(outcome.value, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
