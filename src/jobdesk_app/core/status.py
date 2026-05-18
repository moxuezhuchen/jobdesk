"""本地状态刷新使用的数据结构。

不依赖 SSH，只负责纯状态合并逻辑。
"""

from dataclasses import dataclass, field
from .models import FailureRecord


@dataclass
class TaskStatusSnapshot:
    """单个任务的状态快照，结合本地 Manifest 和远程读取结果。"""

    task_id: str
    batch_id: str
    previous_status: str
    recovered_status: str
    remote_status_marker: str | None = None
    remote_exit_code: int | None = None
    has_submit_log: bool = False
    warnings: list[str] = field(default_factory=list)
    failure_reason: str | None = None


@dataclass
class BatchControlSnapshot:
    """batch_control 状态的快照。"""

    log_tail: str = ""
    exit_code: int | None = None
    finished_marker_found: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass
class StatusRefreshResult:
    """一次状态刷新的完整结果。"""

    batch_id: str
    task_count: int
    changed_count: int = 0
    snapshots: list[TaskStatusSnapshot] = field(default_factory=list)
    failures: list[FailureRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    batch_control: BatchControlSnapshot | None = None
