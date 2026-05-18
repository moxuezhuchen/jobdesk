"""CLI integration tests for the new run + files command groups."""
import tempfile
from pathlib import Path

from jobdesk_app.cli import main


def test_cli_run_create_and_list(capsys):
    with tempfile.TemporaryDirectory() as workspace:
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
    with tempfile.TemporaryDirectory() as workspace:
        rc = main(["run", "list", workspace])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No runs" in out


def test_cli_run_retry_no_failed(capsys):
    with tempfile.TemporaryDirectory() as workspace:
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
        out = capsys.readouterr().out
        assert "No failed" in out


def test_cli_run_delete(capsys):
    with tempfile.TemporaryDirectory() as workspace:
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
