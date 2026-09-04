"""Process composition root for JobDesk presentation adapters.

Only this module may assemble concrete configuration, persistence, and remote
implementations for the GUI and CLI.  Application modules remain independent
of these adapters.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .application.container import ApplicationContainer
from .infrastructure.application_facades import (
    DefaultFilesApplication,
    DefaultRunApplication,
    DefaultSettingsApplication,
    DefaultWorkflowApplication,
)
from .infrastructure.config.servers import get_default_servers_path, load_servers
from .infrastructure.files_connections import InfrastructureFilesConnectionController
from .infrastructure.persistence.settings.analysis_profiles import AnalysisProfileStore
from .infrastructure.persistence.settings.gui_settings import GuiSettings, GuiSettingsStore
from .infrastructure.persistence.settings.method_presets import MethodPresetStore, StepPresetStore
from .infrastructure.runtime.confflow_control import CONTROL_BACKEND
from .infrastructure.runtime.confflow_control_state import load_state, require_all_projections_match_authority
from .infrastructure.runtime.external_terminal import build_terminal_launch, launch_terminal
from .infrastructure.runtime.file_transfer_service import FileTransferService, ensure_safe_remote_path
from .infrastructure.runtime.job_id_overrides import JobIdOverridesError, parse_job_id_overrides
from .infrastructure.runtime.run_coordinator import OperationFailure, RunCoordinator, RunOperationOutcome
from .infrastructure.runtime.run_monitor import DoneEvent, RunMonitor
from .infrastructure.runtime.run_service import RunRecord, RunService
from .infrastructure.runtime.session_pool import SessionPool, pooled_sftp_factory
from .infrastructure.runtime.ssh_confflow_client import SSHConfFlowClient
from .infrastructure.runtime.ssh_session import ConnectedSFTP, create_sftp_client, create_ssh_client


class FilesConnectionController(InfrastructureFilesConnectionController):
    """Compatibility constructor backed by concrete infrastructure adapters."""

    def __init__(
        self,
        *,
        status_cb: Callable[[str], None],
        log_cb: Callable[[str], None],
        create_ssh: Callable[..., Any] = create_ssh_client,
        create_sftp: Callable[..., Any] = create_sftp_client,
        session_pool: SessionPool | None = None,
        server_loader: Callable[[], Any] = load_servers,
        allowed_delete_roots_provider: Callable[[], list[str]] | None = None,
    ) -> None:
        def service_factory(server: Any, server_id: str, delete_roots: list[str]) -> FileTransferService:
            pooled = session_pool is not None
            if pooled:
                assert session_pool is not None
                connection_factory: Callable[[], Any] = pooled_sftp_factory(session_pool, server_id, server)
            else:

                def direct_factory() -> ConnectedSFTP:
                    ssh = create_ssh(server)
                    ssh.connect()
                    return ConnectedSFTP(ssh, create_sftp(ssh))

                connection_factory = direct_factory

            return FileTransferService(
                connection_factory,
                allowed_delete_roots=delete_roots,
                persistent_session=not pooled,
            )

        super().__init__(
            status_cb=status_cb,
            log_cb=log_cb,
            server_loader=server_loader,
            service_factory=service_factory,
            allowed_delete_roots_provider=allowed_delete_roots_provider,
        )


class RunServiceTaskLookup:
    """Concrete adapter for the Files page's narrow task lookup port."""

    def load_tasks(self, workspace: Any, run_id: str) -> list[Any]:
        return RunService(workspace).load_tasks(run_id)


def create_application(
    workspace: str | Any | None = None,
    *,
    session_pool: SessionPool | None = None,
    servers_path: str | Path | None = None,
    runs_dir: str | Path | None = None,
) -> ApplicationContainer:
    """Build the process application graph and give resources one owner."""

    session_pool = session_pool or SessionPool(create_ssh_client, create_sftp_client)
    resolved_servers_path = Path(servers_path) if servers_path is not None else None

    def server_loader():
        return load_servers(resolved_servers_path)

    service = RunService(workspace, runs_dir=runs_dir)
    coordinator = RunCoordinator(
        service,
        server_lookup=lambda server_id: server_loader().servers[server_id],
        ssh_factory=create_ssh_client,
        sftp_factory=create_sftp_client,
        session_pool=session_pool,
    )
    runs = DefaultRunApplication(service, coordinator, session_pool)
    return ApplicationContainer(
        runs=runs,
        files=DefaultFilesApplication(session_pool, server_loader=server_loader),
        workflows=DefaultWorkflowApplication(),
        settings=DefaultSettingsApplication(servers_path=resolved_servers_path),
        close_callbacks=(session_pool.close, runs.close),
    )


__all__ = [
    "AnalysisProfileStore",
    "CONTROL_BACKEND",
    "ConnectedSFTP",
    "DoneEvent",
    "FilesConnectionController",
    "FileTransferService",
    "GuiSettings",
    "GuiSettingsStore",
    "JobIdOverridesError",
    "MethodPresetStore",
    "OperationFailure",
    "RunCoordinator",
    "RunMonitor",
    "RunOperationOutcome",
    "RunRecord",
    "RunService",
    "RunServiceTaskLookup",
    "SSHConfFlowClient",
    "SessionPool",
    "StepPresetStore",
    "build_terminal_launch",
    "create_sftp_client",
    "create_application",
    "create_ssh_client",
    "ensure_safe_remote_path",
    "get_default_servers_path",
    "launch_terminal",
    "load_servers",
    "load_state",
    "parse_job_id_overrides",
    "require_all_projections_match_authority",
]
