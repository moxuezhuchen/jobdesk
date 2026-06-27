from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path, PurePosixPath

from ..core.lifecycle import TaskStatus
from ..core.manifest import Manifest, TaskRecord
from ..core.run import RunPlan, RunSpec, build_run_plan, remote_run_dir
from ..core.transfer import TransferStatus
from ..remote.submitter import JobSubmitter
from .file_transfer_service import ensure_safe_remote_path
from .run_repository import MigrationError, RunRecord, RunRepository


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
        existing = [d.name for d in self.runs_dir.iterdir() if d.is_dir() and d.name.startswith(prefix + "-")]
        max_num = 0
        for name in existing:
            parts = name.split("-", 1)
            if len(parts) == 2 and parts[1].isdigit():
                max_num = max(max_num, int(parts[1]))
        candidate = max_num + 1
        while (self.runs_dir / f"{prefix}-{candidate:03d}").exists():
            candidate += 1
        return f"{prefix}-{candidate:03d}"

    def create_run(self, spec: RunSpec, run_id: str | None = None, local_dir: str = "") -> RunRecord:
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
        record = self._record_from_parts(plan, run_dir, manifest_path, batch_path, _status_summary(tasks), local_dir=local_dir)
        try:
            self.repository.create_run(record, tasks)
        except Exception:
            run_dir.rmdir()
            raise
        return self.repository.load_run(record.run_id)

    def list_runs(self) -> list[RunRecord]:
        return self.repository.list_runs()

    def load_run(self, run_id: str) -> RunRecord:
        self._run_dir(run_id)
        return self.repository.load_run(run_id)

    def migration_errors(self) -> list[MigrationError]:
        return self.repository.list_migration_errors()

    def update_run_from_manifest(self, run_id: str) -> RunRecord:
        record = self.load_run(run_id)
        if not record.manifest_path.exists():
            return record
        tasks = Manifest.read(record.manifest_path)
        self.repository.replace_tasks(run_id, tasks)
        return self.repository.load_run(run_id)

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
        self.repository.update_run(record)
        tasks = self.repository.load_tasks(run_id)
        expected = {task.task_id: task.status for task in tasks}
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
        )
        result = submitter.submit_batch()
        if result.updated_tasks:
            self.repository.merge_tasks(
                run_id,
                result.updated_tasks,
                expected_statuses=expected,
            )
        return result

    def refresh_run(self, run_id: str, ssh):
        from ..remote.status_refresh import refresh_task_statuses

        record = self.load_run(run_id)
        tasks = self.repository.load_tasks(run_id)
        expected = {task.task_id: task.status for task in tasks}
        result, updated = refresh_task_statuses(
            ssh,
            tasks,
            remote_run_dir(record.remote_dir, record.run_id),
            record.run_id,
        )
        self.repository.merge_tasks(run_id, updated, expected_statuses=expected)
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
        expected = {task.task_id: task.status for task in tasks}
        records = []
        failures = []
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
        self.repository.merge_tasks(run_id, tasks, expected_statuses=expected)
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

    def prepare_rerun(self, run_id: str) -> int:
        def mutation(tasks: list[TaskRecord]) -> list[TaskRecord]:
            active = [
                task.task_id
                for task in tasks
                if task.status in {TaskStatus.submitted, TaskStatus.running}
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
        expected = {task.task_id: task.status for task in tasks}
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
        merged = self.repository.merge_tasks(run_id, tasks, expected_statuses=expected)
        merged_by_id = {task.task_id: task for task in merged}
        confirmed = sum(
            1
            for task in tasks
            if task.status == TaskStatus.cancelled
            and merged_by_id[task.task_id].status == TaskStatus.cancelled
        )
        return confirmed, errors

    def delete_run(self, run_id: str) -> None:
        """Delete run directory, results, and analysis profile."""
        import shutil

        run_dir = self._run_dir(run_id)
        results_dir = (self.workspace_dir / "results" / run_id).resolve()
        if not results_dir.is_relative_to((self.workspace_dir / "results").resolve()):
            raise ValueError(f"run_id escapes results dir: {run_id}")
        # Delete results first; if this fails, metadata is preserved for recovery.
        if results_dir.exists():
            try:
                shutil.rmtree(results_dir)
            except OSError as exc:
                raise OSError(
                    f"Failed to delete results for run {run_id} "
                    f"(metadata preserved at {run_dir}): {exc}"
                ) from exc
        if run_dir.exists():
            shutil.rmtree(run_dir)
        self.repository.delete_run(run_id)

    def _run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
            raise ValueError(f"Invalid run_id: {run_id}")
        run_dir = (self.runs_dir / run_id).resolve()
        if not run_dir.is_relative_to(self.runs_dir.resolve()):
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
