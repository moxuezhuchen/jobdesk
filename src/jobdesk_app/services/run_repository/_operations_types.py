"""Shared dataclass types for the run_repository package."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Seconds a non-leader worker will wait for another worker that just
# authored the ``files_isolated`` journal advance to finish the cleanup
# (rmtree + advance-to-files_deleted + advance-to-completed) before
# taking over. Chosen to be well above normal local-filesystem rmtree
# latency while still bounded so a paused/abandoned leader does not pin
# the operation at ``files_isolated`` indefinitely.
_DELETE_CLEANUP_LEADER_GRACE_SECONDS = 0.2


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    run_id: str
    kind: str
    phase: str
    payload: dict
    last_error: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    owner_id: str | None = None
    lease_expires_at: str | None = None


@dataclass
class RunRecord:
    run_id: str
    server_id: str
    remote_dir: str
    command_template: str
    max_parallel: int
    mode: str
    created_at: str
    run_dir: Path
    manifest_path: Path
    batch_path: Path
    local_dir: str = ""
    status_summary: dict = field(default_factory=dict)
    env_init_scripts: list = field(default_factory=list)
    scheduler_type: str = "nohup"
    resources: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MergeResult:
    tasks: list
    accepted_task_ids: set


@dataclass(frozen=True)
class MigrationError:
    legacy_path: Path
    message: str
