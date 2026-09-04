"""Tests for the Runs-page application runtime boundary."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jobdesk_app.application.runs_runtime import (
    MonitorRunInput,
    RunsMonitorInput,
    RunsPageRuntime,
)
from jobdesk_app.bootstrap import RunCoordinator, SSHConfFlowClient
from jobdesk_app.core.lifecycle import TaskStatus


class _Service:
    def __init__(self) -> None:
        self.records = [SimpleNamespace(run_id="run-1")]
        self.tasks = [SimpleNamespace(task_id="task-1")]

    def list_runs(self):
        return self.records

    def load_run(self, run_id: str):
        return SimpleNamespace(run_id=run_id)

    def load_tasks(self, run_id: str):
        return [*self.tasks]


class _Pool:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_runtime_read_ports_use_one_injected_service_factory(tmp_path: Path) -> None:
    service = _Service()
    seen: list[Path] = []

    def service_factory(workspace: Path):
        seen.append(workspace)
        return service

    runtime = RunsPageRuntime(service_factory=service_factory, session_pool=_Pool())

    assert runtime.list_runs(tmp_path) == service.records
    assert runtime.load_run(tmp_path, "run-2").run_id == "run-2"
    assert runtime.load_tasks(tmp_path, "run-2") == service.tasks
    assert seen == [tmp_path, tmp_path, tmp_path]


def test_runtime_lifecycle_actions_use_injected_coordinator_port(
    tmp_path: Path,
) -> None:
    """Delete/retry/rerun share the runtime action boundary."""
    coordinator = MagicMock()
    coordinator.delete.return_value = SimpleNamespace(errors=[])
    coordinator.retry_failed.return_value = SimpleNamespace(changed_count=2, errors=[])
    coordinator.rerun.return_value = SimpleNamespace(changed_count=1, errors=[])
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    assert runtime.delete_run(tmp_path, "run-delete", coordinator=coordinator).errors == []
    assert runtime.retry_failed(tmp_path, "run-retry", coordinator=coordinator).changed_count == 2
    assert runtime.rerun(tmp_path, "run-rerun", coordinator=coordinator).changed_count == 1

    coordinator.delete.assert_called_once_with("run-delete")
    coordinator.retry_failed.assert_called_once_with("run-retry")
    coordinator.rerun.assert_called_once_with("run-rerun")


def test_runtime_lifecycle_actions_preserve_coordinator_resolver_seam(
    tmp_path: Path,
) -> None:
    """The page's historical coordinator resolver remains injectable."""
    coordinator = MagicMock()
    coordinator.rerun.return_value = SimpleNamespace(changed_count=1, errors=[])
    resolver = MagicMock(return_value=coordinator)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    result = runtime.rerun(tmp_path, "run-rerun", resolver=resolver)

    assert result.changed_count == 1
    resolver.assert_called_once_with(tmp_path)
    coordinator.rerun.assert_called_once_with("run-rerun")


def test_runtime_cancel_uses_client_handle_and_preserves_resolver_seams(
    tmp_path: Path,
) -> None:
    coordinator = MagicMock()
    client = MagicMock()
    resolver = MagicMock(return_value=coordinator)
    client_factory = MagicMock(return_value=client)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    result = runtime.cancel_run(
        tmp_path,
        "run-cancel",
        server_id="server",
        resolver=resolver,
        client_factory=client_factory,
    )

    assert result == (1, [])
    resolver.assert_called_once_with(tmp_path)
    client_factory.assert_called_once_with(coordinator, "server")
    client.attach.assert_called_once_with("run-cancel")
    client.attach.return_value.cancel.assert_called_once_with()


def test_runtime_cancel_loads_server_id_when_not_supplied(tmp_path: Path) -> None:
    service = MagicMock()
    service.load_run.return_value = SimpleNamespace(server_id="loaded-server")
    coordinator = MagicMock()
    coordinator.service = service
    client = MagicMock()
    resolver = MagicMock(return_value=coordinator)
    client_factory = MagicMock(return_value=client)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: service, session_pool=_Pool())

    assert runtime.cancel_run(
        tmp_path,
        "run-cancel",
        resolver=resolver,
        client_factory=client_factory,
    ) == (1, [])

    service.load_run.assert_called_once_with("run-cancel")
    client_factory.assert_called_once_with(coordinator, "loaded-server")


def test_runtime_refresh_uses_client_port_and_preserves_backend_policy(
    tmp_path: Path,
) -> None:
    coordinator = MagicMock()
    client = MagicMock()
    handle = MagicMock()
    handle.to_dict.return_value = {"backend": "control"}
    expected = SimpleNamespace(errors=[])
    client.attach.return_value = handle
    client.refresh_outcome.return_value = expected
    client_factory = MagicMock(return_value=client)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    result = runtime.refresh_run(
        tmp_path,
        "run-refresh",
        ["*.out"],
        download=True,
        server_id="server",
        coordinator=coordinator,
        client_factory=client_factory,
    )

    assert result is expected
    client_factory.assert_called_once_with(coordinator, "server")
    client.attach.assert_called_once_with("run-refresh")
    client.refresh_outcome.assert_called_once_with(handle, [], download=True)


def test_runtime_download_uses_client_port_and_preserves_backend_policy(
    tmp_path: Path,
) -> None:
    coordinator = MagicMock()
    client = MagicMock()
    handle = MagicMock()
    handle.to_dict.return_value = {"backend": "control"}
    expected = SimpleNamespace(errors=[], transfer_records=[], failures=[])
    client.attach.return_value = handle
    client.download_outcome.return_value = expected
    client_factory = MagicMock(return_value=client)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    result = runtime.download_run(
        tmp_path,
        "run-download",
        ["*.out"],
        server_id="server",
        coordinator=coordinator,
        client_factory=client_factory,
    )

    assert result is expected
    client_factory.assert_called_once_with(coordinator, "server")
    client.attach.assert_called_once_with("run-download")
    client.download_outcome.assert_called_once_with(handle, [])


def test_runtime_download_resolves_server_id_through_legacy_resolver(
    tmp_path: Path,
) -> None:
    coordinator = MagicMock()
    coordinator.service.load_run.return_value = SimpleNamespace(server_id="loaded")
    client = MagicMock()
    handle = MagicMock()
    handle.to_dict.return_value = {}
    expected = SimpleNamespace(errors=[], transfer_records=[], failures=[])
    client.attach.return_value = handle
    client.download_outcome.return_value = expected
    resolver = MagicMock(return_value=coordinator)
    client_factory = MagicMock(return_value=client)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    result = runtime.download_run(
        tmp_path,
        "run-download",
        ["*.out"],
        resolver=resolver,
        client_factory=client_factory,
    )

    assert result is expected
    resolver.assert_called_once_with(tmp_path)
    coordinator.service.load_run.assert_called_once_with("run-download")
    client_factory.assert_called_once_with(coordinator, "loaded")
    client.download_outcome.assert_called_once_with(handle, ["*.out"])


def test_runtime_progress_uses_coordinator_resolver_seam(tmp_path: Path) -> None:
    coordinator = MagicMock()
    expected = SimpleNamespace(errors=[])
    coordinator.sync_progress.return_value = expected
    resolver = MagicMock(return_value=coordinator)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    result = runtime.sync_progress(tmp_path, "run-progress", resolver=resolver)

    assert result is expected
    resolver.assert_called_once_with(tmp_path)
    coordinator.sync_progress.assert_called_once_with("run-progress")


def test_runtime_submit_uses_client_port_and_resolver_seam(tmp_path: Path) -> None:
    coordinator = MagicMock()
    coordinator.service.load_run.return_value = SimpleNamespace(server_id="server")
    client = MagicMock()
    expected = (object(), SimpleNamespace(errors=[], submit_results=[object()]))
    client.submit_with_outcome.return_value = expected
    resolver = MagicMock(return_value=coordinator)
    client_factory = MagicMock(return_value=client)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: _Service(), session_pool=_Pool())

    result = runtime.submit_run(
        tmp_path,
        "run-submit",
        resource_overrides={"cpus": 2},
        resolver=resolver,
        client_factory=client_factory,
    )

    assert result is expected
    resolver.assert_called_once_with(tmp_path)
    coordinator.service.load_run.assert_called_once_with("run-submit")
    client_factory.assert_called_once_with(coordinator, "server")
    request = client.submit_with_outcome.call_args.args[0]
    assert request.run_id == "run-submit"
    assert request.resource_overrides == {"cpus": 2}


@pytest.mark.parametrize(
    ("runtime_method", "coordinator_method"),
    [("confirm_submitted", "confirm_submitted"), ("abandon_submit", "abandon_submit")],
)
def test_runtime_uncertain_actions_validate_and_use_lifecycle_port(
    tmp_path: Path, runtime_method: str, coordinator_method: str
) -> None:
    service = MagicMock()
    service.load_tasks.return_value = [SimpleNamespace(task_id="task-1", status=TaskStatus.uncertain)]
    coordinator = MagicMock()
    coordinator.service = service
    expected = SimpleNamespace(changed_count=1, errors=[])
    getattr(coordinator, coordinator_method).return_value = expected
    resolver = MagicMock(return_value=coordinator)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: service, session_pool=_Pool())

    result = getattr(runtime, runtime_method)(
        tmp_path,
        "run-uncertain",
        ["task-1"],
        resolver=resolver,
    )

    assert result is expected
    service.load_tasks.assert_called_once_with("run-uncertain")
    resolver.assert_called_once_with(tmp_path)
    getattr(coordinator, coordinator_method).assert_called_once_with("run-uncertain", ("task-1",))


def test_runtime_uncertain_actions_fail_closed_when_task_is_no_longer_uncertain(
    tmp_path: Path,
) -> None:
    service = MagicMock()
    service.load_tasks.return_value = [SimpleNamespace(task_id="task-1", status=TaskStatus.running)]
    coordinator = MagicMock()
    coordinator.service = service
    resolver = MagicMock(return_value=coordinator)
    runtime = RunsPageRuntime(service_factory=lambda _workspace: service, session_pool=_Pool())

    with pytest.raises(ValueError, match="no longer uncertain"):
        runtime.confirm_submitted(
            tmp_path,
            "run-uncertain",
            ["task-1"],
            resolver=resolver,
        )

    resolver.assert_not_called()
    coordinator.confirm_submitted.assert_not_called()


def test_runtime_preserves_legacy_coordinator_and_client_factories(
    tmp_path: Path,
) -> None:
    pool = _Pool()
    coordinator = object()
    client = object()
    coordinator_factory = MagicMock(return_value=coordinator)
    client_factory = MagicMock(return_value=client)
    runtime = RunsPageRuntime(
        service_factory=lambda _workspace: _Service(),
        coordinator_factory=coordinator_factory,
        client_factory=client_factory,
        session_pool=pool,
    )

    assert runtime.coordinator(tmp_path) is coordinator
    assert runtime.client(coordinator, "server") is client
    coordinator_factory.assert_called_once_with(tmp_path)
    client_factory.assert_called_once_with(coordinator, "server")


def test_runtime_default_graph_assembles_without_connecting(tmp_path: Path) -> None:
    service = _Service()
    pool = _Pool()
    server = object()
    ssh_factory = MagicMock()
    sftp_factory = MagicMock()
    runtime = RunsPageRuntime(
        service_factory=lambda _workspace: service,
        session_pool=pool,
        server_loader=lambda: SimpleNamespace(servers={"server": server}),
        coordinator_constructor=RunCoordinator,
        client_constructor=lambda: SSHConfFlowClient,
        ssh_factory=ssh_factory,
        sftp_factory=sftp_factory,
    )

    coordinator = runtime.coordinator(tmp_path)
    client = runtime.client(coordinator, "server")

    assert coordinator.service is service
    assert coordinator._session_pool is pool
    assert coordinator.server_config("server") is server
    assert client._coordinator is coordinator
    assert client._server_id == "server"
    ssh_factory.assert_not_called()
    sftp_factory.assert_not_called()


def test_runtime_closes_owned_pool_once_but_not_borrowed_pool() -> None:
    owned = _Pool()
    owned_runtime = RunsPageRuntime(session_pool_factory=lambda: owned)
    owned_runtime.close()
    owned_runtime.close()
    assert owned.close_calls == 1

    borrowed = _Pool()
    borrowed_runtime = RunsPageRuntime(session_pool=borrowed)
    borrowed_runtime.close()
    assert borrowed.close_calls == 0


def test_runtime_rejects_conflicting_factory_and_constructor_seams() -> None:
    with pytest.raises(TypeError, match="service_factory"):
        RunsPageRuntime(
            service_factory=lambda _workspace: _Service(),
            service_constructor=lambda: _Service,
        )
    with pytest.raises(TypeError, match="session_pool"):
        RunsPageRuntime(session_pool=_Pool(), session_pool_constructor=_Pool)


def test_runtime_monitor_inputs_freeze_service_snapshot_and_backend_paths(
    tmp_path: Path,
) -> None:
    active = SimpleNamespace(
        run_id="run-active",
        server_id="wsl",
        remote_dir="/remote/submission",
        status_summary={"running": 1},
    )
    control = SimpleNamespace(
        run_id="run-control",
        server_id="wsl",
        remote_dir="/remote/control",
        status_summary={"submitted": 1},
    )
    service = MagicMock()
    service.list_runs.return_value = [active, control]
    service.load_tasks.return_value = [
        SimpleNamespace(
            remote_state_path="/remote/submission/work/.workflow_state.json",
            remote_stats_path="/remote/submission/work/workflow_stats.json",
        )
    ]
    durable_loader = MagicMock(side_effect=[{"backend": "legacy"}, {"backend": "control"}])
    server = object()
    runtime = RunsPageRuntime(
        service_factory=lambda _workspace: service,
        server_loader=lambda: SimpleNamespace(servers={"wsl": server}),
        durable_backend_loader=durable_loader,
        session_pool=_Pool(),
    )

    snapshot = runtime.monitor_inputs(tmp_path)

    assert isinstance(snapshot, RunsMonitorInput)
    assert snapshot.workspace == tmp_path
    assert snapshot.server_ids == frozenset({"wsl"})
    assert isinstance(snapshot.runs, tuple)
    assert all(isinstance(item, MonitorRunInput) for item in snapshot.runs)
    legacy, control_input = snapshot.runs
    assert legacy.server_config is server
    assert legacy.remote_batch_dir == "/remote/submission/.jobdesk_runs/run-active"
    assert legacy.progress_paths == (
        "/remote/submission/work/.workflow_state.json",
        "/remote/submission/work/workflow_stats.json",
    )
    assert control_input.durable_backend == {"backend": "control"}
    assert control_input.progress_paths == ()
    service.load_tasks.assert_called_once_with("run-active")
    assert durable_loader.call_count == 2

    with pytest.raises(TypeError):
        legacy.status_summary["failed"] = 1  # type: ignore[index]
    with pytest.raises(TypeError):
        legacy.durable_backend["backend"] = "changed"  # type: ignore[index]
    with pytest.raises(AttributeError):
        snapshot.runs = ()  # type: ignore[misc]
