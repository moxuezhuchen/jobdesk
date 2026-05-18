from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from .config.runtime import RuntimeBindingStore, resolve_execution_contexts_for_project
from .config.servers import load_servers
from .core.manifest import Manifest
from .core.transfer import TransferDirection, TransferRecord
from .gui.session import create_sftp_client, create_ssh_client
from .services.project_service import create_project_context
from .services.workflow_service import WorkflowService


class _ConnectedSFTP:
    def __init__(self, server_config):
        self._ssh = create_ssh_client(server_config)
        self._ssh.connect()
        self._sftp = create_sftp_client(self._ssh)

    def upload_file(self, *args, **kwargs):
        return self._sftp.upload_file(*args, **kwargs)

    def download_file(self, *args, **kwargs):
        return self._sftp.download_file(*args, **kwargs)

    def close(self):
        self._sftp.close()
        self._ssh.close()


class _DryRunSFTP:
    def upload_file(self, local_path, remote_path, **kwargs):
        return TransferRecord(
            direction=TransferDirection.upload,
            local_path=str(local_path),
            remote_path=remote_path,
            dry_run=True,
        )

    def download_file(self, remote_path, local_path, **kwargs):
        return TransferRecord(
            direction=TransferDirection.download,
            local_path=str(local_path),
            remote_path=remote_path,
            dry_run=True,
        )

    def close(self):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobdesk")
    parser.add_argument("--servers-yaml", type=Path, default=None)
    parser.add_argument("--runtime-bindings", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    _project_command(sub, "scan", _cmd_scan)
    _project_command(sub, "preflight", _cmd_preflight)
    _project_command(sub, "list-batches", _cmd_list_batches)

    create = _project_command(sub, "create-batch", _cmd_create_batch)
    create.add_argument("--batch-id", default=None)

    for name, func in (
        ("upload", _cmd_upload),
        ("submit", _cmd_submit),
        ("refresh", _cmd_refresh),
        ("download", _cmd_download),
        ("analyze", _cmd_analyze),
        ("cleanup-remote", _cmd_cleanup_remote),
    ):
        cmd = _project_command(sub, name, func)
        cmd.add_argument("batch_id")
        if name in {"upload", "download", "cleanup-remote"}:
            cmd.add_argument("--dry-run", action="store_true")

    return parser


def _project_command(subparsers, name: str, func):
    cmd = subparsers.add_parser(name)
    cmd.add_argument("project", type=Path)
    cmd.set_defaults(func=func)
    return cmd


def _ctx(args):
    return create_project_context(args.project, args.servers_yaml)


def _binding_store(args):
    return RuntimeBindingStore(args.runtime_bindings)


def _cmd_scan(args) -> int:
    ctx = _ctx(args)
    packages = WorkflowService(ctx).scan_inputs()
    print(f"discovered {len(packages)} tasks")
    for package in packages:
        print(
            f"{package.task_id}\t{package.discovery_name}\t"
            f"{package.execution_profile}\t{package.entry_file}"
        )
    return 0


def _cmd_preflight(args) -> int:
    ctx = _ctx(args)
    report = WorkflowService(ctx).preflight(_binding_store(args), args.servers_yaml)
    state = "ok" if report.ok else "failed"
    print(
        f"preflight {state}: tasks={report.task_count}, "
        f"errors={len(report.errors)}, warnings={len(report.warnings)}"
    )
    for issue in report.errors:
        print(f"ERROR {issue.code}: {issue.message}")
    for issue in report.warnings:
        print(f"WARNING {issue.code}: {issue.message}")
    return 0 if report.ok else 2


def _cmd_list_batches(args) -> int:
    ctx = _ctx(args)
    summaries = WorkflowService(ctx).list_batches()
    if not summaries:
        print("No batches")
        return 0
    for summary in summaries:
        print(
            f"{summary.batch_id}\t{summary.created_at}\t{summary.task_count} tasks\t"
            f"profiles={summary.execution_profiles}\tservers={summary.server_ids}"
        )
    return 0


def _cmd_create_batch(args) -> int:
    ctx = _ctx(args)
    svc = WorkflowService(ctx)
    packages = svc.scan_inputs()
    profiles = {package.execution_profile for package in packages}
    resolved = resolve_execution_contexts_for_project(
        ctx.project_config,
        profiles,
        _binding_store(args),
        args.servers_yaml,
    )
    result = svc.create_batch(packages, resolved, batch_id=args.batch_id)
    print(f"created batch {result.batch_meta.batch_id}: {len(result.tasks)} tasks")
    return 0


def _load_batch_or_raise(args):
    ctx = _ctx(args)
    result = WorkflowService(ctx).load_batch(args.batch_id)
    if result is None:
        raise SystemExit(f"batch not found: {args.batch_id}")
    return ctx, result


def _server_map(ctx):
    if ctx.servers_path is None:
        raise SystemExit("servers.yaml is required for remote operations")
    return load_servers(ctx.servers_path).servers


def _cmd_upload(args) -> int:
    ctx, batch = _load_batch_or_raise(args)
    servers = _server_map(ctx)
    svc = WorkflowService(ctx)
    records, failures = svc.upload_tasks(
        batch.tasks,
        lambda server_id: _DryRunSFTP() if args.dry_run else _ConnectedSFTP(servers[server_id]),
        dry_run=args.dry_run,
        batch_dir=batch.batch_dir,
        manifest_path=batch.manifest_path,
    )
    print(f"upload: records={len(records)}, failures={len(failures)}")
    return 0 if not failures else 2


def _cmd_submit(args) -> int:
    ctx, batch = _load_batch_or_raise(args)
    svc = WorkflowService(ctx)

    def ssh_factory(server_config):
        ssh = create_ssh_client(server_config)
        ssh.connect()
        return ssh

    def sftp_factory(server_config):
        return _ConnectedSFTP(server_config)

    results = svc.submit_batch(batch.manifest_path, args.batch_id, ssh_factory, sftp_factory)
    errors = sum(len(result.errors) for result in results)
    submitted = sum(result.submitted_task_count for result in results)
    print(f"submit: submitted={submitted}, errors={errors}")
    return 0 if errors == 0 else 2


def _cmd_refresh(args) -> int:
    ctx, batch = _load_batch_or_raise(args)
    svc = WorkflowService(ctx)

    def ssh_factory(server_config):
        ssh = create_ssh_client(server_config)
        ssh.connect()
        return ssh

    results, failures = svc.refresh_batch(batch.manifest_path, args.batch_id, ssh_factory)
    changed = sum(result.changed_count for result in results)
    print(f"refresh: changed={changed}, failures={len(failures)}")
    return 0 if not failures else 2


def _cmd_download(args) -> int:
    ctx, batch = _load_batch_or_raise(args)
    servers = _server_map(ctx)
    records, failures = WorkflowService(ctx).download_completed(
        Manifest.read(batch.manifest_path),
        lambda server_id: _DryRunSFTP() if args.dry_run else _ConnectedSFTP(servers[server_id]),
        dry_run=args.dry_run,
        manifest_path=batch.manifest_path,
    )
    print(f"download: records={len(records)}, failures={len(failures)}")
    return 0 if not failures else 2


def _cmd_analyze(args) -> int:
    ctx, batch = _load_batch_or_raise(args)
    results, failures, summaries = WorkflowService(ctx).analyze_batch(batch.tasks, args.batch_id)
    print(
        f"analyze: results={len(results)}, failures={len(failures)}, "
        f"groups={len(summaries)}"
    )
    return 0 if not failures else 2


def build_remote_cleanup_commands(
    remote_work_dirs: list[str],
    batch_id: str,
    dry_run: bool = True,
) -> list[str]:
    _validate_safe_name(batch_id, "batch_id")
    commands: list[str] = []
    for remote_work_dir in remote_work_dirs:
        _validate_remote_work_dir(remote_work_dir)
        target = f"{remote_work_dir.rstrip('/')}/{batch_id}"
        quoted = shlex.quote(target)
        if dry_run:
            commands.append(f"test -d {quoted} && printf '%s\\n' {quoted} || true")
        else:
            commands.append(f"test -d {quoted} && rm -rf -- {quoted} || true")
    return commands


def _cmd_cleanup_remote(args) -> int:
    ctx, batch = _load_batch_or_raise(args)
    by_server: dict[str, set[str]] = {}
    for task in batch.tasks:
        by_server.setdefault(task.server_id or "", set()).add(task.remote_work_dir or "")

    servers = _server_map(ctx)
    failures = 0
    for server_id, remote_work_dirs in sorted(by_server.items()):
        if not server_id:
            failures += 1
            print("cleanup-remote: task missing server_id")
            continue
        ssh = create_ssh_client(servers[server_id])
        ssh.connect()
        try:
            for command in build_remote_cleanup_commands(
                sorted(remote_work_dirs),
                args.batch_id,
                dry_run=args.dry_run,
            ):
                result = ssh.run(command)
                print(f"{server_id}: {result.stdout or command}")
                if result.exit_code != 0:
                    failures += 1
        finally:
            ssh.close()
    return 0 if failures == 0 else 2


def _validate_safe_name(value: str, label: str) -> None:
    if not value or "/" in value or "\\" in value or ".." in value.split("."):
        raise ValueError(f"unsafe {label}: {value!r}")


def _validate_remote_work_dir(value: str) -> None:
    if not value or not value.startswith("/") or "\\" in value or ".." in value.split("/"):
        raise ValueError(f"unsafe remote_work_dir: {value!r}")


if __name__ == "__main__":
    raise SystemExit(main())
