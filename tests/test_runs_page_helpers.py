from jobdesk_app.gui.pages.runs_page import (
    format_run_row,
    format_run_status_summary,
    parse_download_patterns,
    run_log_paths,
)
from jobdesk_app.services.run_service import RunRecord


def test_format_run_status_summary():
    assert format_run_status_summary({"uploaded": 2, "submitted": 1}) == "uploaded=2 | submitted=1"
    assert format_run_status_summary({}) == "(none)"


def test_format_run_row(tmp_path):
    record = RunRecord(
        run_id="run001",
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=4,
        mode="selected_files",
        created_at="2026-05-13T10:00:00",
        run_dir=tmp_path,
        manifest_path=tmp_path / "manifest.tsv",
        batch_path=tmp_path / "batch.json",
        status_summary={"uploaded": 2},
    )

    assert format_run_row(record) == [
        "run001",
        "s1",
        "/remote/jobs",
        "selected_files",
        "4",
        "uploaded=2",
        "g16 {name}",
        "2026-05-13T10:00:00",
    ]


def test_parse_download_patterns_accepts_commas_and_newlines():
    assert parse_download_patterns("result.log, output.out\nsummary.txt") == [
        "result.log",
        "output.out",
        "summary.txt",
    ]
    assert parse_download_patterns(" , \n ") == []


def test_run_log_paths_describe_expected_remote_logs(tmp_path):
    record = RunRecord(
        run_id="run001",
        server_id="s1",
        remote_dir="/remote/jobs",
        command_template="g16 {name}",
        max_parallel=4,
        mode="selected_files",
        created_at="2026-05-13T10:00:00",
        run_dir=tmp_path,
        manifest_path=tmp_path / "manifest.tsv",
        batch_path=tmp_path / "batch.json",
        status_summary={"uploaded": 2},
    )

    assert run_log_paths(record) == [
        "/remote/jobs/.jobdesk_runs/run001/.jobdesk_submit.log",
        "/remote/jobs/.jobdesk_runs/run001/.jobdesk_submit.err",
    ]
