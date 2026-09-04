"""CLI integration tests for the new run + files command groups."""

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from jobdesk_app.application.facades import (
    DownloadResult,
    RecoveryResult,
    RunDetails,
    RunSummary,
    TransferBatchResult,
)
from jobdesk_app.application.outcomes import OperationFailure, OperationOutcome
from jobdesk_app.cli import _build_parser, main
from tests.repository_helpers import replace_tasks_for_test


@pytest.fixture(autouse=True)
def _cli_appdata(tmp_path, monkeypatch):
    """Keep each CLI container's persistent state inside the test workspace."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))


@contextmanager
def _isolated_appdata(tmp):
    """Temporarily set APPDATA so RunService defaults to a temp runs_dir."""
    old = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(tmp)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = old


def _run_details(run_id="run-1", *, changed_count=0):
    summary = RunSummary(run_id, "srv", None, "now")
    return RunDetails(summary, "/tmp/x", "", changed_count=changed_count)


def test_cli_run_create_and_list(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        rc = main(
            [
                "run",
                "create",
                workspace,
                "--server",
                "test_srv",
                "--remote-dir",
                "/tmp/test",
                "--command",
                "echo {name}",
                "--files",
                "/remote/a.gjf",
                "/remote/b.gjf",
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "created run" in out

        rc = main(["run", "list", workspace])
        assert rc == 0
        out = capsys.readouterr().out
        assert "test_srv" in out
        assert "/tmp/test" in out


def test_cli_files_upload_passes_overwrite_and_dry_run(monkeypatch):
    import jobdesk_app.cli as cli

    application = MagicMock()
    application.files.upload.return_value = OperationOutcome.success(TransferBatchResult(()))
    monkeypatch.setattr(cli, "create_application", lambda *args, **kwargs: application)
    rc = main(["files", "upload", "srv", "local.txt", "/remote/x.txt", "--overwrite", "--dry-run"])
    assert rc == 0
    application.files.upload.assert_called_once_with(
        "srv", "local.txt", "/remote/x.txt", policy="overwrite", dry_run=True
    )


def test_cli_run_list_empty(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        rc = main(["run", "list", workspace])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No runs" in out


def test_cli_verify_rollback_is_a_fail_closed_gate(capsys, tmp_path):
    import jobdesk_app.cli as cli

    application = MagicMock()
    application.runs.verify_rollback.return_value = OperationOutcome.failure(
        OperationFailure("verify_rollback", "operation_failed", "control JSON projection is stale", False)
    )
    with patch.object(cli, "create_application", return_value=application):
        rc = main(["run", "verify-rollback", str(tmp_path)])

    assert rc == 2
    assert "stale" in capsys.readouterr().err
    application.runs.verify_rollback.assert_called_once_with()


def test_cli_verify_rollback_reports_ready_after_gate_passes(capsys, tmp_path):
    import jobdesk_app.cli as cli

    application = MagicMock()
    application.runs.verify_rollback.return_value = OperationOutcome.success(None)
    with patch.object(cli, "create_application", return_value=application):
        rc = main(["run", "verify-rollback", str(tmp_path)])

    assert rc == 0
    assert "rollback ready" in capsys.readouterr().out
    application.runs.verify_rollback.assert_called_once_with()


def test_cli_run_list_reports_legacy_migration_errors(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        broken = Path(workspace) / "JobDesk" / "runs" / "broken"
        broken.mkdir(parents=True)
        (broken / "run.json").write_text("{broken", encoding="utf-8")

        rc = main(["run", "list", workspace])

        captured = capsys.readouterr()
        assert rc == 0
        assert "legacy run import failed" in captured.err.lower()
        assert str(broken) in captured.err


def test_cli_run_retry_no_failed(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        main(
            [
                "run",
                "create",
                workspace,
                "--server",
                "s",
                "--remote-dir",
                "/tmp/x",
                "--command",
                "echo {name}",
                "--files",
                "/remote/f.txt",
            ]
        )
        capsys.readouterr()

        from jobdesk_app.infrastructure.runtime.run_service import RunService

        run_id = RunService(workspace).list_runs()[0].run_id

        rc = main(["run", "retry", workspace, run_id])
        assert rc == 0
        capsys.readouterr()


def test_cli_run_rerun_reports_active_remote_tasks(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        main(
            [
                "run",
                "create",
                workspace,
                "--server",
                "s",
                "--remote-dir",
                "/tmp/x",
                "--command",
                "echo {name}",
                "--files",
                "/remote/f.txt",
            ]
        )
        capsys.readouterr()

        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.infrastructure.runtime.run_service import RunService

        service = RunService(workspace)
        record = service.list_runs()[0]
        tasks = service.repository.load_tasks(record.run_id)
        tasks[0].status = TaskStatus.running
        replace_tasks_for_test(service.repository, record.run_id, tasks)

        rc = main(["run", "rerun", workspace, record.run_id])
        out = capsys.readouterr().out

        assert rc == 2
        assert "cannot rerun active remote tasks" in out


def test_cli_run_delete(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        main(
            [
                "run",
                "create",
                workspace,
                "--server",
                "s",
                "--remote-dir",
                "/tmp/x",
                "--command",
                "echo {name}",
                "--files",
                "/remote/f.txt",
            ]
        )
        capsys.readouterr()

        from jobdesk_app.infrastructure.runtime.run_service import RunService

        run_id = RunService(workspace).list_runs()[0].run_id

        rc = main(["run", "delete", workspace, run_id])
        assert rc == 0
        assert RunService(workspace).list_runs() == []


def test_cli_run_cancel_invokes_remote_cancellation(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        main(
            [
                "run",
                "create",
                workspace,
                "--server",
                "s",
                "--remote-dir",
                "/tmp/x",
                "--command",
                "echo {name}",
                "--files",
                "/remote/f.txt",
            ]
        )
        capsys.readouterr()
        from jobdesk_app.infrastructure.runtime.run_service import RunService

        run_id = RunService(workspace).list_runs()[0].run_id
        application = MagicMock()
        application.runs.cancel.return_value = OperationOutcome.success(_run_details(run_id, changed_count=1))
        with patch("jobdesk_app.cli.create_application", return_value=application):
            rc = main(["run", "cancel", workspace, run_id])

        assert rc == 0
        application.runs.cancel.assert_called_once_with(run_id)
        assert "cancelled 1 task(s)" in capsys.readouterr().out


def test_cli_run_download_returns_failure_for_coordinator_error(capsys, tmp_path):
    application = MagicMock()
    application.runs.download.return_value = OperationOutcome.failure(
        OperationFailure("download", "offline", "OSError: offline", True)
    )
    with patch("jobdesk_app.cli.create_application", return_value=application):
        rc = main(["run", "download", str(tmp_path), "run-1", "--patterns", "*.out"])

    assert rc == 2
    assert "offline" in capsys.readouterr().out


def test_cli_no_longer_registers_jobdesk_owned_workflow_commands():
    parser = _build_parser()
    subcommands = next(action.choices for action in parser._actions if getattr(action, "choices", None))

    assert "workflow" not in subcommands


@pytest.mark.parametrize("return_code", [0, 2])
def test_cli_closes_application_container_for_command_results(return_code, tmp_path):
    """Both successful and reported-error commands release shared resources."""
    import jobdesk_app.cli as cli

    application = MagicMock()
    command = MagicMock(return_value=return_code)
    with (
        patch.object(cli, "create_application", return_value=application) as create,
        patch.object(cli, "_build_parser") as build_parser,
    ):
        parser = build_parser.return_value
        parser.parse_args.return_value = SimpleNamespace(func=command, workspace=tmp_path)

        assert main(["ignored"]) == return_code

    create.assert_called_once_with(tmp_path, servers_path=None)
    command.assert_called_once()
    assert command.call_args.args[0].application is application
    application.close.assert_called_once_with()


def test_cli_closes_application_container_when_command_raises(tmp_path):
    """An unexpected command exception must not leak SSH/SFTP resources."""
    import jobdesk_app.cli as cli

    application = MagicMock()
    command = MagicMock(side_effect=RuntimeError("boom"))
    with (
        patch.object(cli, "create_application", return_value=application),
        patch.object(cli, "_build_parser") as build_parser,
    ):
        parser = build_parser.return_value
        parser.parse_args.return_value = SimpleNamespace(func=command, workspace=tmp_path)

        with pytest.raises(RuntimeError, match="boom"):
            main(["ignored"])

    application.close.assert_called_once_with()


class TestDownloadPatterns:
    """Test --patterns supports both comma-separated and multi-arg."""

    def _setup_downloadable_run(self, workspace):
        """Create a run with remote_completed task."""
        from jobdesk_app.core.lifecycle import TaskStatus

        main(
            [
                "run",
                "create",
                workspace,
                "--server",
                "srv",
                "--remote-dir",
                "/tmp/x",
                "--command",
                "echo {name}",
                "--files",
                "/remote/a.gjf",
            ]
        )
        from jobdesk_app.infrastructure.runtime.run_service import RunService

        svc = RunService(workspace)
        run_id = svc.list_runs()[0].run_id
        record = svc.load_run(run_id)
        tasks = svc.repository.load_tasks(record.run_id)
        for t in tasks:
            t.status = TaskStatus.remote_completed
        replace_tasks_for_test(svc.repository, record.run_id, tasks)
        return run_id

    def test_patterns_comma_separated(self):
        with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
            run_id = self._setup_downloadable_run(workspace)
            application = MagicMock()
            application.runs.download.return_value = OperationOutcome.success(DownloadResult(_run_details(run_id), ()))
            with patch("jobdesk_app.cli.create_application", return_value=application):
                rc = main(["run", "download", workspace, run_id, "--patterns", "*.log,*.out"])
            assert rc == 0
            application.runs.download.assert_called_once_with(run_id, ("*.log", "*.out"))

    def test_patterns_multi_arg(self):
        with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
            run_id = self._setup_downloadable_run(workspace)
            application = MagicMock()
            application.runs.download.return_value = OperationOutcome.success(DownloadResult(_run_details(run_id), ()))
            with patch("jobdesk_app.cli.create_application", return_value=application):
                rc = main(["run", "download", workspace, run_id, "--patterns", "*.log", "*.out"])
            assert rc == 0
            application.runs.download.assert_called_once_with(run_id, ("*.log", "*.out"))


def test_cli_files_list_closes_ssh_with_sftp():
    """The CLI closes the application owner after a facade file listing."""
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        application = MagicMock()
        application.files.list_remote.return_value = OperationOutcome.success(())
        with patch("jobdesk_app.cli.create_application", return_value=application):
            rc = main(["files", "list-remote", "srv", "/remote/dir"])
        assert rc == 0
        application.files.list_remote.assert_called_once_with("srv", "/remote/dir")
        application.close.assert_called_once_with()


def test_cli_run_submit_rejects_invalid_resource_override_before_submit(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        application = MagicMock()
        with patch("jobdesk_app.cli.create_application", return_value=application):
            rc = main(["run", "submit", workspace, "run1", "--cpus", "0"])

        assert rc == 2
        assert "scheduler cpus must be >= 1" in capsys.readouterr().err
        application.runs.submit_existing.assert_not_called()


def test_cli_confirm_submitted_requires_tasks():
    parser = _build_parser()
    with __import__("pytest").raises(SystemExit):
        parser.parse_args(["run", "confirm-submitted", ".", "run-1"])


def test_cli_confirm_submitted_reports_changed_count(capsys, tmp_path):
    application = MagicMock()
    application.runs.resolve_uncertain.return_value = OperationOutcome.success(_run_details(changed_count=2))
    with patch("jobdesk_app.cli.create_application", return_value=application):
        rc = main(
            [
                "run",
                "confirm-submitted",
                str(tmp_path),
                "run-1",
                "--tasks",
                "a",
                "b",
                "--job-id",
                "a=101",
                "--job-id",
                "b=102",
            ]
        )
    assert rc == 0
    application.runs.resolve_uncertain.assert_called_once_with(
        "run-1", ("a", "b"), action="confirm", remote_job_ids={"a": "101", "b": "102"}
    )
    assert "confirmed 2 task(s)" in capsys.readouterr().out


def test_cli_abandon_submit_returns_error_exit(capsys, tmp_path):
    application = MagicMock()
    application.runs.resolve_uncertain.return_value = OperationOutcome.failure(
        OperationFailure("abandon", "stale", "ValueError: stale", False)
    )
    with patch("jobdesk_app.cli.create_application", return_value=application):
        rc = main(["run", "abandon-submit", str(tmp_path), "run-1", "--tasks", "a"])
    assert rc == 2
    assert "stale" in capsys.readouterr().out


def test_cli_recover_operations_reports_partial_failure(capsys, tmp_path):
    application = MagicMock()
    application.runs.recover.return_value = OperationOutcome.failure(
        OperationFailure("recover", "locked", "OSError: locked", True),
        value=RecoveryResult(3),
    )
    with patch("jobdesk_app.cli.create_application", return_value=application):
        rc = main(["run", "recover", str(tmp_path)])
    assert rc == 2
    output = capsys.readouterr().out
    assert "recovered 3 operation(s)" in output
    assert "locked" in output
    application.runs.recover.assert_called_once_with(include_legacy_imports=True)


@pytest.mark.parametrize(
    "job_ids, message",
    [
        (["a=1", "a=2"], "duplicate"),
        (["unknown=1"], "unknown task"),
        (["a="], "non-empty"),
        (["=1"], "non-empty"),
        (["a"], "task=id"),
    ],
)
def test_cli_confirm_submitted_rejects_invalid_job_ids(capsys, tmp_path, job_ids, message):
    application = MagicMock()
    argv = [
        "run",
        "confirm-submitted",
        str(tmp_path),
        "run-1",
        "--tasks",
        "a",
    ]
    for value in job_ids:
        argv.extend(["--job-id", value])
    with patch("jobdesk_app.cli.create_application", return_value=application):
        rc = main(argv)
    assert rc == 2
    assert message in capsys.readouterr().err.lower()
    application.runs.resolve_uncertain.assert_not_called()
