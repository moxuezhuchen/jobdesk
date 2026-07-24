import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import jobdesk_app.services.run_service as run_service_module
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.models import FailureRecord
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.core.status import BatchControlSnapshot, StatusRefreshResult, TaskStatusSnapshot
from jobdesk_app.core.submit import SubmitResult
from jobdesk_app.core.transfer import TransferStatus
from jobdesk_app.remote.scheduler import NohupAdapter, ResourceSpec, SlurmAdapter
from jobdesk_app.remote.ssh import SSHResult
from jobdesk_app.services.run_repository import MergeResult, RunRepository
from jobdesk_app.services.run_service import RunService
from tests.repository_helpers import replace_tasks_for_test


@pytest.fixture
def runs_dir(tmp_path):
    d = tmp_path / "_global_runs"
    d.mkdir()
    return d


def test_create_run_persists_only_to_sqlite(tmp_path, runs_dir):
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=4,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.gjf"), RunSource("/remote/jobs/b.gjf")],
    )

    record = RunService(tmp_path, runs_dir=runs_dir).create_run(spec, run_id="run001")

    assert record.run_id == "run001"
    assert record.run_dir.exists()
    assert not (record.run_dir / "run.json").exists()
    assert not (record.run_dir / "batch.json").exists()
    assert not (record.run_dir / "manifest.tsv").exists()
    tasks = RunService(tmp_path, runs_dir=runs_dir).repository.load_tasks(record.run_id)
    assert [task.task_id for task in tasks] == ["a", "b"]
    assert all(task.status == TaskStatus.uploaded for task in tasks)
    assert all(task.server_id == "s1" for task in tasks)
    assert record.local_dir == str(tmp_path.resolve())


def test_create_run_is_immediately_queryable_from_sqlite(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="sqlite-run",
    )

    with sqlite3.connect(runs_dir / "jobdesk.db") as connection:
        assert connection.execute("SELECT run_id FROM runs WHERE run_id = 'sqlite-run'").fetchone() == ("sqlite-run",)


def test_create_run_persists_exact_confflow_paths_in_sqlite(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/submission",
            command_template="confflow {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/source/water.xyz")],
            supporting_sources=[RunSource("/remote/submission/workflow.yaml")],
            result_templates=["water_confflow_work/workflow_stats.json"],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id="sqlite-workflow-paths",
    )

    task = service.repository.load_tasks("sqlite-workflow-paths")[0]

    assert task.workflow_kind == "confflow"
    assert task.remote_config_path == "/remote/submission/workflow.yaml"
    assert task.remote_workflow_dir == "/remote/submission/water_confflow_work"
    assert task.remote_state_path.endswith("/water_confflow_work/.workflow_state.json")
    assert task.remote_stats_path.endswith("/water_confflow_work/workflow_stats.json")
    assert task.remote_log_path.endswith("/.jobdesk_runs/sqlite-workflow-paths/water/.jobdesk_submit.log")
    assert task.remote_result_paths == ["/remote/submission/water_confflow_work/workflow_stats.json"]
    assert task.dry_run_command.endswith(" --dry-run")
    assert task.resume_command.endswith(" --resume")
    assert task.resume_dry_run_command.endswith(" --resume --dry-run")
    assert task.resume_requested is False


def test_run_service_exposes_legacy_migration_errors(tmp_path, runs_dir):
    broken = runs_dir / "broken"
    broken.mkdir()
    (broken / "run.json").write_text("{broken", encoding="utf-8")

    service = RunService(tmp_path, runs_dir=runs_dir)

    errors = service.migration_errors()
    assert len(errors) == 1
    assert errors[0].legacy_path == broken


def test_create_run_rejects_duplicate_explicit_run_id(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )
    service.create_run(spec, run_id="duplicate")

    with pytest.raises(FileExistsError):
        service.create_run(spec, run_id="duplicate")


def test_create_run_rejects_local_dir_outside_service_workspace_before_writes(tmp_path, runs_dir):
    workspace = tmp_path / "workspace"
    service = RunService(workspace, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )

    with pytest.raises(ValueError, match="local_dir.*service workspace"):
        service.create_run(
            spec,
            run_id="wrong-local-dir",
            local_dir=str(tmp_path / "other-workspace"),
        )

    assert not (runs_dir / "wrong-local-dir").exists()
    assert service.list_runs() == []


def test_next_run_id_considers_database_rows_without_directories(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    prefix = datetime.now().strftime("%y%m%d")
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id=f"{prefix}-001",
    )
    record.run_dir.rmdir()

    assert service._next_run_id() == f"{prefix}-002"


def test_create_run_rejects_unsafe_explicit_run_id(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )

    with pytest.raises(ValueError, match="Invalid run_id"):
        service.create_run(spec, run_id="../outside")


def test_load_run_rejects_path_traversal(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)

    with pytest.raises(ValueError, match="Invalid run_id"):
        service.load_run("../outside")


def test_new_run_does_not_create_legacy_manifest(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_atomic",
    )
    assert not record.manifest_path.exists()


def test_list_runs_returns_latest_first(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    for run_id in ("run001", "run002"):
        service.create_run(
            RunSpec(
                server_id="s1",
                remote_dir="/remote/jobs",
                command_template="bash {name}",
                max_parallel=1,
                mode=RunMode.selected_files,
                sources=[RunSource(f"/remote/jobs/{run_id}.sh")],
            ),
            run_id=run_id,
        )

    runs = service.list_runs()

    assert [run.run_id for run in runs] == ["run002", "run001"]


def test_load_run_counts_database_statuses(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run001",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.submitted
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    updated = service.load_run("run001")

    assert updated.status_summary == {"submitted": 1}


def test_download_completed_run_outputs(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run001",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            local_path = Path(local_path) if not isinstance(local_path, Path) else local_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord

            return TransferRecord(
                TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred
            )

    records, failures = service.download_completed("run001", FakeSFTP(), [".log"])

    assert not failures
    assert len(records) == 1
    assert (tmp_path / "results" / "run001" / "a.log").read_text(encoding="utf-8") == "ok"
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.downloaded


def test_download_completed_reports_cas_rejection_instead_of_success(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_download_race",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord

            return TransferRecord(
                TransferDirection.download,
                str(local_path),
                remote_path,
                status=TransferStatus.transferred,
            )

    monkeypatch.setattr(
        service.repository,
        "merge_tasks",
        lambda *_args, **_kwargs: MergeResult(tasks=tasks, accepted_task_ids=set()),
    )

    records, failures = service.download_completed(record.run_id, FakeSFTP(), [".log"])

    assert records == []
    assert failures == [("a", "task state changed during download; downloaded status was not committed")]


def test_download_completed_uses_declared_nested_results(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/jobs",
            command_template="confflow {name} -c settings.yaml -w {basename}_confflow_work",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/water.xyz")],
            supporting_sources=[RunSource("/remote/jobs/settings.yaml")],
            result_templates=["{basename}.txt", "{basename}_confflow_work/run_summary.json"],
        ),
        run_id="run004",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service.repository, record.run_id, tasks)
    requested = []

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            requested.append(remote_path)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord

            return TransferRecord(
                TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred
            )

    records, failures = service.download_completed("run004", FakeSFTP(), ["*.log"])

    assert not failures
    assert len(records) == 2
    assert requested == [
        "/remote/jobs/water.txt",
        "/remote/jobs/water_confflow_work/run_summary.json",
    ]
    assert (tmp_path / "results" / "run004" / "water_confflow_work" / "run_summary.json").exists()


def test_same_basename_downloads_are_isolated_by_run_id(tmp_path, runs_dir):
    """New final-result downloads never share the workspace-root basename."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="wsl",
        remote_dir="/remote/jobs",
        command_template="confflow {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/same.xyz")],
        result_templates=["{basename}_confflow_work/workflow_stats.json"],
    )
    first = service.create_run(spec, run_id="submission-a")
    second = service.create_run(spec, run_id="submission-b")
    for record in (first, second):
        tasks = service.repository.load_tasks(record.run_id)
        tasks[0].status = TaskStatus.remote_completed
        replace_tasks_for_test(service.repository, record.run_id, tasks)

    class FakeSFTP:
        def __init__(self, content: str) -> None:
            self.content = content

        def download_file(self, remote_path, local_path, **kwargs):
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(self.content, encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord

            return TransferRecord(
                TransferDirection.download,
                str(local_path),
                remote_path,
                status=TransferStatus.transferred,
            )

    first_records, first_failures = service.download_completed(first.run_id, FakeSFTP("first"), ["*.json"])
    second_records, second_failures = service.download_completed(second.run_id, FakeSFTP("second"), ["*.json"])

    first_path = tmp_path / "results" / first.run_id / "same_confflow_work" / "workflow_stats.json"
    second_path = tmp_path / "results" / second.run_id / "same_confflow_work" / "workflow_stats.json"
    assert first_failures == second_failures == []
    assert len(first_records) == len(second_records) == 1
    assert first_path != second_path
    assert first_path.read_text(encoding="utf-8") == "first"
    assert second_path.read_text(encoding="utf-8") == "second"


def test_download_rejects_service_workspace_that_differs_from_persisted_run_workspace(tmp_path, runs_dir):
    """A globally visible run cannot silently write results into another project."""
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    service_a = RunService(workspace_a, runs_dir=runs_dir)
    record = service_a.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="workspace-bound",
    )
    tasks = service_a.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service_a.repository, record.run_id, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("result", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord

            return TransferRecord(
                TransferDirection.download,
                str(local_path),
                remote_path,
                status=TransferStatus.transferred,
            )

    wrong_service = RunService(workspace_b, runs_dir=runs_dir)
    with pytest.raises(ValueError, match="does not match download workspace"):
        wrong_service.download_completed(record.run_id, FakeSFTP(), [".log"])
    assert not (workspace_a / "results").exists()
    assert not (workspace_b / "results").exists()

    records, failures = service_a.download_completed(record.run_id, FakeSFTP(), [".log"])
    assert failures == []
    assert len(records) == 1
    assert (workspace_a / "results" / record.run_id / "a.log").read_text(encoding="utf-8") == "result"


def test_download_completed_rejects_declared_result_path_traversal(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/jobs",
            command_template="echo run",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/water.xyz")],
            result_templates=["../outside.json"],
        ),
        run_id="run005",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    class FakeSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            raise AssertionError("unsafe output must not be downloaded")

    records, failures = service.download_completed("run005", FakeSFTP(), ["*.log"])

    assert records == []
    assert failures == [("water", "unsafe declared result path: ../outside.json")]
    assert not (tmp_path / "results" / "outside.json").exists()
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert task.error_message == "download: unsafe declared result path: ../outside.json"


def test_declared_outputs_pattern_semantics(tmp_path, runs_dir):
    """Plain filename patterns are used as-is; glob patterns expand to stem+suffix."""
    from jobdesk_app.core.manifest import TaskRecord
    from jobdesk_app.services.run_service import _declared_outputs

    task = TaskRecord(
        task_id="mol",
        batch_id="b1",
        remote_job_dir="/tmp/mol",
        remote_task_files=["mol.gjf"],
    )
    # glob patterns → stem expansion
    assert _declared_outputs(task, ["*.log"]) == ["mol.log"]
    assert _declared_outputs(task, [".log"]) == ["mol.log"]
    # plain filenames → exact (no stem prepend)
    assert _declared_outputs(task, ["result.log"]) == ["result.log"]
    assert _declared_outputs(task, ["summary.json"]) == ["summary.json"]
    assert _declared_outputs(task, ["subdir/result.json"]) == ["subdir/result.json"]


def test_prepare_retry_failed_marks_failed_tasks_uploaded(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run001",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.failed
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    changed = service.prepare_retry_failed("run001")

    assert changed == 1
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.uploaded


def test_workflow_retry_persists_resume_intent_and_original_namespace_across_restart(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/submission/run-one",
            command_template="confflow {name} -c workflow.yaml -w {basename}_confflow_work",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/source/water.xyz")],
            supporting_sources=[RunSource("/remote/submission/run-one/workflow.yaml")],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id="workflow-retry",
    )
    before = service.repository.load_tasks(record.run_id)[0]
    original_paths = (
        before.remote_config_path,
        before.remote_workflow_dir,
        before.remote_state_path,
        before.remote_stats_path,
        tuple(before.remote_result_paths),
    )
    before.status = TaskStatus.failed
    replace_tasks_for_test(service.repository, record.run_id, [before])

    assert service.prepare_retry_failed(record.run_id) == 1
    restarted = RunService(tmp_path, runs_dir=runs_dir)
    retried = restarted.repository.load_tasks(record.run_id)[0]

    assert retried.status == TaskStatus.uploaded
    assert retried.resume_requested is True
    assert (
        retried.remote_config_path,
        retried.remote_workflow_dir,
        retried.remote_state_path,
        retried.remote_stats_path,
        tuple(retried.remote_result_paths),
    ) == original_paths
    assert retried.resume_command.count("--resume") == 1
    assert retried.resume_dry_run_command.count("--resume") == 1

    from jobdesk_app.remote.submitter import JobSubmitter

    assert JobSubmitter.generate_task_runner(retried).count("--resume") == 1


def test_manual_recovery_returns_only_cas_accepted_task_ids(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh"), RunSource("/remote/jobs/b.sh")],
        ),
        run_id="run_recovery",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.uncertain
    tasks[1].status = TaskStatus.running
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    assert service.confirm_submitted(
        record.run_id,
        [tasks[0].task_id, tasks[1].task_id, "missing"],
        {tasks[0].task_id: "42"},
    ) == [tasks[0].task_id]
    confirmed = service.repository.load_tasks(record.run_id)
    assert confirmed[0].status == TaskStatus.submitted
    confirmed[0].status = TaskStatus.uncertain
    replace_tasks_for_test(service.repository, record.run_id, confirmed)
    assert service.abandon_submit(record.run_id, [tasks[0].task_id]) == [tasks[0].task_id]
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.uploaded


def test_confirm_submitted_requires_selected_task_ids(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)

    with pytest.raises(ValueError, match="task IDs required"):
        service.confirm_submitted("run_recovery", [])


def test_abandon_submit_requires_selected_task_ids(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)

    with pytest.raises(ValueError, match="task IDs required"):
        service.abandon_submit("run_recovery", iter(()))


def test_prepare_retry_failed_leaves_uncertain_tasks_unchanged(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_uncertain_retry",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.uncertain
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    assert service.prepare_retry_failed(record.run_id) == 0
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.uncertain


def test_prepare_rerun_rejects_uncertain_tasks(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_uncertain_rerun",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.uncertain
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    with pytest.raises(ValueError, match="active remote tasks"):
        service.prepare_rerun(record.run_id)


def test_prepare_rerun_rejects_active_remote_tasks(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_active",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].remote_job_id = "12345"
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    with pytest.raises(ValueError, match="active remote tasks"):
        service.prepare_rerun("run_active")

    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.running
    assert task.remote_job_id == "12345"


def test_prepare_rerun_clears_execution_metadata_for_terminal_tasks(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_done",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.failed
    tasks[0].submitted_at = datetime(2026, 5, 31, 8, 0, 0)
    tasks[0].completed_at = datetime(2026, 5, 31, 8, 1, 0)
    tasks[0].scheduler_type = "slurm"
    tasks[0].remote_job_id = "999"
    tasks[0].error_message = "old failure"
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    changed = service.prepare_rerun("run_done")

    assert changed == 1
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.uploaded
    assert task.submitted_at is None
    assert task.completed_at is None
    assert task.remote_job_id is None
    assert task.scheduler_type == "nohup"
    assert task.error_message is None


def test_submit_run_persists_and_reuses_execution_strategy(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_strategy",
    )
    captured = []

    class FakeSubmitter:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def submit_batch(self):
            return SubmitResult("run_strategy", 1, "/remote/jobs")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", FakeSubmitter)
    resources = ResourceSpec(cpus=8, memory_mb=4096, walltime_minutes=60)

    service.submit_run(
        "run_strategy",
        object(),
        object(),
        env_init_scripts=["/opt/module.sh"],
        scheduler=SlurmAdapter(),
        resources=resources,
    )
    service.recover_submit_operations()
    service.submit_run("run_strategy", object(), object())

    loaded = service.load_run("run_strategy")
    assert loaded.scheduler_type == "slurm"
    assert loaded.env_init_scripts == ["/opt/module.sh"]
    assert loaded.resources["cpus"] == 8
    assert isinstance(captured[1]["scheduler"], SlurmAdapter)
    assert captured[1]["env_init_scripts"] == ["/opt/module.sh"]
    assert captured[1]["resources"].memory_mb == 4096


def test_submit_run_skips_tasks_claimed_by_another_process(tmp_path, runs_dir, monkeypatch):
    first = RunService(tmp_path, runs_dir=runs_dir)
    first.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_claimed",
    )
    claimed, operations = first.repository.claim_submit_tasks(
        "run_claimed",
        scheduler_type="nohup",
        resources={},
        env_init_scripts=[],
        per_task=False,
    )
    assert claimed and operations
    submitter = MagicMock()
    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", submitter)

    result = RunService(tmp_path, runs_dir=runs_dir).submit_run("run_claimed", object(), object())

    assert result.submitted_task_count == 0
    submitter.assert_not_called()


def test_submit_run_checkpoints_each_scheduler_success_before_batch_finishes(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh"), RunSource("/remote/jobs/b.sh")],
        ),
        run_id="run_partial_submit",
    )

    class CrashingSubmitter:
        def __init__(self, **kwargs):
            self.tasks = kwargs["tasks"]
            self.checkpoint = kwargs["task_update_callback"]
            self.remote_started = kwargs["remote_started_callback"]

        def submit_batch(self):
            self.remote_started([self.tasks[0].task_id])
            submitted = self.tasks[0].model_copy(
                update={
                    "status": TaskStatus.submitted,
                    "scheduler_type": "slurm",
                    "remote_job_id": "12345",
                }
            )
            self.checkpoint([submitted])
            raise RuntimeError("process crashed after first remote submission")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", CrashingSubmitter)

    with pytest.raises(RuntimeError, match="process crashed"):
        service.submit_run("run_partial_submit", object(), object(), scheduler=SlurmAdapter())

    tasks = service.repository.load_tasks("run_partial_submit")
    assert tasks[0].status == TaskStatus.submitted
    assert tasks[0].remote_job_id == "12345"
    assert tasks[1].status == TaskStatus.uploaded

    RunService(tmp_path, runs_dir=runs_dir).recover_submit_operations()
    tasks = service.repository.load_tasks("run_partial_submit")
    assert tasks[0].status == TaskStatus.submitted
    assert tasks[1].status == TaskStatus.uploaded


def test_submit_run_releases_claim_when_nohup_chmod_fails(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_chmod_failure",
    )
    ssh = MagicMock()
    ssh.run.return_value = SSHResult("chmod", 1, "", "permission denied", 0.01)
    sftp = MagicMock()

    result = service.submit_run("run_chmod_failure", ssh, sftp)

    assert result.errors
    assert service.repository.load_tasks("run_chmod_failure")[0].status == TaskStatus.uploaded
    operations = service.repository.list_operations()
    assert operations[0].phase == "completed"
    assert operations[0].completed_at is not None


def test_submit_run_releases_claim_when_scheduler_preflight_raises(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_scheduler_preflight",
    )
    sftp = MagicMock()
    sftp.mkdir_p.side_effect = RuntimeError("upload path unavailable")

    result = service.submit_run("run_scheduler_preflight", MagicMock(), sftp, scheduler=SlurmAdapter())

    assert result.errors
    assert service.repository.load_tasks("run_scheduler_preflight")[0].status == TaskStatus.uploaded
    assert service.repository.list_operations()[0].phase == "completed"


def test_confflow_capability_failure_releases_claim_before_remote_start(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/submission",
            command_template="confflow {name} -c workflow.yaml -w {basename}_confflow_work",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/source/water.xyz")],
            supporting_sources=[RunSource("/remote/submission/workflow.yaml")],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id="run-capability-failure",
    )
    ssh = MagicMock()
    ssh.run.return_value = SSHResult("capabilities", 0, "not-json", "", 0.01)
    sftp = MagicMock()
    remote_start = MagicMock(wraps=service.repository.start_submit_operation)
    monkeypatch.setattr(service.repository, "start_submit_operation", remote_start)

    result = service.submit_run(record.run_id, ssh, sftp)

    assert any("capability preflight failed" in error for error in result.errors)
    sftp.upload_file.assert_not_called()
    remote_start.assert_not_called()
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.uploaded
    operation = service.repository.list_operations()[0]
    assert operation.phase == "completed"
    assert operation.completed_at is not None
    assert not any("nohup setsid" in call.args[0] for call in ssh.run.call_args_list)


def test_confflow_dry_run_failure_after_upload_releases_claim_without_nohup(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/submission",
            command_template="confflow {name} -c workflow.yaml -w {basename}_confflow_work",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/source/water.xyz")],
            supporting_sources=[RunSource("/remote/submission/workflow.yaml")],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id="run-dry-run-failure",
    )
    capability_json = json.dumps(
        {
            "schema_version": 2,
            "version": "1.4.2",
            "capabilities": {"workflow_state": True, "resume": True, "dag": True},
            "artifacts": {
                "run_summary": "run_summary.json",
                "workflow_stats": "workflow_stats.json",
                "workflow_state": ".workflow_state.json",
            },
        }
    )
    ssh = MagicMock()
    ssh.run.side_effect = [
        SSHResult("capabilities", 0, capability_json, "", 0.01),
        SSHResult("chmod", 0, "", "", 0.01),
        SSHResult("dry-run", 2, "", "invalid workflow", 0.01),
    ]
    sftp = MagicMock()
    remote_start = MagicMock(wraps=service.repository.start_submit_operation)
    monkeypatch.setattr(service.repository, "start_submit_operation", remote_start)

    result = service.submit_run(record.run_id, ssh, sftp)

    assert any("dry-run failed: invalid workflow" in error for error in result.errors)
    assert sftp.upload_file.call_count == 4
    remote_start.assert_not_called()
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.uploaded
    assert service.repository.list_operations()[0].phase == "completed"
    assert not any("nohup setsid" in call.args[0] for call in ssh.run.call_args_list)


def test_submit_exception_recovers_owned_remote_started_operation(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_started_exception",
    )

    class StartedThenCrashingSubmitter:
        def __init__(self, **kwargs):
            self.tasks = kwargs["tasks"]
            self.remote_started = kwargs["remote_started_callback"]

        def submit_batch(self):
            self.remote_started([self.tasks[0].task_id])
            raise RuntimeError("process died after remote start")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", StartedThenCrashingSubmitter)

    with pytest.raises(RuntimeError, match="process died"):
        service.submit_run("run_started_exception", object(), object())

    assert service.repository.load_tasks("run_started_exception")[0].status == TaskStatus.uncertain
    operation = service.repository.list_operations()[0]
    assert operation.phase == "completed"
    assert operation.completed_at is not None


def test_submit_run_recovers_claim_when_operation_payload_mapping_is_invalid(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_invalid_submit_payload",
    )
    original_claim = service.repository.claim_submit_tasks

    def claim_with_invalid_payload(*args, **kwargs):
        tasks, operations = original_claim(*args, **kwargs)
        operations[0].payload["task_ids"] = "not-a-list"
        return tasks, operations

    monkeypatch.setattr(service.repository, "claim_submit_tasks", claim_with_invalid_payload)

    with pytest.raises(RuntimeError, match="submit operation has invalid task ids"):
        service.submit_run("run_invalid_submit_payload", object(), object())

    assert service.repository.load_tasks("run_invalid_submit_payload")[0].status == TaskStatus.uploaded
    assert service.repository.list_operations(incomplete_only=True) == []


def test_submit_run_preserves_primary_error_and_notes_incomplete_failed_recovery(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_failed_recovery",
    )

    class CrashingSubmitter:
        def __init__(self, **kwargs):
            self.tasks = kwargs["tasks"]
            self.remote_started = kwargs["remote_started_callback"]

        def submit_batch(self):
            self.remote_started([self.tasks[0].task_id])
            raise ValueError("primary submit failure")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", CrashingSubmitter)
    monkeypatch.setattr(service.repository, "recover_submit_operation", lambda _operation_id: False)

    with pytest.raises(ValueError) as caught:
        service.submit_run("run_failed_recovery", object(), object())

    assert str(caught.value) == "primary submit failure"
    assert any("submit recovery left operation incomplete" in note for note in caught.value.__notes__)


def test_submit_run_preserves_primary_error_when_release_raises(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_release_failure",
    )

    class CrashingSubmitter:
        def __init__(self, **_kwargs):
            pass

        def submit_batch(self):
            raise KeyError("primary")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", CrashingSubmitter)
    monkeypatch.setattr(
        service.repository,
        "release_claimed_submit_operation",
        MagicMock(side_effect=RuntimeError("release database locked")),
    )

    with pytest.raises(KeyError) as caught:
        service.submit_run("run_release_failure", object(), object())

    assert caught.value.args == ("primary",)
    assert any("release database locked" in note for note in caught.value.__notes__)


def test_submit_run_reports_release_failure_after_success(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_success_cleanup_failure",
    )

    class SuccessfulSubmitter:
        def __init__(self, **_kwargs):
            pass

        def submit_batch(self):
            return SubmitResult("run_success_cleanup_failure", 1, "/remote/jobs")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", SuccessfulSubmitter)
    monkeypatch.setattr(
        service.repository,
        "release_claimed_submit_operation",
        MagicMock(side_effect=RuntimeError("release database locked")),
    )

    with pytest.raises(RuntimeError, match="submit cleanup failed"):
        service.submit_run("run_success_cleanup_failure", object(), object())


def test_losing_submitter_does_not_overwrite_execution_resources(tmp_path, runs_dir):
    first = RunService(tmp_path, runs_dir=runs_dir)
    first.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_claimed_resources",
    )
    claimed, operations = first.repository.claim_submit_tasks(
        "run_claimed_resources",
        scheduler_type="nohup",
        resources={},
        env_init_scripts=[],
        per_task=False,
    )
    assert claimed and operations

    RunService(tmp_path, runs_dir=runs_dir).submit_run(
        "run_claimed_resources",
        object(),
        object(),
        resources=ResourceSpec(cpus=99),
    )

    assert first.load_run("run_claimed_resources").resources == {}


def test_recover_submit_claimed_releases_tasks_and_completes(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_recover_claimed",
    )
    _, operations = service.repository.claim_submit_tasks(
        "run_recover_claimed",
        scheduler_type="nohup",
        resources={},
        env_init_scripts=[],
        per_task=False,
    )

    reopened = RunService(tmp_path, runs_dir=runs_dir)
    reopened.recover_submit_operations()

    assert reopened.repository.load_tasks("run_recover_claimed")[0].status == TaskStatus.uploaded
    assert reopened.repository.list_operations()[0].completed_at is not None


def test_startup_recovery_does_not_take_over_live_submit_lease(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="live_submit",
    )
    _, operations = service.repository.claim_submit_tasks(
        "live_submit",
        scheduler_type="slurm",
        resources={},
        env_init_scripts=[],
        per_task=False,
        owner_id="active-owner",
        lease_seconds=120,
    )

    assert service.recover_submit_operations("live_submit") == 0
    stored = next(
        item for item in service.repository.list_operations() if item.operation_id == operations[0].operation_id
    )
    assert stored.phase == "claimed"
    assert stored.owner_id == "active-owner"


def test_recover_submit_remote_started_marks_uncertain_and_is_idempotent(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_recover_started",
    )
    _, operations = service.repository.claim_submit_tasks(
        "run_recover_started",
        scheduler_type="slurm",
        resources={},
        env_init_scripts=[],
        per_task=True,
    )
    assert service.repository.start_submit_operation(operations[0].operation_id)

    reopened = RunService(tmp_path, runs_dir=runs_dir)
    reopened.recover_submit_operations()
    reopened.recover_submit_operations()

    task = reopened.repository.load_tasks("run_recover_started")[0]
    assert task.status == TaskStatus.uncertain
    assert task.scheduler_type == "slurm"
    assert task.error_message == "submission interrupted after remote command started"
    assert reopened.repository.list_operations(incomplete_only=True) == []


def test_concurrent_submit_recovery_advances_operation_once(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_concurrent_recovery",
    )
    _, operations = service.repository.claim_submit_tasks(
        "run_concurrent_recovery",
        scheduler_type="nohup",
        resources={},
        env_init_scripts=[],
        per_task=False,
    )
    assert service.repository.start_submit_operation(operations[0].operation_id)

    services = [RunService(tmp_path, runs_dir=runs_dir) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovered = list(pool.map(lambda item: item.recover_submit_operations(), services))

    assert sum(recovered) == 1
    assert service.repository.load_tasks("run_concurrent_recovery")[0].status == TaskStatus.uncertain
    assert service.repository.list_operations(incomplete_only=True) == []


def test_application_recovery_quarantines_orphan_submit_and_prunes_old_history(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_legacy_orphan",
    )
    task = service.repository.load_tasks("run_legacy_orphan")[0].model_copy(update={"status": TaskStatus.submitting})
    replace_tasks_for_test(service.repository, "run_legacy_orphan", [task])
    old = service.repository.create_operation("old", "delete", "completed", {})
    recent = service.repository.create_operation("recent", "delete", "completed", {})
    incomplete = service.repository.create_operation("pending", "delete", "prepared", {})
    now = datetime.now()
    with sqlite3.connect(runs_dir / "jobdesk.db") as connection:
        connection.execute(
            "UPDATE operations SET completed_at = ? WHERE operation_id = ?",
            ((now.replace(microsecond=0) - timedelta(days=8)).isoformat(), old.operation_id),
        )
        connection.execute(
            "UPDATE operations SET completed_at = ? WHERE operation_id = ?",
            ((now.replace(microsecond=0) - timedelta(days=6)).isoformat(), recent.operation_id),
        )

    assert service.recover_submit_operations() == 1

    assert service.repository.load_tasks("run_legacy_orphan")[0].status == TaskStatus.uncertain
    operation_ids = {item.operation_id for item in service.repository.list_operations()}
    assert old.operation_id not in operation_ids
    assert recent.operation_id in operation_ids
    assert incomplete.operation_id in operation_ids


def test_recover_confirmed_submit_verifies_durable_job_id_before_completion(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_confirmed_recovery",
    )
    _, operations = service.repository.claim_submit_tasks(
        "run_confirmed_recovery",
        scheduler_type="slurm",
        resources={},
        env_init_scripts=[],
        per_task=True,
    )
    operation = operations[0]
    assert service.repository.start_submit_operation(operation.operation_id)
    task = service.repository.load_tasks("run_confirmed_recovery")[0].model_copy(
        update={
            "status": TaskStatus.submitted,
            "scheduler_type": "slurm",
            "remote_job_id": "123",
        }
    )
    replace_tasks_for_test(service.repository, "run_confirmed_recovery", [task])
    payload = dict(operation.payload)
    payload["outcome_phase"] = "confirmed"
    payload["results"] = {"a": {"job_id": "123"}}
    assert service.repository.advance_operation(operation.operation_id, "remote_started", "confirmed", payload=payload)

    assert RunService(tmp_path, runs_dir=runs_dir).recover_submit_operations() == 1
    completed = service.repository.list_operations()[0]
    assert completed.phase == "completed"
    assert completed.completed_at is not None


@pytest.mark.parametrize(
    ("results", "expected_error"),
    [
        ({}, "confirmed submit outcome is invalid"),
        (
            {"a": {"job_id": "different"}},
            "confirmed submit outcome is invalid",
        ),
    ],
)
def test_recover_confirmed_submit_rejects_missing_or_mismatched_payload_job_id(
    tmp_path, runs_dir, results, expected_error
):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_invalid_confirmed",
    )
    _, operations = service.repository.claim_submit_tasks(
        "run_invalid_confirmed",
        scheduler_type="slurm",
        resources={},
        env_init_scripts=[],
        per_task=True,
    )
    operation = operations[0]
    assert service.repository.start_submit_operation(operation.operation_id)
    task = service.repository.load_tasks("run_invalid_confirmed")[0].model_copy(
        update={
            "status": TaskStatus.submitted,
            "scheduler_type": "slurm",
            "remote_job_id": "123",
        }
    )
    replace_tasks_for_test(service.repository, "run_invalid_confirmed", [task])
    payload = dict(operation.payload)
    payload["outcome_phase"] = "confirmed"
    payload["results"] = results
    assert service.repository.advance_operation(operation.operation_id, "remote_started", "confirmed", payload=payload)

    assert RunService(tmp_path, runs_dir=runs_dir).recover_submit_operations() == 0
    persisted = service.repository.list_operations()[0]
    assert persisted.phase == "confirmed"
    assert persisted.completed_at is None
    assert persisted.last_error == expected_error


@pytest.mark.parametrize(
    ("task_ids", "results", "expected_error"),
    [
        ([], {}, "confirmed operation task set is invalid"),
        (
            ["a"],
            {"a": {"job_id": "123"}, "extra": {"job_id": "456"}},
            "confirmed submit outcome is invalid",
        ),
        (
            ["a", "a"],
            {"a": {"job_id": "123"}},
            "confirmed operation task set is invalid",
        ),
        ([""], {"": {"job_id": "123"}}, "confirmed operation task set is invalid"),
    ],
)
def test_recover_confirmed_submit_rejects_corrupt_task_id_result_sets(
    tmp_path, runs_dir, task_ids, results, expected_error
):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_corrupt_confirmed",
    )
    _, operations = service.repository.claim_submit_tasks(
        "run_corrupt_confirmed",
        scheduler_type="slurm",
        resources={},
        env_init_scripts=[],
        per_task=True,
    )
    operation = operations[0]
    assert service.repository.start_submit_operation(operation.operation_id)
    task = service.repository.load_tasks("run_corrupt_confirmed")[0].model_copy(
        update={
            "status": TaskStatus.submitted,
            "scheduler_type": "slurm",
            "remote_job_id": "123",
        }
    )
    replace_tasks_for_test(service.repository, "run_corrupt_confirmed", [task])
    payload = dict(operation.payload)
    payload.update({"task_ids": task_ids, "outcome_phase": "confirmed", "results": results})
    assert service.repository.advance_operation(operation.operation_id, "remote_started", "confirmed", payload=payload)

    assert RunService(tmp_path, runs_dir=runs_dir).recover_submit_operations() == 0
    persisted = service.repository.list_operations()[0]
    assert persisted.phase == "confirmed"
    assert persisted.completed_at is None
    assert persisted.last_error == expected_error


def test_cancel_run_cancels_remote_job_before_recording_terminal_state(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_cancel",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "slurm"
    tasks[0].remote_job_id = "12345"
    replace_tasks_for_test(service.repository, record.run_id, tasks)
    adapter = pytest.importorskip("unittest.mock").MagicMock()
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)
    ssh = object()

    changed, errors = service.cancel_run("run_cancel", ssh)

    adapter.cancel.assert_called_once_with(ssh, "12345")
    assert changed == 1
    assert errors == []
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.cancelled


def test_cancel_run_does_not_count_same_status_row_rejected_by_full_cas(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_cancel_race",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "slurm"
    tasks[0].remote_job_id = "12345"
    replace_tasks_for_test(service.repository, record.run_id, tasks)
    adapter = MagicMock()
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)
    original_merge = service.repository.merge_tasks

    def concurrent_cancel_then_merge(*args, **kwargs):
        service.repository.mutate_tasks(
            record.run_id,
            lambda current: [
                task.model_copy(
                    update={
                        "status": TaskStatus.cancelled,
                        "error_message": "cancelled by concurrent worker",
                    },
                    deep=True,
                )
                for task in current
            ],
        )
        return original_merge(*args, **kwargs)

    monkeypatch.setattr(service.repository, "merge_tasks", concurrent_cancel_then_merge)

    changed, errors = service.cancel_run(record.run_id, object())

    assert changed == 0
    assert errors == []
    persisted = service.repository.load_tasks(record.run_id)[0]
    assert persisted.status == TaskStatus.cancelled
    assert persisted.error_message == "cancelled by concurrent worker"


def test_cancel_run_reports_cas_rejection_when_task_is_not_cancelled(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_cancel_conflict",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "slurm"
    tasks[0].remote_job_id = "12345"
    replace_tasks_for_test(service.repository, record.run_id, tasks)
    adapter = MagicMock()
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)
    original_merge = service.repository.merge_tasks

    def concurrent_refresh_then_merge(*args, **kwargs):
        service.repository.mutate_tasks(
            record.run_id,
            lambda current: [
                task.model_copy(
                    update={"error_message": "updated by concurrent refresh"},
                    deep=True,
                )
                for task in current
            ],
        )
        return original_merge(*args, **kwargs)

    monkeypatch.setattr(service.repository, "merge_tasks", concurrent_refresh_then_merge)

    changed, errors = service.cancel_run(record.run_id, object())

    adapter.cancel.assert_called_once()
    assert changed == 0
    assert errors == ["a: task state changed during cancellation; cancellation status was not committed"]
    persisted = service.repository.load_tasks(record.run_id)[0]
    assert persisted.status == TaskStatus.running
    assert persisted.error_message == "updated by concurrent refresh"


def test_cancel_run_does_not_claim_cancel_when_remote_cancel_fails(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_cancel_fail",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.running
    tasks[0].scheduler_type = "pbs"
    tasks[0].remote_job_id = "99"
    replace_tasks_for_test(service.repository, record.run_id, tasks)
    adapter = pytest.importorskip("unittest.mock").MagicMock()
    adapter.cancel.side_effect = RuntimeError("qdel rejected")
    monkeypatch.setattr("jobdesk_app.remote.scheduler.make_adapter", lambda _: adapter)

    changed, errors = service.cancel_run("run_cancel_fail", object())

    assert changed == 0
    assert "qdel rejected" in errors[0]
    assert service.repository.load_tasks(record.run_id)[0].status == TaskStatus.running


def test_download_failure_persists_error_message_to_manifest(tmp_path, runs_dir):
    """When SFTP download fails, error_message should be written to manifest."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_err",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    class FailSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            raise TimeoutError("sftp timeout")

    _records, failures = service.download_completed("run_err", FailSFTP(), [".log"])

    assert failures
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert "download:" in task.error_message
    assert "sftp timeout" in task.error_message


def test_successful_download_clears_previous_download_error(tmp_path, runs_dir):
    """After retry succeeds, the download error_message must be cleared."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/b.sh")],
        ),
        run_id="run_retry",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    tasks[0].error_message = "download: b.log: old error"
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    class OkSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            local_path = Path(local_path) if not isinstance(local_path, Path) else local_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("ok", encoding="utf-8")
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord

            return TransferRecord(
                TransferDirection.download, str(local_path), remote_path, status=TransferStatus.transferred
            )

    _records, failures = service.download_completed("run_retry", OkSFTP(), [".log"])

    assert not failures
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.downloaded
    assert task.error_message is None or "download:" not in task.error_message


def test_download_directory_creation_failure_persists_error_message(tmp_path, runs_dir, monkeypatch):
    download_dir = tmp_path / "downloads"
    service = RunService(download_dir, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/c.sh")],
        ),
        run_id="run_mkdir_fail",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    original_mkdir = Path.mkdir

    def fail_download_dir(self, *args, **kwargs):
        if self == download_dir:
            raise PermissionError("download directory denied")
        return original_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_download_dir)

    _records, failures = service.download_completed("run_mkdir_fail", object(), [".log"])

    assert failures == [("c", "download directory denied")]
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert task.error_message == "download: download directory denied"


def test_download_completed_persists_transfer_record_failed_reason(tmp_path, runs_dir):
    """When sftp.download_file returns TransferStatus.failed, the reason must be persisted."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_failed_rec",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    replace_tasks_for_test(service.repository, record.run_id, tasks)

    class FailedRecordSFTP:
        def download_file(self, remote_path, local_path, **kwargs):
            from jobdesk_app.core.transfer import TransferDirection, TransferRecord

            return TransferRecord(
                TransferDirection.download,
                str(local_path),
                remote_path,
                status=TransferStatus.failed,
                reason="remote file not found",
            )

    _records, failures = service.download_completed("run_failed_rec", FailedRecordSFTP(), [".log"])

    assert failures
    task = service.repository.load_tasks(record.run_id)[0]
    assert task.status == TaskStatus.remote_completed
    assert "remote file not found" in task.error_message
    assert task.error_message.startswith("download:")


def test_wrong_workspace_service_cannot_bind_or_delete_run_results(tmp_path, runs_dir):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    service_a = RunService(workspace_a, runs_dir=runs_dir)
    service_b = RunService(workspace_b, runs_dir=runs_dir)
    record = service_a.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="wrong-workspace",
    )
    wrong_results = workspace_b / "results" / record.run_id
    wrong_results.mkdir(parents=True)
    sentinel = wrong_results / "keep.txt"
    sentinel.write_text("do not delete", encoding="utf-8")

    with pytest.raises(ValueError, match="local_dir.*workspace"):
        service_b.delete_run(record.run_id)

    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert service_a.load_run(record.run_id).run_id == record.run_id
    assert service_a.repository.list_operations() == []


def test_delete_run_preserves_metadata_when_results_deletion_fails(tmp_path, runs_dir, monkeypatch):
    """A filesystem failure remains journaled and retryable."""
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_locked",
    )

    # Create a results directory
    results_dir = tmp_path / "results" / "run_locked"
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "output.log").write_text("data", encoding="utf-8")

    # Confirm SQLite metadata exists.
    assert service.load_run(record.run_id).run_id == record.run_id

    import shutil

    original_rmtree = shutil.rmtree

    def failing_rmtree(path, *args, **kwargs):
        resolved = Path(path).resolve()
        if resolved.name == "results" and resolved.parent.parent.name == ".jobdesk-trash":
            raise PermissionError("locked")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", failing_rmtree)

    with pytest.raises(OSError, match="Failed to delete results"):
        service.delete_run("run_locked")

    with pytest.raises(KeyError):
        service.load_run(record.run_id)
    operation = next(op for op in service.repository.list_operations() if op.kind == "delete")
    assert operation.phase == "files_isolated"
    assert "locked" in (operation.last_error or "")

    monkeypatch.setattr(shutil, "rmtree", original_rmtree)
    assert service.recover_delete_operations() == 1
    assert not results_dir.exists()
    assert not record.run_dir.exists()
    completed = next(op for op in service.repository.list_operations() if op.kind == "delete")
    assert completed.phase == "completed"
    assert completed.completed_at is not None


def test_delete_run_keeps_files_when_database_delete_fails(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_db_locked",
    )
    monkeypatch.setattr(
        service.repository,
        "delete_run_metadata",
        MagicMock(side_effect=sqlite3.OperationalError("database is locked")),
    )

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        service.delete_run(record.run_id)

    assert record.run_dir.is_dir()


def test_recover_delete_resumes_from_prepared(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_prepared",
    )
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    service.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=results_dir,
    )

    assert service.recover_delete_operations() == 1
    assert not record.run_dir.exists()
    assert not results_dir.exists()
    with pytest.raises(KeyError):
        service.load_run(record.run_id)


def test_recover_delete_resumes_after_only_run_directory_was_isolated(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_half_isolated",
    )
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    (results_dir / "old.txt").write_text("old", encoding="utf-8")
    operation = service.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=results_dir,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    trash_run = Path(str(operation.payload["trash_run_dir"]))
    trash_run.parent.mkdir(parents=True)
    record.run_dir.replace(trash_run)

    assert service.recover_delete_operations() == 1

    assert not record.run_dir.exists()
    assert not results_dir.exists()
    assert not trash_run.exists()
    stored = next(item for item in service.repository.list_operations() if item.operation_id == operation.operation_id)
    assert stored.phase == "completed"


def test_delete_rename_failure_is_journaled_and_retryable(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_rename_locked",
    )
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    original_replace = Path.replace

    def fail_results_rename(path, target):
        if path.resolve() == results_dir.resolve():
            raise PermissionError("rename locked")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_results_rename)

    with pytest.raises(PermissionError, match="rename locked"):
        service.delete_run(record.run_id)

    operation = next(item for item in service.repository.list_operations() if item.kind == "delete")
    assert operation.phase == "metadata_deleted"
    assert operation.last_error == "rename locked"
    assert Path(str(operation.payload["trash_run_dir"])).is_dir()
    assert results_dir.is_dir()

    monkeypatch.setattr(Path, "replace", original_replace)
    assert service.recover_delete_operations() == 1
    assert not results_dir.exists()


def test_delete_recovery_rejects_untrusted_trash_path(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    outside = tmp_path / "outside-trash"
    operation = service.repository.create_operation(
        "missing",
        "delete",
        "metadata_deleted",
        {
            "run_dir": str((runs_dir / "missing").resolve()),
            "results_root": str((tmp_path / "results").resolve()),
            "results_dir": str((tmp_path / "results" / "missing").resolve()),
            "trash_run_dir": str(outside / "run"),
            "trash_results_dir": str(outside / "results"),
            "run": {},
            "tasks": [],
        },
    )

    assert service.recover_delete_operations() == 0
    assert not outside.exists()
    stored = next(item for item in service.repository.list_operations() if item.operation_id == operation.operation_id)
    assert "binding" in (stored.last_error or "")


@pytest.mark.parametrize("linked_kind", ["run", "results"])
def test_delete_rejects_managed_source_directory_link(tmp_path, runs_dir, linked_kind):
    import os
    import shutil
    import subprocess

    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_link",
    )
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    target = (runs_dir if linked_kind == "run" else tmp_path / "results") / "victim"
    target.mkdir(parents=True)
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    source = record.run_dir if linked_kind == "run" else results_dir
    shutil.rmtree(source)
    try:
        os.symlink(target, source, target_is_directory=True)
    except OSError as exc:
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(source), str(target)],
            capture_output=True,
            check=False,
        )
        if created.returncode:
            pytest.skip(f"directory links unavailable: {exc}; {created.stderr!r}")

    with pytest.raises(ValueError, match="reparse|link"):
        service.delete_run(record.run_id)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert service.load_run(record.run_id).run_id == record.run_id


@pytest.mark.parametrize("initial_phase", ["prepared", "metadata_deleted"])
def test_recover_legacy_delete_journal_without_trash_paths(tmp_path, runs_dir, initial_phase):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id=f"legacy_{initial_phase}",
    )
    results_root = tmp_path / "results"
    results_dir = results_root / record.run_id
    results_dir.mkdir(parents=True)
    operation = service.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=results_root,
        results_dir=results_dir,
    )
    payload = dict(operation.payload)
    payload.pop("trash_run_dir")
    payload.pop("trash_results_dir")
    assert service.repository.advance_operation(
        operation.operation_id,
        "prepared",
        "prepared",
        payload=payload,
    )
    if initial_phase == "metadata_deleted":
        assert service.repository.delete_run_metadata(operation.operation_id)

    assert service.recover_delete_operations() == 1

    stored = next(item for item in service.repository.list_operations() if item.operation_id == operation.operation_id)
    assert stored.phase == "completed"
    assert stored.payload["trash_run_dir"]
    assert stored.payload["trash_results_dir"]
    assert not record.run_dir.exists()
    assert not results_dir.exists()


def test_concurrent_recovery_backfills_legacy_delete_trash_once(tmp_path, runs_dir):
    creator = RunService(tmp_path, runs_dir=runs_dir)
    record = creator.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="legacy_concurrent",
    )
    results_root = tmp_path / "results"
    results_dir = results_root / record.run_id
    results_dir.mkdir(parents=True)
    operation = creator.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=results_root,
        results_dir=results_dir,
    )
    payload = dict(operation.payload)
    payload.pop("trash_run_dir")
    payload.pop("trash_results_dir")
    assert creator.repository.advance_operation(
        operation.operation_id,
        "prepared",
        "prepared",
        payload=payload,
    )
    assert creator.repository.delete_run_metadata(operation.operation_id)
    workers = [
        RunService(tmp_path, runs_dir=runs_dir),
        RunService(tmp_path, runs_dir=runs_dir),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda item: item.recover_delete_operations(), workers))

    assert sum(outcomes) == 1
    stored = next(item for item in creator.repository.list_operations() if item.operation_id == operation.operation_id)
    assert stored.phase == "completed"
    assert operation.operation_id in str(stored.payload["trash_run_dir"])
    assert operation.operation_id in str(stored.payload["trash_results_dir"])


def test_delete_recovery_rejects_untrusted_journal_path(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    operation = service.repository.create_operation(
        "missing",
        "delete",
        "metadata_deleted",
        {"run_dir": str(outside), "results_dir": str(outside), "run": {}, "tasks": []},
    )

    assert service.recover_delete_operations() == 0
    assert outside.exists()
    stored = next(op for op in service.repository.list_operations() if op.operation_id == operation.operation_id)
    assert "binding" in (stored.last_error or "").lower()


def test_recover_delete_completes_files_deleted_phase(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    operation = service.repository.create_operation(
        "run_gone",
        "delete",
        "files_deleted",
        {
            "run_dir": str((runs_dir / "run_gone").resolve()),
            "results_dir": str((tmp_path / "results" / "run_gone").resolve()),
            "run": {},
            "tasks": [],
        },
    )

    assert service.recover_delete_operations() == 0
    stored = next(op for op in service.repository.list_operations() if op.operation_id == operation.operation_id)
    assert stored.phase == "files_deleted"
    assert stored.completed_at is None
    assert "binding" in (stored.last_error or "").lower()


def test_delete_recovery_rejects_other_workspace_with_diagnostic(tmp_path, runs_dir):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    owner = RunService(workspace_a, runs_dir=runs_dir)
    record = owner.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="cross_workspace",
    )
    operation = owner.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=workspace_a / "results",
        results_dir=workspace_a / "results" / record.run_id,
    )
    outsider = RunService(workspace_b, runs_dir=runs_dir)

    assert outsider.recover_delete_operations() == 0
    stored = next(item for item in outsider.repository.list_operations() if item.operation_id == operation.operation_id)
    assert stored.phase == "prepared"
    assert "binding mismatch" in (stored.last_error or "")


def test_scoped_delete_recovery_rejects_operation_bound_to_other_workspace(tmp_path, runs_dir):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    owner = RunService(workspace_a, runs_dir=runs_dir)
    record = owner.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="forged_delete",
    )
    operation = owner.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=workspace_a / "results",
        results_dir=workspace_a / "results" / record.run_id,
    )
    victim = workspace_b / "results" / record.run_id
    victim.mkdir(parents=True)
    forged = dict(operation.payload)
    forged.update(
        {
            "results_root": str((workspace_b / "results").resolve()),
            "results_dir": str(victim.resolve()),
            "trash_results_dir": str(
                (workspace_b / "results" / ".jobdesk-trash" / operation.operation_id / "results").resolve()
            ),
            "run": {**forged["run"], "local_dir": str(workspace_b.resolve())},
        }
    )
    assert owner.repository.advance_operation(operation.operation_id, "prepared", "metadata_deleted", payload=forged)

    outsider = RunService(workspace_b, runs_dir=runs_dir)
    assert outsider.recover_delete_operations() == 0
    assert victim.exists()
    assert owner.repository.delete_operation_workspace(operation.operation_id) == workspace_a.resolve()
    stored = next(item for item in owner.repository.list_operations() if item.operation_id == operation.operation_id)
    assert "binding mismatch" in (stored.last_error or "")


def test_live_submit_finishes_after_concurrent_recovery_declines_lease(tmp_path, runs_dir, monkeypatch):
    service_a = RunService(tmp_path, runs_dir=runs_dir)
    service_a.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="concurrent_live_submit",
    )
    remote_started = threading.Event()
    release_remote = threading.Event()

    class PausedSubmitter:
        def __init__(self, **kwargs):
            self.task = kwargs["tasks"][0]
            self.started = kwargs["remote_started_callback"]
            self.checkpoint = kwargs["task_update_callback"]

        def submit_batch(self):
            self.started([self.task.task_id])
            remote_started.set()
            assert release_remote.wait(10)
            self.checkpoint(
                [
                    self.task.model_copy(
                        update={
                            "status": TaskStatus.submitted,
                            "scheduler_type": "slurm",
                            "remote_job_id": "job-123",
                        }
                    )
                ]
            )
            return SubmitResult("concurrent_live_submit", 1, "/remote/jobs")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", PausedSubmitter)
    with ThreadPoolExecutor(max_workers=1) as pool:
        submitting = pool.submit(
            service_a.submit_run,
            "concurrent_live_submit",
            object(),
            object(),
            None,
            SlurmAdapter(),
            ResourceSpec(),
        )
        assert remote_started.wait(10)
        service_b = RunService(tmp_path, runs_dir=runs_dir)
        assert service_b.recover_submit_operations("concurrent_live_submit") == 0
        release_remote.set()
        submitting.result(timeout=10)

    task = service_a.repository.load_tasks("concurrent_live_submit")[0]
    assert task.status == TaskStatus.submitted
    assert task.remote_job_id == "job-123"


def test_lost_submit_lease_prevents_starting_next_remote_task(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh"), RunSource("/remote/jobs/b.sh")],
        ),
        run_id="lost_lease_submit",
    )
    launched: list[str] = []

    class TwoTaskSubmitter:
        def __init__(self, **kwargs):
            self.tasks = kwargs["tasks"]
            self.started = kwargs["remote_started_callback"]

        def submit_batch(self):
            for task in self.tasks:
                self.started([task.task_id])
                launched.append(task.task_id)
            return SubmitResult("lost_lease_submit", len(launched), "/remote/jobs")

    renewals = iter([True, False])
    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", TwoTaskSubmitter)
    monkeypatch.setattr(
        service.repository,
        "renew_submit_lease",
        lambda *_args, **_kwargs: next(renewals),
    )

    with pytest.raises(RuntimeError, match="ownership lost"):
        service.submit_run("lost_lease_submit", object(), object(), scheduler=SlurmAdapter())

    assert len(launched) == 1


def test_nohup_batch_marks_shared_operation_started_once(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=2,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh"), RunSource("/remote/jobs/b.sh")],
        ),
        run_id="shared_nohup",
    )

    class BatchSubmitter:
        def __init__(self, **kwargs):
            self.tasks = kwargs["tasks"]
            self.started = kwargs["remote_started_callback"]
            self.checkpoint = kwargs["task_update_callback"]

        def submit_batch(self):
            self.started([task.task_id for task in self.tasks])
            submitted = [
                task.model_copy(
                    update={
                        "status": TaskStatus.submitted,
                        "scheduler_type": "nohup",
                        "remote_job_id": "4321",
                    }
                )
                for task in self.tasks
            ]
            self.checkpoint(submitted)
            return SubmitResult("shared_nohup", len(submitted), "/remote/jobs")

    monkeypatch.setattr("jobdesk_app.services.run_service.JobSubmitter", BatchSubmitter)

    result = service.submit_run("shared_nohup", object(), object(), scheduler=NohupAdapter())

    assert not result.errors
    assert [task.status for task in service.repository.load_tasks("shared_nohup")] == [
        TaskStatus.submitted,
        TaskStatus.submitted,
    ]
    operations = service.repository.list_operations()
    assert len(operations) == 1
    assert operations[0].phase == "completed"


def test_submit_cleanup_waits_for_blocked_heartbeat_to_exit(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="blocked_heartbeat",
    )
    entered = threading.Event()
    release = threading.Event()
    original_renew = service.repository.renew_submit_lease

    def blocking_renew(*args, **kwargs):
        if threading.current_thread().name.startswith("submit-lease-"):
            entered.set()
            assert release.wait(10)
        return original_renew(*args, **kwargs)

    class WaitingSubmitter:
        def __init__(self, **_kwargs):
            pass

        def submit_batch(self):
            assert entered.wait(10)
            return SubmitResult("blocked_heartbeat", 0, "/remote/jobs")

    monkeypatch.setattr(run_service_module, "SUBMIT_HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(service.repository, "renew_submit_lease", blocking_renew)
    monkeypatch.setattr(run_service_module, "JobSubmitter", WaitingSubmitter)

    with ThreadPoolExecutor(max_workers=1) as pool:
        submitting = pool.submit(service.submit_run, "blocked_heartbeat", object(), object())
        assert entered.wait(10)
        assert not submitting.done()
        threading.Event().wait(2.2)
        assert not submitting.done()
        release.set()
        submitting.result(timeout=10)

    assert not any(
        thread.name == "submit-lease-blocked_heartbeat" and thread.is_alive() for thread in threading.enumerate()
    )


def test_concurrent_delete_recovery_completes_once(tmp_path, runs_dir):
    from concurrent.futures import ThreadPoolExecutor

    creator = RunService(tmp_path, runs_dir=runs_dir)
    record = creator.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_delete_race",
    )
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    creator.repository.prepare_delete_run(
        record.run_id, run_dir=record.run_dir, results_root=tmp_path / "results", results_dir=results_dir
    )
    services = [RunService(tmp_path, runs_dir=runs_dir), RunService(tmp_path, runs_dir=runs_dir)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda service: service.recover_delete_operations(), services))

    assert sum(outcomes) == 1
    assert not record.run_dir.exists()
    assert not results_dir.exists()
    operation = next(op for op in creator.repository.list_operations() if op.kind == "delete")
    assert operation.phase == "completed"


def test_create_run_cleans_directory_when_delete_tombstone_rejects_id(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )
    record = service.create_run(spec, run_id="run_generation")
    operation = service.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / record.run_id,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    import shutil

    shutil.rmtree(record.run_dir)

    with pytest.raises(ValueError, match="delete is incomplete"):
        service.create_run(spec, run_id=record.run_id)

    assert not record.run_dir.exists()
    with pytest.raises(KeyError):
        service.load_run(record.run_id)


def test_create_run_preserves_repository_error_when_directory_cleanup_fails(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )

    def fail_after_concurrent_file(record, _tasks):
        (record.run_dir / "concurrent.txt").write_text("keep", encoding="utf-8")
        raise ValueError("database write failed")

    monkeypatch.setattr(service.repository, "create_run", fail_after_concurrent_file)

    with pytest.raises(ValueError, match="database write failed"):
        service.create_run(spec, run_id="run_cleanup_race")

    assert (runs_dir / "run_cleanup_race" / "concurrent.txt").read_text() == "keep"


def test_automatic_run_id_skips_incomplete_delete_tombstone(tmp_path, runs_dir):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )
    prefix = datetime.now().strftime("%y%m%d")
    old = service.create_run(spec, run_id=f"{prefix}-001")
    operation = service.repository.prepare_delete_run(
        old.run_id,
        run_dir=old.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / old.run_id,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    import shutil

    shutil.rmtree(old.run_dir)

    created = service.create_run(spec)

    assert created.run_id == f"{prefix}-002"


def test_create_and_delete_recovery_race_leaves_new_generation_consistent(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )
    old = service.create_run(spec, run_id="run_race_generation")
    operation = service.repository.prepare_delete_run(
        old.run_id,
        run_dir=old.run_dir,
        results_root=tmp_path / "results",
        results_dir=tmp_path / "results" / old.run_id,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    import shutil

    shutil.rmtree(old.run_dir)
    original_create = service.repository.create_run

    def finish_old_delete_before_insert(record, tasks):
        assert record.run_dir.exists()
        assert RunService(tmp_path, runs_dir=runs_dir).recover_delete_operations() == 1
        assert not record.run_dir.exists()
        return original_create(record, tasks)

    monkeypatch.setattr(service.repository, "create_run", finish_old_delete_before_insert)

    created = service.create_run(spec, run_id=old.run_id)

    assert created.run_dir.is_dir()
    assert service.load_run(old.run_id).run_id == old.run_id


def test_delete_workers_only_touch_trash_after_new_generation_is_created(tmp_path, runs_dir, monkeypatch):
    import shutil
    import threading

    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )
    old = service.create_run(spec, run_id="run_serialized_delete")
    results_dir = tmp_path / "results" / old.run_id
    results_dir.mkdir(parents=True)
    operation = service.repository.prepare_delete_run(
        old.run_id,
        run_dir=old.run_dir,
        results_root=tmp_path / "results",
        results_dir=results_dir,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    first_entered_trash_delete = threading.Event()
    release_first = threading.Event()
    original_rmtree = shutil.rmtree

    def paused_rmtree(path, *args, **kwargs):
        resolved = Path(path).resolve()
        is_results_trash = resolved.name == "results" and resolved.parent.parent.name == ".jobdesk-trash"
        if is_results_trash and not first_entered_trash_delete.is_set():
            first_entered_trash_delete.set()
            assert release_first.wait(10)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", paused_rmtree)
    workers = [RunService(tmp_path, runs_dir=runs_dir), RunService(tmp_path, runs_dir=runs_dir)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(workers[0].recover_delete_operations)
        assert first_entered_trash_delete.wait(5)
        second = pool.submit(workers[1].recover_delete_operations)
        assert second.result(timeout=5) == 1
        created = service.create_run(spec, run_id=old.run_id)
        marker = created.run_dir / "new-generation.txt"
        marker.write_text("new", encoding="utf-8")
        release_first.set()
        assert first.result(timeout=5) == 0

    assert created.run_dir.is_dir()
    assert marker.read_text(encoding="utf-8") == "new"
    assert RunService(tmp_path, runs_dir=runs_dir).load_run(old.run_id).run_id == old.run_id


def test_delete_isolation_transaction_has_one_rename_winner(tmp_path, runs_dir, monkeypatch):
    import threading
    import time

    service = RunService(tmp_path, runs_dir=runs_dir)
    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="bash {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.sh")],
    )
    old = service.create_run(spec, run_id="run_isolation_winner")
    old_tasks = service.repository.load_tasks(old.run_id)
    results_dir = tmp_path / "results" / old.run_id
    results_dir.mkdir(parents=True)
    operation = service.repository.prepare_delete_run(
        old.run_id,
        run_dir=old.run_dir,
        results_root=tmp_path / "results",
        results_dir=results_dir,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    entered_first_rename = threading.Event()
    release_first_rename = threading.Event()
    replace_calls: list[Path] = []
    original_replace = Path.replace

    def paused_replace(path, target):
        replace_calls.append(path)
        if len(replace_calls) == 1:
            entered_first_rename.set()
            assert release_first_rename.wait(10)
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", paused_replace)
    workers = [RunService(tmp_path, runs_dir=runs_dir), RunService(tmp_path, runs_dir=runs_dir)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(workers[0].recover_delete_operations)
        assert entered_first_rename.wait(5)
        second = pool.submit(workers[1].recover_delete_operations)
        create = pool.submit(service.repository.create_run, old, old_tasks)
        time.sleep(0.2)
        assert not second.done()
        assert not create.done()
        assert len(replace_calls) == 1
        release_first_rename.set()
        assert first.result(timeout=5) == 1
        assert second.result(timeout=5) == 0
        created = create.result(timeout=5)

    created.run_dir.mkdir(parents=True)
    marker = created.run_dir / "new.txt"
    marker.write_text("new", encoding="utf-8")
    assert len(replace_calls) == 2
    assert marker.read_text(encoding="utf-8") == "new"


def test_slow_trash_delete_does_not_hold_sqlite_write_lock(tmp_path, runs_dir, monkeypatch):
    import shutil
    import threading

    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_slow_trash",
    )
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    operation = service.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=results_dir,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    entered = threading.Event()
    release = threading.Event()
    original_rmtree = shutil.rmtree

    def paused_rmtree(path, *args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", paused_rmtree)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(service.recover_delete_operations)
        assert entered.wait(5)
        unrelated = pool.submit(
            RunRepository(runs_dir).create_operation,
            "other",
            "submit",
            "claimed",
            {},
        )
        try:
            assert unrelated.result(timeout=1).run_id == "other"
        finally:
            release.set()
        assert deleting.result(timeout=5) == 1


def test_slow_trash_parent_creation_does_not_hold_sqlite_write_lock(tmp_path, runs_dir, monkeypatch):
    import threading

    service = RunService(tmp_path, runs_dir=runs_dir)
    other_repository = RunRepository(runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_slow_trash_mkdir",
    )
    results_dir = tmp_path / "results" / record.run_id
    results_dir.mkdir(parents=True)
    operation = service.repository.prepare_delete_run(
        record.run_id,
        run_dir=record.run_dir,
        results_root=tmp_path / "results",
        results_dir=results_dir,
    )
    assert service.repository.delete_run_metadata(operation.operation_id)
    entered = threading.Event()
    release = threading.Event()
    original_mkdir = Path.mkdir

    def paused_mkdir(path, *args, **kwargs):
        if ".jobdesk-trash" in path.parts and not entered.is_set():
            entered.set()
            assert release.wait(10)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", paused_mkdir)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(service.recover_delete_operations)
        assert entered.wait(5)
        unrelated = pool.submit(
            other_repository.create_operation,
            "other-mkdir",
            "submit",
            "claimed",
            {},
        )
        try:
            assert unrelated.result(timeout=1).run_id == "other-mkdir"
        finally:
            release.set()
        assert deleting.result(timeout=5) == 1


def test_refresh_run_reports_only_changes_committed_by_compare_and_swap(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_refresh_race",
    )
    original = service.repository.load_tasks(record.run_id)
    refreshed = [original[0].model_copy(update={"status": TaskStatus.running}, deep=True)]
    refresh_result = StatusRefreshResult(
        batch_id=record.run_id,
        task_count=1,
        changed_count=1,
        snapshots=[
            TaskStatusSnapshot(
                task_id=original[0].task_id,
                batch_id=record.run_id,
                previous_status=original[0].status.value,
                recovered_status=TaskStatus.running.value,
            )
        ],
    )
    monkeypatch.setattr(
        "jobdesk_app.remote.status_refresh.refresh_task_statuses",
        lambda *_args, **_kwargs: (refresh_result, refreshed),
    )
    monkeypatch.setattr(
        service.repository,
        "merge_tasks",
        lambda *_args, **_kwargs: MergeResult(tasks=original, accepted_task_ids=set()),
    )

    result = service.refresh_run(record.run_id, object())

    assert result.changed_count == 0


def test_refresh_run_filters_all_rejected_task_diagnostics(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh"), RunSource("/remote/jobs/b.sh")],
        ),
        run_id="run_refresh_partial_race",
    )
    original = service.repository.load_tasks(record.run_id)
    refreshed = [
        task.model_copy(
            update={"status": TaskStatus.failed, "error_message": f"remote {task.task_id} failed"},
            deep=True,
        )
        for task in original
    ]
    refresh_result = StatusRefreshResult(
        batch_id=record.run_id,
        task_count=2,
        changed_count=2,
        snapshots=[
            TaskStatusSnapshot(
                task_id=task.task_id,
                batch_id=record.run_id,
                previous_status=task.status.value,
                recovered_status=TaskStatus.failed.value,
                warnings=[f"warning {task.task_id}"],
            )
            for task in original
        ],
        failures=[
            FailureRecord(
                task_id=task.task_id,
                batch_id=record.run_id,
                stage="runtime",
                reason=f"failure {task.task_id}",
            )
            for task in original
        ],
        warnings=["batch warning"],
        batch_control=BatchControlSnapshot(warnings=["batch warning"]),
    )
    monkeypatch.setattr(
        "jobdesk_app.remote.status_refresh.refresh_task_statuses",
        lambda *_args, **_kwargs: (refresh_result, refreshed),
    )
    original_merge = service.repository.merge_tasks

    def race_then_merge(*args, **kwargs):
        service.repository.mutate_tasks(
            record.run_id,
            lambda tasks: [
                task.model_copy(update={"status": TaskStatus.cancelled}, deep=True) if task.task_id == "b" else task
                for task in tasks
            ],
        )
        return original_merge(*args, **kwargs)

    monkeypatch.setattr(service.repository, "merge_tasks", race_then_merge)

    result = service.refresh_run(record.run_id, object())

    assert result.changed_count == 1
    assert [snapshot.task_id for snapshot in result.snapshots] == ["a"]
    assert [failure.task_id for failure in result.failures] == ["a"]
    assert result.snapshots[0].warnings == ["warning a"]
    assert result.warnings == ["batch warning"]
    assert [task.status for task in service.repository.load_tasks(record.run_id)] == [
        TaskStatus.failed,
        TaskStatus.cancelled,
    ]


def test_refresh_run_keeps_accepted_unchanged_task_diagnostics(tmp_path, runs_dir, monkeypatch):
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="s1",
            remote_dir="/remote/jobs",
            command_template="bash {name}",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/jobs/a.sh")],
        ),
        run_id="run_refresh_unchanged",
    )
    original = service.repository.load_tasks(record.run_id)
    snapshot = TaskStatusSnapshot(
        task_id="a",
        batch_id=record.run_id,
        previous_status=TaskStatus.uploaded.value,
        recovered_status=TaskStatus.uploaded.value,
        warnings=["accepted task warning"],
    )
    failure = FailureRecord(
        task_id="a",
        batch_id=record.run_id,
        stage="runtime",
        reason="accepted task diagnostic",
    )
    refresh_result = StatusRefreshResult(
        batch_id=record.run_id,
        task_count=1,
        snapshots=[snapshot],
        failures=[failure],
    )
    monkeypatch.setattr(
        "jobdesk_app.remote.status_refresh.refresh_task_statuses",
        lambda *_args, **_kwargs: (refresh_result, original),
    )

    result = service.refresh_run(record.run_id, object())

    assert result.changed_count == 0
    assert result.snapshots == [snapshot]
    assert result.snapshots[0].warnings == ["accepted task warning"]
    assert result.failures == [failure]


def test_create_run_rejects_relative_remote_dir(tmp_path, runs_dir):
    from jobdesk_app.remote.errors import RemotePathError

    spec = RunSpec(
        server_id="s1",
        remote_dir="relative/path",
        command_template="g16 {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/a.gjf")],
    )
    with pytest.raises(RemotePathError):
        RunService(tmp_path, runs_dir=runs_dir).create_run(spec, run_id="rel")


def test_create_run_rejects_remote_source_with_parent_ref(tmp_path, runs_dir):
    from jobdesk_app.remote.errors import RemotePathError

    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/../etc/passwd")],
    )
    with pytest.raises(RemotePathError):
        RunService(tmp_path, runs_dir=runs_dir).create_run(spec, run_id="parref")


def test_download_completed_rejects_backslash_result_traversal(tmp_path, runs_dir):
    from unittest.mock import MagicMock

    spec = RunSpec(
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=1,
        mode=RunMode.selected_files,
        sources=[RunSource("/remote/jobs/a.gjf")],
        result_templates=["..\\evil.txt"],
    )
    svc = RunService(tmp_path, runs_dir=runs_dir)
    record = svc.create_run(spec, run_id="bsrun")
    tasks = svc.repository.load_tasks(record.run_id)
    for t in tasks:
        t.status = TaskStatus.remote_completed
    replace_tasks_for_test(svc.repository, record.run_id, tasks)

    sftp = MagicMock()
    _, failures = svc.download_completed("bsrun", sftp, patterns=["*.log"])

    sftp.download_file.assert_not_called()
    assert failures
    assert svc.repository.load_tasks(record.run_id)[0].status == TaskStatus.remote_completed


def test_retry_legacy_imports_delegates_to_repository(tmp_path, monkeypatch) -> None:
    service = RunService(tmp_path, runs_dir=tmp_path / "runs")
    expected = service.migration_errors()
    retry = MagicMock(return_value=expected)
    monkeypatch.setattr(service.repository, "retry_legacy_imports", retry)

    assert service.retry_legacy_imports() == expected
    retry.assert_called_once_with()
