"""JobDesk CLI — run + files commands powered by RunService."""
from __future__ import annotations

import argparse
from pathlib import Path

from .config.servers import load_servers
from .core.file_transfer import OverwritePolicy, RemoteFileInfo
from .core.run import RunMode, RunSource, RunSpec
from .core.transfer import TransferDirection, TransferRecord, TransferStatus
from .remote.status_refresh import refresh_batch_status
from .services.file_transfer_service import FileTransferService
from .services.run_service import RunService
from .services.ssh_session import create_sftp_client, create_ssh_client


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
        p.set_defaults(func=func)

    dl = run_sub.add_parser("download")
    dl.add_argument("workspace", type=Path)
    dl.add_argument("run_id")
    dl.add_argument("--patterns", default="*.log")
    dl.set_defaults(func=_cmd_run_download)

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
    up.set_defaults(func=_cmd_files_upload)

    dn = files_sub.add_parser("download")
    dn.add_argument("server_id")
    dn.add_argument("remote_path")
    dn.add_argument("local_path", type=Path)
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
    record = RunService(args.workspace).create_run(spec)
    print(f"created run {record.run_id}: {record.status_summary}")
    return 0


def _cmd_run_list(args) -> int:
    runs = RunService(args.workspace).list_runs()
    if not runs:
        print("No runs")
        return 0
    for r in runs:
        print(f"{r.run_id}\t{r.server_id}\t{r.remote_dir}\t{r.mode}\t{r.status_summary}")
    return 0


def _cmd_run_submit(args) -> int:
    server = _get_server(args)
    ssh = create_ssh_client(server)
    ssh.connect()
    sftp = create_sftp_client(ssh)
    try:
        result = RunService(args.workspace).submit_run(
            args.run_id, ssh, sftp,
            env_init_scripts=list(getattr(server, "env_init_scripts", []) or []),
        )
    finally:
        sftp.close()
        ssh.close()
    print(f"submitted={result.submitted_task_count}, errors={len(result.errors)}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    return 0 if not result.errors else 2


def _cmd_run_refresh(args) -> int:
    record = RunService(args.workspace).load_run(args.run_id)
    server = _get_server_by_id(args, record.server_id)
    ssh = create_ssh_client(server)
    ssh.connect()
    try:
        result = refresh_batch_status(
            ssh=ssh,
            manifest_path=record.manifest_path,
            remote_batch_dir=f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{record.run_id}",
            batch_id=record.run_id,
            write=True,
        )
    finally:
        ssh.close()
    RunService(args.workspace).update_run_from_manifest(args.run_id)
    print(f"changed={result.changed_count}, warnings={len(result.warnings)}")
    return 0


def _cmd_run_download(args) -> int:
    record = RunService(args.workspace).load_run(args.run_id)
    server = _get_server_by_id(args, record.server_id)
    ssh = create_ssh_client(server)
    ssh.connect()
    sftp = create_sftp_client(ssh)
    try:
        patterns = [p.strip() for p in args.patterns.split(",") if p.strip()]
        records, failures = RunService(args.workspace).download_completed(args.run_id, sftp, patterns)
    finally:
        sftp.close()
        ssh.close()
    transferred = sum(1 for r in records if r.status == TransferStatus.transferred)
    print(f"downloaded={transferred}, failures={len(failures)}")
    return 0 if not failures else 2


def _cmd_run_cancel(args) -> int:
    svc = RunService(args.workspace)
    record = svc.load_run(args.run_id)
    tasks = __import__("jobdesk_app.core.manifest", fromlist=["Manifest"]).Manifest.read(record.manifest_path)
    changed = 0
    for task in tasks:
        if task.status in ("uploaded", "submitted", "running"):
            from .core.lifecycle import TaskStatus
            task.status = TaskStatus.failed
            task.error_message = "cancelled"
            changed += 1
    from .core.manifest import Manifest
    Manifest.write(record.manifest_path, tasks)
    svc.update_run_from_manifest(args.run_id)
    print(f"cancelled {changed} task(s)")
    return 0


def _cmd_run_delete(args) -> int:
    import shutil
    svc = RunService(args.workspace)
    record = svc.load_run(args.run_id)
    shutil.rmtree(record.run_dir)
    results_dir = Path(args.workspace) / "results" / args.run_id
    shutil.rmtree(results_dir, ignore_errors=True)
    print(f"deleted run {args.run_id}")
    return 0


def _cmd_run_retry(args) -> int:
    changed = RunService(args.workspace).prepare_retry_failed(args.run_id)
    if changed == 0:
        print("No failed tasks to retry")
        return 0
    print(f"reset {changed} failed task(s) to uploaded, run `jobdesk run submit` to resubmit")
    return 0


def _cmd_run_rerun(args) -> int:
    changed = RunService(args.workspace).prepare_rerun(args.run_id)
    print(f"reset {changed} task(s) to uploaded, run `jobdesk run submit` to resubmit")
    return 0


# ---- files commands -------------------------------------------------------


def _file_transfer_service(args, server_id: str) -> FileTransferService:
    server = _get_server_by_id(args, server_id)

    def factory():
        ssh = create_ssh_client(server)
        ssh.connect()
        sftp = create_sftp_client(ssh)
        return sftp

    return FileTransferService(factory)


def _cmd_files_list_remote(args) -> int:
    entries = _file_transfer_service(args, args.server_id).list_remote(args.remote_path)
    for entry in entries:
        kind = "dir" if entry.is_dir else "file"
        size = "" if entry.size_bytes is None else str(entry.size_bytes)
        print(f"{kind}\t{size}\t{entry.permissions}\t{entry.path}")
    return 0


def _cmd_files_upload(args) -> int:
    records = _file_transfer_service(args, args.server_id).upload_path(
        args.local_path, args.remote_path, OverwritePolicy.skip_same_size
    )
    if not isinstance(records, list):
        records = [records]
    failures = sum(1 for r in records if r.status == TransferStatus.failed)
    print(f"upload: records={len(records)}, failures={failures}")
    return 0 if failures == 0 else 2


def _cmd_files_download(args) -> int:
    records = _file_transfer_service(args, args.server_id).download_path(
        args.remote_path, args.local_path, OverwritePolicy.skip_same_size
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


def _get_server_by_id(args, server_id: str):
    servers = load_servers(args.servers_yaml).servers if args.servers_yaml else load_servers().servers
    if server_id not in servers:
        raise SystemExit(f"server not found: {server_id}")
    return servers[server_id]


if __name__ == "__main__":
    raise SystemExit(main())
