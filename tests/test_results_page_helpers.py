import csv
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="PySide6 not installed")

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.manifest import Manifest, TaskRecord
from jobdesk_app.gui.pages.results_page import (
    build_results_diagnostics,
    load_enriched_results_rows,
)


def test_load_enriched_results_rows_joins_manifest_metadata(tmp_path):
    final_results = tmp_path / "final_results.tsv"
    manifest = tmp_path / "manifest.tsv"
    with open(final_results, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow([
            "batch_id", "task_id", "group_key", "result_id", "field_name",
            "value", "value_type", "unit", "source_file", "is_best_for_task",
            "relative_group", "relative_global",
        ])
        writer.writerow(["b1", "t1", "", "", "energy", "-1.23", "float", "", "result.out", "false", "", ""])

    Manifest.write(manifest, [
        TaskRecord(
            task_id="t1",
            batch_id="b1",
            remote_job_dir="/r/b1/t1",
            discovery_name="g16_jobs",
            execution_profile="g16",
            server_id="srv1",
            remote_work_dir="/r",
            status=TaskStatus.downloaded,
        )
    ])

    header, rows = load_enriched_results_rows(final_results, manifest)

    assert "discovery_name" in header
    assert "execution_profile" in header
    assert "server_id" in header
    assert "status" in header
    row = dict(zip(header, rows[0]))
    assert row["task_id"] == "t1"
    assert row["field_name"] == "energy"
    assert row["discovery_name"] == "g16_jobs"
    assert row["execution_profile"] == "g16"
    assert row["server_id"] == "srv1"
    assert row["status"] == "downloaded"


def test_build_results_diagnostics_reports_key_files(tmp_path):
    batch_dir = tmp_path / ".jobdesk" / "batches" / "b1"
    result_dir = tmp_path / "results" / "b1"
    batch_dir.mkdir(parents=True)
    result_dir.mkdir(parents=True)
    (batch_dir / "manifest.tsv").write_text("x\n", encoding="utf-8")
    (batch_dir / "failures.tsv").write_text("x\n", encoding="utf-8")
    (result_dir / "final_results.tsv").write_text("x\n", encoding="utf-8")

    diagnostics = build_results_diagnostics(batch_dir, result_dir)

    assert diagnostics["manifest.tsv"].endswith("present")
    assert diagnostics["failures.tsv"].endswith("present")
    assert diagnostics["final_results.tsv"].endswith("present")
    assert diagnostics["job_status.tsv"].endswith("missing")
