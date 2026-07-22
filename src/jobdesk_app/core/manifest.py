import csv
import io
import json
import threading
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path

from pydantic import BaseModel, Field

from .atomic_write import atomic_write_text
from .lifecycle import TaskStatus

_manifest_locks_guard = threading.Lock()
_manifest_locks: dict[str, threading.RLock] = {}


def manifest_lock(manifest_path: Path | str) -> threading.RLock:
    """Return a process-wide reentrant lock keyed by the manifest's resolved path.

    Callers hold this lock around a read-modify-write sequence so concurrent
    refresh/download/retry workers cannot lose each other's status updates.
    """
    key = str(Path(manifest_path).resolve())
    with _manifest_locks_guard:
        lock = _manifest_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _manifest_locks[key] = lock
    return lock


_MANIFEST_COLUMNS: list[str] = [
    "task_id",
    "batch_id",
    "group_key",
    "remote_job_dir",
    "execution_profile",
    "discovery_name",
    "server_id",
    "remote_work_dir",
    "max_parallel",
    "task_files",
    "remote_task_files",
    "remote_result_files",
    "workflow_kind",
    "remote_config_path",
    "remote_workflow_dir",
    "remote_state_path",
    "remote_stats_path",
    "remote_log_path",
    "remote_result_paths",
    "task_dir",
    "entry_file",
    "parsed_fields",
    "rendered_command",
    "dry_run_command",
    "resume_command",
    "resume_dry_run_command",
    "resume_requested",
    "status",
    "scheduler_type",
    "remote_job_id",
    "uploaded_at",
    "submitted_at",
    "started_at",
    "completed_at",
    "downloaded_at",
    "analyzed_at",
    "error_message",
]


class TaskRecord(BaseModel):
    task_id: str = Field(...)
    batch_id: str = Field(...)
    group_key: str | None = None
    remote_job_dir: str = Field(...)
    task_files: list[str] = Field(default_factory=list)
    remote_task_files: list[str] = Field(default_factory=list)
    remote_result_files: list[str] = Field(default_factory=list)
    workflow_kind: str = ""
    remote_config_path: str = ""
    remote_workflow_dir: str = ""
    remote_state_path: str = ""
    remote_stats_path: str = ""
    remote_log_path: str = ""
    remote_result_paths: list[str] = Field(default_factory=list)
    task_dir: str | None = None
    entry_file: str | None = None
    parsed_fields: dict[str, str] = Field(default_factory=dict)
    execution_profile: str = "default"
    discovery_name: str = ""
    server_id: str = ""
    remote_work_dir: str = ""
    max_parallel: int | None = None
    rendered_command: str = ""
    dry_run_command: str = ""
    resume_command: str = ""
    resume_dry_run_command: str = ""
    resume_requested: bool = False
    status: TaskStatus = TaskStatus.local_ready
    scheduler_type: str = "nohup"
    remote_job_id: str | None = None
    uploaded_at: datetime | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    downloaded_at: datetime | None = None
    analyzed_at: datetime | None = None
    error_message: str | None = None


class Manifest:
    def __init__(self, tasks: list[TaskRecord] | None = None):
        self.tasks = tasks or []

    @staticmethod
    def write(manifest_path: Path, tasks: list[TaskRecord]) -> None:
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        writer.writerow(_MANIFEST_COLUMNS)
        for task in tasks:
            writer.writerow(_task_to_row(task))
        atomic_write_text(manifest_path, output.getvalue(), newline="")

    @staticmethod
    def read(manifest_path: Path) -> list[TaskRecord]:
        tasks: list[TaskRecord] = []
        with open(manifest_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader, None)
            if header is None:
                return tasks

            # Older manifests did not have max_parallel. Map by header so both
            # old and new manifests load predictably.
            columns = header if header else _MANIFEST_COLUMNS
            for row_number, row in enumerate(reader, start=2):
                if not row or all(cell == "" for cell in row):
                    continue
                tasks.append(_row_to_task(row, columns, manifest_path, row_number))
        return tasks


def _fmt_dt(dt: datetime | None) -> str:
    return dt.isoformat() if dt is not None else ""


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def _task_to_row(task: TaskRecord) -> list[str]:
    return [
        task.task_id,
        task.batch_id,
        task.group_key or "",
        task.remote_job_dir,
        task.execution_profile,
        task.discovery_name,
        task.server_id,
        task.remote_work_dir,
        str(task.max_parallel) if task.max_parallel is not None else "",
        json.dumps(task.task_files, ensure_ascii=False) if task.task_files else "",
        json.dumps(task.remote_task_files, ensure_ascii=False) if task.remote_task_files else "",
        json.dumps(task.remote_result_files, ensure_ascii=False) if task.remote_result_files else "",
        task.workflow_kind,
        task.remote_config_path,
        task.remote_workflow_dir,
        task.remote_state_path,
        task.remote_stats_path,
        task.remote_log_path,
        json.dumps(task.remote_result_paths, ensure_ascii=False) if task.remote_result_paths else "",
        task.task_dir or "",
        task.entry_file or "",
        json.dumps(task.parsed_fields, ensure_ascii=False) if task.parsed_fields else "",
        task.rendered_command,
        task.dry_run_command,
        task.resume_command,
        task.resume_dry_run_command,
        "true" if task.resume_requested else "false",
        task.status.value,
        task.scheduler_type,
        task.remote_job_id or "",
        _fmt_dt(task.uploaded_at),
        _fmt_dt(task.submitted_at),
        _fmt_dt(task.started_at),
        _fmt_dt(task.completed_at),
        _fmt_dt(task.downloaded_at),
        _fmt_dt(task.analyzed_at),
        task.error_message or "",
    ]


def _manifest_read_error(
    manifest_path: Path | None,
    row_number: int | None,
    field_name: str,
    exc: Exception,
) -> ValueError:
    path_text = str(manifest_path) if manifest_path is not None else "manifest"
    row_text = f" row {row_number}" if row_number is not None else ""
    return ValueError(f"{path_text}{row_text}: invalid {field_name}: {exc}")


def _parse_json_list(
    s: str,
    field_name: str,
    manifest_path: Path | None = None,
    row_number: int | None = None,
) -> list[str]:
    if not s:
        return []
    try:
        value = json.loads(s)
    except JSONDecodeError as exc:
        raise _manifest_read_error(manifest_path, row_number, field_name, exc) from exc
    return value if isinstance(value, list) else []


def _parse_json_dict(
    s: str,
    field_name: str,
    manifest_path: Path | None = None,
    row_number: int | None = None,
) -> dict[str, str]:
    if not s:
        return {}
    try:
        value = json.loads(s)
    except JSONDecodeError as exc:
        raise _manifest_read_error(manifest_path, row_number, field_name, exc) from exc
    return value if isinstance(value, dict) else {}


def _parse_int(s: str) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _row_to_task(
    row: list[str],
    columns: list[str] | None = None,
    manifest_path: Path | None = None,
    row_number: int | None = None,
) -> TaskRecord:
    cols = columns or _MANIFEST_COLUMNS
    values = {col: row[i] if i < len(row) else "" for i, col in enumerate(cols)}

    return TaskRecord(
        task_id=values.get("task_id", ""),
        batch_id=values.get("batch_id", ""),
        group_key=values.get("group_key") or None,
        remote_job_dir=values.get("remote_job_dir", ""),
        execution_profile=values.get("execution_profile", "default") or "default",
        discovery_name=values.get("discovery_name", ""),
        server_id=values.get("server_id", ""),
        remote_work_dir=values.get("remote_work_dir", ""),
        max_parallel=_parse_int(values.get("max_parallel", "")),
        task_files=_parse_json_list(values.get("task_files", ""), "task_files", manifest_path, row_number),
        remote_task_files=_parse_json_list(
            values.get("remote_task_files", ""), "remote_task_files", manifest_path, row_number
        ),
        remote_result_files=_parse_json_list(
            values.get("remote_result_files", ""), "remote_result_files", manifest_path, row_number
        ),
        workflow_kind=values.get("workflow_kind", ""),
        remote_config_path=values.get("remote_config_path", ""),
        remote_workflow_dir=values.get("remote_workflow_dir", ""),
        remote_state_path=values.get("remote_state_path", ""),
        remote_stats_path=values.get("remote_stats_path", ""),
        remote_log_path=values.get("remote_log_path", ""),
        remote_result_paths=_parse_json_list(
            values.get("remote_result_paths", ""), "remote_result_paths", manifest_path, row_number
        ),
        task_dir=values.get("task_dir") or None,
        entry_file=values.get("entry_file") or None,
        parsed_fields=_parse_json_dict(values.get("parsed_fields", ""), "parsed_fields", manifest_path, row_number),
        rendered_command=values.get("rendered_command", ""),
        dry_run_command=values.get("dry_run_command", ""),
        resume_command=values.get("resume_command", ""),
        resume_dry_run_command=values.get("resume_dry_run_command", ""),
        resume_requested=values.get("resume_requested", "").lower() == "true",
        status=TaskStatus(values["status"]) if values.get("status") else TaskStatus.local_ready,
        scheduler_type=values.get("scheduler_type", "nohup") or "nohup",
        remote_job_id=values.get("remote_job_id") or None,
        uploaded_at=_parse_dt(values.get("uploaded_at", "")),
        submitted_at=_parse_dt(values.get("submitted_at", "")),
        started_at=_parse_dt(values.get("started_at", "")),
        completed_at=_parse_dt(values.get("completed_at", "")),
        downloaded_at=_parse_dt(values.get("downloaded_at", "")),
        analyzed_at=_parse_dt(values.get("analyzed_at", "")),
        error_message=values.get("error_message") or None,
    )
