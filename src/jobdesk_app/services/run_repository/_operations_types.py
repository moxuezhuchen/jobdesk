"""Shared dataclass types for the run_repository package."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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
