from enum import Enum


class TaskStatus(str, Enum):
    """任务生命周期状态枚举。

    注意：failed 可以从任意非终态进入。
    failed 重跑不是回到原 Batch，而是创建新 Batch。
    """

    local_ready = "local_ready"
    uploaded = "uploaded"
    submitted = "submitted"
    running = "running"
    remote_completed = "remote_completed"
    downloaded = "downloaded"
    analyzed = "analyzed"
    failed = "failed"
    cancelled = "cancelled"


# 合法的状态迁移对
_ALLOWED_TRANSITIONS: set[tuple[TaskStatus, TaskStatus]] = {
    # 正向主线
    (TaskStatus.local_ready, TaskStatus.uploaded),
    (TaskStatus.uploaded, TaskStatus.submitted),
    (TaskStatus.submitted, TaskStatus.running),
    (TaskStatus.running, TaskStatus.remote_completed),
    (TaskStatus.remote_completed, TaskStatus.downloaded),
    (TaskStatus.downloaded, TaskStatus.analyzed),
    # 回退（远程输入不存在时）
    (TaskStatus.uploaded, TaskStatus.local_ready),
    # 重试下载
    (TaskStatus.remote_completed, TaskStatus.remote_completed),
}

# 终态集合：analyzed 是正常终态，failed 是异常终态
_TERMINAL_STATUSES: set[TaskStatus] = {
    TaskStatus.analyzed,
    TaskStatus.failed,
    TaskStatus.cancelled,
}


def can_transition(from_status: TaskStatus, to_status: TaskStatus) -> bool:
    """检查是否允许从 from_status 迁移到 to_status。

    - failed 可以从任意非终态进入。
    - failed 本身是终态，不能从 failed 迁出。
    - analyzed 是终态，不能从 analyzed 迁出。
    - 如果 from_status 已经是终态，拒绝所有迁移。
    """
    if from_status in _TERMINAL_STATUSES:
        return False
    if to_status in (TaskStatus.failed, TaskStatus.cancelled):
        return True
    return (from_status, to_status) in _ALLOWED_TRANSITIONS


def allowed_transitions_from(status: TaskStatus) -> set[TaskStatus]:
    """返回从给定状态可以迁移到的所有目标状态集合。"""
    if status in _TERMINAL_STATUSES:
        return set()
    targets = {TaskStatus.failed, TaskStatus.cancelled}
    for src, dst in _ALLOWED_TRANSITIONS:
        if src == status:
            targets.add(dst)
    return targets
