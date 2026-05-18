"""文件传输相关数据模型。

TransferRecord 是业务记录，后续 GUI 和 manifest 都会用到。
放在 core 层，避免 remote 层承担业务模型职责。
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class TransferDirection(str, Enum):
    upload = "upload"
    download = "download"


class TransferStatus(str, Enum):
    planned = "planned"
    transferred = "transferred"
    skipped = "skipped"
    failed = "failed"


@dataclass
class TransferRecord:
    """单次文件传输记录。"""

    direction: TransferDirection
    local_path: str
    remote_path: str
    size_bytes: int | None = None
    status: TransferStatus = TransferStatus.planned
    reason: str = ""
    dry_run: bool = False
    category: str = "task"  # "task" | "shared"
