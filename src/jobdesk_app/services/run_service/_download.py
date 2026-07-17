"""Download operations for run_service."""

from __future__ import annotations

from pathlib import Path

from jobdesk_app.core.lifecycle import TaskStatus
from jobdesk_app.core.transfer import TransferStatus
from jobdesk_app.services.run_repository import RunRepository

from ._helpers import _declared_outputs, _safe_declared_result_path


def download_completed(service, run_id: str, sftp, patterns: list[str]):
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
) -> tuple[list, list[tuple[str, str]]]:
    """Internal download implementation shared by download_completed and public API."""
    record = repository.load_run(run_id)
    tasks = repository.load_tasks(run_id)
    expected = {task.task_id: task.model_copy(deep=True) for task in tasks}
    records = []
    failures = []
    successful_task_records: dict[str, list] = {}
    download_base = Path(record.local_dir).resolve() if record.local_dir else workspace_dir
    for task in tasks:
        if task.status != TaskStatus.remote_completed:
            continue
        recs = []
        download_errors: list[str] = []
        requested_outputs: list[str] = []
        task_ok = False
        try:
            download_base.mkdir(parents=True, exist_ok=True)
            work_dir = task.remote_work_dir or task.remote_job_dir
            requested_outputs = _declared_outputs(task, patterns)
            for relative_output in requested_outputs:
                safe_path = _safe_declared_result_path(relative_output)
                remote_file = f"{work_dir.rstrip('/')}/{safe_path.as_posix()}"
                local_file = download_base.joinpath(*safe_path.parts)
                if not local_file.resolve().is_relative_to(download_base):
                    raise ValueError(f"declared result path escapes local dir: {relative_output}")
                try:
                    rec = sftp.download_file(remote_file, local_file, overwrite=True, skip_if_same_size=False)
                    recs.append(rec)
                    if rec.status == TransferStatus.failed:
                        download_errors.append(f"{relative_output}: {rec.reason}")
                except Exception as exc:
                    download_errors.append(f"{relative_output}: {exc}")
            successful = sum(1 for r in recs if r.status in (TransferStatus.transferred, TransferStatus.skipped))
            task_ok = successful == len(requested_outputs) and bool(requested_outputs)
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
