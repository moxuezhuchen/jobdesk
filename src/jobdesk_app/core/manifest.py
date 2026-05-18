import csv
import json
from json import JSONDecodeError
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .lifecycle import TaskStatus


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
    "task_dir",
    "entry_file",
    "parsed_fields",
    "rendered_command",
    "status",
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
    task_dir: str | None = None
    entry_file: str | None = None
    parsed_fields: dict[str, str] = Field(default_factory=dict)
    execution_profile: str = "default"
    discovery_name: str = ""
    server_id: str = ""
    remote_work_dir: str = ""
    max_parallel: int | None = None
    rendered_command: str = ""
    status: TaskStatus = TaskStatus.local_ready
    uploaded_at: datetime | None = None
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    downloaded_at: datetime | None = None
    analyzed_at: datetime | None = None
    error_message: str | None = None

    @property
    def entry_name(self) -> str:
        if self.entry_file:
            return Path(self.entry_file).name
        if self.task_files:
            return Path(self.task_files[0]).name
        return ""

    @property
    def entry_stem(self) -> str:
        return Path(self.entry_name).stem


class Manifest:
    def __init__(self, tasks: list[TaskRecord] | None = None):
        self.tasks = tasks or []

    @staticmethod
    def write(manifest_path: Path, tasks: list[TaskRecord]) -> None:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = manifest_path.with_name(f"{manifest_path.name}.tmp")
        try:
            with open(tmp_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f, delimiter="\t", lineterminator="\n")
                writer.writerow(_MANIFEST_COLUMNS)
                for task in tasks:
                    writer.writerow(_task_to_row(task))
            tmp_path.replace(manifest_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

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
        task.task_dir or "",
        task.entry_file or "",
        json.dumps(task.parsed_fields, ensure_ascii=False) if task.parsed_fields else "",
        task.rendered_command,
        task.status.value,
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
        remote_task_files=_parse_json_list(values.get("remote_task_files", ""), "remote_task_files", manifest_path, row_number),
        task_dir=values.get("task_dir") or None,
        entry_file=values.get("entry_file") or None,
        parsed_fields=_parse_json_dict(values.get("parsed_fields", ""), "parsed_fields", manifest_path, row_number),
        rendered_command=values.get("rendered_command", ""),
        status=TaskStatus(values["status"]) if values.get("status") else TaskStatus.local_ready,
        uploaded_at=_parse_dt(values.get("uploaded_at", "")),
        submitted_at=_parse_dt(values.get("submitted_at", "")),
        started_at=_parse_dt(values.get("started_at", "")),
        completed_at=_parse_dt(values.get("completed_at", "")),
        downloaded_at=_parse_dt(values.get("downloaded_at", "")),
        analyzed_at=_parse_dt(values.get("analyzed_at", "")),
        error_message=values.get("error_message") or None,
    )
