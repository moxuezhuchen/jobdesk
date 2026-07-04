from __future__ import annotations

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.core.manifest_ops import reset_all_to_uploaded


@pytest.mark.parametrize("status", [TaskStatus.submitting, TaskStatus.uncertain])
def test_reset_all_rejects_in_flight_submission_states(tmp_path, status):
    manifest_path = tmp_path / "manifest.tsv"
    Manifest.write(
        manifest_path,
        [
            TaskRecord(
                task_id="task-1",
                batch_id="run-1",
                remote_job_dir="/remote/task-1",
                status=status,
            )
        ],
    )

    with pytest.raises(ValueError, match="cannot rerun active remote tasks: task-1"):
        reset_all_to_uploaded(manifest_path)

    assert Manifest.read(manifest_path)[0].status == status


@pytest.mark.parametrize(
    "status",
    [TaskStatus.local_ready, TaskStatus.uploaded, TaskStatus.failed, TaskStatus.downloaded],
)
def test_reset_all_preserves_legacy_non_active_rerun_behavior(tmp_path, status):
    manifest_path = tmp_path / "manifest.tsv"
    Manifest.write(
        manifest_path,
        [
            TaskRecord(
                task_id="task-1",
                batch_id="run-1",
                remote_job_dir="/remote/task-1",
                status=status,
                remote_job_id="old-job",
                error_message="old error",
            )
        ],
    )

    assert reset_all_to_uploaded(manifest_path) == 1
    task = Manifest.read(manifest_path)[0]
    assert task.status == TaskStatus.uploaded
    assert task.remote_job_id is None
    assert task.error_message is None
