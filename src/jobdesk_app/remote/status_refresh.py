"""远程状态刷新模块。

结合 Manifest + 远程 JobDesk 状态标记文件，更新任务生命周期状态。
不下载输出文件、不做本地分析、不提交任务。
"""

import shlex
from datetime import datetime
from pathlib import Path

from ..core.lifecycle import TaskStatus
from ..core.manifest import Manifest, TaskRecord
from ..core.models import FailureRecord
from ..core.status import BatchControlSnapshot, StatusRefreshResult, TaskStatusSnapshot
from .status import read_remote_task_status, read_remote_task_statuses_batch


def refresh_batch_status(
    ssh,      # SSHClientWrapper
    manifest_path: Path,
    remote_batch_dir: str,
    batch_id: str,
    write: bool = False,
    log_tail_lines: int = 50,
    control_subdir: str = "_batch",
    stale_timeout_seconds: int | None = None,
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
    result = StatusRefreshResult(batch_id=batch_id, task_count=len(tasks))

    # 读取 batch_control 状态
    result.batch_control = _read_batch_control(ssh, remote_batch_dir, control_subdir)

    # 批量读取所有 task 的远程状态文件（一条 SSH 命令）。
    # 没有 remote_job_dir 的 task 不会被远程查询。
    batch_pairs = [(t.task_id, t.remote_job_dir) for t in tasks if t.remote_job_dir]
    if batch_pairs:
        batch_snapshots = read_remote_task_statuses_batch(
            ssh, batch_pairs, log_tail_lines=log_tail_lines
        )
    else:
        batch_snapshots = {}

    # 遍历每个任务
    changed_tasks: dict[str, TaskRecord] = {}
    for task in tasks:
        old_status = task.status
        if task.remote_job_dir:
            remote_snap = batch_snapshots.get(task.task_id)
            if remote_snap is None:
                # 防御：批量读取应已为该 task 生成 snapshot；缺失则回退单读。
                remote_snap = read_remote_task_status(
                    ssh, task.task_id, task.remote_job_dir, log_tail_lines
                )
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
            result.failures.append(FailureRecord(
                task_id=task.task_id,
                batch_id=batch_id,
                stage="runtime",
                reason=reason,
                source_file=f"{task.remote_job_dir}/.jobdesk_submit.log",
                context=log_tail[-200:] if log_tail else None,
            ))

        # 准备写回
        if write:
            task.status = new_status
            if new_status == TaskStatus.remote_completed and task.completed_at is None:
                task.completed_at = datetime.now()
            if new_status == TaskStatus.failed and task.error_message is None:
                task.error_message = snap.failure_reason
            changed_tasks[task.task_id] = task

    result.warnings.extend(result.batch_control.warnings)

    # 写回 Manifest
    if write and changed_tasks:
        Manifest.write(manifest_path, tasks)

    return result


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
                new_status = _check_exit_code(remote_snap, snap)
            elif marker == "failed":
                new_status = TaskStatus.failed
                snap.failure_reason = "远程状态标记为 failed"
                snap.warnings.append("uploaded 任务在远程已标记为 failed")

    elif current == TaskStatus.submitted:
        if remote_snap and remote_snap.marker_exists:
            marker = remote_snap.status_marker.strip()
            if marker == "running":
                new_status = TaskStatus.running
            elif marker in ("completed",):
                new_status = _check_exit_code(remote_snap, snap)
            elif marker == "failed":
                new_status = TaskStatus.failed
                snap.failure_reason = "远程状态标记为 failed"
        else:
            # Check for stale timeout
            if stale_timeout_seconds and task.submitted_at:
                from datetime import datetime
                elapsed = (datetime.now() - task.submitted_at).total_seconds()
                if elapsed > stale_timeout_seconds:
                    new_status = TaskStatus.failed
                    snap.failure_reason = f"no remote response after {int(elapsed)}s (timeout={stale_timeout_seconds}s)"
                else:
                    snap.warnings.append("已提交但远程无状态文件")
            else:
                snap.warnings.append("已提交但远程无状态文件")

    elif current == TaskStatus.running:
        if remote_snap and remote_snap.marker_exists:
            marker = remote_snap.status_marker.strip()
            if marker == "completed":
                new_status = _check_exit_code(remote_snap, snap)
            elif marker == "failed":
                new_status = TaskStatus.failed
                snap.failure_reason = "远程状态标记为 failed"
            elif marker == "running":
                pass  # 保持 running
        else:
            snap.warnings.append("运行中但远程无状态文件")

    elif current in (TaskStatus.remote_completed, TaskStatus.downloaded,
                     TaskStatus.analyzed, TaskStatus.failed, TaskStatus.cancelled):
        pass  # 不自动回退

    snap.recovered_status = new_status.value
    return new_status, snap


def _check_exit_code(remote_snap, snap: TaskStatusSnapshot) -> TaskStatus:
    if remote_snap.exit_code_exists and remote_snap.exit_code == 0:
        return TaskStatus.remote_completed
    elif remote_snap.exit_code_exists and remote_snap.exit_code != 0:
        snap.failure_reason = f"远程退出码非零: {remote_snap.exit_code}"
        return TaskStatus.failed
    else:
        return TaskStatus.remote_completed


def _read_batch_control(ssh, remote_batch_dir: str, control_subdir: str = "_batch") -> BatchControlSnapshot:
    """读取 batch_control 相关状态文件。"""
    snap = BatchControlSnapshot()
    control_dir = f"{remote_batch_dir.rstrip('/')}/{control_subdir}"
    cd_q = shlex.quote(control_dir)

    # 读取 batch_control_exit_code
    try:
        r = ssh.run(
            f"test -f {cd_q}/batch_control_exit_code && cat {cd_q}/batch_control_exit_code"
            f" || echo '__NOT_FOUND__'",
            timeout=10,
        )
        if "__NOT_FOUND__" not in r.stdout:
            try:
                snap.exit_code = int(r.stdout.strip())
            except ValueError:
                snap.warnings.append(f"batch_control_exit_code 非整数: {r.stdout.strip()!r}")
    except Exception as e:
        snap.warnings.append(f"读取 batch_control_exit_code 失败: {e}")

    # 读取 batch_control.log tail
    try:
        r = ssh.run(
            f"test -f {cd_q}/batch_control.log && tail -n 20 {cd_q}/batch_control.log 2>/dev/null"
            f" || echo '__NOT_FOUND__'",
            timeout=15,
        )
        if "__NOT_FOUND__" not in r.stdout:
            snap.log_tail = r.stdout
    except Exception as e:
        snap.warnings.append(f"读取 batch_control.log 失败: {e}")

    # 检查 BATCH_FINISHED
    snap.finished_marker_found = "BATCH_FINISHED" in snap.log_tail

    if snap.exit_code is not None and snap.exit_code != 0:
        snap.warnings.append(
            f"batch_control 退出码非零 ({snap.exit_code}) —— "
            f"部分 task 可能失败，请查看各 task 的 .jobdesk_status"
        )

    return snap
