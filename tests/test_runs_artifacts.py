"""Pure tests for Runs artifact paths and immutable worker payloads."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobdesk_app.application.runs_artifacts import (
    ComparePayload,
    PreviewPayload,
    PreviewRequest,
    UncertainTaskPayload,
    build_preview_payload,
    choose_existing_artifact,
    is_preview_too_large,
    resolve_run_artifacts,
)


def test_bound_artifact_paths_do_not_fall_back_to_other_roots(tmp_path: Path):
    bound = tmp_path / "bound"
    gui = tmp_path / "gui"
    paths = resolve_run_artifacts("run-b", str(bound), gui, default_local_folder=str(tmp_path / "downloads"))

    assert paths.bound_workspace is True
    assert paths.workspace == bound
    assert paths.download_dir == bound / "results" / "run-b"
    assert paths.search_dirs == (bound / "results" / "run-b",)


def test_legacy_artifact_paths_prioritize_run_owned_then_roots(tmp_path: Path):
    workspace = tmp_path / "workspace"
    default = tmp_path / "downloads"
    paths = resolve_run_artifacts("run-a", "", workspace, default_local_folder=default)

    assert paths.bound_workspace is False
    assert paths.search_dirs[:3] == (
        workspace / "results" / "run-a",
        default / "results" / "run-a",
        workspace,
    )


def test_artifact_selection_and_size_guard_are_fail_closed(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "final_results.tsv").write_text("small", encoding="utf-8")
    chosen = second / "analysis_preview.tsv"
    chosen.write_text("header\nrow\n", encoding="utf-8")

    assert (
        choose_existing_artifact((first, second), ("final_results.tsv", "analysis_preview.tsv"), minimum_bytes=5)
        == chosen
    )
    assert not is_preview_too_large(chosen)
    assert is_preview_too_large(chosen, max_bytes=1)


def test_preview_payload_freezes_rows_and_discards_mutable_record():
    record = SimpleNamespace(run_id="run-1")
    payload = PreviewPayload.from_legacy(("analysis", [["task", "file"]], "Preview", True))

    assert payload.kind == "analysis"
    assert payload.rows == (("task", "file"),)
    assert payload.stale is True
    with pytest.raises(FrozenInstanceError):
        payload.kind = "empty"  # type: ignore[misc]

    confflow = PreviewPayload.from_legacy(("confflow", record, Path("results")))
    assert confflow.run_id == "run-1"
    assert confflow.result_dir == Path("results")


def test_preview_payload_deep_freezes_uncertain_task_projection():
    task = SimpleNamespace(
        task_id="task-1",
        status=SimpleNamespace(value="uncertain"),
        error_message="response lost",
    )

    payload = PreviewPayload.from_legacy(("uncertain", [task]))
    task.task_id = "mutated-after-worker-return"
    task.error_message = "mutated"

    assert payload.tasks == (UncertainTaskPayload("task-1", "uncertain", "response lost"),)
    assert isinstance(payload.tasks[0], UncertainTaskPayload)
    with pytest.raises(FrozenInstanceError):
        payload.tasks[0].task_id = "mutated"  # type: ignore[misc]


def test_preview_and_compare_payloads_copy_nested_display_values():
    rows = [{"run_id": "a", "energy": -1.2}]
    fields = ["run_id", "energy"]
    comparison = SimpleNamespace(field_names=fields, rows=rows)

    preview = PreviewPayload(kind="analysis", rows=[["task", {"value": 1}]])
    compare = ComparePayload.from_comparison(comparison)
    rows[0]["energy"] = "changed"
    fields.append("new-field")

    assert preview.rows == (("task", "{'value': 1}"),)
    assert compare.headers == ("run_id", "energy")
    assert compare.rows == (("a", "-1.2"),)
    with pytest.raises(TypeError):
        compare.rows[0][0] = "mutated"  # type: ignore[index]


def test_compare_payload_freezes_display_cells():
    payload = ComparePayload.from_comparison(
        SimpleNamespace(
            field_names=["run_id", "energy"],
            rows=[{"run_id": "a", "energy": -1.2}],
        )
    )

    assert payload.headers == ("run_id", "energy")
    assert payload.rows == (("a", "-1.2"),)
    with pytest.raises(FrozenInstanceError):
        payload.headers = ()  # type: ignore[misc]


def test_preview_request_is_frozen_and_builds_tsv_payload(tmp_path: Path):
    result_dir = tmp_path / "results" / "run-1"
    result_dir.mkdir(parents=True)
    (result_dir / "final_results.tsv").write_text("header\tvalue\n" + "x\t1\n" * 20, encoding="utf-8")
    request = PreviewRequest(
        run_id="run-1",
        result_dirs=(result_dir,),
        download_dir=result_dir,
        tsv_label="Preview",
    )

    payload = build_preview_payload(request)

    assert payload.kind == "tsv"
    assert payload.artifact_path == result_dir / "final_results.tsv"
    assert payload.label == "Preview - final_results.tsv"
    with pytest.raises(FrozenInstanceError):
        request.run_id = "mutated"  # type: ignore[misc]


def test_preview_builder_copies_task_metadata_into_analysis_payload(tmp_path: Path, monkeypatch):
    result_dir = tmp_path / "results" / "run-1"
    result_dir.mkdir(parents=True)
    output = result_dir / "water.log"
    output.write_text("output", encoding="utf-8")
    task = UncertainTaskPayload(
        task_id="water",
        status="downloaded",
        remote_task_files=("/remote/water.gjf",),
    )

    parsed = SimpleNamespace(
        final_energy_au=-76.1,
        gibbs_au=None,
        zpe_au=None,
        imaginary_freq_count=0,
    )
    monkeypatch.setattr("jobdesk_app.core.parsers.gaussian.parse_gaussian_log", lambda _path: parsed)
    monkeypatch.setattr("jobdesk_app.core.parsers.gaussian.diagnose_gaussian_result", lambda _result: "OK")

    payload = build_preview_payload(
        PreviewRequest(
            run_id="run-1",
            result_dirs=(result_dir,),
            download_dir=result_dir,
            tasks=(task,),
        )
    )

    assert payload.kind == "analysis"
    assert payload.rows[0][:4] == ("water", "water.log", "Gaussian", "-76.100000")
    assert payload.tasks[0].task_id == "water"
    assert payload.tasks[0].remote_task_files == ("/remote/water.gjf",)
    assert payload.tasks[0].task_dir == result_dir / "water"
