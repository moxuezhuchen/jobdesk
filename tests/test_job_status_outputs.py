"""M6 测试: core/outputs.py — write_job_status + failures。"""

import tempfile
from pathlib import Path
from datetime import datetime

from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.status import TaskStatusSnapshot
from jobdesk_app.core.models import FailureRecord
from jobdesk_app.core.outputs import (
    write_job_status,
    write_all_failures,
    _JOB_STATUS_COLUMNS,
    read_final_results_tsv,
)


class TestJobStatusOutput:
    def test_write_job_status_column_order(self):
        tasks = [
            TaskRecord(
                task_id="t1", batch_id="b1",
                task_files=["in/t1.gjf"], remote_job_dir="/r/t1",
                remote_task_files=["t1.gjf"], rendered_command="cmd",
                status=TaskStatus.running,
                submitted_at=datetime(2026, 5, 11, 12, 0),
            )
        ]
        snapshots = [
            TaskStatusSnapshot(
                task_id="t1", batch_id="b1",
                previous_status="submitted", recovered_status="running",
                remote_status_marker="running", remote_exit_code=None,
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "job_status.tsv"
            write_job_status(path, tasks, snapshots)
            lines = path.read_text(encoding="utf-8").split("\n")
            header = lines[0].split("\t")
            assert header == _JOB_STATUS_COLUMNS

    def test_write_job_status_content(self):
        tasks = [
            TaskRecord(
                task_id="t1", batch_id="b1", group_key="g",
                task_files=["in/t1.gjf"], remote_job_dir="/r/t1",
                remote_task_files=["t1.gjf"], rendered_command="cmd",
                status=TaskStatus.running, error_message="test err",
                submitted_at=datetime(2026, 5, 11, 12, 0),
            )
        ]
        snapshots = [
            TaskStatusSnapshot(
                task_id="t1", batch_id="b1",
                previous_status="submitted", recovered_status="running",
                remote_status_marker="running", remote_exit_code=0,
                warnings=["warn1", "warn2"],
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "job_status.tsv"
            write_job_status(path, tasks, snapshots)
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2  # header + 1 row
            data = lines[1].split("\t")
            assert data[0] == "b1"
            assert data[1] == "t1"
            assert data[2] == "g"
            assert data[3] == "running"
            assert data[4] == "submitted"
            assert data[5] == "true"  # changed
            assert data[10] == "running"  # remote_status_marker (shifted)
            assert data[11] == "0"       # remote_exit_code
            assert data[12] == "test err" # error_message
            assert "warn1" in data[13]    # warnings

    def test_write_all_failures(self):
        failures = [
            FailureRecord(task_id="t1", batch_id="b1", stage="runtime",
                          reason="failed marker", source_file="/r/t1/.jobdesk_status",
                          context="log tail"),
            FailureRecord(task_id="t2", batch_id="b1", stage="analysis",
                          reason="no match", source_file="out.log"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "failures.tsv"
            write_all_failures(path, failures)
            content = path.read_text(encoding="utf-8").split("\n")
            assert len(content) >= 3  # header + 2 rows
            assert "runtime" in content[1]
            assert "analysis" in content[2]
