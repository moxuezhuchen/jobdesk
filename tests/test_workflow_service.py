"""M8.5C 测试: services/workflow_service.py — WorkflowService facade (新 schema)。"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from jobdesk_app.services.project_service import create_project_context
from jobdesk_app.services.workflow_service import WorkflowService
from jobdesk_app.core.manifest import TaskRecord, Manifest
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.models import FailureRecord
from jobdesk_app.remote.ssh import SSHResult
from jobdesk_app.remote.status import RemoteTaskStatusSnapshot
from jobdesk_app.config.runtime import ResolvedExecutionContext
from jobdesk_app.config.schema import ServerConfig


def _make_ctx(base: Path) -> callable:
    def _create(**overrides):
        proj_dir = base / "proj"
        (proj_dir / "inputs").mkdir(parents=True)
        yaml_text = """project_id: test
project:
  name: test
local_paths:
  input_dir: ./inputs
  result_dir: ./results
task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*.gjf"
execution_profiles:
  default:
    label: Default
    command: "echo {input_name}"
submit:
  shell: bash
extract:
  results: []
"""
        (proj_dir / "project.yaml").write_text(yaml_text, encoding="utf-8")
        (base / "servers.yaml").write_text("""
servers:
  s:
    host: h
    username: u
    auth_method: key
""", encoding="utf-8")
        return create_project_context(proj_dir)
    return _create


def _make_resolved_contexts(remote_work_dir="/remote/work", max_parallel=4) -> dict:
    return {
        "default": ResolvedExecutionContext(
            project_id="test",
            execution_profile_name="default",
            server_id="s",
            server_config=ServerConfig(server_id="s", host="h", username="u"),
            remote_work_dir=remote_work_dir,
            command_template="echo {input_name}",
            max_parallel=max_parallel,
        )
    }


class TestWorkflowService:
    def test_scan_and_create(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx_factory = _make_ctx(base)
            ctx = ctx_factory()
            (ctx.local_input_dir / "t1.gjf").write_text("", encoding="utf-8")

            svc = WorkflowService(ctx)
            packages = svc.scan_inputs()
            assert len(packages) == 1

            result = svc.create_batch(packages, _make_resolved_contexts())
            assert result.batch_meta.task_count == 1
            assert result.manifest_path.exists()
            assert result.tasks[0].execution_profile == "default"

    def test_preflight_project_returns_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx_factory = _make_ctx(base)
            ctx = ctx_factory()
            (ctx.local_input_dir / "t1.gjf").write_text("", encoding="utf-8")

            svc = WorkflowService(ctx)
            report = svc.preflight()

            assert report.task_count == 1
            assert any(issue.code == "missing_binding" for issue in report.errors)

    def test_analyze_batch_fixture(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            (proj_dir / "project.yaml").write_text(r"""project_id: test
project:
  name: test
local_paths:
  input_dir: ./inputs
  result_dir: ./results
task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*.gjf"
execution_profiles:
  default:
    label: D
    command: "echo {input_name}"
submit:
  shell: bash
extract:
  results:
    - name: energy
      source_glob: "output.log"
      regex: 'E=\s*(?P<value>-?[\d.]+)'
      strategy: last
      type: float
""", encoding="utf-8")
            (base / "servers.yaml").write_text("""
servers:
  s:
    host: h
    username: u
    auth_method: key
""", encoding="utf-8")
            ctx = create_project_context(proj_dir)

            batch_id = "20260511_120000_000001"
            results_dir = ctx.local_result_dir / batch_id / "t1"
            results_dir.mkdir(parents=True)
            (results_dir / "output.log").write_text("E= -150.5\n", encoding="utf-8")

            tasks = [
                TaskRecord(
                    task_id="t1", batch_id=batch_id,
                    task_files=["in/t1.gjf"], remote_job_dir="/r/b/t1",
                    remote_task_files=["t1.gjf"], rendered_command="cmd",
                    execution_profile="default",
                    status=TaskStatus.downloaded,
                )
            ]

            svc = WorkflowService(ctx)
            results, failures, summaries = svc.analyze_batch(tasks, batch_id)

            assert len(results) == 1
            assert len(failures) == 0
            assert len(summaries) == 1
            assert abs(results[0].value - (-150.5)) < 1e-8

            out_dir = ctx.local_result_dir / batch_id
            assert (out_dir / "final_results.tsv").exists()
            assert (out_dir / "summary.json").exists()

    def test_refresh_batch_calls_status_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx_factory = _make_ctx(base)
            ctx = ctx_factory()

            tasks = [TaskRecord(
                task_id="t1", batch_id="b1",
                task_files=["in/t1.gjf"], remote_job_dir="/r/b1/t1",
                remote_task_files=["t1.gjf"], rendered_command="cmd",
                execution_profile="default",
                status=TaskStatus.running,
            )]
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))
            ssh_factory = lambda sc: mock_ssh

            svc = WorkflowService(ctx)
            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_status",
                return_value=RemoteTaskStatusSnapshot("t1", "/r/b1/t1", "completed", 0, "", True, True, False),
            ):
                result, failures = svc.refresh_batch(mp, "b1", ssh_factory, _make_resolved_contexts(), write=False)
                assert len(result) == 1
                assert result[0].task_count == 1

    def test_download_completed_only_remote_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            proj_dir = base / "proj"
            (proj_dir / "inputs").mkdir(parents=True)
            (proj_dir / "project.yaml").write_text("""project_id: test
project:
  name: test
local_paths:
  input_dir: ./inputs
  result_dir: ./results
task_discoveries:
  - name: default
    mode: flat_single
    entry_glob: "*.gjf"
execution_profiles:
  default:
    label: D
    command: "echo ok"
download:
  patterns:
    - "*.log"
    - "*.out"
submit:
  shell: bash
""", encoding="utf-8")
            (base / "servers.yaml").write_text("""
servers:
  s:
    host: h
    username: u
    auth_method: key
""", encoding="utf-8")
            ctx = create_project_context(proj_dir)

            tasks = [
                TaskRecord(task_id="t1", batch_id="b1", task_files=["in/t1.gjf"],
                           remote_job_dir="/r/t1", remote_task_files=["t1.gjf"],
                           execution_profile="default",
                           rendered_command="cmd", status=TaskStatus.remote_completed),
                TaskRecord(task_id="t2", batch_id="b1", task_files=["in/t2.gjf"],
                           remote_job_dir="/r/t2", remote_task_files=["t2.gjf"],
                           execution_profile="default",
                           rendered_command="cmd", status=TaskStatus.running),
                TaskRecord(task_id="t3", batch_id="b1", task_files=["in/t3.gjf"],
                           remote_job_dir="/r/t3", remote_task_files=["t3.gjf"],
                           execution_profile="default",
                           rendered_command="cmd", status=TaskStatus.local_ready),
            ]

            mock_sftp = MagicMock()
            from jobdesk_app.core.transfer import TransferRecord, TransferDirection, TransferStatus as Ts
            def fake_download(remote_path, local_path, **kw):
                rec = TransferRecord(
                    direction=TransferDirection.download,
                    local_path=str(local_path),
                    remote_path=remote_path,
                    status=Ts.transferred if not kw.get("dry_run") else Ts.planned,
                    dry_run=kw.get("dry_run", False),
                )
                return rec
            mock_sftp.download_file = MagicMock(side_effect=fake_download)
            sftp_factory = lambda sc: mock_sftp

            svc = WorkflowService(ctx)
            records, failures = svc.download_completed(tasks, sftp_factory, dry_run=True)
            assert len(records) == 2
            for r in records:
                assert "/r/t1" in r.remote_path

    def test_dry_run_does_not_overwrite_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx_factory = _make_ctx(base)
            ctx = ctx_factory()
            (ctx.local_input_dir / "t1.gjf").write_text("", encoding="utf-8")

            svc = WorkflowService(ctx)
            packages = svc.scan_inputs()
            result = svc.create_batch(packages, _make_resolved_contexts())

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))
            ssh_factory = lambda sc: mock_ssh
            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_status",
                return_value=RemoteTaskStatusSnapshot("t1", "/r/t1", "completed", 0, "", True, True, False),
            ):
                svc.refresh_batch(result.manifest_path, result.batch_meta.batch_id,
                                  ssh_factory, _make_resolved_contexts(), write=False)

            reloaded = Manifest.read(result.manifest_path)
            assert reloaded[0].status == TaskStatus.local_ready

    def test_mixed_profile_batch_fail_fast(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            ctx_factory = _make_ctx(base)
            ctx = ctx_factory()

            tasks = [
                TaskRecord(task_id="t1", batch_id="b1",
                           remote_job_dir="/r/t1", execution_profile="g16",
                           task_files=["in/t1.gjf"], remote_task_files=["t1.gjf"],
                           rendered_command="cmd", status=TaskStatus.uploaded),
                TaskRecord(task_id="t2", batch_id="b1",
                           remote_job_dir="/r/t2", execution_profile="orca",
                           task_files=["in/t2.gjf"], remote_task_files=["t2.gjf"],
                           rendered_command="cmd", status=TaskStatus.uploaded),
            ]
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            svc = WorkflowService(ctx)
            with pytest.raises(ValueError, match="g16"):
                svc.submit_batch(mp, "b1", lambda sc: MagicMock(), lambda sc: MagicMock(), _make_resolved_contexts("/r", 2))
