"""M8.6C 测试: lifecycle hardening - batch recovery, refresh, download, failures."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from jobdesk_app.services.project_service import create_project_context
from jobdesk_app.services.workflow_service import WorkflowService
from jobdesk_app.services.batch_service import discover_task_packages, create_batch, list_batches, load_batch, load_latest_batch
from jobdesk_app.core.models import BatchMeta, BatchSummary, FailureRecord, SharedFileRecord
from jobdesk_app.core.manifest import TaskRecord, Manifest
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.core.transfer import TransferRecord, TransferDirection, TransferStatus
from jobdesk_app.remote.ssh import SSHResult
from jobdesk_app.remote.status import RemoteTaskStatusSnapshot
from jobdesk_app.config.runtime import ResolvedExecutionContext
from jobdesk_app.config.schema import ServerConfig


def _make_mixed_project(base: Path):
    proj_dir = base / "proj"
    (proj_dir / "inputs" / "g16").mkdir(parents=True)
    (proj_dir / "inputs" / "orca").mkdir(parents=True)
    (proj_dir / "inputs" / "g16" / "a.gjf").write_text("")
    (proj_dir / "inputs" / "orca" / "b.inp").write_text("")
    yaml_data = {
        "project_id": "test-lifecycle",
        "project": {"name": "lifecycle_test"},
        "local_paths": {"input_dir": "./inputs"},
        "task_discoveries": [
            {"name": "g16_jobs", "mode": "flat_single", "entry_glob": "g16/*.gjf", "execution_profile": "g16"},
            {"name": "orca_jobs", "mode": "flat_single", "entry_glob": "orca/*.inp", "execution_profile": "orca"},
        ],
        "execution_profiles": {
            "g16": {"label": "G16", "command": "g16 {input_name}"},
            "orca": {"label": "ORCA", "command": "orca {input_name}"},
        },
        "submit": {"shell": "bash"},
        "download": {"patterns": ["*.log"]},
    }
    (proj_dir / "project.yaml").write_text(yaml.safe_dump(yaml_data), encoding="utf-8")
    (base / "servers.yaml").write_text("""
servers:
  srv1: {host: 10.0.0.1, username: root, auth_method: key}
  srv2: {host: 10.0.0.2, username: root, auth_method: key}
""", encoding="utf-8")
    return proj_dir


def _make_rctx():
    return {
        "g16": ResolvedExecutionContext(
            project_id="test-lifecycle", execution_profile_name="g16",
            server_id="srv1", server_config=ServerConfig(server_id="srv1", host="10.0.0.1", username="root"),
            remote_work_dir="/remote/g16", command_template="g16 {input_name}", max_parallel=4,
        ),
        "orca": ResolvedExecutionContext(
            project_id="test-lifecycle", execution_profile_name="orca",
            server_id="srv2", server_config=ServerConfig(server_id="srv2", host="10.0.0.2", username="root"),
            remote_work_dir="/remote/orca", command_template="orca {input_name}", max_parallel=2,
        ),
    }


class TestBatchRecovery:
    def test_list_batches_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            summaries = list_batches(ctx)
            assert summaries == []

    def test_list_batches_finds_created_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            summaries = list_batches(ctx)
            assert len(summaries) == 1
            assert summaries[0].batch_id == result.batch_meta.batch_id
            assert summaries[0].task_count == 2
            assert set(summaries[0].execution_profiles) == {"g16", "orca"}
            assert set(summaries[0].server_ids) == {"srv1", "srv2"}

    def test_list_batches_sorts_latest_first(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            r1 = create_batch(ctx, pkgs, _make_rctx())
            r2 = create_batch(ctx, pkgs, _make_rctx())
            summaries = list_batches(ctx)
            assert len(summaries) == 2
            assert summaries[0].batch_id == r2.batch_meta.batch_id

    def test_load_batch_restores_all(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            bid = result.batch_meta.batch_id

            loaded = load_batch(ctx, bid)
            assert loaded is not None
            assert loaded.batch_meta.batch_id == bid
            assert len(loaded.tasks) == 2
            assert loaded.tasks[0].task_id is not None

    def test_load_latest_batch_restores_newest_batch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            first = create_batch(ctx, pkgs, _make_rctx())
            second = create_batch(ctx, pkgs, _make_rctx())

            loaded = load_latest_batch(ctx)

            assert loaded is not None
            assert loaded.batch_meta.batch_id == second.batch_meta.batch_id
            assert loaded.batch_meta.batch_id != first.batch_meta.batch_id

    def test_load_batch_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            assert load_batch(ctx, "nonexistent") is None

    def test_load_batch_corrupt_batch_json_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            batch_dir = ctx.batches_dir / "b1"
            batch_dir.mkdir(parents=True)
            (batch_dir / "batch.json").write_text("{broken", encoding="utf-8")
            Manifest.write(batch_dir / "manifest.tsv", [])

            with pytest.raises(ValueError) as exc:
                load_batch(ctx, "b1")

            message = str(exc.value)
            assert "b1" in message
            assert "batch.json" in message

    def test_load_batch_corrupt_manifest_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            batch_dir = ctx.batches_dir / "b1"
            batch_dir.mkdir(parents=True)
            (batch_dir / "batch.json").write_text(
                '{"batch_id":"b1","project_name":"test-lifecycle","max_parallel":1,'
                '"remote_batch_dir":"","task_count":1}',
                encoding="utf-8",
            )
            (batch_dir / "manifest.tsv").write_text(
                "\t".join(["task_id", "batch_id", "remote_job_dir", "task_files", "status"]) + "\n"
                + "t1\tb1\t/r/t1\t[broken\tlocal_ready\n",
                encoding="utf-8",
            )

            with pytest.raises(ValueError) as exc:
                load_batch(ctx, "b1")

            message = str(exc.value)
            assert "b1" in message
            assert "manifest.tsv" in message


class TestFrozenExecutionPlan:
    def test_create_batch_freezes_max_parallel_per_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)

            result = create_batch(ctx, pkgs, _make_rctx())

            by_profile = {t.execution_profile: t for t in result.tasks}
            assert by_profile["g16"].max_parallel == 4
            assert by_profile["orca"].max_parallel == 2

    def test_submit_uses_manifest_frozen_max_parallel_not_runtime_binding(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            tasks = Manifest.read(result.manifest_path)
            for t in tasks:
                t.status = TaskStatus.uploaded
            Manifest.write(result.manifest_path, tasks)

            seen = []

            def submitter_factory(**kwargs):
                seen.append(kwargs)
                submitter = MagicMock()
                submitter.submit_batch.return_value = MagicMock(errors=[], submitted_task_count=1)
                return submitter

            svc = WorkflowService(ctx)
            svc.submit_batch(
                result.manifest_path, result.batch_meta.batch_id,
                ssh_factory=lambda sc: MagicMock(),
                sftp_factory=lambda sc: MagicMock(),
                submitter_factory=submitter_factory,
            )

            max_by_control = {kw["control_subdir"]: kw["max_parallel"] for kw in seen}
            assert max_by_control["_batch/g16"] == 4
            assert max_by_control["_batch/orca"] == 2

    def test_submit_updates_original_manifest_from_group_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            tasks = Manifest.read(result.manifest_path)
            for t in tasks:
                t.status = TaskStatus.uploaded
            Manifest.write(result.manifest_path, tasks)

            def submitter_factory(**kwargs):
                group_tasks = Manifest.read(kwargs["manifest_path"])
                submitter = MagicMock()
                submitter.submit_batch.return_value = SubmitResult(
                    batch_id=result.batch_meta.batch_id,
                    submitted_task_count=len(group_tasks),
                    remote_batch_dir=kwargs["remote_batch_dir"],
                    updated_task_ids=[t.task_id for t in group_tasks],
                )
                return submitter

            svc = WorkflowService(ctx)
            svc.submit_batch(
                result.manifest_path, result.batch_meta.batch_id,
                ssh_factory=lambda sc: MagicMock(close=MagicMock()),
                sftp_factory=lambda sc: MagicMock(close=MagicMock()),
                submitter_factory=submitter_factory,
            )

            reloaded = Manifest.read(result.manifest_path)
            assert {t.status for t in reloaded} == {TaskStatus.submitted}
            assert all(t.submitted_at is not None for t in reloaded)

    def test_submit_errors_are_written_to_failures_tsv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            tasks = Manifest.read(result.manifest_path)
            for t in tasks:
                t.status = TaskStatus.uploaded
            Manifest.write(result.manifest_path, tasks)

            def submitter_factory(**kwargs):
                submitter = MagicMock()
                submitter.submit_batch.return_value = SubmitResult(
                    batch_id=result.batch_meta.batch_id,
                    submitted_task_count=0,
                    remote_batch_dir=kwargs["remote_batch_dir"],
                    errors=["nohup failed"],
                )
                return submitter

            svc = WorkflowService(ctx)
            svc.submit_batch(
                result.manifest_path, result.batch_meta.batch_id,
                ssh_factory=lambda sc: MagicMock(close=MagicMock()),
                sftp_factory=lambda sc: MagicMock(close=MagicMock()),
                submitter_factory=submitter_factory,
            )

            content = (result.batch_dir / "failures.tsv").read_text(encoding="utf-8")
            assert "\tsubmit\tnohup failed" in content
            assert "server_id" in content.splitlines()[0]

    def test_repeated_submit_without_uploaded_tasks_records_noop_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            tasks = Manifest.read(result.manifest_path)
            for t in tasks:
                t.status = TaskStatus.submitted
            Manifest.write(result.manifest_path, tasks)

            svc = WorkflowService(ctx)
            submit_results = svc.submit_batch(
                result.manifest_path, result.batch_meta.batch_id,
                ssh_factory=lambda sc: MagicMock(),
                sftp_factory=lambda sc: MagicMock(),
            )

            assert submit_results
            assert submit_results[0].submitted_task_count == 0
            assert submit_results[0].errors
            content = (result.batch_dir / "failures.tsv").read_text(encoding="utf-8")
            assert "\tsubmit\t" in content
            assert "no uploaded tasks" in content


class TestRefreshLifecycle:
    def test_refresh_recovers_status_per_server(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            bid = result.batch_meta.batch_id
            mp = result.manifest_path

            # mark tasks as submitted
            tasks = Manifest.read(mp)
            for t in tasks:
                t.status = TaskStatus.submitted
            Manifest.write(mp, tasks)

            mock_ssh = MagicMock()
            mock_ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))

            svc = WorkflowService(ctx)
            with patch(
                "jobdesk_app.remote.status_refresh.read_remote_task_status",
                return_value=RemoteTaskStatusSnapshot("t", "/r/t", "completed", 0, "", True, True, False),
            ):
                refresh_results, failures = svc.refresh_batch(
                    mp, bid,
                    ssh_factory=lambda sc: mock_ssh,
                    resolved_contexts=_make_rctx(),
                    write=True,
                )
            assert len(refresh_results) > 0
            assert len(failures) == 0

            # re-read manifest: status should be updated
            tasks2 = Manifest.read(mp)
            statuses = {t.status for t in tasks2}
            assert TaskStatus.remote_completed in statuses

    def test_refresh_server_unreachable_does_not_corrupt(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            bid = result.batch_meta.batch_id
            mp = result.manifest_path

            svc = WorkflowService(ctx)
            # ssh_factory that raises on srv1 but works on srv2
            call_count = [0]
            def broken_factory(sc):
                call_count[0] += 1
                if call_count[0] <= 1:  # srv1 (g16) fails
                    raise ConnectionError("srv1 unreachable")
                ssh = MagicMock()
                ssh.run = MagicMock(return_value=SSHResult("", 0, "__NOT_FOUND__", "", 0.01))
                return ssh

            refresh_results, failures = svc.refresh_batch(
                mp, bid,
                ssh_factory=broken_factory,
                resolved_contexts=_make_rctx(),
                write=True,
            )
            # should have at least one failure for the unreachable server
            assert len(failures) > 0
            assert any("srv1" in f.reason or "srv1" in (f.server_id or "") for f in failures)
            # manifest should still be valid
            tasks = Manifest.read(mp)
            assert len(tasks) == 2

    def test_refresh_groups_same_server_by_remote_work_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            tasks = [
                TaskRecord(task_id="t1", batch_id="b1", execution_profile="g16",
                           server_id="srv1", remote_work_dir="/r/g16",
                           remote_job_dir="/r/g16/b1/t1", max_parallel=4,
                           status=TaskStatus.submitted),
                TaskRecord(task_id="t2", batch_id="b1", execution_profile="orca",
                           server_id="srv1", remote_work_dir="/r/orca",
                           remote_job_dir="/r/orca/b1/t2", max_parallel=2,
                           status=TaskStatus.submitted),
            ]
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)
            remote_batches = []

            def fake_refresh(**kwargs):
                remote_batches.append((kwargs["remote_batch_dir"], kwargs["control_subdir"]))
                return MagicMock(batch_id="b1", task_count=1, changed_count=0,
                                 snapshots=[], failures=[],
                                 batch_control=MagicMock(warnings=[]), warnings=[])

            svc = WorkflowService(ctx)
            svc.refresh_batch(
                mp, "b1",
                ssh_factory=lambda sc: MagicMock(close=MagicMock()),
                refresh_func=fake_refresh,
            )

            assert ("/r/g16/b1", "_batch/g16") in remote_batches
            assert ("/r/orca/b1", "_batch/orca") in remote_batches


class TestDownloadLifecycle:
    def test_download_isolation_per_task(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            bid = result.batch_meta.batch_id

            tasks = Manifest.read(result.manifest_path)
            tasks[0].status = TaskStatus.remote_completed
            tasks[1].status = TaskStatus.running  # not ready
            Manifest.write(result.manifest_path, tasks)

            mock_sftp = MagicMock()
            def fake_download(remote_path, local_path, **kw):
                r = TransferRecord(direction=TransferDirection.download,
                                  local_path=str(local_path), remote_path=remote_path,
                                  status=TransferStatus.transferred, dry_run=kw.get("dry_run", False))
                return r
            mock_sftp.download_file = MagicMock(side_effect=fake_download)

            svc = WorkflowService(ctx)
            records, failures = svc.download_completed(
                tasks, sftp_factory=lambda sid: mock_sftp, dry_run=True)
            # only 1 task is remote_completed → 1 record per pattern (1 pattern "*.log")
            assert len(records) == 1
            assert len(failures) == 0


    def test_download_updates_manifest_only_for_successful_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            tasks = [
                TaskRecord(task_id="ok", batch_id="b1", server_id="srv1",
                           execution_profile="g16", remote_job_dir="/r/ok",
                           remote_work_dir="/r", status=TaskStatus.remote_completed),
                TaskRecord(task_id="bad", batch_id="b1", server_id="srv1",
                           execution_profile="g16", remote_job_dir="/r/bad",
                           remote_work_dir="/r", status=TaskStatus.remote_completed),
            ]
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            def fake_download(remote_path, local_path, **kw):
                status = TransferStatus.transferred if "/ok/" in remote_path else TransferStatus.failed
                return TransferRecord(direction=TransferDirection.download,
                                      local_path=str(local_path), remote_path=remote_path,
                                      status=status, reason="x")

            mock_sftp = MagicMock()
            mock_sftp.download_file = MagicMock(side_effect=fake_download)

            svc = WorkflowService(ctx)
            records, failures = svc.download_completed(
                Manifest.read(mp), sftp_factory=lambda sid: mock_sftp,
                manifest_path=mp,
            )

            reloaded = {t.task_id: t for t in Manifest.read(mp)}
            assert reloaded["ok"].status == TaskStatus.downloaded
            assert reloaded["ok"].downloaded_at is not None
            assert reloaded["bad"].status == TaskStatus.remote_completed
            assert len(records) == 2
            assert failures

    def test_download_failures_are_appended_to_existing_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            tasks = [
                TaskRecord(task_id="bad", batch_id="b1", server_id="srv1",
                           execution_profile="g16", remote_job_dir="/r/bad",
                           remote_work_dir="/r", status=TaskStatus.remote_completed),
            ]
            batch_dir = Path(tmpdir) / "batch"
            batch_dir.mkdir()
            mp = batch_dir / "manifest.tsv"
            Manifest.write(mp, tasks)
            from jobdesk_app.core.outputs import write_failures_tsv
            write_failures_tsv(
                [FailureRecord(task_id="old", batch_id="b1", stage="upload", reason="old failure")],
                batch_dir / "failures.tsv",
            )

            mock_sftp = MagicMock()
            mock_sftp.download_file = MagicMock(return_value=TransferRecord(
                direction=TransferDirection.download,
                local_path="x", remote_path="y",
                status=TransferStatus.failed, reason="new failure",
            ))

            svc = WorkflowService(ctx)
            svc.download_completed(
                Manifest.read(mp), sftp_factory=lambda sid: mock_sftp,
                manifest_path=mp,
            )

            content = (batch_dir / "failures.tsv").read_text(encoding="utf-8")
            assert content.count("batch_id\ttask_id\tstage") == 1
            assert "\told\tupload\told failure" in content
            assert "\tbad\tdownload\tnew failure" in content


class TestUploadLifecycle:
    def test_upload_updates_manifest_only_for_successful_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            good = Path(tmpdir) / "good.gjf"
            bad = Path(tmpdir) / "bad.gjf"
            good.write_text("")
            bad.write_text("")
            tasks = [
                TaskRecord(task_id="ok", batch_id="b1", server_id="srv1",
                           execution_profile="g16", remote_job_dir="/r/ok",
                           remote_work_dir="/r", task_files=[str(good)],
                           remote_task_files=["good.gjf"], status=TaskStatus.local_ready),
                TaskRecord(task_id="bad", batch_id="b1", server_id="srv1",
                           execution_profile="g16", remote_job_dir="/r/bad",
                           remote_work_dir="/r", task_files=[str(bad)],
                           remote_task_files=["bad.gjf"], status=TaskStatus.local_ready),
            ]
            mp = Path(tmpdir) / "manifest.tsv"
            Manifest.write(mp, tasks)

            def fake_upload(local_path, remote_path, **kw):
                status = TransferStatus.transferred if "good" in remote_path else TransferStatus.failed
                return TransferRecord(direction=TransferDirection.upload,
                                      local_path=str(local_path), remote_path=remote_path,
                                      status=status, reason="x")

            mock_sftp = MagicMock()
            mock_sftp.upload_file = MagicMock(side_effect=fake_upload)

            svc = WorkflowService(ctx)
            records, failures = svc.upload_tasks(
                Manifest.read(mp), sftp_factory=lambda sid: mock_sftp,
                manifest_path=mp,
            )

            reloaded = {t.task_id: t for t in Manifest.read(mp)}
            assert reloaded["ok"].status == TaskStatus.uploaded
            assert reloaded["ok"].uploaded_at is not None
            assert reloaded["bad"].status == TaskStatus.local_ready
            assert len(records) == 2
            assert failures


class TestFailuresOutput:
    def test_failures_tsv_contains_new_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "failures.tsv"
            failures = [
                FailureRecord(task_id="t1", batch_id="b1", stage="refresh",
                              reason="server unreachable", server_id="srv1",
                              execution_profile="g16", remote_job_dir="/r/t1"),
                FailureRecord(task_id=None, batch_id="b1", stage="refresh",
                              reason="batch-level failure", server_id="srv2"),
            ]
            from jobdesk_app.core.outputs import write_failures_tsv
            write_failures_tsv(failures, out)
            content = out.read_text(encoding="utf-8").split("\n")
            assert len(content) >= 3  # header + 2 rows
            # check header has new fields
            assert "server_id" in content[0]
            assert "execution_profile" in content[0]
            assert "timestamp" in content[0]

    def test_append_failures_tsv_preserves_existing_failures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "failures.tsv"
            first = [
                FailureRecord(task_id="t1", batch_id="b1", stage="upload", reason="upload failed"),
            ]
            second = [
                FailureRecord(task_id="t2", batch_id="b1", stage="download", reason="download failed"),
            ]
            from jobdesk_app.core.outputs import write_failures_tsv, append_failures_tsv

            write_failures_tsv(first, out)
            append_failures_tsv(second, out)

            content = out.read_text(encoding="utf-8")
            assert content.count("batch_id\ttask_id\tstage") == 1
            assert "\tt1\tupload\t" in content
            assert "\tt2\tdownload\t" in content


class TestBatchSummaryFields:
    def test_summary_has_execution_profiles_and_servers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _make_mixed_project(base)
            ctx = create_project_context(base / "proj", base / "servers.yaml")
            pkgs = discover_task_packages(ctx)
            result = create_batch(ctx, pkgs, _make_rctx())
            summaries = list_batches(ctx)
            s = summaries[0]
            assert "g16" in s.execution_profiles
            assert "orca" in s.execution_profiles
            assert "srv1" in s.server_ids
            assert "srv2" in s.server_ids
            assert s.shared_files_count >= 0
