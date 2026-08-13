"""Safe, manifest-driven downloads for the ConfFlow control backend."""

from __future__ import annotations

import fnmatch
import hashlib
import posixpath
import stat
import tempfile
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from pathlib import Path, PurePosixPath
from typing import Protocol, TypeVar

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.transfer import TransferRecord, TransferStatus
from jobdesk_app.services.confflow_control import ControlArtifact


class _Task(Protocol):
    task_id: str
    remote_workflow_dir: str | None

    def model_copy(self, *, update: dict[str, object], deep: bool) -> _Task: ...


class _RunService(Protocol):
    workspace_dir: Path

    def load_tasks(self, run_id: str) -> Iterable[_Task]: ...

    def mutate_tasks(self, run_id: str, mutation: Callable[[list[_Task]], list[_Task]]) -> object: ...


class _RemoteMetadata(Protocol):
    st_mode: object
    st_size: object


class _SFTP(Protocol):
    def lstat(self, remote_path: str) -> _RemoteMetadata | None: ...

    def stat(self, remote_path: str) -> _RemoteMetadata | None: ...

    def download_file(
        self,
        remote_path: str,
        local_path: Path,
        overwrite: bool = False,
        skip_if_same_size: bool = True,
    ) -> TransferRecord: ...


_ContextFactory = Callable[..., AbstractContextManager[str]]
_T = TypeVar("_T")


class ControlArtifactDownloader:
    """Download declared control artifacts with fail-closed local placement.

    The service and SFTP objects are injected so this boundary can be tested
    without constructing an SSH client or reaching a live endpoint.
    """

    def __init__(
        self,
        service: _RunService,
        sftp: _SFTP,
        *,
        temporary_directory: _ContextFactory = tempfile.TemporaryDirectory,
        file_digest: Callable[[Path], str] | None = None,
    ) -> None:
        self._service = service
        self._sftp = sftp
        self._temporary_directory = temporary_directory
        self._file_digest = file_digest or _sha256_file

    def download(
        self,
        run_id: str,
        artifacts: tuple[ControlArtifact, ...],
        patterns: list[str],
    ) -> tuple[list[TransferRecord], list[tuple[str, str]]]:
        """Download selected artifacts and return the established result shape."""
        tasks = tuple(self._service.load_tasks(run_id))
        selected = tuple(
            artifact
            for artifact in artifacts
            if not patterns or any(_pattern_matches(artifact.path, pattern) for pattern in patterns)
        )
        if not selected:
            return [], []

        download_base = self._service.workspace_dir / "results" / run_id
        claimed: set[Path] = set()
        transfers: list[TransferRecord] = []
        failures: list[tuple[str, str]] = []
        for artifact in selected:
            try:
                remote_path, target = self._target_for(tasks, download_base, artifact, claimed)
                transfer = self._download_one(artifact, remote_path, target)
                transfers.append(transfer)
            except Exception as exc:
                failures.append((artifact.terminal, f"{artifact.path}: {exc}"))

        if not failures:
            terminals = {artifact.terminal for artifact in selected}
            self._mark_downloaded(run_id, terminals)
        return transfers, failures

    def _target_for(
        self,
        tasks: Iterable[_Task],
        download_base: Path,
        artifact: ControlArtifact,
        claimed: set[Path],
    ) -> tuple[str, Path]:
        _assert_safe_relative_artifact_path(artifact.path)
        work_dir = _work_dir_for_artifact(tasks, artifact.terminal)
        local_root = download_base / PurePosixPath(work_dir).name
        target = local_root.joinpath(*PurePosixPath(artifact.path).parts)
        if not target.is_relative_to(local_root):
            raise ValueError(f"artifact path escapes local results root: {artifact.path}")
        _assert_local_not_symlink(target)
        if target in claimed:
            raise ValueError(f"artifact target conflict: {artifact.path}")
        claimed.add(target)

        remote_path = posixpath.join(work_dir.rstrip("/"), artifact.path)
        _assert_remote_not_symlink(self._sftp, work_dir, artifact.path)
        metadata = self._sftp.stat(remote_path)
        _assert_remote_file_metadata(metadata, artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        return remote_path, target

    def _download_one(self, artifact: ControlArtifact, remote_path: str, target: Path) -> TransferRecord:
        with self._temporary_directory(
            prefix="jobdesk-control-download-",
            dir=str(target.parent),
        ) as temp_dir:
            staging = Path(temp_dir) / target.name
            transfer = self._sftp.download_file(
                remote_path,
                staging,
                overwrite=True,
                skip_if_same_size=False,
            )
            if getattr(transfer.status, "value", transfer.status) == TransferStatus.failed.value:
                raise ValueError(getattr(transfer, "reason", "artifact download failed"))
            if staging.stat().st_size != artifact.size or self._file_digest(staging) != artifact.sha256:
                raise ValueError(f"artifact integrity mismatch: {artifact.path}")
            staging.replace(target)
            return transfer

    def _mark_downloaded(self, run_id: str, terminals: set[str]) -> None:
        self._service.mutate_tasks(
            run_id,
            lambda task_list: [
                task.model_copy(
                    update={"status": TaskStatus.downloaded}
                    if _task_matches_terminal(task, terminals)
                    else {},
                    deep=True,
                )
                for task in task_list
            ],
        )


def _pattern_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(PurePosixPath(path).name, pattern)


def _work_dir_for_artifact(tasks: Iterable[_Task], terminal: str) -> str:
    candidates = [
        task.remote_workflow_dir
        for task in tasks
        if isinstance(task.remote_workflow_dir, str)
        and _is_safe_absolute_remote_path(task.remote_workflow_dir)
        and (task.task_id == terminal or PurePosixPath(task.remote_workflow_dir).name == terminal)
    ]
    if not candidates:
        all_work_dirs = [
            task.remote_workflow_dir
            for task in tasks
            if isinstance(task.remote_workflow_dir, str)
            and _is_safe_absolute_remote_path(task.remote_workflow_dir)
        ]
        if len(all_work_dirs) == 1:
            return all_work_dirs[0]
        raise ValueError(f"control artifact terminal has no unambiguous workflow directory: {terminal}")
    if len(set(candidates)) != 1:
        raise ValueError(f"control artifact terminal maps to multiple workflow directories: {terminal}")
    return candidates[0]


def _task_matches_terminal(task: _Task, terminals: set[str]) -> bool:
    work_dir = task.remote_workflow_dir
    return task.task_id in terminals or (
        isinstance(work_dir, str) and PurePosixPath(work_dir).name in terminals
    )


def _assert_remote_not_symlink(sftp: _SFTP, work_dir: str, relative_path: str) -> None:
    current = "/"
    for part in (*PurePosixPath(work_dir).parts[1:], *PurePosixPath(relative_path).parts):
        current = posixpath.join(current, part)
        metadata = sftp.lstat(current)
        mode = getattr(metadata, "st_mode", None) if metadata is not None else None
        if type(mode) is not int or stat.S_ISLNK(mode):
            raise ValueError(f"remote artifact path is a symlink or has invalid metadata: {current}")


def _assert_remote_file_metadata(metadata: _RemoteMetadata | None, artifact: ControlArtifact) -> None:
    size = getattr(metadata, "st_size", None) if metadata is not None else None
    if type(size) is not int or size < 0 or size != artifact.size:
        raise ValueError(f"artifact size mismatch: {artifact.path}")


def _assert_safe_relative_artifact_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or posixpath.normpath(path) != path
        or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
    ):
        raise ValueError(f"artifact path is unsafe: {path}")


def _is_safe_absolute_remote_path(path: str) -> bool:
    return (
        isinstance(path, str)
        and path.startswith("/")
        and "\\" not in path
        and posixpath.normpath(path) == path
        and all(part not in {"", ".", ".."} for part in PurePosixPath(path).parts[1:])
    )


def _assert_local_not_symlink(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError(f"local artifact target is a symlink: {component}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["ControlArtifactDownloader"]
