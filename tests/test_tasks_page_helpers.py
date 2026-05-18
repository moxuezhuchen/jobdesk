import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.core.models import BatchSummary
from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus
from jobdesk_app.gui.pages.tasks_page import (
    build_button_reasons,
    format_batch_header,
    format_preflight_report,
    format_transfer_summary,
    summarize_task_statuses,
)
from jobdesk_app.services.preflight import PreflightIssue, PreflightReport


def test_summarize_task_statuses_counts_manifest_statuses():
    tasks = [
        TaskRecord(task_id="t1", batch_id="b1", remote_job_dir="/r/t1", status=TaskStatus.local_ready),
        TaskRecord(task_id="t2", batch_id="b1", remote_job_dir="/r/t2", status=TaskStatus.uploaded),
        TaskRecord(task_id="t3", batch_id="b1", remote_job_dir="/r/t3", status=TaskStatus.uploaded),
    ]

    summary = summarize_task_statuses(tasks)

    assert summary["local_ready"] == 1
    assert summary["uploaded"] == 2
    assert summary["running"] == 0


def test_build_button_reasons_reports_next_available_actions():
    reasons = build_button_reasons({TaskStatus.uploaded})

    assert reasons["upload"] == "No local_ready tasks"
    assert reasons["submit"] == ""
    assert reasons["refresh"] == "No submitted or running tasks"
    assert reasons["download"] == "No remote_completed tasks"


def test_format_transfer_summary_counts_transfer_statuses():
    records = [
        TransferRecord(direction=TransferDirection.upload, local_path="a", remote_path="/r/a", status=TransferStatus.transferred),
        TransferRecord(direction=TransferDirection.upload, local_path="b", remote_path="/r/b", status=TransferStatus.skipped),
        TransferRecord(direction=TransferDirection.upload, local_path="c", remote_path="/r/c", status=TransferStatus.failed),
    ]

    summary = format_transfer_summary("Upload", records, failure_count=1)

    assert summary == "Upload complete: 1 transferred, 1 skipped, 1 failed, 1 recorded failures"


def test_format_preflight_report_includes_errors_and_warnings():
    report = PreflightReport(
        errors=[PreflightIssue("missing_binding", "Missing binding.")],
        warnings=[PreflightIssue("no_tasks", "No tasks.", "warning")],
        task_count=0,
        profiles=["shell"],
        servers=[],
    )

    lines = format_preflight_report(report)

    assert lines[0] == "Preflight failed: 1 errors, 1 warnings"
    assert "tasks=0" in lines[1]
    assert "ERROR missing_binding: Missing binding." in lines
    assert "WARNING no_tasks: No tasks." in lines


def test_format_batch_header_shows_profile_server_and_shared_count():
    summary = BatchSummary(
        batch_id="batch_001",
        task_count=3,
        execution_profiles=["g16", "orca"],
        server_ids=["814new"],
        shared_files_count=1,
    )

    header = format_batch_header(summary, manifest_path="C:/p/.jobdesk/batches/batch_001/manifest.tsv")

    assert header == (
        "batch_001 | tasks=3 | profiles=g16, orca | "
        "servers=814new | shared=1 | manifest=C:/p/.jobdesk/batches/batch_001/manifest.tsv"
    )
