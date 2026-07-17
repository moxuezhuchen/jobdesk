"""远程状态刷新模块。

结合 Manifest + 远程 JobDesk 状态标记文件，更新任务生命周期状态。
不下载输出文件、不做本地分析、不提交任务。
"""

from datetime import datetime
from pathlib import Path

from ..core.lifecycle import TaskStatus
from ..core.manifest import Manifest, TaskRecord, manifest_lock
from ..core.models import FailureRecord
from ..core.status import BatchControlSnapshot, StatusRefreshResult, TaskStatusSnapshot
from .status import read_remote_task_status, read_remote_task_statuses_batch

DEFAULT_STALE_TIMEOUT_SECONDS = 24 * 60 * 60


def refresh_batch_status(
    ssh,  # SSHClientWrapper
    manifest_path: Path,
    remote_batch_dir: str,
    batch_id: str,
    write: bool = False,
    log_tail_lines: int = 50,
    control_subdir: str = "_batch",
    stale_timeout_seconds: int | None = DEFAULT_STALE_TIMEOUT_SECONDS,
) -> StatusRefreshResult:
    """Serialize the manifest read-modify-write against other run workers."""
    with manifest_lock(manifest_path):
        return _refresh_batch_status(
            ssh,
            manifest_path,
            remote_batch_dir,
            batch_id,
            write,
            log_tail_lines,
            control_subdir,
            stale_timeout_seconds,
        )


def _refresh_batch_status(
    ssh,  # SSHClientWrapper
    manifest_path: Path,
    remote_batch_dir: str,
    batch_id: str,
    write: bool = False,
    log_tail_lines: int = 50,
    control_subdir: str = "_batch",
    stale_timeout_seconds: int | None = DEFAULT_STALE_TIMEOUT_SECONDS,
) -> StatusRefreshResult:
    """刷新整个 Batch 的任务状态。

    读取远程 .jobdesk_status 等标记文件，与 Manifest 合并，
    生成新的状态。默认不写回 Manifest（write=False）。

    Args:
        ssh: SSHClientWrapper 实例。
        manifest_path: 本地 manifest.tsv 路径。
        remote_batch_dir: 远程 Batch 根目录。
        batch_id: Batch ID。
        write: 是否写回 Manifest。
        log_tail_lines: submit log 最大行数。

    Returns:
        StatusRefreshResult。
    """
    tasks = Manifest.read(manifest_path)
    result, updated_tasks = _refresh_tasks(
        ssh,
        tasks,
        remote_batch_dir,
        batch_id,
        apply_updates=write,
        log_tail_lines=log_tail_lines,
        control_subdir=control_subdir,
        stale_timeout_seconds=stale_timeout_seconds,
    )
    if write and tasks:
        Manifest.write(manifest_path, updated_tasks)
    return result


def refresh_task_statuses(
    ssh,
    tasks: list[TaskRecord],
    remote_batch_dir: str,
    batch_id: str,
    log_tail_lines: int = 50,
    control_subdir: str = "_batch",
    stale_timeout_seconds: int | None = DEFAULT_STALE_TIMEOUT_SECONDS,
) -> tuple[StatusRefreshResult, list[TaskRecord]]:
    """Refresh detached task records without touching legacy manifest files."""
    copies = [task.model_copy(deep=True) for task in tasks]
    return _refresh_tasks(
        ssh,
        copies,
        remote_batch_dir,
        batch_id,
        apply_updates=True,
        log_tail_lines=log_tail_lines,
        control_subdir=control_subdir,
        stale_timeout_seconds=stale_timeout_seconds,
    )


def _refresh_tasks(
    ssh,
    tasks: list[TaskRecord],
    remote_batch_dir: str,
    batch_id: str,
    *,
    apply_updates: bool,
    log_tail_lines: int,
    control_subdir: str,
    stale_timeout_seconds: int | None,
) -> tuple[StatusRefreshResult, list[TaskRecord]]:
    result = StatusRefreshResult(batch_id=batch_id, task_count=len(tasks))

    # 把 batch_control 文件与所有 task 的状态文件合并为「一条」SSH 命令读取，
    # 减少高延迟链路（如 frp 中继隧道）上的往返次数。
    control_dir = f"{remote_batch_dir.rstrip('/')}/{control_subdir}"
    extra_files = [
        ("BC:E", f"{control_dir}/batch_control_exit_code", 0),
        ("BC:L", f"{control_dir}/batch_control.nohup.log", 20),
    ]
    extra_out: dict[str, bytes | None] = {}

    # 没有 remote_job_dir 的 task 不会被远程查询。
    batch_pairs = [(t.task_id, t.remote_job_dir) for t in tasks if t.remote_job_dir]
    batch_snapshots = read_remote_task_statuses_batch(
        ssh,
        batch_pairs,
        log_tail_lines=log_tail_lines,
        extra_files=extra_files,
        extra_out=extra_out,
    )
    result.batch_control = _parse_batch_control(extra_out)

    # 遍历每个任务
    for task in tasks:
        old_status = task.status
        if task.remote_job_dir:
            remote_snap = batch_snapshots.get(task.task_id)
            if remote_snap is None:
                # 防御：批量读取应已为该 task 生成 snapshot；缺失则回退单读。
                remote_snap = read_remote_task_status(ssh, task.task_id, task.remote_job_dir, log_tail_lines)
        else:
            remote_snap = None

        new_status, snap = _recover_status(old_status, remote_snap, task, stale_timeout_seconds)
        result.snapshots.append(snap)

        if new_status != old_status:
            result.changed_count += 1

        # 生成 runtime failure
        if new_status == TaskStatus.failed and old_status != TaskStatus.failed:
            reason = snap.failure_reason or "远程任务失败"
            log_tail = remote_snap.submit_log_tail if remote_snap else ""
            result.failures.append(
                FailureRecord(
                    task_id=task.task_id,
                    batch_id=batch_id,
                    stage="runtime",
                    reason=reason,
                    source_file=f"{task.remote_job_dir}/.jobdesk_submit.log",
                    context=log_tail[-200:] if log_tail else None,
                )
            )

        # 准备写回
        if apply_updates:
            task.status = new_status
            if new_status == TaskStatus.remote_completed and task.completed_at is None:
                task.completed_at = datetime.now()
            if new_status == TaskStatus.failed and task.error_message is None:
                task.error_message = snap.failure_reason

    result.warnings.extend(result.batch_control.warnings)

    return result, tasks


def _recover_status(
    current: TaskStatus,
    remote_snap,  # RemoteTaskStatusSnapshot | None
    task: TaskRecord,
    stale_timeout_seconds: int | None = None,
) -> tuple[TaskStatus, TaskStatusSnapshot]:
    """根据当前状态和远程快照恢复新状态。

    Returns:
        (new_status, snapshot)。
    """
    snap = TaskStatusSnapshot(
        task_id=task.task_id,
        batch_id=task.batch_id,
        previous_status=current.value,
        recovered_status=current.value,
    )

    if remote_snap is not None:
        snap.remote_status_marker = remote_snap.status_marker if remote_snap.marker_exists else None
        snap.remote_exit_code = remote_snap.exit_code if remote_snap.exit_code_exists else None
        snap.has_submit_log = remote_snap.log_exists
        snap.warnings = list(remote_snap.warnings)

    new_status = current

    # ---- 状态恢复规则 ----

    if current == TaskStatus.local_ready:
        pass  # 不主动检查远程

    elif current == TaskStatus.uploaded:
        if remote_snap and remote_snap.marker_exists:
            marker = remote_snap.status_marker.strip()
            if marker == "running":
                new_status = TaskStatus.running
                snap.failure_reason = None
            elif marker == "completed":
                result = _check_exit_code(remote_snap, snap)
                if result is not None:
                    new_status = result
            elif marker == "failed":
                new_status = TaskStatus.failed
                snap.failure_reason = "远程状态标记为 failed"
                snap.warnings.append("uploaded 任务在远程已标记为 failed")

    elif current == TaskStatus.submitting:
        if _remote_read_incomplete(remote_snap):
            snap.warnings.append("submission claim retained because remote status read was incomplete")
        elif stale_timeout_seconds and task.submitted_at:
            elapsed = (datetime.now() - task.submitted_at).total_seconds()
            if elapsed > stale_timeout_seconds:
                marker = remote_snap.status_marker.strip() if remote_snap and remote_snap.marker_exists else ""
                if marker == "running":
                    new_status = TaskStatus.running
                elif marker == "completed":
                    result = _check_exit_code(remote_snap, snap)
                    if result is not None:
                        new_status = result
                elif marker == "failed":
                    new_status = TaskStatus.failed
                    snap.failure_reason = "remote status marker is failed"
                else:
                    snap.warnings.append(
                        f"submission remains ambiguous after {int(elapsed)}s; "
                        "manual reconciliation is required before retry"
                    )
            else:
                snap.warnings.append("submission is still being finalized")
        else:
            snap.warnings.append("submission is still being finalized")

    elif current == TaskStatus.submitted:
        if remote_snap and remote_snap.marker_exists:
            marker = remote_snap.status_marker.strip()
            if marker == "running":
                new_status = TaskStatus.running
            elif marker in ("completed",):
                result = _check_exit_code(remote_snap, snap)
                if result is not None:
                    new_status = result
            elif marker == "failed":
                new_status = TaskStatus.failed
                snap.failure_reason = "远程状态标记为 failed"
        else:
            # Check for stale timeout
            if _remote_read_incomplete(remote_snap):
                snap.warnings.append("stale timeout skipped because remote status read was incomplete")
            elif stale_timeout_seconds and task.submitted_at:
                elapsed = (datetime.now() - task.submitted_at).total_seconds()
                if elapsed > stale_timeout_seconds:
                    new_status = TaskStatus.failed
                    snap.failure_reason = f"no remote response after {int(elapsed)}s (timeout={stale_timeout_seconds}s)"
                else:
                    snap.warnings.append("已提交但远程无状态文件")
            else:
                snap.warnings.append("已提交但远程无状态文件")

    elif current == TaskStatus.uncertain:
        if remote_snap and remote_snap.marker_exists:
            marker = remote_snap.status_marker.strip()
            if marker == "running":
                new_status = TaskStatus.running
            elif marker == "completed":
                result = _check_exit_code(remote_snap, snap)
                if result is not None:
                    new_status = result
            elif marker == "failed":
                new_status = TaskStatus.failed
                snap.failure_reason = "remote status marker is failed"

    elif current == TaskStatus.running:
        if remote_snap and remote_snap.marker_exists:
            marker = remote_snap.status_marker.strip()
            if marker == "completed":
                result = _check_exit_code(remote_snap, snap)
                if result is not None:
                    new_status = result
            elif marker == "failed":
                new_status = TaskStatus.failed
                snap.failure_reason = "远程状态标记为 failed"
            elif marker == "running":
                pass  # 保持 running
        else:
            # No marker: the runner may have been killed (e.g. OOM kill -9)
            # before writing a terminal status. Fail on stale timeout so the
            # task does not hang in running indefinitely.
            ref_time = task.started_at or task.submitted_at
            if _remote_read_incomplete(remote_snap):
                snap.warnings.append("stale timeout skipped because remote status read was incomplete")
            elif stale_timeout_seconds and ref_time:
                elapsed = (datetime.now() - ref_time).total_seconds()
                if elapsed > stale_timeout_seconds:
                    new_status = TaskStatus.failed
                    snap.failure_reason = f"no remote status after {int(elapsed)}s (timeout={stale_timeout_seconds}s)"
                else:
                    snap.warnings.append("运行中但远程无状态文件")
            else:
                snap.warnings.append("运行中但远程无状态文件")

    elif current in (
        TaskStatus.remote_completed,
        TaskStatus.downloaded,
        TaskStatus.analyzed,
        TaskStatus.failed,
        TaskStatus.cancelled,
    ):
        pass  # 不自动回退

    snap.recovered_status = new_status.value
    return new_status, snap


def _remote_read_incomplete(remote_snap) -> bool:
    return bool(remote_snap and remote_snap.warnings and not remote_snap.marker_exists)


def _check_exit_code(remote_snap, snap: TaskStatusSnapshot) -> TaskStatus | None:
    """Determine status from exit_code. Returns None if exit_code is missing (keep current)."""
    if remote_snap.exit_code_exists and remote_snap.exit_code == 0:
        return TaskStatus.remote_completed
    elif remote_snap.exit_code_exists and remote_snap.exit_code != 0:
        snap.failure_reason = f"远程退出码非零: {remote_snap.exit_code}"
        return TaskStatus.failed
    else:
        # marker=completed but .jobdesk_exit_code missing — cannot confirm success yet.
        # Keep current (non-terminal) status so next refresh can resolve it.
        snap.warnings.append("marker=completed 但 .jobdesk_exit_code 缺失，无法确认退出码")
        snap.failure_reason = "marker=completed 但退出码缺失，等待下次刷新"
        return None


def _parse_batch_control(extra_out: dict[str, bytes | None]) -> BatchControlSnapshot:
    """从合并批量读取的结果解析 batch_control 状态（不再单独发起 SSH）。

    Args:
        extra_out: ``{"BC:E": bytes|None, "BC:L": bytes|None}``，
            ``None`` 表示文件不存在或读取失败。
    """
    snap = BatchControlSnapshot()

    ec = extra_out.get("BC:E")
    if ec is not None:
        text = ec.decode("utf-8", errors="replace").strip()
        try:
            snap.exit_code = int(text)
        except ValueError:
            snap.warnings.append(f"batch_control_exit_code 非整数: {text!r}")

    log = extra_out.get("BC:L")
    if log is not None:
        snap.log_tail = log.decode("utf-8", errors="replace")

    snap.finished_marker_found = "BATCH_FINISHED" in snap.log_tail

    if snap.exit_code is not None and snap.exit_code != 0:
        snap.warnings.append(
            f"batch_control 退出码非零 ({snap.exit_code}) —— 部分 task 可能失败，请查看各 task 的 .jobdesk_status"
        )

    return snap
