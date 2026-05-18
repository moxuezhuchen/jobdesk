from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config.runtime import ResolvedExecutionContext
from ..config.servers import load_servers
from ..core.analyzer import analyze_tasks
from ..core.batch import read_batch_json
from ..core.grouping import compute_summary
from ..core.lifecycle import TaskStatus
from ..core.manifest import Manifest, TaskRecord
from ..core.models import FailureRecord
from ..core.outputs import (
    append_failures_tsv,
    write_failures_tsv,
    write_final_results_tsv,
    write_group_summary_tsv,
    write_job_status,
    write_summary_json,
)
from ..core.status import StatusRefreshResult
from ..core.submit import SubmitMode, SubmitResult
from ..core.transfer import TransferRecord, TransferStatus
from ..remote.status_refresh import refresh_batch_status
from ..remote.submitter import JobSubmitter
from .batch_service import (
    BatchCreateResult,
    create_batch,
    discover_task_packages,
    list_batches,
    load_batch,
    load_latest_batch,
)
from .project_service import ProjectContext
from .preflight import preflight_project


@dataclass(frozen=True)
class _GroupKey:
    server_id: str
    execution_profile: str
    remote_work_dir: str


class WorkflowService:
    def __init__(self, ctx: ProjectContext):
        self.ctx = ctx

    def scan_inputs(self):
        return discover_task_packages(self.ctx)

    def preflight(self, binding_store=None, servers_path=None):
        return preflight_project(self.ctx, binding_store, servers_path)

    def list_batches(self):
        return list_batches(self.ctx)

    def load_batch(self, batch_id: str) -> BatchCreateResult | None:
        return load_batch(self.ctx, batch_id)

    def load_latest_batch(self) -> BatchCreateResult | None:
        return load_latest_batch(self.ctx)

    def create_batch(
        self,
        packages: list,
        resolved_contexts: dict[str, ResolvedExecutionContext],
        batch_id: str | None = None,
    ) -> BatchCreateResult:
        return create_batch(self.ctx, packages, resolved_contexts, batch_id)

    def analyze_batch(self, tasks: list[TaskRecord], batch_id: str):
        results_base = self.ctx.local_result_dir
        results, failures = analyze_tasks(self.ctx.project_config, tasks, results_base, batch_id)
        summaries, _ = compute_summary(tasks, results)

        out_dir = self.ctx.local_result_dir / batch_id
        out_dir.mkdir(parents=True, exist_ok=True)
        write_final_results_tsv(results, out_dir / "final_results.tsv")
        write_failures_tsv(failures, out_dir / "failures.tsv")
        write_group_summary_tsv(summaries, out_dir / "group_summary.tsv")
        write_summary_json(
            batch_id=batch_id,
            task_count=len(tasks),
            analyzed_count=len(set(r.task_id for r in results)),
            result_count=len(results),
            failure_count=len(failures),
            group_count=len(summaries),
            output_path=out_dir / "summary.json",
        )
        return results, failures, summaries

    def upload_tasks(
        self,
        tasks: list[TaskRecord],
        sftp_factory,
        dry_run: bool = False,
        batch_dir: Path | None = None,
        manifest_path: Path | None = None,
    ):
        records: list[TransferRecord] = []
        failures: list[FailureRecord] = []
        shared_records = []
        shared_target = "_shared"
        if batch_dir and batch_dir.exists():
            batch_json = batch_dir / "batch.json"
            if batch_json.exists():
                bm = read_batch_json(batch_json)
                shared_records = bm.shared_files
                shared_target = bm.shared_target_subdir

        groups: dict[tuple[str, str], list[TaskRecord]] = defaultdict(list)
        for t in tasks:
            if t.status == TaskStatus.local_ready:
                groups[(t.server_id or "unknown", t.remote_work_dir or "")].append(t)

        ts = datetime.now().isoformat()
        successful_task_ids: set[str] = set()

        for (server_id, remote_work_dir), group_tasks in groups.items():
            sftp = sftp_factory(server_id)
            if sftp is None:
                for task in group_tasks:
                    failures.append(_failure(task, "upload", f"server {server_id} SFTP unavailable", ts))
                continue
            try:
                shared_batch_dir = f"{remote_work_dir}/{group_tasks[0].batch_id}"
                for sr in shared_records:
                    remote_path = f"{shared_batch_dir}/{shared_target}/{sr.remote_name}"
                    rec = sftp.upload_file(
                        Path(sr.local_path), remote_path,
                        overwrite=False, skip_if_same_size=True, dry_run=dry_run,
                    )
                    rec.category = "shared"
                    records.append(rec)

                for task in group_tasks:
                    if not task.task_files:
                        continue
                    task_ok = True
                    for i, local_str in enumerate(task.task_files):
                        remote_name = task.remote_task_files[i] if i < len(task.remote_task_files) else Path(local_str).name
                        remote_path = f"{task.remote_job_dir}/{remote_name}"
                        rec = sftp.upload_file(
                            Path(local_str), remote_path,
                            overwrite=False, skip_if_same_size=True, dry_run=dry_run,
                        )
                        rec.category = "task"
                        records.append(rec)
                        ok_statuses = (TransferStatus.transferred, TransferStatus.skipped)
                        if dry_run:
                            ok_statuses = ok_statuses + (TransferStatus.planned,)
                        if rec.status not in ok_statuses:
                            task_ok = False
                            failures.append(_failure(task, "upload", rec.reason or f"upload failed: {remote_path}", ts, local_str))
                    if task_ok and not dry_run:
                        successful_task_ids.add(task.task_id)
            finally:
                if hasattr(sftp, "close"):
                    sftp.close()

        if manifest_path is not None:
            current = Manifest.read(manifest_path)
            now = datetime.now()
            for task in current:
                if task.task_id in successful_task_ids and task.status == TaskStatus.local_ready:
                    task.status = TaskStatus.uploaded
                    task.uploaded_at = now
            Manifest.write(manifest_path, current)
            if failures:
                append_failures_tsv(failures, manifest_path.parent / "failures.tsv")
            return records, failures
        return records

    def submit_batch(
        self,
        manifest_path: Path,
        batch_id: str,
        ssh_factory,
        sftp_factory,
        resolved_contexts: dict[str, ResolvedExecutionContext] | None = None,
        mode: SubmitMode = SubmitMode.all,
        submitter_factory=None,
    ) -> list[SubmitResult]:
        tasks = Manifest.read(manifest_path)
        uploadable = [t for t in tasks if t.status == TaskStatus.uploaded]
        if not uploadable and tasks:
            result = SubmitResult(
                batch_id=batch_id,
                submitted_task_count=0,
                remote_batch_dir="",
                errors=["no uploaded tasks to submit; repeated submit skipped"],
            )
            ts = datetime.now().isoformat()
            append_failures_tsv(
                [
                    FailureRecord(
                        batch_id=batch_id,
                        task_id=None,
                        stage="submit",
                        reason=result.errors[0],
                        timestamp=ts,
                    )
                ],
                manifest_path.parent / "failures.tsv",
            )
            return [result]
        _fill_missing_runtime_fields(uploadable, resolved_contexts)
        groups = _group_by_key(uploadable)
        results: list[SubmitResult] = []
        submitted_ids: set[str] = set()
        failures: list[FailureRecord] = []
        ts = datetime.now().isoformat()

        for key, group_tasks in groups.items():
            server_config = _server_config_for(self.ctx, key.server_id, resolved_contexts)
            ssh = ssh_factory(server_config)
            sftp = sftp_factory(server_config)
            try:
                sub_manifest = _write_sub_manifest(group_tasks)
                max_parallel = group_tasks[0].max_parallel or _max_parallel_from_resolved(
                    resolved_contexts, key.execution_profile
                )
                kwargs = dict(
                    manifest_path=sub_manifest,
                    ssh=ssh,
                    sftp=sftp,
                    max_parallel=max_parallel,
                    remote_batch_dir=f"{key.remote_work_dir}/{batch_id}",
                    batch_id=batch_id,
                    control_subdir=f"_batch/{key.execution_profile}",
                )
                submitter = submitter_factory(**kwargs) if submitter_factory else JobSubmitter(**kwargs)
                result = submitter.submit_batch(mode)
                results.append(result)
                if result.errors:
                    reason = "; ".join(result.errors)
                    for task in group_tasks:
                        failures.append(_failure(task, "submit", reason, ts))
                else:
                    submitted_ids.update(result.updated_task_ids)
            finally:
                if hasattr(sftp, "close"):
                    sftp.close()
                if hasattr(ssh, "close"):
                    ssh.close()

        if submitted_ids:
            current = Manifest.read(manifest_path)
            now = datetime.now()
            for task in current:
                if task.task_id in submitted_ids and task.status == TaskStatus.uploaded:
                    task.status = TaskStatus.submitted
                    task.submitted_at = now
            Manifest.write(manifest_path, current)
        if failures:
            append_failures_tsv(failures, manifest_path.parent / "failures.tsv")
        return results

    def refresh_batch(
        self,
        manifest_path: Path,
        batch_id: str,
        ssh_factory,
        resolved_contexts: dict[str, ResolvedExecutionContext] | None = None,
        write: bool = True,
        refresh_func=refresh_batch_status,
    ) -> tuple[list[StatusRefreshResult], list[FailureRecord]]:
        tasks = Manifest.read(manifest_path)
        _fill_missing_runtime_fields(tasks, resolved_contexts)
        groups = _group_by_key(tasks)
        task_map = {t.task_id: t for t in tasks}
        all_results: list[StatusRefreshResult] = []
        all_failures: list[FailureRecord] = []
        ts = datetime.now().isoformat()

        for key, group_tasks in groups.items():
            try:
                server_config = _server_config_for(self.ctx, key.server_id, resolved_contexts)
                ssh = ssh_factory(server_config)
            except Exception as e:
                for task in group_tasks:
                    all_failures.append(_failure(task, "refresh", f"server {key.server_id} connection failed: {e}", ts))
                continue

            try:
                sub_manifest = _write_sub_manifest(group_tasks)
                result = refresh_func(
                    ssh=ssh,
                    manifest_path=sub_manifest,
                    remote_batch_dir=f"{key.remote_work_dir}/{batch_id}",
                    batch_id=batch_id,
                    write=True,
                    control_subdir=f"_batch/{key.execution_profile}",
                )
                all_results.append(result)
                for ut in Manifest.read(sub_manifest):
                    if ut.task_id in task_map:
                        original = task_map[ut.task_id]
                        original.status = ut.status
                        original.uploaded_at = ut.uploaded_at
                        original.submitted_at = ut.submitted_at
                        original.started_at = ut.started_at
                        original.completed_at = ut.completed_at
                        original.error_message = ut.error_message
            except Exception as e:
                for task in group_tasks:
                    all_failures.append(_failure(task, "refresh", f"refresh failed: {e}", ts))
            finally:
                if hasattr(ssh, "close"):
                    ssh.close()

        if write:
            Manifest.write(manifest_path, list(task_map.values()))
        if all_failures:
            append_failures_tsv(all_failures, manifest_path.parent / "failures.tsv")
        return all_results, all_failures

    def download_completed(
        self,
        tasks: list[TaskRecord],
        sftp_factory,
        dry_run: bool = False,
        manifest_path: Path | None = None,
    ) -> tuple[list[TransferRecord], list[FailureRecord]]:
        patterns = self.ctx.project_config.download.patterns
        results_dir = self.ctx.local_result_dir
        records: list[TransferRecord] = []
        failures: list[FailureRecord] = []
        successful_task_ids: set[str] = set()
        ts = datetime.now().isoformat()

        for server_id, group_tasks in _group_by_server(tasks).items():
            sftp = sftp_factory(server_id)
            if sftp is None:
                for task in group_tasks:
                    failures.append(_failure(task, "download", f"server {server_id} SFTP unavailable", ts))
                continue
            try:
                for task in group_tasks:
                    if task.status != TaskStatus.remote_completed:
                        continue
                    task_ok = True
                    task_dir = results_dir / task.batch_id / task.task_id
                    task_dir.mkdir(parents=True, exist_ok=True)
                    for pat in patterns:
                        rec = sftp.download_file(
                            f"{task.remote_job_dir}/{pat}",
                            task_dir / pat,
                            overwrite=False, skip_if_same_size=True,
                            dry_run=dry_run,
                        )
                        records.append(rec)
                        ok_statuses = (TransferStatus.transferred, TransferStatus.skipped)
                        if dry_run:
                            ok_statuses = ok_statuses + (TransferStatus.planned,)
                        if rec.status not in ok_statuses:
                            task_ok = False
                            failures.append(_failure(task, "download", rec.reason or f"download failed: {pat}", ts))
                    if task_ok and not dry_run:
                        successful_task_ids.add(task.task_id)
            finally:
                if hasattr(sftp, "close"):
                    sftp.close()

        if manifest_path is not None:
            current = Manifest.read(manifest_path)
            now = datetime.now()
            for task in current:
                if task.task_id in successful_task_ids and task.status == TaskStatus.remote_completed:
                    task.status = TaskStatus.downloaded
                    task.downloaded_at = now
            Manifest.write(manifest_path, current)
            if failures:
                append_failures_tsv(failures, manifest_path.parent / "failures.tsv")
        return records, failures

    def write_job_status_tsv(self, output_path: Path, tasks: list[TaskRecord], snapshots: list) -> None:
        write_job_status(output_path, tasks, snapshots)

    def write_failures_report(self, failures: list[FailureRecord], output_path: Path) -> None:
        write_failures_tsv(failures, output_path)


def _group_by_server(tasks: list[TaskRecord]) -> dict[str, list[TaskRecord]]:
    groups = defaultdict(list)
    for task in tasks:
        groups[task.server_id or "unknown"].append(task)
    return dict(groups)


def _group_by_key(tasks: list[TaskRecord]) -> dict[_GroupKey, list[TaskRecord]]:
    groups = defaultdict(list)
    for task in tasks:
        groups[_GroupKey(
            server_id=task.server_id or "unknown",
            execution_profile=task.execution_profile,
            remote_work_dir=task.remote_work_dir or "",
        )].append(task)
    return dict(groups)


def _write_sub_manifest(tasks: list[TaskRecord]) -> Path:
    import tempfile

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".tsv", delete=False, encoding="utf-8")
    tmp.close()
    Manifest.write(Path(tmp.name), tasks)
    return Path(tmp.name)


def _server_config_for(
    ctx: ProjectContext,
    server_id: str,
    resolved_contexts: dict[str, ResolvedExecutionContext] | None = None,
):
    if ctx.servers_path is not None:
        servers = load_servers(ctx.servers_path)
        if server_id in servers.servers:
            return servers.servers[server_id]
    if resolved_contexts:
        for rctx in resolved_contexts.values():
            if rctx.server_id == server_id:
                return rctx.server_config
    raise ValueError(f"server_id {server_id!r} not found in servers.yaml")


def _max_parallel_from_resolved(
    resolved_contexts: dict[str, ResolvedExecutionContext] | None,
    execution_profile: str,
) -> int:
    if resolved_contexts and execution_profile in resolved_contexts:
        return resolved_contexts[execution_profile].max_parallel
    return 1


def _fill_missing_runtime_fields(
    tasks: list[TaskRecord],
    resolved_contexts: dict[str, ResolvedExecutionContext] | None,
) -> None:
    if not resolved_contexts:
        return
    for task in tasks:
        if task.server_id and task.remote_work_dir and task.max_parallel is not None:
            continue
        if task.execution_profile not in resolved_contexts:
            raise ValueError(
                f"missing frozen runtime fields and no resolved context for "
                f"execution_profile={task.execution_profile!r}"
            )
        rctx = resolved_contexts[task.execution_profile]
        if not task.server_id:
            task.server_id = rctx.server_id
        if not task.remote_work_dir:
            task.remote_work_dir = rctx.remote_work_dir
        if task.max_parallel is None:
            task.max_parallel = rctx.max_parallel


def _failure(
    task: TaskRecord,
    stage: str,
    reason: str,
    timestamp: str,
    source_file: str | None = None,
) -> FailureRecord:
    return FailureRecord(
        task_id=task.task_id,
        batch_id=task.batch_id,
        stage=stage,
        reason=reason,
        server_id=task.server_id,
        execution_profile=task.execution_profile,
        remote_job_dir=task.remote_job_dir,
        source_file=source_file,
        timestamp=timestamp,
    )
