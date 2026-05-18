"""dry-run 统一表示模型。

可被上传/下载/提交/覆盖检查复用。
"""

from dataclasses import dataclass, field
from enum import Enum


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


@dataclass
class DryRunAction:
    """单个 dry-run 动作。"""

    action_type: str        # e.g. "upload_file", "download_file", "submit_batch", "overwrite_check"
    target: str             # e.g. local_path or remote_path or batch_id
    description: str
    would_modify_remote: bool = False
    would_modify_local: bool = False
    risk_level: RiskLevel = RiskLevel.low


@dataclass
class DryRunPlan:
    """一组 dry-run 动作的汇总。"""

    title: str
    actions: list[DryRunAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def action_count(self) -> int:
        return len(self.actions)

    @property
    def has_risks(self) -> bool:
        return any(a.risk_level != RiskLevel.low for a in self.actions)
