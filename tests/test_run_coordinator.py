"""Application-layer run lifecycle coordination tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from jobdesk_app.config.schema import ServerConfig
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.services.run_coordinator import RunCoordinator
from jobdesk_app.services.run_service import RunService


def _spec() -> RunSpec:
    return RunSpec(
        server_id="server",
        remote_dir="/remote/project",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/project/a.sh")],
    )


def _server(_server_id: str) -> ServerConfig:
    return ServerConfig(server_id="server", host="example", username="user")


def test_create_and_submit_preserves_created_run_when_connect_fails(tmp_path) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    ssh = MagicMock()
    ssh.connect.side_effect = OSError("offline")
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: ssh,
        sftp_factory=MagicMock(),
    )

    outcome = coordinator.create_and_submit(_spec(), local_dir=str(tmp_path))

    assert len(outcome.records) == 1
    assert service.load_run(outcome.records[0].run_id).run_id == outcome.records[0].run_id
    assert outcome.errors == ["OSError: offline"]
    ssh.close.assert_called_once_with()


def test_submit_returns_result_and_closes_both_clients(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    ssh = MagicMock()
    sftp = MagicMock()
    result = SubmitResult("run-1", 1, "/remote/project")
    submit = MagicMock(return_value=result)
    monkeypatch.setattr(service, "submit_run", submit)
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: ssh,
        sftp_factory=lambda _ssh: sftp,
    )

    outcome = coordinator.submit(record.run_id)

    assert outcome.submit_results == [result]
    assert outcome.errors == []
    ssh.connect.assert_called_once_with()
    sftp.close.assert_called_once_with()
    ssh.close.assert_called_once_with()


def test_submit_preserves_success_when_client_close_fails(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    ssh = MagicMock()
    sftp = MagicMock()
    sftp.close.side_effect = OSError("close failed")
    result = SubmitResult("run-1", 1, "/remote/project")
    monkeypatch.setattr(service, "submit_run", MagicMock(return_value=result))
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: ssh,
        sftp_factory=lambda _ssh: sftp,
    )

    outcome = coordinator.submit(record.run_id)

    assert outcome.submit_results == [result]
    assert outcome.errors == []
    ssh.close.assert_called_once_with()


def test_submit_failure_after_remote_start_is_recovered_immediately(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-started-failure")

    class StartedThenFailingSubmitter:
        def __init__(self, **kwargs):
            self.tasks = kwargs["tasks"]
            self.remote_started = kwargs["remote_started_callback"]

        def submit_batch(self):
            self.remote_started([self.tasks[0].task_id])
            raise RuntimeError("connection lost after remote start")

    monkeypatch.setattr(
        "jobdesk_app.services.run_service.JobSubmitter", StartedThenFailingSubmitter
    )
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: MagicMock(),
        sftp_factory=lambda _ssh: MagicMock(),
    )

    outcome = coordinator.submit(record.run_id)

    assert outcome.errors == ["RuntimeError: connection lost after remote start"]
    assert outcome.records[0].status_summary == {"uncertain": 1}
    operation = service.repository.list_operations()[0]
    assert operation.phase == "completed"
    assert operation.completed_at is not None


def test_submit_preflight_failure_does_not_recover_other_operations(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-preflight-failure")
    recover = MagicMock()
    monkeypatch.setattr(service, "recover_submit_operations", recover)
    coordinator = RunCoordinator(
        service,
        server_lookup=MagicMock(side_effect=OSError("server config unavailable")),
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
    )

    outcome = coordinator.submit(record.run_id)

    assert outcome.errors == ["OSError: server config unavailable"]
    recover.assert_not_called()


def test_missing_run_is_returned_as_outcome_error(tmp_path) -> None:
    coordinator = RunCoordinator(
        RunService(tmp_path, runs_dir=tmp_path / "runs"),
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
    )

    outcome = coordinator.download("missing", ["*.out"])

    assert outcome.records == []
    assert outcome.errors == ["KeyError: 'run not found: missing'"]


def test_non_owning_coordinator_does_not_close_shared_clients(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    ssh = MagicMock()
    sftp = MagicMock()
    result = SubmitResult("run-1", 1, "/remote/project")
    monkeypatch.setattr(service, "submit_run", MagicMock(return_value=result))
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: ssh,
        sftp_factory=lambda _ssh: sftp,
        close_clients=False,
    )

    coordinator.submit(record.run_id)

    sftp.close.assert_not_called()
    ssh.close.assert_not_called()


def test_refresh_and_download_downloads_after_refresh_reveals_completed_task(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    refresh_ssh = MagicMock()
    download_ssh = MagicMock()
    ssh_clients = iter([refresh_ssh, download_ssh])
    sftp = MagicMock()
    refresh_result = MagicMock(changed_count=1, warnings=[])

    def refresh_to_completed(run_id, _ssh):
        service.repository.mutate_tasks(
            run_id,
            lambda tasks: [
                task.model_copy(update={"status": TaskStatus.remote_completed})
                for task in tasks
            ],
        )
        return refresh_result

    monkeypatch.setattr(service, "refresh_run", MagicMock(side_effect=refresh_to_completed))
    monkeypatch.setattr(service, "download_completed", MagicMock(return_value=([], [("a", "missing")])))
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: next(ssh_clients),
        sftp_factory=lambda _ssh: sftp,
    )

    outcome = coordinator.refresh_and_download(record.run_id, ["*.out"])

    assert outcome.refresh_result is refresh_result
    assert outcome.failures == [("a", "missing")]
    service.refresh_run.assert_called_once_with(record.run_id, refresh_ssh)
    service.download_completed.assert_called_once_with(record.run_id, sftp, ["*.out"])
    refresh_ssh.connect.assert_called_once_with()
    download_ssh.connect.assert_called_once_with()
    sftp.close.assert_called_once_with()
    refresh_ssh.close.assert_called_once_with()
    download_ssh.close.assert_called_once_with()


def test_refresh_and_download_preserves_refresh_when_sftp_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    refresh_result = MagicMock(changed_count=1, warnings=[])

    def refresh_to_completed(run_id, _ssh):
        service.repository.mutate_tasks(
            run_id,
            lambda tasks: [
                task.model_copy(update={"status": TaskStatus.remote_completed})
                for task in tasks
            ],
        )
        return refresh_result

    refresh = MagicMock(side_effect=refresh_to_completed)
    monkeypatch.setattr(service, "refresh_run", refresh)
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: MagicMock(),
        sftp_factory=MagicMock(side_effect=OSError("sftp unavailable")),
    )

    outcome = coordinator.refresh_and_download(record.run_id, ["*.out"])

    assert refresh.call_count == 1
    assert outcome.refresh_result is refresh_result
    assert outcome.changed_count == 1
    assert outcome.records[0].status_summary == {"remote_completed": 1}
    assert outcome.errors == ["OSError: sftp unavailable"]


def test_confirm_submitted_returns_consistent_outcome(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    monkeypatch.setattr(service, "confirm_submitted", MagicMock(return_value=["a"] ))
    coordinator = RunCoordinator(service, server_lookup=_server, ssh_factory=MagicMock(), sftp_factory=MagicMock())

    outcome = coordinator.confirm_submitted(record.run_id, ["a"])

    assert outcome.records == [service.load_run(record.run_id)]
    assert outcome.changed_count == 1
    assert outcome.errors == []


def test_abandon_submit_converts_service_error_to_outcome(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    service.create_run(_spec(), run_id="run-1")
    monkeypatch.setattr(service, "abandon_submit", MagicMock(side_effect=ValueError("bad selection")))
    coordinator = RunCoordinator(service, server_lookup=_server, ssh_factory=MagicMock(), sftp_factory=MagicMock())

    outcome = coordinator.abandon_submit("run-1", ["a"])

    assert outcome.changed_count == 0
    assert outcome.errors == ["ValueError: bad selection"]


def test_recover_operations_replays_all_kinds_without_retrying_legacy_imports(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    retry_migrations = MagicMock(return_value=[])
    monkeypatch.setattr(service, "retry_legacy_imports", retry_migrations)
    monkeypatch.setattr(service, "recover_submit_operations", MagicMock(return_value=2))
    monkeypatch.setattr(
        service,
        "recover_delete_operations_globally",
        MagicMock(side_effect=OSError("locked")),
    )
    coordinator = RunCoordinator(service, server_lookup=_server, ssh_factory=MagicMock(), sftp_factory=MagicMock())

    outcome = coordinator.recover_operations()

    assert outcome.changed_count == 2
    assert outcome.errors == ["OSError: locked"]
    retry_migrations.assert_not_called()


def test_recover_operations_can_explicitly_retry_legacy_imports(
    tmp_path, monkeypatch
) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    migration_error = SimpleNamespace(
        legacy_path=tmp_path / "legacy" / "run.json",
        message="invalid manifest",
    )
    retry_migrations = MagicMock(return_value=[migration_error])
    monkeypatch.setattr(service, "retry_legacy_imports", retry_migrations)
    monkeypatch.setattr(service, "recover_submit_operations", MagicMock(return_value=0))
    monkeypatch.setattr(
        service,
        "recover_delete_operations_globally",
        MagicMock(return_value=(0, [])),
    )
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
    )

    outcome = coordinator.recover_operations(include_legacy_imports=True)

    retry_migrations.assert_called_once_with()
    assert outcome.changed_count == 0
    assert outcome.errors == [
        f"legacy migration failed for {migration_error.legacy_path}: invalid manifest"
    ]


def test_recover_operations_replays_delete_journals_from_all_workspaces(tmp_path) -> None:
    runs_dir = tmp_path / "runs"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    services = [
        RunService(workspace_a, runs_dir=runs_dir),
        RunService(workspace_b, runs_dir=runs_dir),
    ]
    operations = []
    for index, service in enumerate(services, start=1):
        record = service.create_run(
            _spec(),
            run_id=f"run-{index}",
            local_dir=str(service.workspace_dir),
        )
        results_dir = service.workspace_dir / "results" / record.run_id
        results_dir.mkdir(parents=True)
        operations.append(
            service.repository.prepare_delete_run(
                record.run_id,
                run_dir=record.run_dir,
                results_root=service.workspace_dir / "results",
                results_dir=results_dir,
            )
        )

    coordinator = RunCoordinator(
        services[0],
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
    )
    outcome = coordinator.recover_operations()

    assert outcome.changed_count == 2
    stored = {item.operation_id: item for item in services[0].repository.list_operations()}
    assert all(stored[operation.operation_id].phase == "completed" for operation in operations)


def test_recover_operations_rejects_forged_external_results_root(tmp_path) -> None:
    import copy

    runs_dir = tmp_path / "runs"
    workspace = tmp_path / "workspace"
    service = RunService(workspace, runs_dir=runs_dir)
    record = service.create_run(
        _spec(),
        run_id="forged-root",
        local_dir=str(workspace),
    )
    operation = service.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=workspace / "results",
        results_dir=workspace / "results" / record.run_id,
    )
    external_root = tmp_path / "external" / "results"
    external_results = external_root / record.run_id
    external_results.mkdir(parents=True)
    sentinel = external_results / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    forged_payload = copy.deepcopy(operation.payload)
    forged_payload["run"]["local_dir"] = str((tmp_path / "external").resolve())
    forged_payload["results_root"] = str(external_root.resolve())
    forged_payload["results_dir"] = str(external_results.resolve())
    forged_payload["trash_results_dir"] = str(
        (external_root / ".jobdesk-trash" / operation.operation_id / "results").resolve()
    )
    service.repository.advance_operation(
        operation.operation_id,
        "prepared",
        "prepared",
        payload=forged_payload,
    )

    outcome = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
    ).recover_operations()

    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert outcome.changed_count == 0
    assert any("workspace binding" in error for error in outcome.errors)


def test_recover_operations_rejects_forged_registered_workspace_binding(
    tmp_path,
) -> None:
    import copy

    runs_dir = tmp_path / "runs"
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    service_a = RunService(workspace_a, runs_dir=runs_dir)
    service_b = RunService(workspace_b, runs_dir=runs_dir)

    anchor = service_b.create_run(
        _spec(), run_id="workspace-b-anchor", local_dir=str(workspace_b)
    )
    anchor_operation = service_b.repository.prepare_delete_run(
        anchor.run_id,
        run_dir=anchor.run_dir,
        results_root=workspace_b / "results",
        results_dir=workspace_b / "results" / anchor.run_id,
    )
    service_b._recover_delete_operation(anchor_operation, raise_errors=True)

    record = service_a.create_run(
        _spec(), run_id="forged-binding", local_dir=str(workspace_a)
    )
    operation = service_a.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=workspace_a / "results",
        results_dir=workspace_a / "results" / record.run_id,
    )
    external_results = workspace_b / "results" / record.run_id
    external_results.mkdir(parents=True)
    sentinel = external_results / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")
    forged_payload = copy.deepcopy(operation.payload)
    forged_payload["run"]["local_dir"] = str(workspace_b.resolve())
    forged_payload["results_root"] = str((workspace_b / "results").resolve())
    forged_payload["results_dir"] = str(external_results.resolve())
    forged_payload["trash_results_dir"] = str(
        (
            workspace_b
            / "results"
            / ".jobdesk-trash"
            / operation.operation_id
            / "results"
        ).resolve()
    )
    service_a.repository.advance_operation(
        operation.operation_id,
        "prepared",
        "prepared",
        payload=forged_payload,
    )

    outcome = RunCoordinator(
        service_a,
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
    ).recover_operations()

    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert outcome.changed_count == 0
    assert any("workspace binding" in error for error in outcome.errors)


def test_recover_operations_surfaces_unanchored_metadata_deleted_journal(
    tmp_path,
) -> None:
    runs_dir = tmp_path / "runs"
    workspace = tmp_path / "legacy-workspace"
    service = RunService(tmp_path / "current", runs_dir=runs_dir)
    operation = service.repository.create_operation(
        "legacy-deleted",
        "delete",
        "metadata_deleted",
        {
            "run": {"local_dir": str(workspace.resolve())},
            "results_root": str((workspace / "results").resolve()),
            "results_dir": str((workspace / "results" / "legacy-deleted").resolve()),
        },
    )

    outcome = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
    ).recover_operations()

    assert outcome.changed_count == 0
    assert any("trusted workspace" in error for error in outcome.errors)
    stored = {item.operation_id: item for item in service.repository.list_operations()}
    assert stored[operation.operation_id].phase == "metadata_deleted"
    assert stored[operation.operation_id].completed_at is None


def test_submit_uses_session_pool_lease(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    result = SubmitResult("run-1", 1, "/remote/project")
    monkeypatch.setattr(service, "submit_run", MagicMock(return_value=result))
    lease = MagicMock()
    lease.__enter__.return_value = SimpleNamespace(ssh=MagicMock(), sftp=MagicMock())
    pool = MagicMock()
    pool.lease.return_value = lease
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
        session_pool=pool,
    )

    assert coordinator.submit(record.run_id).submit_results == [result]
    pool.lease.assert_called_once_with("server", _server("server"), need_sftp=True)
    lease.__exit__.assert_called_once()


def test_refresh_requests_ssh_only_session_pool_lease(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    refresh_result = MagicMock(changed_count=0, warnings=[])
    monkeypatch.setattr(service, "refresh_run", MagicMock(return_value=refresh_result))
    ssh = MagicMock()
    lease = MagicMock()
    lease.__enter__.return_value = SimpleNamespace(ssh=ssh, sftp=None)
    pool = MagicMock()
    pool.lease.return_value = lease
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=MagicMock(),
        sftp_factory=MagicMock(),
        session_pool=pool,
    )

    outcome = coordinator.refresh(record.run_id)

    assert outcome.errors == []
    service.refresh_run.assert_called_once_with(record.run_id, ssh)
    pool.lease.assert_called_once_with("server", _server("server"), need_sftp=False)
