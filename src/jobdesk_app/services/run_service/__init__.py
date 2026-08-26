"""RunService — shared run coordination for both CLI and GUI."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from jobdesk_app.core.atomic_write import atomic_write_text
from jobdesk_app.core.configuration_binding import ConfigurationBinding
from jobdesk_app.core.manifest import TaskRecord
from jobdesk_app.core.run import RunPlan, RunSpec, build_run_plan

# Explicit re-export for tests that monkeypatch run_service.JobSubmitter
from jobdesk_app.remote.submitter import JobSubmitter as JobSubmitter
from jobdesk_app.services.file_transfer_service import ensure_safe_remote_path
from jobdesk_app.services.run_repository import (
    MigrationError,
    RunRecord,
    RunRepository,
    _lexical_absolute,
)
from jobdesk_app.services.submit_ownership import SUBMIT_HEARTBEAT_INTERVAL

from . import _cancel, _confirm, _delete, _download, _helpers, _progress, _refresh, _rerun, _submit

# re-export so tests can patch run_service.SUBMIT_HEARTBEAT_INTERVAL
SUBMIT_HEARTBEAT_INTERVAL = SUBMIT_HEARTBEAT_INTERVAL

# ---- re-export helpers for backward compatibility with tests & compat layer ----
_declared_outputs = _helpers._declared_outputs
_safe_declared_result_path = _helpers._safe_declared_result_path
_scheduler_type = _helpers._scheduler_type
_status_summary = _helpers._status_summary
_tasks_from_plan = _helpers._tasks_from_plan


class RunService:
    def __init__(self, workspace_dir: str | Path | None = None, runs_dir: str | Path | None = None):
        if runs_dir:
            self.runs_dir = Path(runs_dir)
        else:
            from jobdesk_app.app_paths import get_app_data_dir

            self.runs_dir = get_app_data_dir() / "runs"
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()
        self.repository = RunRepository(self.runs_dir)

    def _next_run_id(self) -> str:
        prefix = datetime.now().strftime("%y%m%d")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        existing = {d.name for d in self.runs_dir.iterdir() if d.is_dir() and d.name.startswith(prefix + "-")}
        existing.update(
            record.run_id for record in self.repository.list_runs() if record.run_id.startswith(prefix + "-")
        )
        existing.update(
            run_id for run_id in self.repository.incomplete_delete_run_ids() if run_id.startswith(prefix + "-")
        )
        max_num = 0
        for name in existing:
            parts = name.split("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                max_num = max(max_num, int(parts[1]))
        candidate = max_num + 1
        while f"{prefix}-{candidate:03d}" in existing or (self.runs_dir / f"{prefix}-{candidate:03d}").exists():
            candidate += 1
        return f"{prefix}-{candidate:03d}"

    def create_run(self, spec: RunSpec, run_id: str | None = None, local_dir: str = "") -> RunRecord:
        """Create a legacy run without a configuration-contract binding."""
        return self._create_run(spec, run_id=run_id, local_dir=local_dir, binding=None)

    def create_run_with_configuration_binding(
        self,
        spec: RunSpec,
        binding: ConfigurationBinding,
        run_id: str | None = None,
        local_dir: str = "",
    ) -> RunRecord:
        """Atomically create a run and persist its accepted configuration binding.

        If database persistence fails, the just-created empty run directory is
        removed so callers never observe a filesystem-only run.
        """
        return self._create_run(spec, run_id=run_id, local_dir=local_dir, binding=binding)

    def _create_run(
        self,
        spec: RunSpec,
        *,
        run_id: str | None,
        local_dir: str,
        binding: ConfigurationBinding | None,
    ) -> RunRecord:
        workspace_anchor = _lexical_absolute(self.workspace_dir)
        if local_dir:
            requested_anchor = _lexical_absolute(Path(local_dir))
            if requested_anchor != workspace_anchor:
                raise ValueError(
                    f"local_dir does not match service workspace: {requested_anchor} != {workspace_anchor}"
                )
        ensure_safe_remote_path(spec.remote_dir)
        for src in (*spec.sources, *spec.supporting_sources):
            ensure_safe_remote_path(src.path)
        if run_id is None:
            while True:
                run_id = self._next_run_id()
                run_dir = self.runs_dir / run_id
                try:
                    run_dir.mkdir(parents=True, exist_ok=False)
                    break
                except FileExistsError:
                    continue
        else:
            run_dir = self._run_dir(run_id)
            run_dir.mkdir(parents=True, exist_ok=False)
        plan = build_run_plan(spec, run_id)
        manifest_path = run_dir / "manifest.tsv"
        batch_path = run_dir / "batch.json"
        tasks = _tasks_from_plan(plan)
        record = self._record_from_parts(
            plan,
            run_dir,
            manifest_path,
            batch_path,
            _status_summary(tasks),
            local_dir=str(workspace_anchor),
        )
        try:
            if binding is None:
                self.repository.create_run(record, tasks)
            else:
                self.repository.create_run_with_configuration_binding(record, tasks, binding)
        except Exception:
            try:
                run_dir.rmdir()
            except OSError:
                pass
            raise
        run_dir.mkdir(parents=True, exist_ok=True)
        return self.repository.load_run(record.run_id)

    def list_runs(self) -> list[RunRecord]:
        return self.repository.list_runs()

    def load_run(self, run_id: str) -> RunRecord:
        self._run_dir(run_id)
        return self.repository.load_run(run_id)

    def load_configuration_binding(self, run_id: str) -> ConfigurationBinding | None:
        """Return the immutable configuration admission bound to ``run_id``."""

        self._run_dir(run_id)
        return self.repository.load_configuration_binding(run_id)

    def load_tasks(self, run_id: str) -> list[TaskRecord]:
        """Return the persisted task snapshot through the service boundary."""
        self._run_dir(run_id)
        return self.repository.load_tasks(run_id)

    def mutate_tasks(
        self,
        run_id: str,
        mutation: Callable[[list[TaskRecord]], list[TaskRecord]],
    ) -> list[TaskRecord]:
        """Apply an atomic task mutation without exposing the repository."""
        self._run_dir(run_id)
        return self.repository.mutate_tasks(run_id, mutation)

    def update_run(self, record: RunRecord) -> RunRecord:
        """Persist a run-record update through the application service boundary."""
        self._run_dir(record.run_id)
        return self.repository.update_run(record)

    def load_run_provenance(self, run_id: str) -> dict | None:
        """Read accepted producer provenance without exposing SQLite internals."""
        self._run_dir(run_id)
        return self.repository.load_run_provenance(run_id)

    def migration_errors(self) -> list[MigrationError]:
        return self.repository.list_migration_errors()

    def retry_legacy_imports(self) -> list[MigrationError]:
        return self.repository.retry_legacy_imports()

    def submit_run(
        self,
        run_id: str,
        ssh,
        sftp,
        env_init_scripts: list[str] | None = None,
        scheduler=None,
        resources=None,
        max_cores: int | None = None,
    ):
        return _submit.submit_run(self, run_id, ssh, sftp, env_init_scripts, scheduler, resources, max_cores)

    def persist_confflow_provenance(
        self,
        run_id: str,
        capability: dict,
        *,
        resolved_executable: str,
        resolved_realpath: str = "",
        executable_identity: dict[str, object] | None = None,
    ) -> None:
        """Persist accepted producer identity in SQLite and the run directory."""
        record = self.load_run(run_id)
        self.repository.record_run_provenance(
            run_id,
            capability,
            resolved_executable=resolved_executable,
            resolved_realpath=resolved_realpath,
        )
        manifest = {
            "content_schema": "confflow.provenance.v1",
            "run_id": run_id,
            "resolved_executable": resolved_executable,
            "resolved_realpath": resolved_realpath,
            "executable_identity": executable_identity or {},
            "capability": capability,
        }
        atomic_write_text(
            record.run_dir / "provenance.json",
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

    def recover_submit_operations(self, run_id: str | None = None) -> int:
        return _submit.recover_submit_operations(self, run_id)

    def refresh_run(self, run_id: str, ssh):
        return _refresh.refresh_run(self, run_id, ssh)

    def download_completed(self, run_id: str, sftp, patterns: list[str]):
        return _download.download_completed(self, run_id, sftp, patterns)

    def sync_progress(self, run_id: str, sftp):
        return _progress.sync_progress(self, run_id, sftp)

    def prepare_retry_failed(self, run_id: str) -> int:
        return _rerun.prepare_retry_failed(self, run_id)

    def confirm_submitted(
        self,
        run_id: str,
        task_ids: Iterable[str],
        remote_job_ids: dict[str, str] | None = None,
    ) -> list[str]:
        return _confirm.confirm_submitted(self, run_id, task_ids, remote_job_ids)

    def abandon_submit(self, run_id: str, task_ids: Iterable[str]) -> list[str]:
        return _confirm.abandon_submit(self, run_id, task_ids)

    @staticmethod
    def _require_task_ids(task_ids: Iterable[str]) -> list[str]:
        return _confirm._require_task_ids(task_ids)

    def prepare_rerun(self, run_id: str) -> int:
        return _rerun.prepare_rerun(self, run_id)

    def cancel_run(self, run_id: str, ssh) -> tuple[int, list[str]]:
        return _cancel.cancel_run(self, run_id, ssh)

    def _cancel_run_locked(self, record: RunRecord, run_id: str, ssh) -> tuple[int, list[str]]:
        return _cancel._cancel_run_locked(self.repository, self.workspace_dir, record, run_id, ssh)

    def delete_run(self, run_id: str) -> None:
        return _delete.delete_run(self, run_id)

    def recover_delete_operations(self) -> int:
        return _delete.recover_delete_operations(self)

    def recover_delete_operations_globally(self) -> tuple[int, list[str]]:
        return _delete.recover_delete_operations_globally(self)

    def _recover_delete_operation(self, operation, *, raise_errors: bool = False) -> bool:
        return _delete._recover_delete_operation(self, operation, raise_errors=raise_errors)

    def _wait_for_files_isolated_leader(
        self,
        operation_id: str,
        *,
        grace_seconds: float,
    ) -> bool:
        return _delete._wait_for_files_isolated_leader(self, operation_id, grace_seconds=grace_seconds)

    def _authorized_delete_workspace(self, operation) -> Path:
        return _delete._authorized_delete_workspace(self, operation)

    def _validated_delete_paths(self, operation) -> tuple[Path, Path, Path, Path]:
        return _delete._validated_delete_paths(self, operation)

    def _run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError(f"Invalid run_id: {run_id}")
        run_dir = _lexical_absolute(self.runs_dir / run_id)
        if not run_dir.is_relative_to(_lexical_absolute(self.runs_dir)):
            raise ValueError(f"run_id escapes runs_dir: {run_id}")
        return run_dir

    def _record_from_parts(
        self,
        plan: RunPlan,
        run_dir: Path,
        manifest_path: Path,
        batch_path: Path,
        status_summary: dict[str, int],
        local_dir: str = "",
    ) -> RunRecord:
        return RunRecord(
            run_id=plan.run_id,
            server_id=plan.spec.server_id,
            remote_dir=plan.spec.remote_dir,
            command_template=plan.spec.command_template,
            max_parallel=plan.spec.max_parallel,
            mode=plan.spec.mode.value,
            created_at=plan.created_at.isoformat(),
            run_dir=run_dir,
            manifest_path=manifest_path,
            batch_path=batch_path,
            local_dir=local_dir,
            status_summary=status_summary,
            env_init_scripts=[],
            scheduler_type="nohup",
            resources={},
        )
