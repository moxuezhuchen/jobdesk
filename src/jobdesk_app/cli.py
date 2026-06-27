"""JobDesk CLI — run + files commands powered by RunService."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config.servers import load_servers
from .core.file_transfer import OverwritePolicy
from .core.run import RunMode, RunSource, RunSpec
from .core.transfer import TransferStatus
from .services.file_transfer_service import FileTransferService
from .services.run_service import RunService
from .services.ssh_session import ConnectedSFTP, create_sftp_client, create_ssh_client


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

    # -- compare subcommand --
    cmp = sub.add_parser("compare", help="Compare results across runs")
    cmp.add_argument("workspace", type=Path)
    cmp.add_argument("run_ids", nargs="+")
    cmp.add_argument("--field", default="scf_energy")
    cmp.add_argument("--profile", default="gaussian_opt_freq")
    cmp.add_argument("--output", type=Path, default=None)
    cmp.add_argument("--format", choices=["csv", "markdown"], default="csv")
    cmp.set_defaults(func=_cmd_compare)

    # -- input subcommand group --
    inp = sub.add_parser("input", help="Build Gaussian/ORCA input files")
    inp_sub = inp.add_subparsers(dest="inp_command", required=True)

    inp_list = inp_sub.add_parser("list-presets")
    inp_list.set_defaults(func=_cmd_input_list_presets)

    inp_build = inp_sub.add_parser("build")
    inp_build.add_argument("xyz_path", type=Path)
    inp_build.add_argument("--preset", default=None)
    inp_build.add_argument("--method", default="B3LYP/6-31G(d)")
    inp_build.add_argument("--keywords", nargs="+", default=["opt", "freq"])
    inp_build.add_argument("--charge", type=int, default=0)
    inp_build.add_argument("--mult", type=int, default=1)
    inp_build.add_argument("--nproc", type=int, default=8)
    inp_build.add_argument("--mem", default="16GB")
    inp_build.add_argument("--output", type=Path, default=None)
    inp_build.add_argument("--orca", action="store_true")
    inp_build.set_defaults(func=_cmd_input_build)

    # -- viewer subcommand --
    viewer = sub.add_parser("viewer", help="Open files in molecular viewers")
    viewer_sub = viewer.add_subparsers(dest="viewer_command", required=True)

    v_list = viewer_sub.add_parser("list")
    v_list.set_defaults(func=_cmd_viewer_list)

    v_open = viewer_sub.add_parser("open")
    v_open.add_argument("file_path", type=Path)
    v_open.add_argument("--viewer", default="avogadro")
    v_open.add_argument("--exe", default=None)
    v_open.set_defaults(func=_cmd_viewer_open)

    # -- smiles subcommand --
    smiles = sub.add_parser("smiles", help="SMILES to 3D structure")
    smiles_sub = smiles.add_subparsers(dest="smiles_command", required=True)

    s_xyz = smiles_sub.add_parser("to-xyz")
    s_xyz.add_argument("smiles")
    s_xyz.add_argument("--output", type=Path, default=None)
    s_xyz.add_argument("--title", default="")
    s_xyz.set_defaults(func=_cmd_smiles_to_xyz)

    s_gjf = smiles_sub.add_parser("to-gjf")
    s_gjf.add_argument("smiles")
    s_gjf.add_argument("--output", type=Path, default=None)
    s_gjf.add_argument("--preset", default="b3lyp_631gd_opt_freq")
    s_gjf.add_argument("--title", default="")
    s_gjf.set_defaults(func=_cmd_smiles_to_gjf)

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
    record = RunService(args.workspace).create_run(spec)
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
    server = _get_server(args)
    from .services.scheduler_helpers import resources_from_server, scheduler_from_server
    overrides = {}
    if getattr(args, "cpus", None) is not None:
        overrides["cpus"] = args.cpus
    if getattr(args, "mem_mb", None) is not None:
        overrides["memory_mb"] = args.mem_mb
    if getattr(args, "walltime", None) is not None:
        overrides["walltime_minutes"] = args.walltime
    if getattr(args, "partition", None) is not None:
        overrides["partition"] = args.partition
    scheduler = scheduler_from_server(server)
    resources = resources_from_server(server, overrides or None)
    ssh = create_ssh_client(server)
    ssh.connect()
    sftp = create_sftp_client(ssh)
    try:
        result = RunService(args.workspace).submit_run(
            args.run_id, ssh, sftp,
            env_init_scripts=list(getattr(server, "env_init_scripts", []) or []),
            scheduler=scheduler,
            resources=resources,
        )
    finally:
        sftp.close()
        ssh.close()
    print(f"submitted={result.submitted_task_count}, errors={len(result.errors)}")
    for e in result.errors:
        print(f"  ERROR: {e}")
    return 0 if not result.errors else 2


def _cmd_run_refresh(args) -> int:
    service = RunService(args.workspace)
    record = service.load_run(args.run_id)
    server = _get_server_by_id(args, record.server_id)
    ssh = create_ssh_client(server)
    ssh.connect()
    try:
        result = service.refresh_run(args.run_id, ssh)
    finally:
        ssh.close()
    print(f"changed={result.changed_count}, warnings={len(result.warnings)}")
    return 0


def _cmd_run_download(args) -> int:
    record = RunService(args.workspace).load_run(args.run_id)
    server = _get_server_by_id(args, record.server_id)
    ssh = create_ssh_client(server)
    ssh.connect()
    sftp = create_sftp_client(ssh)
    try:
        patterns = [p.strip() for arg in args.patterns for p in arg.split(",") if p.strip()]
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
    server = _get_server_by_id(args, record.server_id)
    ssh = create_ssh_client(server)
    ssh.connect()
    try:
        changed, errors = svc.cancel_run(args.run_id, ssh)
    finally:
        ssh.close()
    print(f"cancelled {changed} task(s)")
    for error in errors:
        print(f"  ERROR: {error}")
    return 0 if not errors else 2


def _cmd_run_delete(args) -> int:
    svc = RunService(args.workspace)
    svc.delete_run(args.run_id)
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
    try:
        changed = RunService(args.workspace).prepare_rerun(args.run_id)
    except ValueError as exc:
        print(str(exc))
        return 2
    print(f"reset {changed} task(s) to uploaded, run `jobdesk run submit` to resubmit")
    return 0


def _cmd_compare(args) -> int:
    from .services.comparison import compare_runs, export_csv, export_markdown
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


def _cmd_input_list_presets(args) -> int:
    from .core.input_builder import list_presets
    for name, desc in sorted(list_presets().items()):
        print(f"{name}: {desc}")
    return 0


def _cmd_input_build(args) -> int:
    from .core.input_builder import (
        GaussianInputSpec,
        OrcaInputSpec,
        build_from_preset,
        build_gjf,
        build_inp,
    )
    if args.preset:
        content = build_from_preset(args.xyz_path, args.preset, args.output)
    elif args.orca:
        orca_spec = OrcaInputSpec(
            keywords=f"! {args.method} {' '.join(args.keywords)}",
            charge=args.charge,
            multiplicity=args.mult,
            nproc=args.nproc,
        )
        content = build_inp(args.xyz_path, orca_spec, args.output)
    else:
        gauss_spec = GaussianInputSpec(
            method_basis=args.method,
            job_keywords=args.keywords,
            charge=args.charge,
            multiplicity=args.mult,
            nproc=args.nproc,
            mem=args.mem,
        )
        content = build_gjf(args.xyz_path, gauss_spec, args.output)
    if args.output:
        print(f"Written to {args.output}")
    else:
        print(content)
    return 0


def _cmd_viewer_list(args) -> int:
    from .core.viewer import list_available_viewers
    viewers = list_available_viewers()
    if not viewers:
        print("No molecular viewers found. Install Avogadro, GaussView, or ChemCraft.")
        return 0
    for name, path in sorted(viewers.items()):
        print(f"{name}: {path}")
    return 0


def _cmd_viewer_open(args) -> int:
    from .core.viewer import open_in_viewer
    if open_in_viewer(args.file_path, args.viewer, args.exe):
        print(f"Opened {args.file_path} in {args.viewer}")
        return 0
    print(f"Viewer not found: {args.viewer}. Use 'jobdesk viewer list' to see available viewers.")
    return 2


def _cmd_smiles_to_xyz(args) -> int:
    from .core.viewer import is_rdkit_available, smiles_to_xyz
    if not is_rdkit_available():
        print("rdkit is required. Install with: pip install rdkit")
        return 2
    try:
        content = smiles_to_xyz(args.smiles, args.output, args.title)
        if args.output:
            print(f"Written to {args.output}")
        else:
            print(content)
        return 0
    except ValueError as e:
        print(f"Error: {e}")
        return 2


def _cmd_smiles_to_gjf(args) -> int:
    from .core.viewer import is_rdkit_available, smiles_to_gjf
    if not is_rdkit_available():
        print("rdkit is required. Install with: pip install rdkit")
        return 2
    try:
        content = smiles_to_gjf(args.smiles, args.output, args.preset, args.title)
        if args.output:
            print(f"Written to {args.output}")
        else:
            print(content)
        return 0
    except ValueError as e:
        print(f"Error: {e}")
        return 2


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


def _get_server_by_id(args, server_id: str):
    servers = load_servers(args.servers_yaml).servers if args.servers_yaml else load_servers().servers
    if server_id not in servers:
        raise SystemExit(f"server not found: {server_id}")
    return servers[server_id]


if __name__ == "__main__":
    raise SystemExit(main())
