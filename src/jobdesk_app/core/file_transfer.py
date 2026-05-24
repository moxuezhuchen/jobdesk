from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import uuid4

from .transfer import TransferDirection, TransferStatus


class OverwritePolicy(str, Enum):
    skip_same_size = "skip_same_size"
    overwrite = "overwrite"
    fail_if_exists = "fail_if_exists"


@dataclass(frozen=True)
class TransferPlan:
    direction: TransferDirection
    local_path: str
    remote_path: str
    is_dir: bool = False
    overwrite_policy: OverwritePolicy = OverwritePolicy.skip_same_size
    dry_run: bool = False


@dataclass
class TransferQueueItem:
    plan: TransferPlan
    id: str = field(default_factory=lambda: uuid4().hex)
    status: TransferStatus = TransferStatus.planned
    bytes_total: int | None = None
    bytes_done: int = 0
    reason: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TransferQueue:
    def __init__(self):
        self.items: list[TransferQueueItem] = []

    def add(self, plan: TransferPlan) -> TransferQueueItem:
        item = TransferQueueItem(plan=plan)
        self.items.append(item)
        return item

    def cancel(self, item_id: str) -> bool:
        for item in self.items:
            if item.id == item_id and item.status == TransferStatus.planned:
                item.status = TransferStatus.failed
                item.reason = "cancelled"
                item.finished_at = datetime.now()
                return True
        return False

    def run(self, runner) -> list[TransferQueueItem]:
        for item in self.items:
            if item.status != TransferStatus.planned:
                continue
            self._run_item(item, runner)
        return self.items

    def retry_failed(self, runner) -> list[TransferQueueItem]:
        for item in self.items:
            if item.status == TransferStatus.failed and item.reason != "cancelled":
                item.status = TransferStatus.planned
                item.reason = ""
                item.started_at = None
                item.finished_at = None
                self._run_item(item, runner)
        return self.items

    def _run_item(self, item: TransferQueueItem, runner) -> None:
        item.started_at = datetime.now()
        try:
            record = runner(item.plan)
            item.status = record.status
            item.bytes_total = record.size_bytes
            item.bytes_done = record.size_bytes or 0
            item.reason = record.reason
        except Exception as exc:
            item.status = TransferStatus.failed
            item.reason = str(exc)
        finally:
            item.finished_at = datetime.now()


def policy_to_transfer_flags(policy: OverwritePolicy) -> tuple[bool, bool]:
    if policy == OverwritePolicy.overwrite:
        return True, False
    if policy == OverwritePolicy.fail_if_exists:
        return False, False
    return False, True
