from dataclasses import dataclass, field
from pathlib import Path

from ..config.runtime import RuntimeBindingStore
from ..config.servers import load_servers
from .batch_service import discover_task_packages
from .project_service import ProjectContext


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class PreflightReport:
    errors: list[PreflightIssue] = field(default_factory=list)
    warnings: list[PreflightIssue] = field(default_factory=list)
    task_count: int = 0
    profiles: list[str] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def preflight_project(
    ctx: ProjectContext,
    binding_store: RuntimeBindingStore | None = None,
    servers_path: str | Path | None = None,
) -> PreflightReport:
    errors: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []
    profiles = sorted(ctx.project_config.execution_profiles.keys())
    task_count = 0
    server_ids: set[str] = set()

    try:
        packages = discover_task_packages(ctx)
        task_count = len(packages)
        if task_count == 0:
            warnings.append(PreflightIssue("no_tasks", "No task packages discovered.", "warning"))
    except Exception as exc:
        errors.append(PreflightIssue("scan_failed", f"Input discovery failed: {exc}"))
        packages = []

    if binding_store is None:
        binding_store = RuntimeBindingStore()
    if servers_path is None:
        servers_path = ctx.servers_path

    servers_config = None
    if servers_path is None:
        errors.append(PreflightIssue("missing_servers_path", "No servers.yaml path is configured."))
    else:
        try:
            servers_config = load_servers(servers_path)
        except Exception as exc:
            errors.append(PreflightIssue("servers_load_failed", f"Failed to load servers.yaml: {exc}"))

    needed_profiles = sorted({pkg.execution_profile for pkg in packages} or set(profiles))
    for profile in needed_profiles:
        binding = binding_store.get_binding(ctx.project_id, profile)
        if binding is None:
            errors.append(PreflightIssue(
                "missing_binding",
                f"Missing runtime binding for project={ctx.project_id!r}, profile={profile!r}.",
            ))
            continue
        server_ids.add(binding.server_id)
        if not _is_absolute_posix(binding.remote_work_dir):
            errors.append(PreflightIssue(
                "invalid_remote_work_dir",
                f"remote_work_dir for profile={profile!r} must be an absolute POSIX path.",
            ))
        if servers_config is not None and binding.server_id not in servers_config.servers:
            errors.append(PreflightIssue(
                "unknown_server",
                f"server_id={binding.server_id!r} for profile={profile!r} is not in servers.yaml.",
            ))

    return PreflightReport(
        errors=errors,
        warnings=warnings,
        task_count=task_count,
        profiles=needed_profiles,
        servers=sorted(server_ids),
    )


def _is_absolute_posix(path: str) -> bool:
    return bool(path) and path.startswith("/") and "\\" not in path and ".." not in path.split("/")
