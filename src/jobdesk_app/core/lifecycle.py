from enum import Enum


class TaskStatus(str, Enum):
    """任务生命周期状态枚举。

    任意非终态都可进入 failed/cancelled。重试通过 manifest_ops 将
    failed 就地重置为 uploaded（见 reset_failed_to_uploaded）。
    """

    local_ready = "local_ready"
    uploaded = "uploaded"
    submitting = "submitting"
    submitted = "submitted"
    running = "running"
    remote_completed = "remote_completed"
    downloaded = "downloaded"
    analyzed = "analyzed"
    failed = "failed"
    cancelled = "cancelled"
