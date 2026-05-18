"""覆盖保护策略纯逻辑模块。

不直接操作文件或 SFTP，只做策略决策。
"""

from dataclasses import dataclass
from enum import Enum


class OverwriteDecision(str, Enum):
    allow = "allow"
    skip = "skip"
    refuse = "refuse"
    require_overwrite_flag = "require_overwrite_flag"


@dataclass
class OverwriteResult:
    decision: OverwriteDecision
    reason: str


def decide_overwrite(
    same_batch: bool,
    size_same: bool | None,
    policy: str = "deny_cross_batch",
) -> OverwriteResult:
    """根据批次关系和文件大小判断覆盖行为。

    Args:
        same_batch: 源和目标是否属于同一个 Batch。
        size_same: 文件大小是否相同（None 表示未知/目标不存在）。
        policy: 覆盖策略，默认 deny_cross_batch。

    Returns:
        OverwriteResult。

    规则：
    - 目标不存在 → allow；
    - 同 batch + 大小相同 → skip；
    - 同 batch + 大小不同 + 默认策略 → refuse；
    - 跨 batch + 默认策略 → refuse；
    - overwrite 策略 → allow。
    """
    if policy == "overwrite":
        return OverwriteResult(OverwriteDecision.allow, "policy=overwrite，允许覆盖")

    if size_same is None:
        return OverwriteResult(OverwriteDecision.allow, "目标不存在，允许写入")

    if not same_batch:
        return OverwriteResult(
            OverwriteDecision.refuse,
            f"跨 Batch 拒绝覆盖 (policy={policy})",
        )

    if size_same:
        return OverwriteResult(OverwriteDecision.skip, "同 Batch 且文件大小相同，跳过")

    # same batch, different size
    if policy == "deny_cross_batch":
        return OverwriteResult(
            OverwriteDecision.require_overwrite_flag,
            "同 Batch 但大小不同，需要显式 overwrite 标志",
        )

    return OverwriteResult(OverwriteDecision.allow, "允许写入")
