"""CLI integration tests for the new run + files command groups."""
import re
import tempfile
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from jobdesk_app.cli import main
from jobdesk_app.remote.ssh import SSHResult


def _patch_runs_dir(tmp):
    """Patch RunService to use tmp as runs_dir."""
    original_init = None
    from jobdesk_app.services.run_service import RunService
    original_init = RunService.__init__

    def _patched_init(self, workspace_dir=None):
        original_init(self, workspace_dir)
        self.runs_dir = Path(tmp) / "JobDesk" / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    return patch.object(RunService, "__init__", _patched_init)


def test_cli_run_create_and_list(capsys):
    with tempfile.TemporaryDirectory() as workspace, _patch_runs_dir(workspace):
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
    with tempfile.TemporaryDirectory() as workspace, _patch_runs_dir(workspace):
        rc = main(["run", "list", workspace])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No runs" in out


def test_cli_run_retry_no_failed(capsys):
    with tempfile.TemporaryDirectory() as workspace, _patch_runs_dir(workspace):
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


def test_cli_run_delete(capsys):
    with tempfile.TemporaryDirectory() as workspace, _patch_runs_dir(workspace):
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


# ---- Workflow CLI tests (mocked SSH/SFTP/server) ----


def _mock_server():
    """Minimal ServerConfig-like object for CLI workflow tests."""
    server = MagicMock()
    server.host = "127.0.0.1"
    server.port = 22
    server.username = "user"
    server.env_init_scripts = []
    server.scheduler = MagicMock(
        type="nohup",
        default_cpus=1,
        default_memory_mb=2048,
        default_walltime_minutes=60,
        default_partition="",
        default_account="",
        default_gpus=0,
        extra_directives=[],
    )
    return server


def _workflow_patches(tmp, server):
    """Context managers to mock SSH/SFTP/server loading for workflow CLI tests."""
    mock_ssh = MagicMock()
    mock_ssh.connect = MagicMock()
    mock_ssh.close = MagicMock()
    mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "", "", 0.01))

    mock_sftp = MagicMock()
    mock_sftp.upload_file = MagicMock()
    mock_sftp.mkdir_p = MagicMock()
    mock_sftp.close = MagicMock()

    patches = [
        _patch_runs_dir(tmp),
        patch("jobdesk_app.cli.load_servers", return_value=MagicMock(servers={"srv": server})),
        patch("jobdesk_app.cli.create_ssh_client", return_value=mock_ssh),
        patch("jobdesk_app.cli.create_sftp_client", return_value=mock_sftp),
    ]
    return patches, mock_ssh, mock_sftp


class TestWorkflowCLI:
    def test_workflow_run_success(self, capsys):
        server = _mock_server()
        with tempfile.TemporaryDirectory() as workspace:
            patches, _, _ = _workflow_patches(workspace, server)
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                rc = main([
                    "workflow", "run", workspace, "opt_freq",
                    "--server", "srv",
                    "--remote-dir", "/scratch/test",
                    "--files", "/remote/mol.gjf",
                ])
            assert rc == 0
            out = capsys.readouterr().out
            assert "Started workflow" in out
            assert "opt" in out

    def test_workflow_status_not_found(self, capsys):
        with tempfile.TemporaryDirectory() as workspace, _patch_runs_dir(workspace):
            rc = main(["workflow", "status", workspace, "nonexistent_id"])
            assert rc == 2
            out = capsys.readouterr().out
            assert "not found" in out.lower()

    def test_workflow_status_shows_steps(self, capsys):
        server = _mock_server()
        with tempfile.TemporaryDirectory() as workspace:
            patches, _, _ = _workflow_patches(workspace, server)
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                main([
                    "workflow", "run", workspace, "opt_freq",
                    "--server", "srv", "--remote-dir", "/scratch/t",
                    "--files", "/remote/a.gjf",
                ])
                out = capsys.readouterr().out

                m = re.search(r"Started workflow (\S+)", out)
                wf_id = m.group(1)

                rc = main(["workflow", "status", workspace, wf_id])
            assert rc == 0
            out = capsys.readouterr().out
            assert "opt" in out
            assert "running" in out

    def test_workflow_status_hints_when_run_downloaded(self, capsys):
        server = _mock_server()
        with tempfile.TemporaryDirectory() as workspace:
            patches, _, _ = _workflow_patches(workspace, server)
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                main([
                    "workflow", "run", workspace, "opt_freq",
                    "--server", "srv", "--remote-dir", "/scratch/t",
                    "--files", "/remote/a.gjf",
                ])
                out = capsys.readouterr().out
                wf_id = re.search(r"Started workflow (\S+)", out).group(1)

                from jobdesk_app.core.lifecycle import TaskStatus
                from jobdesk_app.core.manifest import Manifest
                from jobdesk_app.services.run_service import RunService
                from jobdesk_app.services.workflow_service import WorkflowRun

                wf_run = WorkflowRun.load(Path(workspace), wf_id)
                run_id = wf_run.step_run_ids["opt"]
                svc = RunService(workspace)
                record = svc.load_run(run_id)
                tasks = Manifest.read(record.manifest_path)
                for task in tasks:
                    task.status = TaskStatus.downloaded
                Manifest.write(record.manifest_path, tasks)
                svc.update_run_from_manifest(run_id)

                rc = main(["workflow", "status", workspace, wf_id])

            assert rc == 0
            out = capsys.readouterr().out
            assert "Hint: run 'jobdesk workflow advance" in out

    def test_workflow_advance_upload_failure_returns_2(self, capsys):
        server = _mock_server()
        with tempfile.TemporaryDirectory() as workspace:
            patches, _, mock_sftp = _workflow_patches(workspace, server)
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                # Create workflow and start opt
                main([
                    "workflow", "run", workspace, "opt_freq",
                    "--server", "srv", "--remote-dir", "/scratch/t",
                    "--files", "/remote/mol.gjf",
                ])
                out = capsys.readouterr().out

                wf_id = re.search(r"Started workflow (\S+)", out).group(1)

                # Simulate opt completed with valid geometry
                from jobdesk_app.services.workflow_service import WorkflowRun
                wf_run = WorkflowRun.load(Path(workspace), wf_id)
                run_id = wf_run.step_run_ids["opt"]
                results_dir = Path(workspace) / "results" / run_id / "mol"
                results_dir.mkdir(parents=True)
                (results_dir / "mol.log").write_text(
                    " Standard orientation:\n"
                    " ---------------------------------------------------------------------\n"
                    " Center     Atomic      Atomic             Coordinates (Angstroms)\n"
                    " Number     Number       Type             X           Y           Z\n"
                    " ---------------------------------------------------------------------\n"
                    "      1          6           0        0.000000    0.000000    0.000000\n"
                    "      2          8           0        0.000000    0.000000    1.200000\n"
                    " ---------------------------------------------------------------------\n"
                    " Normal termination of Gaussian 16.\n",
                    encoding="utf-8",
                )
                # Mark opt completed in workflow and underlying run
                wf_run.step_status["opt"] = "completed"
                wf_run.save()
                from jobdesk_app.core.lifecycle import TaskStatus
                from jobdesk_app.core.manifest import Manifest
                from jobdesk_app.services.run_service import RunService

                svc = RunService(workspace)
                record = svc.load_run(run_id)
                tasks = Manifest.read(record.manifest_path)
                for t in tasks:
                    t.status = TaskStatus.downloaded
                Manifest.write(record.manifest_path, tasks)

                # Make upload fail
                mock_sftp.upload_file.side_effect = OSError("connection reset")

                rc = main(["workflow", "advance", workspace, wf_id])

            assert rc == 2
            out = capsys.readouterr().out
            assert "ERROR" in out



    def test_workflow_status_shows_events(self, capsys):
        server = _mock_server()
        with tempfile.TemporaryDirectory() as workspace:
            patches, _, _ = _workflow_patches(workspace, server)
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                main([
                    "workflow", "run", workspace, "opt_freq",
                    "--server", "srv", "--remote-dir", "/scratch/t",
                    "--files", "/remote/a.gjf",
                ])
                out = capsys.readouterr().out

                wf_id = re.search(r"Started workflow (\S+)", out).group(1)

                rc = main(["workflow", "status", workspace, wf_id])
            assert rc == 0
            out = capsys.readouterr().out
            assert "Recent events:" in out
            assert "[workflow_started]" in out
            assert "[step_started]" in out

    def test_workflow_advance_upload_failure_records_event(self, capsys):
        from jobdesk_app.services.workflow_service import WorkflowRun, read_events

        server = _mock_server()
        with tempfile.TemporaryDirectory() as workspace:
            patches, _, mock_sftp = _workflow_patches(workspace, server)
            with ExitStack() as stack:
                for p in patches:
                    stack.enter_context(p)
                main([
                    "workflow", "run", workspace, "opt_freq",
                    "--server", "srv", "--remote-dir", "/scratch/t",
                    "--files", "/remote/mol.gjf",
                ])
                out = capsys.readouterr().out

                wf_id = re.search(r"Started workflow (\S+)", out).group(1)

                wf_run = WorkflowRun.load(Path(workspace), wf_id)
                run_id = wf_run.step_run_ids["opt"]
                results_dir = Path(workspace) / "results" / run_id / "mol"
                results_dir.mkdir(parents=True)
                (results_dir / "mol.log").write_text(
                    " Standard orientation:\n"
                    " ---------------------------------------------------------------------\n"
                    " Center     Atomic      Atomic             Coordinates (Angstroms)\n"
                    " Number     Number       Type             X           Y           Z\n"
                    " ---------------------------------------------------------------------\n"
                    "      1          6           0        0.000000    0.000000    0.000000\n"
                    "      2          8           0        0.000000    0.000000    1.200000\n"
                    " ---------------------------------------------------------------------\n"
                    " Normal termination of Gaussian 16.\n",
                    encoding="utf-8",
                )
                wf_run.step_status["opt"] = "completed"
                wf_run.save()
                from jobdesk_app.services.run_service import RunService
                from jobdesk_app.core.manifest import Manifest
                from jobdesk_app.core.lifecycle import TaskStatus
                svc = RunService(workspace)
                record = svc.load_run(run_id)
                tasks = Manifest.read(record.manifest_path)
                for t in tasks:
                    t.status = TaskStatus.downloaded
                Manifest.write(record.manifest_path, tasks)

                mock_sftp.upload_file.side_effect = OSError("disk full")
                main(["workflow", "advance", workspace, wf_id])
                capsys.readouterr()

            # Verify upload_failed event was recorded
            events = read_events(Path(workspace), wf_id)
            upload_event = next(e for e in events if e["event_type"] == "upload_failed")
            assert upload_event["step_name"] == "freq"


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
        with tempfile.TemporaryDirectory() as workspace, _patch_runs_dir(workspace):
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
        with tempfile.TemporaryDirectory() as workspace, _patch_runs_dir(workspace):
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
