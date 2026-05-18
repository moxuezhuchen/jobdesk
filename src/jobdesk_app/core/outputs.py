"""输出文件写入模块。

将分析结果写入稳定的 TSV / JSON 文件。
"""

import csv
import json
from pathlib import Path
from datetime import datetime

from .models import ResultRecord, FailureRecord
from .grouping import GroupSummaryRecord
from .lifecycle import TaskStatus

# ---- TSV 列定义 ------------------------------------------------------------

_FINAL_RESULTS_COLUMNS: list[str] = [
    "batch_id",
    "task_id",
    "group_key",
    "result_id",
    "field_name",
    "value",
    "value_type",
    "unit",
    "source_file",
    "is_best_for_task",
    "relative_group",
    "relative_global",
]

_FAILURES_COLUMNS: list[str] = [
    "batch_id",
    "task_id",
    "stage",
    "reason",
    "server_id",
    "execution_profile",
    "remote_job_dir",
    "source_file",
    "context",
    "timestamp",
]

_GROUP_SUMMARY_COLUMNS: list[str] = [
    "group_key",
    "task_count",
    "result_count",
    "best_task_id",
    "best_result_id",
    "best_value",
]


# ---- 写入函数 ---------------------------------------------------------------


def write_final_results_tsv(
    results: list[ResultRecord],
    output_path: Path,
) -> None:
    """将 ResultRecord 列表写入 final_results.tsv。

    Args:
        results: ResultRecord 列表。
        output_path: 目标 TSV 文件路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(_FINAL_RESULTS_COLUMNS)
        for r in results:
            writer.writerow([
                r.batch_id,
                r.task_id,
                r.group_key or "",
                r.result_id or "",
                r.field_name,
                str(r.value),
                r.value_type,
                r.unit or "",
                r.source_file,
                str(r.is_best_for_task).lower(),
                _fmt_num(r.relative_group),
                _fmt_num(r.relative_global),
            ])


def write_failures_tsv(
    failures: list[FailureRecord],
    output_path: Path,
) -> None:
    """将 FailureRecord 列表写入 failures.tsv。

    Args:
        failures: FailureRecord 列表。
        output_path: 目标 TSV 文件路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(_FAILURES_COLUMNS)
        for fr in failures:
            writer.writerow(_failure_to_row(fr))


def append_failures_tsv(
    failures: list[FailureRecord],
    output_path: Path,
) -> None:
    if not failures:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not output_path.exists() or output_path.stat().st_size == 0
    with open(output_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        if needs_header:
            writer.writerow(_FAILURES_COLUMNS)
        for fr in failures:
            writer.writerow(_failure_to_row(fr))


def write_group_summary_tsv(
    summaries: list[GroupSummaryRecord],
    output_path: Path,
) -> None:
    """将 GroupSummaryRecord 列表写入 group_summary.tsv。

    Args:
        summaries: GroupSummaryRecord 列表。
        output_path: 目标 TSV 文件路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(_GROUP_SUMMARY_COLUMNS)
        for s in summaries:
            writer.writerow([
                s.group_key,
                str(s.task_count),
                str(s.result_count),
                s.best_task_id or "",
                s.best_result_id or "",
                _fmt_num(s.best_value),
            ])


def write_summary_json(
    batch_id: str,
    task_count: int,
    analyzed_count: int,
    result_count: int,
    failure_count: int,
    group_count: int,
    output_path: Path,
) -> None:
    """写入 summary.json。

    Args:
        batch_id: Batch ID。
        task_count: 任务总数。
        analyzed_count: 已分析任务数。
        result_count: 提取到的结果数。
        failure_count: 分析失败数。
        group_count: 分组数。
        output_path: 目标 JSON 文件路径。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "scope": "per_batch",
        "note": "Batch 自身结果文件为权威源，aggregate 仅为派生视图",
        "batch_id": batch_id,
        "task_count": task_count,
        "analyzed_task_count": analyzed_count,
        "result_count": result_count,
        "failure_count": failure_count,
        "group_count": group_count,
        "generated_at": datetime.now().isoformat(),
    }
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def read_final_results_tsv(file_path: Path) -> list[ResultRecord]:
    """从 final_results.tsv 读回 ResultRecord 列表（用于测试验证）。

    Args:
        file_path: TSV 文件路径。

    Returns:
        ResultRecord 列表。
    """
    results: list[ResultRecord] = []
    with open(file_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        if header is None:
            return results
        for row in reader:
            if not row or all(c == "" for c in row):
                continue
            values = {_FINAL_RESULTS_COLUMNS[i]: row[i] if i < len(row) else "" for i in range(len(_FINAL_RESULTS_COLUMNS))}
            value_str = values["value"]
            value_type = values["value_type"]
            try:
                if value_type == "float":
                    value = float(value_str)
                elif value_type == "int":
                    value = int(value_str)
                else:
                    value = value_str
            except (ValueError, TypeError):
                value = value_str

            results.append(ResultRecord(
                batch_id=values["batch_id"],
                task_id=values["task_id"],
                group_key=values["group_key"] or None,
                result_id=values["result_id"] or None,
                source_file=values["source_file"],
                field_name=values["field_name"],
                value=value,
                value_type=value_type,
                unit=values["unit"] or None,
                is_best_for_task=values["is_best_for_task"] == "true",
                relative_group=_parse_float(values["relative_group"]),
                relative_global=_parse_float(values["relative_global"]),
            ))
    return results


_JOB_STATUS_COLUMNS: list[str] = [
    "batch_id",
    "task_id",
    "group_key",
    "status",
    "previous_status",
    "changed",
    "discovery_name",
    "execution_profile",
    "server_id",
    "remote_work_dir",
    "remote_status_marker",
    "remote_exit_code",
    "error_message",
    "warnings",
    "task_files",
    "remote_job_dir",
    "submitted_at",
    "completed_at",
    "downloaded_at",
    "analyzed_at",
]


def write_job_status(
    output_path: Path,
    tasks: list,          # list[TaskRecord]
    snapshots: list,      # list[TaskStatusSnapshot]
) -> None:
    """将状态刷新结果写入 job_status.tsv。

    Args:
        output_path: 目标 TSV 文件路径。
        tasks: TaskRecord 列表。
        snapshots: TaskStatusSnapshot 列表（按 task_id 与 tasks 对应）。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    snap_by_id = {s.task_id: s for s in snapshots}

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(_JOB_STATUS_COLUMNS)
        for task in tasks:
            s = snap_by_id.get(task.task_id)
            changed = "true" if s and s.previous_status != s.recovered_status else "false"
            writer.writerow([
                task.batch_id,
                task.task_id,
                task.group_key or "",
                task.status.value,
                s.previous_status if s else "",
                changed,
                task.discovery_name or "",
                task.execution_profile,
                task.server_id or "",
                task.remote_work_dir or "",
                s.remote_status_marker if s else "",
                str(s.remote_exit_code) if s and s.remote_exit_code is not None else "",
                task.error_message or "",
                "; ".join(s.warnings) if s else "",
                "; ".join(task.task_files),
                task.remote_job_dir,
                task.submitted_at.isoformat() if task.submitted_at else "",
                task.completed_at.isoformat() if task.completed_at else "",
                task.downloaded_at.isoformat() if task.downloaded_at else "",
                task.analyzed_at.isoformat() if task.analyzed_at else "",
            ])


def write_all_failures(
    output_path: Path,
    failures: list[FailureRecord],
) -> None:
    """将 FailureRecord 列表写入 failures.tsv（统一 runtime + analysis）。

    Args:
        output_path: 目标 TSV 文件路径。
        failures: FailureRecord 列表。
    """
    write_failures_tsv(failures, output_path)


# ---- 内部辅助 ---------------------------------------------------------------


def _fmt_num(v: float | None) -> str:
    if v is None:
        return ""
    return str(v)


def _failure_to_row(fr: FailureRecord) -> list[str]:
    return [
        fr.batch_id,
        fr.task_id or "",
        fr.stage,
        fr.reason,
        fr.server_id or "",
        fr.execution_profile or "",
        fr.remote_job_dir or "",
        fr.source_file or "",
        fr.context or "",
        fr.timestamp,
    ]


def _parse_float(s: str) -> float | None:
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None
