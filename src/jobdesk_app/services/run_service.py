from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..core.lifecycle import TaskStatus
from ..core.manifest import TaskRecord
from ..core.run import RunPlan, RunSpec, build_run_plan, remote_run_dir
from ..core.submit import SubmitResult
from ..core.transfer import TransferStatus
from ..remote.submitter import JobSubmitter
from .file_transfer_service import ensure_safe_remote_path
from .run_repository import (
    MigrationError,
    OperationRecord,
    RunRecord,
    RunRepository,
    _lexical_absolute,
    _reject_reparse_chain,
)
from .submit_ownership import (
    SUBMIT_HEARTBEAT_INTERVAL,
    SUBMIT_LEASE_SECONDS,
    _CheckpointSink,
    _SubmitOwnershipGuard,
)

# re-export so tests can patch run_service.SUBMIT_HEARTBEAT_INTERVAL
SUBMIT_HEARTBEAT_INTERVAL = SUBMIT_HEARTBEAT_INTERVAL

class RunService:
    def __init__(self, workspace_dir: str | Path | None = None, runs_dir: str | Path | None = None):
        if runs_dir:
            self.runs_dir = Path(runs_dir)
        else:
            from ..app_paths import get_app_data_dir
            self.runs_dir = get_app_data_dir() / "runs"
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()
        self.repository = RunRepository(self.runs_dir)

    def _next_run_id(self) -> str:
        prefix = datetime.now().strftime("%y%m%d")
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        existing = {
            d.name
            for d in self.runs_dir.iterdir()
            if d.is_dir() and d.name.startswith(prefix + "-")
        }
        existing.update(
            record.run_id
            for record in self.repository.list_runs()
            if record.run_id.startswith(prefix + "-")
        )
        existing.update(
            run_id
            for run_id in self.repository.incomplete_delete_run_ids()
            if run_id.startswith(prefix + "-")
        )
        max_num = 0
        for name in existing:
            parts = name.split("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                max_num = max(max_num, int(parts[1]))
        candidate = max_num + 1
        while (
            f"{prefix}-{candidate:03d}" in existing
            or (self.runs_dir / f"{prefix}-{candidate:03d}").exists()
        ):
            candidate += 1
        return f"{prefix}-{candidate:03d}"

    def create_run(self, spec: RunSpec, run_id: str | None = None, local_dir: str = "") -> RunRecord:
        workspace_anchor = _lexical_absolute(self.workspace_dir)
        if local_dir:
            requested_anchor = _lexical_absolute(Path(local_dir))
            if requested_anchor != workspace_anchor:
                raise ValueError(
                    "local_dir does not match service workspace: "
                    f"{requested_anchor} != {workspace_anchor}"
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
            self.repository.create_run(record, tasks)
        except Exception:
            try:
                run_dir.rmdir()
            except OSError:
                pass
            raise
        # An older delete recovery can remove the newly-created empty directory
        # before its tombstone is completed.  Once create_run commits, the
        # tombstone is either absent or completed, so recreating it is safe.
        run_dir.mkdir(parents=True, exist_ok=True)
        return self.repository.load_run(record.run_id)

    def list_runs(self) -> list[RunRecord]:
        return self.repository.list_runs()

    def load_run(self, run_id: str) -> RunRecord:
        self._run_dir(run_id)
        return self.repository.load_run(run_id)

    def migration_errors(self) -> list[MigrationError]:
        return self.repository.list_migration_errors()

    def retry_legacy_imports(self) -> list[MigrationError]:
        return self.repository.retry_legacy_imports()

    def submit_run(self, run_id: str, ssh, sftp, env_init_scripts: list[str] | None = None,
                   scheduler=None, resources=None):
        record = self.load_run(run_id)
        from ..remote.scheduler import ResourceSpec, make_adapter

        if env_init_scripts is None:
            env_init_scripts = list(record.env_init_scripts)
        else:
            record.env_init_scripts = list(env_init_scripts)
        if scheduler is None:
            scheduler = make_adapter(record.scheduler_type)
        else:
            record.scheduler_type = _scheduler_type(scheduler)
        if resources is None:
            resources = ResourceSpec.from_dict(record.resources)
        else:
            record.resources = asdict(resources)
        scheduler_type = _scheduler_type(scheduler)
        owner_id = str(uuid4())
        lease_seconds = SUBMIT_LEASE_SECONDS
        tasks, operations = self.repository.claim_submit_tasks(
            run_id,
            scheduler_type=scheduler_type,
            resources=asdict(resources),
            env_init_scripts=list(env_init_scripts),
            per_task=scheduler_type != "nohup",
            owner_id=owner_id,
            lease_seconds=lease_seconds,
        )
        if not tasks:
            return SubmitResult(record.run_id, 0, remote_run_dir(record.remote_dir, record.run_id))
        primary_error: Exception | None = None
        recovery_diagnostics: list[str] = []
        release_diagnostics: list[str] = []

        try:
            with _SubmitOwnershipGuard(
                self.repository,
                [op.operation_id for op in operations],
                owner_id,
                lease_seconds=lease_seconds,
            ) as guard:
                operation_by_task: dict[str, OperationRecord] = {}
                for operation in operations:
                    task_ids = operation.payload.get("task_ids")
                    if not isinstance(task_ids, list):
                        raise RuntimeError(
                            f"submit operation has invalid task ids: {operation.operation_id}"
                        )
                    for task_id in task_ids:
                        operation_by_task[str(task_id)] = operation

                sink = _CheckpointSink(
                    repository=self.repository,
                    guard=guard,
                    operation_by_task=operation_by_task,
                )

                self.repository.update_run(record)
                submitter = JobSubmitter(
                    tasks=tasks,
                    ssh=ssh,
                    sftp=sftp,
                    max_parallel=record.max_parallel,
                    remote_batch_dir=remote_run_dir(record.remote_dir, record.run_id),
                    batch_id=record.run_id,
                    env_init_scripts=list(env_init_scripts),
                    scheduler=scheduler,
                    resources=resources,
                    task_update_callback=sink.update_tasks,
                    remote_started_callback=sink.mark_remote_started,
                )
                result = submitter.submit_batch()
        except Exception as exc:
            primary_error = exc
            guard.stop_heartbeat()
            for operation in operations:
                try:
                    self.repository.recover_submit_operation(
                        operation.operation_id, owner_id=owner_id
                    )
                except Exception as recovery_exc:
                    recovery_diagnostics.append(
                        f"submit recovery failed for {operation.operation_id}: "
                        f"{type(recovery_exc).__name__}: {recovery_exc}"
                    )
            raise
        finally:
            guard.stop_heartbeat()
            for operation in operations:
                try:
                    self.repository.release_claimed_submit_operation(
                        operation.operation_id, owner_id=owner_id
                    )
                except Exception as release_exc:
                    release_diagnostics.append(
                        f"submit claim release failed for {operation.operation_id}: "
                        f"{type(release_exc).__name__}: {release_exc}"
                    )

            incomplete_ids: set[str] = set()
            try:
                incomplete_ids = {
                    operation.operation_id
                    for operation in self.repository.list_operations(incomplete_only=True)
                }
            except Exception as inspection_exc:
                release_diagnostics.append(
                    "submit cleanup state inspection failed: "
                    f"{type(inspection_exc).__name__}: {inspection_exc}"
                )
            for operation in operations:
                if operation.operation_id in incomplete_ids:
                    release_diagnostics.append(
                        "submit recovery left operation incomplete: "
                        f"{operation.operation_id}"
                    )

            cleanup_diagnostics = recovery_diagnostics + release_diagnostics
            if primary_error is not None:
                for diagnostic in cleanup_diagnostics:
                    primary_error.add_note(diagnostic)
            elif cleanup_diagnostics:
                raise RuntimeError(
                    "submit cleanup failed: " + "; ".join(cleanup_diagnostics)
                )
        return result

    def recover_submit_operations(self, run_id: str | None = None) -> int:
        recovered = (
            self.repository.recover_legacy_orphan_submit_tasks()
            if run_id is None
            else 0
        )
        for operation in self.repository.list_operations(incomplete_only=True):
            recovery_owner = str(uuid4())
            if (
                operation.kind == "submit"
                and (run_id is None or operation.run_id == run_id)
                and self.repository.acquire_submit_recovery(
                    operation.operation_id, recovery_owner
                )
                and self.repository.recover_submit_operation(
                    operation.operation_id, owner_id=recovery_owner
                )
            ):
                recovered += 1
        self.repository.prune_completed_operations(datetime.now() - timedelta(days=7))
        return recovered

    def refresh_run(self, run_id: str, ssh):
        from ..remote.status_refresh import refresh_task_statuses

        record = self.load_run(run_id)
        tasks = self.repository.load_tasks(run_id)
        expected = {task.task_id: task.model_copy(deep=True) for task in tasks}
        result, updated = refresh_task_statuses(
            ssh,
            tasks,
            remote_run_dir(record.remote_dir, record.run_id),
            record.run_id,
        )
        merged = self.repository.merge_tasks(run_id, updated, expected_tasks=expected)
        original_by_id = {task.task_id: task for task in tasks}
        accepted_task_ids = merged.accepted_task_ids
        accepted_transitions = {
            task.task_id
            for task in updated
            if task.task_id in accepted_task_ids
            and task.task_id in original_by_id
            and original_by_id[task.task_id].status != task.status
        }
        result.snapshots = [
            snapshot for snapshot in result.snapshots
            if snapshot.task_id in accepted_task_ids
        ]
        result.failures = [
            failure for failure in result.failures
            if failure.task_id in accepted_task_ids
        ]
        result.changed_count = len(accepted_transitions)
        return result

    def download_completed(self, run_id: str, sftp, patterns: list[str]):
        """Download declared outputs for remote_completed tasks.

        All-or-nothing per task: a task is marked ``downloaded`` only when every
        declared output transfers (or is skipped as identical). If any declared
        output is missing/fails, the task keeps its status and records the error.
        """
        record = self.load_run(run_id)
        return self._download_completed_locked(record, run_id, sftp, patterns)

    def _download_completed_locked(self, record: RunRecord, run_id: str, sftp, patterns: list[str]):
        tasks = self.repository.load_tasks(run_id)
        expected = {task.task_id: task.model_copy(deep=True) for task in tasks}
        records = []
        failures = []
        successful_task_records: dict[str, list] = {}
        download_base = Path(record.local_dir).resolve() if record.local_dir else self.workspace_dir
        for task in tasks:
            if task.status != TaskStatus.remote_completed:
                continue
            recs = []
            download_errors: list[str] = []
            requested_outputs: list[str] = []
            task_ok = False
            try:
                download_base.mkdir(parents=True, exist_ok=True)
                work_dir = task.remote_work_dir or task.remote_job_dir
                requested_outputs = _declared_outputs(task, patterns)
                for relative_output in requested_outputs:
                    safe_path = _safe_declared_result_path(relative_output)
                    remote_file = f"{work_dir.rstrip('/')}/{safe_path.as_posix()}"
                    local_file = download_base.joinpath(*safe_path.parts)
                    if not local_file.resolve().is_relative_to(download_base):
                        raise ValueError(f"declared result path escapes local dir: {relative_output}")
                    try:
                        rec = sftp.download_file(remote_file, local_file, overwrite=True, skip_if_same_size=False)
                        recs.append(rec)
                        if rec.status == TransferStatus.failed:
                            download_errors.append(f"{relative_output}: {rec.reason}")
                    except Exception as exc:
                        download_errors.append(f"{relative_output}: {exc}")
                successful = sum(
                    1
                    for r in recs
                    if r.status in (TransferStatus.transferred, TransferStatus.skipped)
                )
                task_ok = successful == len(requested_outputs) and bool(requested_outputs)
                if download_errors:
                    failures.append((task.task_id, "; ".join(download_errors)))
                elif not task_ok:
                    failures.append((task.task_id, "无匹配输出文件"))
            except ValueError as exc:
                download_errors.append(str(exc))
                failures.append((task.task_id, str(exc)))
            except Exception as exc:
                download_errors.append(str(exc))
                failures.append((task.task_id, str(exc)))
            records.extend(recs)
            if task_ok:
                task.status = TaskStatus.downloaded
                successful_task_records[task.task_id] = list(recs)
                if task.error_message and task.error_message.startswith("download:"):
                    task.error_message = None
            else:
                error_parts = []
                if download_errors:
                    error_parts = download_errors
                elif not requested_outputs:
                    error_parts = ["无匹配输出文件"]
                if error_parts:
                    task.error_message = "download: " + "; ".join(error_parts)
        merged = self.repository.merge_tasks(run_id, tasks, expected_tasks=expected)
        rejected_successes = set(successful_task_records) - merged.accepted_task_ids
        if rejected_successes:
            rejected_record_ids = {
                id(record)
                for task_id in rejected_successes
                for record in successful_task_records[task_id]
            }
            records = [record for record in records if id(record) not in rejected_record_ids]
            failures.extend(
                (
                    task_id,
                    "task state changed during download; downloaded status was not committed",
                )
                for task_id in sorted(rejected_successes)
            )
        return records, failures

    def prepare_retry_failed(self, run_id: str) -> int:
        changed = 0

        def mutation(tasks: list[TaskRecord]) -> list[TaskRecord]:
            nonlocal changed
            for task in tasks:
                if task.status == TaskStatus.failed:
                    task.status = TaskStatus.uploaded
                    task.error_message = None
                    changed += 1
            return tasks

        self.repository.mutate_tasks(run_id, mutation)
        return changed

    def confirm_submitted(
        self,
        run_id: str,
        task_ids: Iterable[str],
        remote_job_ids: dict[str, str] | None = None,
    ) -> list[str]:
        selected = self._require_task_ids(task_ids)
        accepted, _tasks = self.repository.resolve_uncertain_tasks(
            run_id,
            selected,
            action="confirm",
            remote_job_ids=remote_job_ids,
        )
        return accepted

    def abandon_submit(self, run_id: str, task_ids: Iterable[str]) -> list[str]:
        selected = self._require_task_ids(task_ids)
        accepted, _tasks = self.repository.resolve_uncertain_tasks(
            run_id,
            selected,
            action="abandon",
        )
        return accepted

    @staticmethod
    def _require_task_ids(task_ids: Iterable[str]) -> list[str]:
        selected = list(dict.fromkeys(task_id for task_id in task_ids if task_id.strip()))
        if not selected:
            raise ValueError("selected task IDs required")
        return selected

    def prepare_rerun(self, run_id: str) -> int:
        def mutation(tasks: list[TaskRecord]) -> list[TaskRecord]:
            active = [
                task.task_id
                for task in tasks
                if task.status
                in {
                    TaskStatus.submitting,
                    TaskStatus.uncertain,
                    TaskStatus.submitted,
                    TaskStatus.running,
                }
            ]
            if active:
                raise ValueError(f"cannot rerun active remote tasks: {', '.join(active)}")
            for task in tasks:
                task.status = TaskStatus.uploaded
                task.submitted_at = None
                task.started_at = None
                task.completed_at = None
                task.downloaded_at = None
                task.analyzed_at = None
                task.remote_job_id = None
                task.scheduler_type = "nohup"
                task.error_message = None
            return tasks

        return len(self.repository.mutate_tasks(run_id, mutation))

    def cancel_run(self, run_id: str, ssh) -> tuple[int, list[str]]:
        """Cancel remote jobs, recording cancellation only after the remote action succeeds."""
        record = self.load_run(run_id)
        return self._cancel_run_locked(record, run_id, ssh)

    def _cancel_run_locked(self, record: RunRecord, run_id: str, ssh) -> tuple[int, list[str]]:
        from ..remote.scheduler import make_adapter

        tasks = self.repository.load_tasks(run_id)
        expected = {task.task_id: task.model_copy(deep=True) for task in tasks}
        changed = 0
        errors: list[str] = []
        terminal = {
            TaskStatus.remote_completed,
            TaskStatus.downloaded,
            TaskStatus.analyzed,
            TaskStatus.failed,
            TaskStatus.cancelled,
        }
        cancelled_jobs: set[tuple[str, str]] = set()
        for task in tasks:
            if task.status in terminal:
                continue
            if task.status in {TaskStatus.local_ready, TaskStatus.uploaded}:
                task.status = TaskStatus.cancelled
                task.error_message = "cancelled before remote execution"
                changed += 1
                continue
            if not task.remote_job_id:
                errors.append(f"{task.task_id}: no remote job id available for cancellation")
                continue
            job_key = (task.scheduler_type or record.scheduler_type, task.remote_job_id)
            if job_key not in cancelled_jobs:
                try:
                    make_adapter(job_key[0]).cancel(ssh, job_key[1])
                    cancelled_jobs.add(job_key)
                except Exception as exc:
                    errors.append(f"{task.task_id}: remote cancellation failed: {exc}")
                    continue
            task.status = TaskStatus.cancelled
            task.error_message = "cancelled after remote termination request"
            changed += 1
        if not changed:
            return 0, errors
        merged = self.repository.merge_tasks(run_id, tasks, expected_tasks=expected)
        merged_by_id = {task.task_id: task for task in merged.tasks}
        rejected_cancellations = sorted(
            task.task_id
            for task in tasks
            if task.status == TaskStatus.cancelled
            and task.task_id not in merged.accepted_task_ids
            and (
                task.task_id not in merged_by_id
                or merged_by_id[task.task_id].status != TaskStatus.cancelled
            )
        )
        errors.extend(
            f"{task_id}: task state changed during cancellation; "
            "cancellation status was not committed"
            for task_id in rejected_cancellations
        )
        confirmed = sum(
            1
            for task in tasks
            if task.status == TaskStatus.cancelled
            and task.task_id in merged.accepted_task_ids
        )
        return confirmed, errors

    def delete_run(self, run_id: str) -> None:
        """Journal and execute a replayable deletion."""
        run_dir = self._run_dir(run_id)
        results_dir = _lexical_absolute(self.workspace_dir / "results" / run_id)
        if not results_dir.is_relative_to(
            _lexical_absolute(self.workspace_dir / "results")
        ):
            raise ValueError(f"run_id escapes results dir: {run_id}")
        operation = self.repository.prepare_delete_run(
            run_id,
            run_dir=run_dir,
            results_root=self.workspace_dir / "results",
            results_dir=results_dir,
        )
        self._recover_delete_operation(operation, raise_errors=True)

    def recover_delete_operations(self) -> int:
        """Resume incomplete deletions; return operations completed by this call."""
        completed = 0
        for operation in self.repository.list_operations(incomplete_only=True):
            if operation.kind != "delete":
                continue
            if self._recover_delete_operation(operation):
                completed += 1
        return completed

    def recover_delete_operations_globally(self) -> tuple[int, list[str]]:
        """Recover deletion journals for every trusted recorded workspace."""
        workspaces: set[Path] = set()
        errors: list[str] = []
        trusted_workspaces = {
            _lexical_absolute(path)
            for path in self.repository.list_workspace_roots()
        }
        for operation in self.repository.list_operations(incomplete_only=True):
            if operation.kind != "delete":
                continue
            try:
                bound_workspace = self.repository.delete_operation_workspace(
                    operation.operation_id
                )
                if bound_workspace is None:
                    raise ValueError("delete operation has no trusted workspace binding")
                workspace = _lexical_absolute(bound_workspace)
                if workspace not in trusted_workspaces:
                    raise ValueError(
                        f"workspace binding is not a trusted workspace: {workspace}"
                    )
                raw_root = operation.payload.get("results_root")
                if not isinstance(raw_root, str) or not raw_root:
                    raise ValueError("missing results_root")
                recorded_root_path = Path(raw_root)
                if not recorded_root_path.is_absolute():
                    raise ValueError("results_root must be absolute")
                results_root = _lexical_absolute(recorded_root_path)
                run_snapshot = operation.payload.get("run")
                if not isinstance(run_snapshot, dict):
                    raise ValueError("delete payload has no run snapshot")
                raw_local_dir = run_snapshot.get("local_dir")
                if not isinstance(raw_local_dir, str) or not raw_local_dir:
                    raise ValueError("run.local_dir must be a nonempty absolute path")
                local_dir_path = Path(raw_local_dir)
                if not local_dir_path.is_absolute():
                    raise ValueError("run.local_dir must be a nonempty absolute path")
                payload_workspace = _lexical_absolute(local_dir_path)
                if payload_workspace != workspace:
                    raise ValueError(
                        "run.local_dir does not match delete operation workspace binding"
                    )
                if results_root != _lexical_absolute(workspace / "results"):
                    raise ValueError(
                        "results_root does not match run.local_dir/results"
                    )
                _reject_reparse_chain(workspace, results_root)
                workspaces.add(workspace)
            except Exception as exc:
                errors.append(
                    f"delete recovery rejected {operation.operation_id}: "
                    f"{type(exc).__name__}: {exc}"
                )

        completed = 0
        for workspace in sorted(workspaces, key=str):
            try:
                completed += RunService(
                    workspace,
                    runs_dir=self.runs_dir,
                ).recover_delete_operations()
            except Exception as exc:
                errors.append(
                    f"delete recovery failed for {workspace}: "
                    f"{type(exc).__name__}: {exc}"
                )
        return completed, errors

    def _recover_delete_operation(
        self, operation: OperationRecord, *, raise_errors: bool = False
    ) -> bool:
        import shutil

        phase = operation.phase
        try:
            self._authorized_delete_workspace(operation)
            if phase == "files_deleted":
                return self.repository.advance_operation(
                    operation.operation_id, "files_deleted", "completed", complete=True
                )
            operation = self.repository.ensure_delete_trash_paths(
                operation.operation_id
            )
            phase = operation.phase
            run_dir, results_dir, trash_run_dir, trash_results_dir = (
                self._validated_delete_paths(operation)
            )
            if phase == "prepared":
                if not self.repository.delete_run_metadata(operation.operation_id):
                    return False
                phase = "metadata_deleted"
            if phase == "metadata_deleted":
                # Directory preparation may involve antivirus/filesystem latency,
                # so it must happen before the bounded isolation transaction.
                trash_run_dir.parent.mkdir(parents=True, exist_ok=True)
                trash_results_dir.parent.mkdir(parents=True, exist_ok=True)

                def isolate_files(stored: OperationRecord) -> None:
                    paths = self._validated_delete_paths(stored)
                    for source, trash in ((paths[0], paths[2]), (paths[1], paths[3])):
                        if trash.exists():
                            if source.exists():
                                raise OSError(
                                    f"Both managed and trash paths exist for {stored.run_id}"
                                )
                            continue
                        if not source.exists():
                            continue
                        source.replace(trash)

                if not self.repository.execute_delete_isolation(
                    operation.operation_id, isolate_files
                ):
                    return False
                phase = "files_isolated"
            if phase == "files_isolated":
                for trash, label in (
                    (trash_results_dir, "results"),
                    (trash_run_dir, "run directory"),
                ):
                    if trash.exists():
                        try:
                            shutil.rmtree(trash)
                        except OSError as exc:
                            raise OSError(
                                f"Failed to delete {label} for run {operation.run_id}: {exc}"
                            ) from exc
                if not self.repository.advance_operation(
                    operation.operation_id, "files_isolated", "files_deleted"
                ):
                    return False
                return self.repository.advance_operation(
                    operation.operation_id, "files_deleted", "completed", complete=True
                )
            return False
        except Exception as exc:
            self.repository.advance_operation(
                operation.operation_id,
                phase,
                phase,
                last_error=str(exc),
            )
            if raise_errors:
                raise
            return False

    def _authorized_delete_workspace(self, operation: OperationRecord) -> Path:
        """Validate independent delete authorization before filesystem mutation."""
        workspace = _lexical_absolute(self.workspace_dir)
        trusted = {
            _lexical_absolute(path) for path in self.repository.list_workspace_roots()
        }
        bound = self.repository.delete_operation_workspace(operation.operation_id)
        if bound is None or _lexical_absolute(bound) != workspace:
            raise ValueError("delete operation workspace binding mismatch")
        if workspace not in trusted:
            raise ValueError("delete operation workspace is not trusted")
        raw_root = operation.payload.get("results_root")
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
            raise ValueError("delete operation results_root must be absolute")
        if _lexical_absolute(Path(raw_root)) != _lexical_absolute(workspace / "results"):
            raise ValueError("delete operation results_root mismatches workspace binding")
        snapshot = operation.payload.get("run")
        if not isinstance(snapshot, dict):
            raise ValueError("delete operation has no run snapshot")
        raw_local_dir = snapshot.get("local_dir")
        if (
            not isinstance(raw_local_dir, str)
            or not Path(raw_local_dir).is_absolute()
            or _lexical_absolute(Path(raw_local_dir)) != workspace
        ):
            raise ValueError("delete operation run.local_dir mismatches workspace binding")
        return workspace

    def _validated_delete_paths(
        self, operation: OperationRecord
    ) -> tuple[Path, Path, Path, Path]:
        runs_root = _lexical_absolute(self.runs_dir)
        run_dir = _lexical_absolute(
            Path(str(operation.payload.get("run_dir", "")))
        )
        results_dir = _lexical_absolute(
            Path(str(operation.payload.get("results_dir", "")))
        )
        expected_run_dir = self._run_dir(operation.run_id)
        expected_results_dir = _lexical_absolute(
            self.workspace_dir / "results" / operation.run_id
        )
        if run_dir != expected_run_dir:
            raise ValueError(f"unsafe delete run path: {run_dir}")
        _reject_reparse_chain(runs_root, run_dir)
        results_root = _lexical_absolute(self.workspace_dir / "results")
        if results_dir != expected_results_dir:
            raise ValueError(f"unsafe delete results path: {results_dir}")
        _reject_reparse_chain(results_root, results_dir)
        run_trash_root = (
            self.runs_dir / ".jobdesk-trash" / operation.operation_id
        )
        results_trash_root = (
            results_root / ".jobdesk-trash" / operation.operation_id
        )
        run_trash_root = _lexical_absolute(run_trash_root)
        results_trash_root = _lexical_absolute(results_trash_root)
        trash_run_dir = _lexical_absolute(
            Path(str(operation.payload.get("trash_run_dir", "")))
        )
        trash_results_dir = _lexical_absolute(
            Path(str(operation.payload.get("trash_results_dir", "")))
        )
        if (
            trash_run_dir != run_trash_root / "run"
            or trash_results_dir != results_trash_root / "results"
            or not trash_run_dir.is_relative_to(runs_root)
            or not trash_results_dir.is_relative_to(results_root)
        ):
            raise ValueError("unsafe delete trash path")
        _reject_reparse_chain(runs_root, trash_run_dir)
        _reject_reparse_chain(results_root, trash_results_dir)
        return run_dir, results_dir, trash_run_dir, trash_results_dir

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

def _declared_outputs(task: TaskRecord, patterns: list[str]) -> list[str]:
    if task.remote_result_files:
        return list(task.remote_result_files)
    input_name = task.remote_task_files[0] if task.remote_task_files else task.task_id
    stem = input_name.rsplit(".", 1)[0] if "." in input_name else input_name
    results = []
    for pattern in patterns:
        if pattern.startswith("."):
            # Extension shorthand: ".log" → "<stem>.log"
            results.append(f"{stem}{pattern}")
        elif "*" in pattern:
            # Glob: "*.log" → "<stem>.log"
            results.append(f"{stem}{pattern.lstrip('*')}")
        else:
            # Plain filename or relative path: use as-is
            results.append(pattern)
    return results


def _safe_declared_result_path(value: str) -> PurePosixPath:
    if "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe declared result path: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe declared result path: {value}")
    return path


def _tasks_from_plan(plan: RunPlan) -> list[TaskRecord]:
    return [
        TaskRecord(
            task_id=task.task_id,
            batch_id=plan.run_id,
            remote_job_dir=task.remote_job_dir,
            task_files=[],
            remote_task_files=[task.source_name, *[Path(path).name for path in task.supporting_paths]],
            remote_result_files=list(task.remote_result_files),
            execution_profile="quick_run",
            discovery_name="files",
            server_id=plan.spec.server_id,
            remote_work_dir=plan.spec.remote_dir,
            max_parallel=plan.spec.max_parallel,
            rendered_command=task.command,
            status=TaskStatus.uploaded,
        )
        for task in plan.tasks
    ]


def _status_summary(tasks: list[TaskRecord]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for task in tasks:
        summary[task.status.value] = summary.get(task.status.value, 0) + 1
    return summary


def _scheduler_type(scheduler) -> str:
    from ..remote.scheduler import PBSAdapter, SlurmAdapter

    if isinstance(scheduler, SlurmAdapter):
        return "slurm"
    if isinstance(scheduler, PBSAdapter):
        return "pbs"
    return "nohup"
