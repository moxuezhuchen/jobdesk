from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus
from jobdesk_app.services.run_service import RunService
from tests.repository_helpers import replace_tasks_for_test


def _service_with_task(
    tmp_path: Path,
    status: TaskStatus = TaskStatus.running,
    run_id: str = "progress-run",
) -> tuple[RunService, object]:
    runs_dir = tmp_path / "runs"
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
            result_templates=[
                "water.txt",
                "water_confflow_work/.workflow_state.json",
                "water_confflow_work/workflow_stats.json",
            ],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id=run_id,
    )
    task = service.repository.load_tasks(run_id)[0]
    task.status = status
    replace_tasks_for_test(service.repository, run_id, [task])
    return service, task


def _progress_path(tmp_path: Path, run_id: str = "progress-run") -> Path:
    return tmp_path / "runs" / run_id / "progress" / "water_confflow_work" / ".workflow_state.json"


class FakeProgressSFTP:
    def __init__(self, files: dict[str, bytes], *, stat_error: Exception | None = None) -> None:
        self.files = files
        self.stat_error = stat_error
        self.downloads: list[str] = []

    def stat(self, remote_path: str):
        if self.stat_error is not None:
            raise self.stat_error
        content = self.files.get(remote_path)
        return None if content is None else SimpleNamespace(st_size=len(content))

    def download_file(self, remote_path: str, local_path: Path, **_kwargs):
        self.downloads.append(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.files[remote_path])
        return TransferRecord(
            direction=TransferDirection.download,
            local_path=str(local_path),
            remote_path=remote_path,
            size_bytes=len(self.files[remote_path]),
            status=TransferStatus.transferred,
        )


def test_sync_progress_downloads_only_declared_files_for_running_task(tmp_path):
    service, task = _service_with_task(tmp_path)
    sftp = FakeProgressSFTP(
        {
            task.remote_state_path: b'{"step": "opt"}',
            task.remote_stats_path: b'{"completed": 1}',
            task.remote_log_path: b"must not download",
            task.remote_result_paths[0]: b"must not download",
        }
    )

    records, failures = service.sync_progress("progress-run", sftp)

    assert failures == []
    assert sftp.downloads == [task.remote_state_path, task.remote_stats_path]
    assert len(records) == 2
    assert _progress_path(tmp_path).read_text() == '{"step": "opt"}'
    assert _progress_path(tmp_path).with_name("workflow_stats.json").read_text() == '{"completed": 1}'
    assert service.repository.load_tasks("progress-run")[0].status == TaskStatus.running


def test_same_basename_progress_checkpoints_are_owned_by_each_run(tmp_path):
    """Separate submissions with ``water.xyz`` cannot share live state."""
    first_service, first_task = _service_with_task(tmp_path, run_id="submission-a")
    second_service, second_task = _service_with_task(tmp_path, run_id="submission-b")

    first_records, first_failures = first_service.sync_progress(
        "submission-a",
        FakeProgressSFTP(
            {
                first_task.remote_state_path: b'{"steps": {"a": {"name": "first", "status": "completed"}}}',
                first_task.remote_stats_path: b'{"steps": []}',
            }
        ),
    )
    second_records, second_failures = second_service.sync_progress(
        "submission-b",
        FakeProgressSFTP(
            {
                second_task.remote_state_path: b'{"steps": {"b": {"name": "second", "status": "submitted"}}}',
                second_task.remote_stats_path: b'{"steps": []}',
            }
        ),
    )

    first_path = _progress_path(tmp_path, "submission-a")
    second_path = _progress_path(tmp_path, "submission-b")
    assert first_failures == second_failures == []
    assert len(first_records) == len(second_records) == 2
    assert first_path != second_path
    assert '"first"' in first_path.read_text(encoding="utf-8")
    assert '"second"' in second_path.read_text(encoding="utf-8")


def test_sync_progress_missing_remote_files_are_pending_not_failures(tmp_path):
    service, _task = _service_with_task(tmp_path, TaskStatus.submitted)
    sftp = FakeProgressSFTP({})

    records, failures = service.sync_progress("progress-run", sftp)

    assert records == []
    assert failures == []
    assert sftp.downloads == []


def test_sync_progress_accepts_uncertain_task_that_may_be_running_remotely(tmp_path):
    service, task = _service_with_task(tmp_path, TaskStatus.uncertain)
    sftp = FakeProgressSFTP(
        {
            task.remote_state_path: b'{"step": "opt"}',
            task.remote_stats_path: b'{"completed": 1}',
        }
    )

    records, failures = service.sync_progress("progress-run", sftp)

    assert failures == []
    assert len(records) == 2
    assert sftp.downloads == [task.remote_state_path, task.remote_stats_path]
    assert service.repository.load_tasks("progress-run")[0].status == TaskStatus.uncertain


def test_sync_progress_preserves_previous_file_on_malformed_json(tmp_path):
    service, task = _service_with_task(tmp_path)
    local_state = _progress_path(tmp_path)
    local_state.parent.mkdir(parents=True)
    local_state.write_text('{"old": true}', encoding="utf-8")
    sftp = FakeProgressSFTP(
        {
            task.remote_state_path: b'{"broken":',
            task.remote_stats_path: b'{"completed": 1}',
        }
    )

    records, failures = service.sync_progress("progress-run", sftp)

    assert len(records) == 1
    assert len(failures) == 1
    assert "malformed progress JSON" in failures[0][1]
    assert local_state.read_text(encoding="utf-8") == '{"old": true}'
    assert list(local_state.parent.glob(".*.progress")) == []


def test_sync_progress_preserves_previous_file_on_partial_transfer(tmp_path):
    service, task = _service_with_task(tmp_path)
    local_state = _progress_path(tmp_path)
    local_state.parent.mkdir(parents=True)
    local_state.write_text('{"old": true}', encoding="utf-8")

    class PartialSFTP(FakeProgressSFTP):
        def download_file(self, remote_path: str, local_path: Path, **_kwargs):
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text("partial", encoding="utf-8")
            raise ConnectionError("transfer interrupted")

    sftp = PartialSFTP({task.remote_state_path: b"{}"})

    records, failures = service.sync_progress("progress-run", sftp)

    assert records == []
    assert len(failures) == 1
    assert "transfer interrupted" in failures[0][1]
    assert local_state.read_text(encoding="utf-8") == '{"old": true}'
    assert list(local_state.parent.glob(".*.progress")) == []


def test_sync_progress_surfaces_permission_error_and_skips_completed_task(tmp_path):
    service, _task = _service_with_task(tmp_path)
    records, failures = service.sync_progress(
        "progress-run",
        FakeProgressSFTP({}, stat_error=PermissionError("permission denied")),
    )
    assert records == []
    assert len(failures) == 2
    assert all("permission denied" in message for _task_id, message in failures)

    service, task = _service_with_task(tmp_path / "completed", TaskStatus.remote_completed)
    sftp = FakeProgressSFTP({task.remote_state_path: b"{}", task.remote_stats_path: b"{}"})
    assert service.sync_progress("progress-run", sftp) == ([], [])
    assert sftp.downloads == []


def test_running_task_cannot_trigger_full_result_download(tmp_path):
    service, task = _service_with_task(tmp_path)
    sftp = FakeProgressSFTP({task.remote_result_paths[0]: b"result"})

    records, failures = service.download_completed("progress-run", sftp, ["*.txt"])

    assert records == []
    assert failures == []
    assert sftp.downloads == []
