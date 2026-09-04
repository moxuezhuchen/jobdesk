"""Artifact validation, download staging, and durable task projection."""

from __future__ import annotations

import fnmatch
import posixpath
import stat
import tempfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jobdesk_app.application.confflow_client import ArtifactEntry
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.infrastructure.runtime.confflow_control import ControlArtifact
from jobdesk_app.infrastructure.runtime.confflow_control_handoff import is_safe_absolute_remote_path, sha256_file


def artifact_entries(artifacts: Iterable[ControlArtifact]) -> tuple[ArtifactEntry, ...]:
    grouped: dict[str, list[str]] = {}
    for artifact in artifacts:
        grouped.setdefault(artifact.terminal, []).append(artifact.path)
    return tuple(ArtifactEntry(terminal, tuple(paths)) for terminal, paths in sorted(grouped.items()))


def download_control_artifacts(service, run_id: str, artifacts: tuple[ControlArtifact, ...], patterns: list[str], sftp):
    tasks = service.load_tasks(run_id)
    selected = [
        artifact
        for artifact in artifacts
        if not patterns or any(pattern_matches(artifact.path, pattern) for pattern in patterns)
    ]
    if not selected:
        return [], []
    download_base = service.workspace_dir / "results" / run_id
    claimed: set[Path] = set()
    transfers = []
    failures: list[tuple[str, str]] = []
    for artifact in selected:
        try:
            assert_safe_relative_artifact_path(artifact.path)
            work_dir = work_dir_for_artifact(tasks, artifact.terminal)
            local_root = download_base / Path(work_dir).name
            remote_path = posixpath.join(work_dir.rstrip("/"), artifact.path)
            assert_remote_not_symlink(sftp, work_dir, artifact.path)
            local_path = local_root / Path(*PurePosixPath(artifact.path).parts)
            if not local_path.is_relative_to(local_root):
                raise ValueError(f"artifact path escapes local results root: {artifact.path}")
            assert_local_not_symlink(local_path)
            if local_path in claimed:
                raise ValueError(f"artifact target conflict: {artifact.path}")
            claimed.add(local_path)
            remote_stat = sftp.stat(remote_path)
            if remote_stat is None or int(remote_stat.st_size) != artifact.size:
                raise ValueError(f"artifact size mismatch: {artifact.path}")
            local_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="jobdesk-control-download-", dir=str(local_path.parent)
            ) as temp_dir:
                staging = Path(temp_dir) / local_path.name
                transfer = sftp.download_file(remote_path, staging, overwrite=True, skip_if_same_size=False)
                transfers.append(transfer)
                if getattr(transfer.status, "value", transfer.status) == "failed":
                    raise ValueError(getattr(transfer, "reason", "artifact download failed"))
                if staging.stat().st_size != artifact.size or sha256_file(staging) != artifact.sha256:
                    raise ValueError(f"artifact integrity mismatch: {artifact.path}")
                staging.replace(local_path)
                transfer.local_path = str(local_path)
        except Exception as exc:
            failures.append((artifact.terminal, f"{artifact.path}: {exc}"))
    if not failures and selected:
        selected_terminals = {artifact.terminal for artifact in selected}
        service.mutate_tasks(
            run_id,
            lambda task_list: [
                task.model_copy(
                    update=(
                        {"status": TaskStatus.downloaded, "downloaded_at": datetime.now()}
                        if task_matches_terminal(task, selected_terminals)
                        else {}
                    ),
                    deep=True,
                )
                for task in task_list
            ],
        )
    return transfers, failures


def work_dir_for_artifact(tasks: Iterable[Any], terminal: str) -> str:
    candidates = [
        task.remote_workflow_dir
        for task in tasks
        if isinstance(task.remote_workflow_dir, str)
        and is_safe_absolute_remote_path(task.remote_workflow_dir)
        and (task.task_id == terminal or PurePosixPath(task.remote_workflow_dir).name == terminal)
    ]
    if not candidates:
        all_work_dirs = [
            task.remote_workflow_dir
            for task in tasks
            if isinstance(task.remote_workflow_dir, str) and is_safe_absolute_remote_path(task.remote_workflow_dir)
        ]
        if len(all_work_dirs) == 1:
            return all_work_dirs[0]
        raise ValueError(f"control artifact terminal has no unambiguous workflow directory: {terminal}")
    if len(set(candidates)) != 1:
        raise ValueError(f"control artifact terminal maps to multiple workflow directories: {terminal}")
    return candidates[0]


def task_matches_terminal(task: Any, terminals: set[str]) -> bool:
    work_dir = getattr(task, "remote_workflow_dir", "")
    return task.task_id in terminals or (isinstance(work_dir, str) and PurePosixPath(work_dir).name in terminals)


def pattern_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(PurePosixPath(path).name, pattern)


def assert_remote_not_symlink(sftp, work_dir: str, relative_path: str) -> None:
    current = "/"
    for part in (*PurePosixPath(work_dir).parts[1:], *PurePosixPath(relative_path).parts):
        current = posixpath.join(current, part)
        metadata = sftp.lstat(current)
        if metadata is None:
            raise ValueError(f"remote artifact path is missing: {current}")
        mode = getattr(metadata, "st_mode", None)
        if type(mode) is not int or stat.S_ISLNK(mode):
            raise ValueError(f"remote artifact path is a symlink or has invalid metadata: {current}")


def assert_safe_relative_artifact_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path
        or path.startswith("/")
        or "\\" in path
        or posixpath.normpath(path) != path
        or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
    ):
        raise ValueError(f"artifact path is unsafe: {path}")


def assert_local_not_symlink(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        if component.is_symlink():
            raise ValueError(f"local artifact target is a symlink: {component}")
