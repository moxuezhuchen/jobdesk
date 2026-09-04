"""Composition boundary for the Runs page runtime.

The Runs page is a Qt adapter.  It still has a few legacy constructor seams
(``coordinator_factory``, ``client_factory`` and ``session_pool``), so the
runtime keeps those seams while owning the concrete service graph.  Read and
mutation paths can use the narrow methods here without constructing
``RunService`` or ``SSHConfFlowClient`` in the page.  The lifecycle-action,
submission, and uncertain-task ports below keep the full remote action graph
in the same composition boundary while retaining the old seams for callers
and tests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, cast


class RunServicePort(Protocol):
    """Read surface required by the first Runs-page migration slice."""

    def list_runs(self) -> list[Any]: ...

    def load_run(self, run_id: str) -> Any: ...

    def load_tasks(self, run_id: str) -> list[Any]: ...


class SessionPoolPort(Protocol):
    """Lifecycle surface owned by :class:`RunsPageRuntime`."""

    def close(self) -> None: ...


class RunLifecyclePort(Protocol):
    """Coordinator surface required by the migrated Runs-page actions."""

    @property
    def service(self) -> RunServicePort: ...

    def delete(self, run_id: str) -> Any: ...

    def retry_failed(self, run_id: str) -> Any: ...

    def rerun(self, run_id: str) -> Any: ...

    def confirm_submitted(self, run_id: str, task_ids: tuple[str, ...]) -> Any: ...

    def abandon_submit(self, run_id: str, task_ids: tuple[str, ...]) -> Any: ...

    def sync_progress(self, run_id: str) -> Any: ...


ServiceFactory = Callable[[Path], RunServicePort]
ServiceConstructor = Callable[[], Callable[[Path], RunServicePort]]
CoordinatorFactory = Callable[[Path], Any]
ClientFactory = Callable[[Any, str], Any]
ClientConstructor = Callable[[], Callable[[Any, str], Any]]
CoordinatorConstructor = Callable[..., Any]
SessionPoolFactory = Callable[[], SessionPoolPort]
SessionPoolConstructor = Callable[..., SessionPoolPort]
ServerLoader = Callable[[], Any]
DurableBackendLoader = Callable[[RunServicePort, str], Mapping[str, object] | None]
_MONITOR_ACTIVE_STATUSES = frozenset({"submitting", "submitted", "running"})
_CONTROL_BACKEND = "control"


@dataclass(frozen=True, slots=True)
class _EmptyServerConfiguration:
    servers: Mapping[str, object] = field(default_factory=dict)


def _has_monitor_active_status(summary: Mapping[str, int]) -> bool:
    return any(int(summary.get(status, 0) or 0) > 0 for status in _MONITOR_ACTIVE_STATUSES)


@dataclass(frozen=True, slots=True)
class MonitorRunInput:
    """Immutable monitor inputs for one persisted run.

    The page only needs these projections to admit a watcher.  Keeping the
    service-owned ``RunRecord`` out of the DTO prevents a later service
    refresh from changing the data while the GUI is iterating it.
    """

    run_id: str
    server_id: str
    remote_dir: str
    status_summary: Mapping[str, int] = field(default_factory=dict)
    server_config: object | None = None
    durable_backend: Mapping[str, object] | None = None
    remote_batch_dir: str = ""
    progress_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", str(self.run_id))
        object.__setattr__(self, "server_id", str(self.server_id))
        object.__setattr__(self, "remote_dir", str(self.remote_dir))
        object.__setattr__(self, "remote_batch_dir", str(self.remote_batch_dir))
        object.__setattr__(
            self,
            "status_summary",
            MappingProxyType({str(key): int(value) for key, value in dict(self.status_summary or {}).items()}),
        )
        if self.durable_backend is not None:
            object.__setattr__(
                self,
                "durable_backend",
                MappingProxyType(dict(self.durable_backend)),
            )
        object.__setattr__(
            self,
            "progress_paths",
            tuple(dict.fromkeys(str(path) for path in self.progress_paths if str(path))),
        )


@dataclass(frozen=True, slots=True)
class RunsMonitorInput:
    """Immutable snapshot consumed by ``RunsResultsPage._start_monitoring``."""

    workspace: Path
    runs: tuple[MonitorRunInput, ...]
    server_ids: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace))
        object.__setattr__(self, "runs", tuple(self.runs))
        object.__setattr__(self, "server_ids", frozenset(str(value) for value in self.server_ids))


class RunsPageRuntime:
    """Own the concrete runtime graph used by ``RunsResultsPage``.

    Factories are deliberately injectable.  The page passes small closures
    over its historical module-level symbols, so existing tests and extension
    code that monkeypatch ``runs_results_page.RunService`` (or the SSH
    factories) continue to observe the same calls while production code uses
    this application-level composition boundary.
    """

    def __init__(
        self,
        *,
        service_factory: ServiceFactory | None = None,
        service_constructor: ServiceConstructor | None = None,
        coordinator_factory: CoordinatorFactory | None = None,
        coordinator_constructor: CoordinatorConstructor | None = None,
        client_factory: ClientFactory | None = None,
        client_constructor: ClientConstructor | None = None,
        session_pool: SessionPoolPort | None = None,
        session_pool_factory: SessionPoolFactory | None = None,
        session_pool_constructor: SessionPoolConstructor | None = None,
        server_loader: ServerLoader | None = None,
        durable_backend_loader: DurableBackendLoader | None = None,
        ssh_factory: Callable[..., Any] | None = None,
        sftp_factory: Callable[..., Any] | None = None,
    ) -> None:
        if service_factory is not None and service_constructor is not None:
            raise TypeError("provide service_factory or service_constructor, not both")
        self._service_factory = service_factory
        self._service_constructor = service_constructor
        self._coordinator_factory = coordinator_factory
        self._coordinator_constructor = coordinator_constructor
        self._client_factory = client_factory
        self._client_constructor = client_constructor
        if session_pool is not None and session_pool_factory is not None:
            raise TypeError("provide session_pool or session_pool_factory, not both")
        if session_pool is not None and session_pool_constructor is not None:
            raise TypeError("provide session_pool or session_pool_constructor, not both")
        self._owns_session_pool = session_pool is None
        self._server_loader = server_loader or (lambda: _EmptyServerConfiguration())
        self._durable_backend_loader = durable_backend_loader or (lambda _service, _run_id: None)
        self._ssh_factory = ssh_factory
        self._sftp_factory = sftp_factory
        if session_pool is not None:
            self._session_pool = session_pool
        elif session_pool_factory is not None:
            self._session_pool = session_pool_factory()
        elif session_pool_constructor is not None:
            ssh_factory_value, sftp_factory_value = self._connection_factories()
            self._session_pool = session_pool_constructor(ssh_factory_value, sftp_factory_value)
        else:
            raise TypeError("session_pool, session_pool_factory, or session_pool_constructor is required")
        self._closed = False

    def _connection_factories(self) -> tuple[Callable[..., Any], Callable[..., Any]]:
        if self._ssh_factory is None or self._sftp_factory is None:
            raise RuntimeError("SSH and SFTP factories must be supplied by bootstrap")
        return self._ssh_factory, self._sftp_factory

    @property
    def session_pool(self) -> SessionPoolPort:
        """Return the owned or caller-supplied session pool."""

        return self._session_pool

    @property
    def owns_session_pool(self) -> bool:
        return self._owns_session_pool

    def service(self, workspace: Path) -> RunServicePort:
        """Build a service for one workspace through the injected port."""

        if self._closed:
            raise RuntimeError("Runs page runtime is closed")
        if self._service_factory is not None:
            service_factory = self._service_factory
        elif self._service_constructor is not None:
            service_factory = self._service_constructor()
        else:
            raise RuntimeError("run service factory must be supplied by bootstrap")
        return service_factory(Path(workspace))

    def list_runs(self, workspace: Path) -> list[Any]:
        return self.service(workspace).list_runs()

    def load_run(self, workspace: Path, run_id: str) -> Any:
        return self.service(workspace).load_run(str(run_id))

    def load_tasks(self, workspace: Path, run_id: str) -> list[Any]:
        return self.service(workspace).load_tasks(str(run_id))

    def monitor_inputs(self, workspace: Path) -> RunsMonitorInput:
        """Assemble the complete, immutable input for remote monitoring.

        This is intentionally a read-only composition method.  It loads the
        run snapshot, server profiles, durable backend marker, and persisted
        checkpoint paths once; the Qt page only decides which already-built
        watcher inputs to admit.  Active legacy-event runs load task paths,
        while control-backend runs are intentionally excluded from that
        legacy watcher path.
        """

        normalized_workspace = Path(workspace)
        service = self.service(normalized_workspace)
        records = tuple(service.list_runs())
        config = self._server_loader()
        servers = getattr(config, "servers", {}) or {}
        server_ids = frozenset(str(server_id) for server_id in servers)

        from ..core.run import remote_run_dir

        inputs: list[MonitorRunInput] = []
        for record in records:
            run_id = str(getattr(record, "run_id", ""))
            server_id = str(getattr(record, "server_id", ""))
            remote_dir = str(getattr(record, "remote_dir", ""))
            active = _has_monitor_active_status(getattr(record, "status_summary", {}) or {})
            durable_backend: Mapping[str, object] | None = None
            progress_paths: tuple[str, ...] = ()
            if active:
                loaded_backend = self._durable_backend_loader(service, run_id)
                if loaded_backend is not None:
                    durable_backend = dict(loaded_backend)
                if not (durable_backend is not None and durable_backend.get("backend") == _CONTROL_BACKEND):
                    tasks = service.load_tasks(run_id)
                    progress_paths = tuple(
                        path
                        for task in tasks
                        for path in (
                            getattr(task, "remote_state_path", ""),
                            getattr(task, "remote_stats_path", ""),
                        )
                        if path
                    )
            inputs.append(
                MonitorRunInput(
                    run_id=run_id,
                    server_id=server_id,
                    remote_dir=remote_dir,
                    status_summary=getattr(record, "status_summary", {}) or {},
                    server_config=servers.get(server_id),
                    durable_backend=durable_backend,
                    remote_batch_dir=remote_run_dir(remote_dir, run_id),
                    progress_paths=progress_paths,
                )
            )
        return RunsMonitorInput(
            workspace=normalized_workspace,
            runs=tuple(inputs),
            server_ids=server_ids,
        )

    # Keep a descriptive alias for callers that name the result rather than
    # the operation.  Both names are application-level ports; page code uses
    # ``monitor_inputs`` so the legacy service method cannot leak back into
    # the GUI boundary.
    load_monitor_inputs = monitor_inputs

    def coordinator(
        self,
        workspace: Path,
        *,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
    ) -> Any:
        """Resolve a coordinator while preserving the legacy factory seam."""

        if self._closed:
            raise RuntimeError("Runs page runtime is closed")
        selected_factory = factory if factory is not None else self._coordinator_factory
        if selected_factory is not None:
            return selected_factory(Path(workspace))

        pool = session_pool or self._session_pool
        ssh_factory, sftp_factory = self._connection_factories()
        if self._coordinator_constructor is None:
            raise RuntimeError("run coordinator constructor must be supplied by bootstrap")
        service = self.service(Path(workspace))
        return self._coordinator_constructor(
            service,
            server_lookup=lambda server_id: self._server_loader().servers[server_id],
            ssh_factory=ssh_factory,
            sftp_factory=sftp_factory,
            session_pool=pool,
        )

    def client(
        self,
        coordinator: Any,
        server_id: str,
        *,
        factory: ClientFactory | None = None,
    ) -> Any:
        """Resolve the SSH ConfFlow client through the legacy seam."""

        if self._closed:
            raise RuntimeError("Runs page runtime is closed")
        selected_factory = factory if factory is not None else self._client_factory
        if selected_factory is not None:
            return selected_factory(coordinator, str(server_id))
        if self._client_constructor is not None:
            return self._client_constructor()(coordinator, str(server_id))
        raise RuntimeError("ConfFlow client constructor must be supplied by bootstrap")

    def _action_coordinator(
        self,
        workspace: Path,
        *,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
    ) -> RunLifecyclePort:
        """Resolve a coordinator for one migrated lifecycle action.

        ``resolver`` is deliberately injectable for the page's historical
        ``_coordinator_for`` monkeypatch seam.  It is a resolver, not a
        concrete service dependency, and therefore keeps the runtime port
        independent of Qt and page state.
        """

        if self._closed:
            raise RuntimeError("Runs page runtime is closed")
        if coordinator is not None:
            return coordinator
        if resolver is not None:
            return resolver(Path(workspace))
        return cast(
            RunLifecyclePort,
            self.coordinator(
                Path(workspace),
                factory=factory,
                session_pool=session_pool,
            ),
        )

    def delete_run(
        self,
        workspace: Path,
        run_id: str,
        *,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
    ) -> Any:
        """Delete one run through the runtime-owned lifecycle port."""

        return self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        ).delete(str(run_id))

    def retry_failed(
        self,
        workspace: Path,
        run_id: str,
        *,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
    ) -> Any:
        """Prepare failed tasks for retry through the runtime port."""

        return self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        ).retry_failed(str(run_id))

    def rerun(
        self,
        workspace: Path,
        run_id: str,
        *,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
    ) -> Any:
        """Prepare a run for rerun through the runtime port."""

        return self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        ).rerun(str(run_id))

    def cancel_run(
        self,
        workspace: Path,
        run_id: str,
        *,
        server_id: str | None = None,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> tuple[int, list[str]]:
        """Cancel one run through the runtime-owned client boundary.

        ConfFlow cancellation is performed by the attached remote handle,
        rather than by the Qt page or by the legacy ``RunService``.  Keep the
        historical ``(changed_count, errors)`` payload so the page's worker
        and feedback callbacks retain their existing behavior.
        """

        selected = self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        )
        selected_server_id = server_id
        if selected_server_id is None:
            record = selected.service.load_run(str(run_id))
            selected_server_id = str(record.server_id)
        client = self.client(
            selected,
            str(selected_server_id),
            factory=client_factory,
        )
        client.attach(str(run_id)).cancel()
        return 1, []

    def refresh_run(
        self,
        workspace: Path,
        run_id: str,
        patterns: list[str],
        *,
        download: bool,
        server_id: str | None = None,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> Any:
        """Refresh one run through the runtime-owned client boundary.

        The ConfFlow client owns the remote handle and backend-specific
        refresh behavior.  Keep that detail here so a Qt page only supplies
        its historical coordinator/client seams.  ``server_id`` is optional
        for callers that already have the selected record; when omitted the
        runtime reloads the authoritative record through the coordinator's
        service.
        """

        selected = self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        )
        selected_server_id = server_id
        if selected_server_id is None:
            record = selected.service.load_run(str(run_id))
            selected_server_id = str(record.server_id)
        client = self.client(
            selected,
            str(selected_server_id),
            factory=client_factory,
        )
        handle = client.attach(str(run_id))
        selected_patterns = list(patterns)
        if handle.to_dict().get("backend") == _CONTROL_BACKEND:
            selected_patterns = []
        return client.refresh_outcome(
            handle,
            selected_patterns,
            download=download,
        )

    def download_run(
        self,
        workspace: Path,
        run_id: str,
        patterns: list[str],
        *,
        server_id: str | None = None,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> Any:
        """Download one completed run through the runtime-owned client port.

        Keep the client attach and backend-specific pattern policy beside the
        refresh path.  The Qt page only supplies its historical coordinator
        and client factory seams; it must not construct or invoke the concrete
        remote client itself.
        """

        selected = self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        )
        selected_server_id = server_id
        if selected_server_id is None:
            record = selected.service.load_run(str(run_id))
            selected_server_id = str(record.server_id)
        client = self.client(
            selected,
            str(selected_server_id),
            factory=client_factory,
        )
        handle = client.attach(str(run_id))
        selected_patterns = list(patterns)
        if handle.to_dict().get("backend") == _CONTROL_BACKEND:
            selected_patterns = []
        return client.download_outcome(handle, selected_patterns)

    def sync_progress(
        self,
        workspace: Path,
        run_id: str,
        *,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
    ) -> Any:
        """Synchronize declared live-progress files through the runtime port."""

        return self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        ).sync_progress(str(run_id))

    def submit_run(
        self,
        workspace: Path,
        run_id: str,
        *,
        resource_overrides: dict[str, object] | None = None,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
        client_factory: ClientFactory | None = None,
    ) -> Any:
        """Submit one durable run through the runtime-owned client port.

        Submission has historically been routed through ``SSHConfFlowClient``
        rather than directly through ``RunCoordinator`` because the client
        owns the control-backend attach/idempotency behavior.  Keep that
        distinction inside the runtime so the Qt page only supplies its
        legacy coordinator/client seams.
        """

        selected = self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        )
        service = cast("RunServicePort", selected.service)
        record = service.load_run(str(run_id))
        client = self.client(
            selected,
            str(record.server_id),
            factory=client_factory,
        )
        from .confflow_client import SubmitRequest

        return client.submit_with_outcome(SubmitRequest(str(run_id), resource_overrides=resource_overrides))

    def confirm_submitted(
        self,
        workspace: Path,
        run_id: str,
        task_ids: list[str] | tuple[str, ...],
        *,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
    ) -> Any:
        """Confirm selected uncertain tasks through the runtime port."""

        return self._resolve_uncertain(
            Path(workspace),
            str(run_id),
            task_ids,
            confirm=True,
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        )

    def abandon_submit(
        self,
        workspace: Path,
        run_id: str,
        task_ids: list[str] | tuple[str, ...],
        *,
        coordinator: RunLifecyclePort | None = None,
        factory: CoordinatorFactory | None = None,
        session_pool: SessionPoolPort | None = None,
        resolver: Callable[[Path], RunLifecyclePort] | None = None,
    ) -> Any:
        """Abandon selected uncertain tasks through the runtime port."""

        return self._resolve_uncertain(
            Path(workspace),
            str(run_id),
            task_ids,
            confirm=False,
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        )

    def _resolve_uncertain(
        self,
        workspace: Path,
        run_id: str,
        task_ids: list[str] | tuple[str, ...],
        *,
        confirm: bool,
        coordinator: RunLifecyclePort | None,
        factory: CoordinatorFactory | None,
        session_pool: SessionPoolPort | None,
        resolver: Callable[[Path], RunLifecyclePort] | None,
    ) -> Any:
        selected_task_ids = tuple(dict.fromkeys(str(task_id) for task_id in task_ids if str(task_id)))
        if not selected_task_ids:
            raise ValueError("task_ids must not be empty")

        from ..core.lifecycle import TaskStatus

        current = self.load_tasks(Path(workspace), str(run_id))
        current_by_id = {str(task.task_id): task for task in current}
        if any(
            task_id not in current_by_id or current_by_id[task_id].status != TaskStatus.uncertain
            for task_id in selected_task_ids
        ):
            raise ValueError("selected tasks are no longer uncertain")

        selected = self._action_coordinator(
            Path(workspace),
            coordinator=coordinator,
            factory=factory,
            session_pool=session_pool,
            resolver=resolver,
        )
        if confirm:
            return selected.confirm_submitted(str(run_id), selected_task_ids)
        return selected.abandon_submit(str(run_id), selected_task_ids)

    def close(self, *, session_pool: SessionPoolPort | None = None) -> None:
        """Close owned resources once; borrowed pools remain caller-owned."""

        if self._closed:
            return
        self._closed = True
        if self._owns_session_pool:
            (session_pool or self._session_pool).close()


__all__ = [
    "ClientFactory",
    "ClientConstructor",
    "CoordinatorFactory",
    "DurableBackendLoader",
    "MonitorRunInput",
    "RunServicePort",
    "RunLifecyclePort",
    "RunsMonitorInput",
    "RunsPageRuntime",
    "ServiceFactory",
    "ServiceConstructor",
    "SessionPoolFactory",
    "SessionPoolConstructor",
    "SessionPoolPort",
]
