"""CLI integration tests for the new run + files command groups."""
import os
import tempfile
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from jobdesk_app.cli import _build_parser, main


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


def test_cli_run_create_and_list(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        rc = main([
            "run", "create", workspace,
            "--server", "test_srv",
            "--remote-dir", "/tmp/test",
            "--command", "echo {name}",
            "--files", "/remote/a.gjf", "/remote/b.gjf",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "created run" in out

        rc = main(["run", "list", workspace])
        assert rc == 0
        out = capsys.readouterr().out
        assert "test_srv" in out
        assert "/tmp/test" in out


def test_cli_run_list_empty(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        rc = main(["run", "list", workspace])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No runs" in out


def test_cli_run_retry_no_failed(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        main([
            "run", "create", workspace,
            "--server", "s", "--remote-dir", "/tmp/x",
            "--command", "echo {name}", "--files", "/remote/f.txt",
        ])
        capsys.readouterr()

        from jobdesk_app.services.run_service import RunService
        run_id = RunService(workspace).list_runs()[0].run_id

        rc = main(["run", "retry", workspace, run_id])
        assert rc == 0
        capsys.readouterr()


def test_cli_run_delete(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        main([
            "run", "create", workspace,
            "--server", "s", "--remote-dir", "/tmp/x",
            "--command", "echo {name}", "--files", "/remote/f.txt",
        ])
        capsys.readouterr()

        from jobdesk_app.services.run_service import RunService
        run_id = RunService(workspace).list_runs()[0].run_id

        rc = main(["run", "delete", workspace, run_id])
        assert rc == 0
        assert RunService(workspace).list_runs() == []


def test_cli_run_cancel_invokes_remote_cancellation(capsys):
    with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
        main([
            "run", "create", workspace,
            "--server", "s", "--remote-dir", "/tmp/x",
            "--command", "echo {name}", "--files", "/remote/f.txt",
        ])
        capsys.readouterr()
        from jobdesk_app.services.run_service import RunService

        run_id = RunService(workspace).list_runs()[0].run_id
        ssh = MagicMock()
        with patch("jobdesk_app.cli._get_server_by_id", return_value=MagicMock()), \
             patch("jobdesk_app.cli.create_ssh_client", return_value=ssh), \
             patch.object(RunService, "cancel_run", return_value=(1, [])) as cancel:
            rc = main(["run", "cancel", workspace, run_id])

        assert rc == 0
        cancel.assert_called_once_with(run_id, ssh)
        ssh.connect.assert_called_once_with()
        ssh.close.assert_called_once_with()
        assert "cancelled 1 task(s)" in capsys.readouterr().out


def test_cli_no_longer_registers_jobdesk_owned_workflow_commands():
    parser = _build_parser()
    subcommands = next(
        action.choices
        for action in parser._actions
        if getattr(action, "choices", None)
    )

    assert "workflow" not in subcommands


class TestDownloadPatterns:
    """Test --patterns supports both comma-separated and multi-arg."""

    def _setup_downloadable_run(self, workspace):
        """Create a run with remote_completed task."""
        from jobdesk_app.core.lifecycle import TaskStatus
        from jobdesk_app.core.manifest import Manifest
        main([
            "run", "create", workspace,
            "--server", "srv", "--remote-dir", "/tmp/x",
            "--command", "echo {name}", "--files", "/remote/a.gjf",
        ])
        from jobdesk_app.services.run_service import RunService
        svc = RunService(workspace)
        run_id = svc.list_runs()[0].run_id
        record = svc.load_run(run_id)
        tasks = Manifest.read(record.manifest_path)
        for t in tasks:
            t.status = TaskStatus.remote_completed
        Manifest.write(record.manifest_path, tasks)
        return run_id

    def test_patterns_comma_separated(self):
        with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
            run_id = self._setup_downloadable_run(workspace)
            mock_ssh = MagicMock()
            mock_ssh.connect = MagicMock()
            mock_ssh.close = MagicMock()
            captured = {}

            def fake_download(self, passed_run_id, sftp, patterns):
                captured["run_id"] = passed_run_id
                captured["patterns"] = patterns
                return [], []

            with patch("jobdesk_app.cli._get_server_by_id", return_value=MagicMock()), \
                 patch("jobdesk_app.cli.create_ssh_client", return_value=mock_ssh), \
                 patch("jobdesk_app.cli.create_sftp_client", return_value=MagicMock()), \
                 patch("jobdesk_app.cli.RunService.download_completed", fake_download):
                rc = main(["run", "download", workspace, run_id, "--patterns", "*.log,*.out"])
            assert rc == 0
            assert captured == {"run_id": run_id, "patterns": ["*.log", "*.out"]}

    def test_patterns_multi_arg(self):
        with tempfile.TemporaryDirectory() as workspace, _isolated_appdata(workspace):
            run_id = self._setup_downloadable_run(workspace)
            mock_ssh = MagicMock()
            mock_ssh.connect = MagicMock()
            mock_ssh.close = MagicMock()
            captured = {}

            def fake_download(self, passed_run_id, sftp, patterns):
                captured["run_id"] = passed_run_id
                captured["patterns"] = patterns
                return [], []

            with patch("jobdesk_app.cli._get_server_by_id", return_value=MagicMock()), \
                 patch("jobdesk_app.cli.create_ssh_client", return_value=mock_ssh), \
                 patch("jobdesk_app.cli.create_sftp_client", return_value=MagicMock()), \
                 patch("jobdesk_app.cli.RunService.download_completed", fake_download):
                rc = main(["run", "download", workspace, run_id, "--patterns", "*.log", "*.out"])
            assert rc == 0
            assert captured == {"run_id": run_id, "patterns": ["*.log", "*.out"]}
