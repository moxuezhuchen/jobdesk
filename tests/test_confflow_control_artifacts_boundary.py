from __future__ import annotations

import hashlib
import stat
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.transfer import TransferDirection, TransferRecord, TransferStatus
from jobdesk_app.services.confflow_control import ControlArtifact
from jobdesk_app.services.confflow_control_artifacts import ControlArtifactDownloader


@dataclass
class _Task:
    task_id: str
    remote_workflow_dir: str
    status: TaskStatus = TaskStatus.remote_completed

    def model_copy(self, *, update: dict[str, object], deep: bool) -> _Task:
        copied = deepcopy(self) if deep else self
        for key, value in update.items():
            setattr(copied, key, value)
        return copied


class _Service:
    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.tasks = [_Task("task-1", "/remote/run/task-1")]

    def load_tasks(self, run_id: str):
        return list(self.tasks)

    def mutate_tasks(self, run_id: str, mutation):
        self.tasks = mutation(self.tasks)


class _FakeSFTP:
    def __init__(self, content: bytes, *, symlink_path: str | None = None, bad_metadata: bool = False) -> None:
        self.content = content
        self.symlink_path = symlink_path
        self.bad_metadata = bad_metadata
        self.downloaded_to: Path | None = None

    def lstat(self, remote_path: str):
        if remote_path == self.symlink_path:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        if self.bad_metadata:
            return SimpleNamespace(st_mode=None)
        return SimpleNamespace(st_mode=stat.S_IFREG)

    def stat(self, remote_path: str):
        if self.bad_metadata:
            return SimpleNamespace(st_size="not-an-int")
        return SimpleNamespace(st_size=len(self.content))

    def download_file(self, remote_path: str, local_path: Path, **kwargs) -> TransferRecord:
        self.downloaded_to = local_path
        local_path.write_bytes(self.content)
        return TransferRecord(
            TransferDirection.download,
            str(local_path),
            remote_path,
            size_bytes=len(self.content),
            status=TransferStatus.transferred,
        )


def _artifact(path: str, content: bytes, *, terminal: str = "task-1") -> ControlArtifact:
    return ControlArtifact(terminal, path, hashlib.sha256(content).hexdigest(), len(content), "test")


def _downloader(tmp_path: Path, sftp: _FakeSFTP) -> tuple[ControlArtifactDownloader, _Service]:
    service = _Service(tmp_path)
    return ControlArtifactDownloader(service, sftp), service


def test_rejects_relative_path_traversal_before_sftp_access(tmp_path: Path) -> None:
    content = b"unsafe"
    sftp = _FakeSFTP(content)
    downloader, service = _downloader(tmp_path, sftp)

    transfers, failures = downloader.download("run-1", (_artifact("../outside.txt", content),), [])

    assert transfers == []
    assert failures and "artifact path is unsafe" in failures[0][1]
    assert sftp.downloaded_to is None
    assert service.tasks[0].status is TaskStatus.remote_completed
    assert not (tmp_path / "results" / "outside.txt").exists()


@pytest.mark.parametrize("kind", ["symlink", "metadata"])
def test_rejects_remote_symlink_or_invalid_metadata(tmp_path: Path, kind: str) -> None:
    content = b"remote"
    artifact = _artifact("result.json", content)
    sftp = _FakeSFTP(
        content,
        symlink_path="/remote/run/task-1/result.json" if kind == "symlink" else None,
        bad_metadata=kind == "metadata",
    )
    downloader, service = _downloader(tmp_path, sftp)

    transfers, failures = downloader.download("run-1", (artifact,), [])

    assert transfers == []
    assert failures
    assert sftp.downloaded_to is None
    assert service.tasks[0].status is TaskStatus.remote_completed
    assert not (tmp_path / "results").exists()


def test_digest_mismatch_does_not_replace_existing_target(tmp_path: Path) -> None:
    expected = b"expected"
    sftp = _FakeSFTP(b"tampered")
    downloader, service = _downloader(tmp_path, sftp)
    target = tmp_path / "results" / "run-1" / "task-1" / "result.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous")

    transfers, failures = downloader.download("run-1", (_artifact("result.json", expected),), [])

    assert transfers == []
    assert failures and "artifact integrity mismatch" in failures[0][1]
    assert target.read_bytes() == b"previous"
    assert service.tasks[0].status is TaskStatus.remote_completed


def test_success_stages_then_atomically_places_and_marks_downloaded(tmp_path: Path) -> None:
    content = b"manifest output\n"
    sftp = _FakeSFTP(content)
    downloader, service = _downloader(tmp_path, sftp)

    transfers, failures = downloader.download("run-1", (_artifact("nested/result.json", content),), ["*.json"])

    target = tmp_path / "results" / "run-1" / "task-1" / "nested" / "result.json"
    assert len(transfers) == 1
    assert failures == []
    assert target.read_bytes() == content
    assert sftp.downloaded_to is not None
    assert sftp.downloaded_to.parent != target.parent
    assert list(target.parent.glob("jobdesk-control-download-*")) == []
    assert service.tasks[0].status is TaskStatus.downloaded
