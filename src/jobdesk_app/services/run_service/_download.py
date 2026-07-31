"""Download operations for run_service."""

from __future__ import annotations

import fnmatch
import posixpath
import stat
from pathlib import Path, PurePosixPath

from jobdesk_app.core.confflow_contract import OUTPUT_MANIFEST_FILE
from jobdesk_app.core.confflow_output_manifest import load_output_manifest
from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.transfer import TransferRecord, TransferStatus
from jobdesk_app.services.run_repository import RunRepository

from ._helpers import _declared_outputs, _safe_declared_result_path


def download_completed(service, run_id: str, sftp, patterns: list[str]) -> tuple[list[TransferRecord], list[tuple[str, str]]]:
    """Download declared outputs for remote_completed tasks.

    This is a module-level function to enable method extraction from RunService.
    The ``service`` argument must be a RunService instance.
    """
    return _download_completed_locked(
        service.repository,
        service.workspace_dir,
        run_id,
        sftp,
        patterns,
    )


def _download_completed_locked(
    repository: RunRepository,
    workspace_dir: Path,
    run_id: str,
    sftp,
    patterns: list[str],
) -> tuple[list[TransferRecord], list[tuple[str, str]]]:
    """Internal download implementation shared by download_completed and public API."""
    record = repository.load_run(run_id)
    tasks = repository.load_tasks(run_id)
    expected = {task.task_id: task.model_copy(deep=True) for task in tasks}
    records: list[TransferRecord] = []
    failures: list[tuple[str, str]] = []
    successful_task_records: dict[str, list[TransferRecord]] = {}
    claimed_manifest_targets: set[Path] = set()
    # Final outputs are owned by a run, just like deletion and result-preview
    # contracts.  Never write new downloads into the shared workspace root:
    # two submissions may legitimately contain the same molecule basename.
    caller_workspace = workspace_dir.resolve()
    if record.local_dir:
        recorded_workspace = Path(record.local_dir)
        if not recorded_workspace.is_absolute():
            raise ValueError(f"run {run_id!r} has a non-absolute local_dir workspace anchor")
        recorded_workspace = recorded_workspace.resolve()
        if recorded_workspace != caller_workspace:
            raise ValueError(
                f"run local_dir does not match download workspace: {recorded_workspace} != {caller_workspace}"
            )
        download_workspace = recorded_workspace
    else:
        # Legacy records did not persist a workspace binding.
        download_workspace = caller_workspace
    results_root = download_workspace / "results"
    download_base = (results_root / run_id).resolve()
    if not download_base.is_relative_to(results_root):
        raise ValueError(f"run_id escapes results dir: {run_id}")
    for task in tasks:
        if task.status != TaskStatus.remote_completed:
            continue
        recs: list[TransferRecord] = []
        download_errors: list[str] = []
        requested_outputs: list[str] = []
        task_ok = False
        try:
            download_base.mkdir(parents=True, exist_ok=True)
            work_dir = task.remote_work_dir or task.remote_job_dir
            uses_output_manifest = _requires_output_manifest(task)
            if uses_output_manifest:
                work_dir, requested_outputs, manifest_record = _load_manifest_outputs(
                    task,
                    sftp,
                    download_base,
                    patterns,
                    claimed_manifest_targets,
                )
                recs.append(manifest_record)
                local_root = _manifest_download_root(download_base, work_dir)
            else:
                requested_outputs = _declared_outputs(task, patterns)
                local_root = download_base
            for relative_output in requested_outputs:
                safe_path = _safe_declared_result_path(relative_output)
                remote_file = f"{work_dir.rstrip('/')}/{safe_path.as_posix()}"
                if uses_output_manifest:
                    _assert_remote_manifest_path_not_symlink(sftp, work_dir, safe_path)
                local_file = local_root.joinpath(*safe_path.parts)
                if not local_file.resolve().is_relative_to(local_root):
                    raise ValueError(f"declared result path escapes local dir: {relative_output}")
                if uses_output_manifest:
                    canonical_local_file = local_file.resolve()
                    if canonical_local_file in claimed_manifest_targets:
                        raise ValueError(f"output manifest conflicts with another task target: {relative_output}")
                    claimed_manifest_targets.add(canonical_local_file)
                try:
                    rec = sftp.download_file(remote_file, local_file, overwrite=True, skip_if_same_size=False)
                    recs.append(rec)
                    if rec.status == TransferStatus.failed:
                        download_errors.append(f"{relative_output}: {rec.reason}")
                except Exception as exc:
                    download_errors.append(f"{relative_output}: {exc}")
            successful = sum(1 for r in recs if r.status in (TransferStatus.transferred, TransferStatus.skipped))
            task_ok = not download_errors and successful == len(recs) and bool(requested_outputs)
            if download_errors:
                failures.append((task.task_id, "; ".join(download_errors)))
            elif not task_ok:
                failures.append((task.task_id, "无匹配输出文件"))
        except ValueError as exc:
            download_errors.append(str(exc))
            failures.append((task.task_id, str(exc)))
        except Exception as exc:
            download_errors.append(str(exc))
            failures.append((task.task_id, str(exc)))
        records.extend(recs)
        if task_ok:
            task.status = TaskStatus.downloaded
            successful_task_records[task.task_id] = list(recs)
            if task.error_message and task.error_message.startswith("download:"):
                task.error_message = None
        else:
            error_parts = []
            if download_errors:
                error_parts = download_errors
            elif not requested_outputs:
                error_parts = ["无匹配输出文件"]
            if error_parts:
                task.error_message = "download: " + "; ".join(error_parts)
    merged = repository.merge_tasks(run_id, tasks, expected_tasks=expected)
    rejected_successes = set(successful_task_records) - merged.accepted_task_ids
    if rejected_successes:
        rejected_record_ids = {
            id(record) for task_id in rejected_successes for record in successful_task_records[task_id]
        }
        records = [record for record in records if id(record) not in rejected_record_ids]
        failures.extend(
            (
                task_id,
                "task state changed during download; downloaded status was not committed",
            )
            for task_id in sorted(rejected_successes)
        )
    return records, failures


def _requires_output_manifest(task) -> bool:
    """Return whether a task is governed by ConfFlow's output manifest."""
    return task.workflow_kind in {"confflow", "dag"}


def _manifest_download_root(download_base: Path, remote_workflow_dir: str) -> Path:
    """Return the task-isolated local root used for manifest-declared files."""
    work_dir_name = PurePosixPath(remote_workflow_dir).name
    safe_name = _safe_declared_result_path(work_dir_name)
    root = download_base.joinpath(*safe_name.parts).resolve()
    if not root.is_relative_to(download_base):
        raise ValueError(f"workflow directory escapes result directory: {remote_workflow_dir}")
    return root


def _load_manifest_outputs(
    task,
    sftp,
    download_base: Path,
    patterns: list[str],
    claimed_targets: set[Path],
) -> tuple[str, list[str], TransferRecord]:
    """Download and validate the only authority for ConfFlow result paths."""
    work_dir = _safe_remote_workflow_dir(task.remote_workflow_dir)
    safe_manifest = _safe_declared_result_path(OUTPUT_MANIFEST_FILE)
    _assert_remote_manifest_path_not_symlink(sftp, work_dir, safe_manifest)
    local_root = _manifest_download_root(download_base, work_dir)
    local_manifest = local_root.joinpath(*safe_manifest.parts)
    canonical_manifest = local_manifest.resolve()
    if canonical_manifest in claimed_targets:
        raise ValueError("output manifest conflicts with another task target")
    claimed_targets.add(canonical_manifest)
    remote_manifest = posixpath.join(work_dir.rstrip("/"), safe_manifest.as_posix())
    manifest_record = sftp.download_file(
        remote_manifest,
        local_manifest,
        overwrite=True,
        skip_if_same_size=False,
    )
    if manifest_record.status == TransferStatus.failed:
        raise ValueError(f"output manifest download failed: {manifest_record.reason}")
    manifest = load_output_manifest(local_manifest, work_dir=local_root)
    outputs = [path for path in manifest.paths if _matches_requested_patterns(path, patterns)]
    if not outputs:
        raise ValueError("output manifest has no outputs matching the requested patterns")
    return work_dir, outputs, manifest_record


def _matches_requested_patterns(path: str, patterns: list[str]) -> bool:
    """Apply the user-selected result filters without inventing filenames."""
    if not patterns:
        return True
    name = PurePosixPath(path).name
    return any(
        (
            fnmatch.fnmatch(path, pattern)
            or (pattern.startswith("*/") and fnmatch.fnmatch(path, pattern[2:]))
        )
        if "/" in pattern
        else fnmatch.fnmatch(name, pattern)
        for pattern in patterns
    )


def _safe_remote_workflow_dir(value: object) -> str:
    """Require a canonical absolute POSIX workflow directory."""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("ConfFlow task is missing its remote workflow directory")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or value != path.as_posix()
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError(f"ConfFlow workflow directory is unsafe: {value}")
    return path.as_posix()


def _assert_remote_manifest_path_not_symlink(sftp, work_dir: str, relative_path: PurePosixPath) -> None:
    """Reject remote symlink traversal before following any manifest target."""
    lstat = getattr(sftp, "lstat", None)
    if not callable(lstat):
        raise ValueError("SFTP client lacks lstat required for ConfFlow manifest download")
    root = PurePosixPath(work_dir)
    if not root.is_absolute():
        raise ValueError(f"ConfFlow workflow directory is not absolute: {work_dir}")
    current = "/"
    for part in (*root.parts[1:], *relative_path.parts):
        current = posixpath.join(current, part)
        metadata = lstat(current)
        if metadata is None:
            raise ValueError(f"remote manifest path is missing: {current}")
        mode = getattr(metadata, "st_mode", None)
        if type(mode) is not int:
            raise ValueError(f"remote manifest path has no mode: {current}")
        if stat.S_ISLNK(mode):
            raise ValueError(f"remote manifest path is a symlink: {current}")
