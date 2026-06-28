"""Application-layer run lifecycle coordination tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from jobdesk_app.config.schema import ServerConfig
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


def test_refresh_and_download_share_one_session(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    record = service.create_run(_spec(), run_id="run-1")
    ssh = MagicMock()
    sftp = MagicMock()
    refresh_result = MagicMock(changed_count=1, warnings=[])
    monkeypatch.setattr(service, "refresh_run", MagicMock(return_value=refresh_result))
    monkeypatch.setattr(service, "download_completed", MagicMock(return_value=([], [("a", "missing")])))
    coordinator = RunCoordinator(
        service,
        server_lookup=_server,
        ssh_factory=lambda _config: ssh,
        sftp_factory=lambda _ssh: sftp,
    )

    outcome = coordinator.refresh_and_download(record.run_id, ["*.out"])

    assert outcome.refresh_result is refresh_result
    assert outcome.failures == [("a", "missing")]
    service.refresh_run.assert_called_once_with(record.run_id, ssh)
    service.download_completed.assert_called_once_with(record.run_id, sftp, ["*.out"])
    sftp.close.assert_called_once_with()
    ssh.close.assert_called_once_with()
