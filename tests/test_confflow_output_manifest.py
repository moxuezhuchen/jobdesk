"""Safety coverage for ConfFlow v1.4.6 output-manifest downloads."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobdesk_app.core.confflow_output_manifest import OutputManifestError, parse_output_manifest
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.run import RunMode, RunSource, RunSpec, WorkflowKind
from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus
from jobdesk_app.services.run_service import RunService
from tests.repository_helpers import replace_tasks_for_test


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "runs"
    path.mkdir()
    return path


def _manifest(paths: list[str]) -> dict[str, object]:
    return {
        "content_schema": "confflow.output_manifest.v1",
        "terminals": {"final": paths},
    }


def _workflow_service(tmp_path: Path, runs_dir: Path) -> tuple[RunService, str]:
    service = RunService(tmp_path, runs_dir=runs_dir)
    record = service.create_run(
        RunSpec(
            server_id="wsl",
            remote_dir="/remote/project",
            command_template="confflow {name} -c workflow.yaml -w {basename}_confflow_work",
            max_parallel=1,
            mode=RunMode.selected_files,
            sources=[RunSource("/remote/project/water.xyz")],
            workflow_kind=WorkflowKind.confflow,
        ),
        run_id="manifest-run",
    )
    tasks = service.repository.load_tasks(record.run_id)
    tasks[0].status = TaskStatus.remote_completed
    tasks[0].remote_result_files = ["legacy-must-not-download.txt"]
    replace_tasks_for_test(service.repository, record.run_id, tasks)
    return service, record.run_id


class _ManifestSFTP:
    def __init__(self, manifest: dict[str, object], *, symlink_path: str = "") -> None:
        self.manifest = manifest
        self.symlink_path = symlink_path
        self.requested: list[str] = []

    def lstat(self, remote_path: str):
        mode = stat.S_IFLNK if remote_path == self.symlink_path else stat.S_IFREG
        if remote_path in {"/remote", "/remote/project", "/remote/project/water_confflow_work"}:
            mode = stat.S_IFDIR
        return SimpleNamespace(st_mode=mode)

    def download_file(self, remote_path: str, local_path: Path, **_kwargs):
        self.requested.append(remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if remote_path.endswith("/output_manifest.json"):
            local_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        else:
            local_path.write_text("output", encoding="utf-8")
        return TransferRecord(
            TransferDirection.download,
            str(local_path),
            remote_path,
            status=TransferStatus.transferred,
        )


def test_parse_output_manifest_requires_schema_and_safe_unique_relative_paths(tmp_path: Path) -> None:
    parsed = parse_output_manifest(_manifest(["final/output.xyz", "reports/final.txt"]), work_dir=tmp_path)

    assert parsed.paths == ("final/output.xyz", "reports/final.txt")
    assert parsed.terminals["final"] == ("final/output.xyz", "reports/final.txt")

    for bad_path in ("/etc/passwd", "../outside", "one/../two", "one/./two", "one//two", r"one\\two"):
        with pytest.raises(OutputManifestError, match="unsafe output manifest path"):
            parse_output_manifest(_manifest([bad_path]))
    with pytest.raises(OutputManifestError, match="at least one terminal"):
        parse_output_manifest({"content_schema": "confflow.output_manifest.v1", "terminals": {}})
    with pytest.raises(OutputManifestError, match="must not be empty"):
        parse_output_manifest(_manifest([]))
    with pytest.raises(OutputManifestError, match="duplicate target"):
        parse_output_manifest({"content_schema": "confflow.output_manifest.v1", "terminals": {"a": ["a.xyz"], "b": ["a.xyz"]}})
    with pytest.raises(OutputManifestError, match="unsupported output manifest schema"):
        parse_output_manifest({"content_schema": "confflow.output_manifest.v2", "terminals": {}})


def test_parse_output_manifest_accepts_146_producer_relative_terminal_path() -> None:
    """The 1.4.6 hotfix publishes terminal artifacts relative to the work dir."""
    parsed = parse_output_manifest(
        {
            "content_schema": "confflow.output_manifest.v1",
            "terminals": {"g16_opt": ["g16_opt/output.xyz"]},
        }
    )

    assert parsed.terminals == {"g16_opt": ("g16_opt/output.xyz",)}


def test_workflow_download_uses_only_safe_manifest_declared_outputs(tmp_path: Path, runs_dir: Path) -> None:
    service, run_id = _workflow_service(tmp_path, runs_dir)
    sftp = _ManifestSFTP(_manifest(["final/output.xyz", "reports/final.txt"]))

    records, failures = service.download_completed(run_id, sftp, ["*.xyz"])

    assert failures == []
    assert len(records) == 2  # manifest plus the selected declared output
    assert sftp.requested == [
        "/remote/project/water_confflow_work/output_manifest.json",
        "/remote/project/water_confflow_work/final/output.xyz",
    ]
    assert not any("legacy-must-not-download" in path for path in sftp.requested)
    result_root = tmp_path / "results" / run_id / "water_confflow_work"
    assert (result_root / "output_manifest.json").exists()
    assert (result_root / "final" / "output.xyz").exists()
    assert service.repository.load_tasks(run_id)[0].status == TaskStatus.downloaded


@pytest.mark.parametrize("unsafe_path", ["../secret.txt", "/etc/passwd", r"nested\\escape.txt"])
def test_workflow_download_rejects_unsafe_manifest_before_output_transfer(tmp_path: Path, runs_dir: Path, unsafe_path: str) -> None:
    service, run_id = _workflow_service(tmp_path, runs_dir)
    sftp = _ManifestSFTP(_manifest([unsafe_path]))

    records, failures = service.download_completed(run_id, sftp, ["*"])

    assert records == []  # malformed manifest does not become an accepted transfer record
    assert len(failures) == 1
    assert "unsafe output manifest path" in failures[0][1]
    assert service.repository.load_tasks(run_id)[0].status == TaskStatus.remote_completed


def test_workflow_download_rejects_remote_symlink_target(tmp_path: Path, runs_dir: Path) -> None:
    service, run_id = _workflow_service(tmp_path, runs_dir)
    target = "/remote/project/water_confflow_work/final/output.xyz"
    sftp = _ManifestSFTP(_manifest(["final/output.xyz"]), symlink_path=target)

    records, failures = service.download_completed(run_id, sftp, ["*.xyz"])

    assert len(records) == 1
    assert failures == [("water", f"remote manifest path is a symlink: {target}")]
    assert sftp.requested == ["/remote/project/water_confflow_work/output_manifest.json"]
    assert service.repository.load_tasks(run_id)[0].status == TaskStatus.remote_completed


def test_workflow_download_rejects_unsafe_remote_workflow_directory(tmp_path: Path, runs_dir: Path) -> None:
    service, run_id = _workflow_service(tmp_path, runs_dir)
    tasks = service.repository.load_tasks(run_id)
    tasks[0].remote_workflow_dir = "/remote/project/../escape"
    replace_tasks_for_test(service.repository, run_id, tasks)
    sftp = _ManifestSFTP(_manifest(["final/output.xyz"]))

    records, failures = service.download_completed(run_id, sftp, ["*.xyz"])

    assert records == []
    assert failures == [("water", "ConfFlow workflow directory is unsafe: /remote/project/../escape")]
    assert sftp.requested == []


def test_workflow_download_rejects_conflicting_manifest_target_between_tasks(tmp_path: Path, runs_dir: Path) -> None:
    service, run_id = _workflow_service(tmp_path, runs_dir)
    tasks = service.repository.load_tasks(run_id)
    second = tasks[0].model_copy(
        update={
            "task_id": "water-2",
            "remote_job_dir": "/remote/project/.jobdesk_runs/manifest-run/water-2",
            "status": TaskStatus.remote_completed,
        }
    )
    replace_tasks_for_test(service.repository, run_id, [tasks[0], second])
    sftp = _ManifestSFTP(_manifest(["final/output.xyz"]))

    records, failures = service.download_completed(run_id, sftp, ["*.xyz"])

    assert len(records) == 2
    assert failures == [("water-2", "output manifest conflicts with another task target")]
    persisted = {task.task_id: task.status for task in service.repository.load_tasks(run_id)}
    assert persisted == {"water": TaskStatus.downloaded, "water-2": TaskStatus.remote_completed}
