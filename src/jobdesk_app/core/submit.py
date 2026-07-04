"""任务提交相关数据模型。

SubmitMode / SubmitPlan / SubmitResult 是业务记录，
后续 GUI 会用到。放在 core 层。
"""

from dataclasses import dataclass, field
from enum import Enum

from .manifest import TaskRecord


class SubmitMode(str, Enum):
    all = "all"
    selected = "selected"
    unfinished = "unfinished"


@dataclass
class SubmitPlan:
    """一次提交的预计划（dry-run 输出）。"""

    batch_id: str
    max_parallel: int
    task_count: int
    selected_task_ids: list[str]
    remote_batch_dir: str
    generated_files: list[str] = field(default_factory=list)
    control_command: str = ""
    dry_run: bool = True


@dataclass
class SubmitResult:
    """一次提交的执行结果。"""

    batch_id: str
    submitted_task_count: int
    remote_batch_dir: str
    control_script_path: str = ""
    control_log_path: str = ""
    control_nohup_log_path: str = ""
    nohup_command: str = ""
    updated_task_ids: list[str] = field(default_factory=list)
    updated_tasks: list[TaskRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
