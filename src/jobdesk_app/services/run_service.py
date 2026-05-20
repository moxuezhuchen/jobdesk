from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.batch import create_batch, read_batch_json, write_batch_json
from ..core.lifecycle import TaskStatus
from ..core.manifest import Manifest, TaskRecord
from ..core.models import BatchMeta
from ..core.run import RunPlan, RunSpec, build_run_plan
from ..core.transfer import TransferStatus
from ..remote.submitter import JobSubmitter


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
    status_summary: dict[str, int] = field(default_factory=dict)


class RunService:
    def __init__(self, workspace_dir: str | Path | None = None):
        import os
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        self.runs_dir = Path(appdata) / "JobDesk" / "runs"
        self.workspace_dir = Path(workspace_dir).resolve() if workspace_dir else Path.cwd()

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
        if run_id is None:
            run_id = self._next_run_id()
        plan = build_run_plan(spec, run_id)
        run_dir = self.runs_dir / plan.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = run_dir / "manifest.tsv"
        batch_path = run_dir / "batch.json"
        batch = create_batch(
            project_name=self.workspace_dir.name,
            max_parallel=spec.max_parallel,
            remote_batch_dir=f"{spec.remote_dir.rstrip('/')}/.jobdesk_runs/{plan.run_id}",
            task_count=len(plan.tasks),
            manifest_path=str(manifest_path),
        )
        batch.batch_id = plan.run_id
        tasks = _tasks_from_plan(plan, batch)
        write_batch_json(batch, batch_path)
        Manifest.write(manifest_path, tasks)
        record = self._record_from_parts(plan, run_dir, manifest_path, batch_path, _status_summary(tasks), local_dir=local_dir)
        self._write_run_json(record)
        return record

    def list_runs(self) -> list[RunRecord]:
        if not self.runs_dir.exists():
            return []
        records: list[RunRecord] = []
        for run_dir in sorted(self.runs_dir.iterdir(), reverse=True):
            if run_dir.is_dir() and (run_dir / "run.json").exists():
                try:
                    records.append(self.load_run(run_dir.name))
                except Exception:
                    continue
        return records

    def load_run(self, run_id: str) -> RunRecord:
        run_dir = self.runs_dir / run_id
        data = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        return RunRecord(
            run_id=data["run_id"],
            server_id=data["server_id"],
            remote_dir=data["remote_dir"],
            command_template=data["command_template"],
            max_parallel=int(data["max_parallel"]),
            mode=data["mode"],
            created_at=data["created_at"],
            run_dir=run_dir,
            manifest_path=run_dir / "manifest.tsv",
            batch_path=run_dir / "batch.json",
            local_dir=data.get("local_dir", ""),
            status_summary=data.get("status_summary", {}),
        )

    def update_run_from_manifest(self, run_id: str) -> RunRecord:
        record = self.load_run(run_id)
        tasks = Manifest.read(record.manifest_path)
        record.status_summary = _status_summary(tasks)
        self._write_run_json(record)
        return record

    def submit_run(self, run_id: str, ssh, sftp, env_init_scripts: list[str] | None = None,
                   scheduler=None, resources=None):
        record = self.load_run(run_id)
        submitter = JobSubmitter(
            manifest_path=record.manifest_path,
            ssh=ssh,
            sftp=sftp,
            max_parallel=record.max_parallel,
            remote_batch_dir=f"{record.remote_dir.rstrip('/')}/.jobdesk_runs/{record.run_id}",
            batch_id=record.run_id,
            env_init_scripts=list(env_init_scripts or []),
            scheduler=scheduler,
            resources=resources,
        )
        result = submitter.submit_batch()
        self.update_run_from_manifest(run_id)
        return result

    def download_completed(self, run_id: str, sftp, patterns: list[str]):
        record = self.load_run(run_id)
        tasks = Manifest.read(record.manifest_path)
        records = []
        failures = []
        # Download destination: record.local_dir (flat) or fallback to workspace/results
        dest_dir = Path(record.local_dir) if record.local_dir else self.workspace_dir / "results" / run_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            if task.status != TaskStatus.remote_completed:
                continue
            try:
                recs = sftp.download_dir(
                    task.remote_job_dir,
                    dest_dir,
                    include_globs=patterns,
                    overwrite=False,
                    skip_if_same_size=True,
                )
                records.extend(recs)
                task_ok = any(
                    r.status in (TransferStatus.transferred, TransferStatus.skipped)
                    and not r.remote_path.split("/")[-1].startswith(".jobdesk")
                    for r in recs
                )
                if not task_ok:
                    for r in recs:
                        if r.status not in (TransferStatus.transferred, TransferStatus.skipped):
                            failures.append((task.task_id, r.reason))
            except Exception as exc:
                task_ok = False
                failures.append((task.task_id, str(exc)))
            if task_ok:
                task.status = TaskStatus.downloaded
        Manifest.write(record.manifest_path, tasks)
        self.update_run_from_manifest(run_id)
        return records, failures

    def prepare_retry_failed(self, run_id: str) -> int:
        record = self.load_run(run_id)
        from ..core.manifest_ops import reset_failed_to_uploaded
        changed = reset_failed_to_uploaded(record.manifest_path)
        self.update_run_from_manifest(run_id)
        return changed

    def prepare_rerun(self, run_id: str) -> int:
        record = self.load_run(run_id)
        from ..core.manifest_ops import reset_all_to_uploaded
        changed = reset_all_to_uploaded(record.manifest_path)
        self.update_run_from_manifest(run_id)
        return changed

    def mark_run_cancelled(self, run_id: str) -> int:
        """Mark all unfinished tasks as failed/cancelled."""
        record = self.load_run(run_id)
        from ..core.manifest import Manifest
        from ..core.lifecycle import TaskStatus
        tasks = list(Manifest.read(record.manifest_path))
        changed = 0
        terminal = {TaskStatus.remote_completed, TaskStatus.downloaded, TaskStatus.failed}
        for task in tasks:
            if task.status not in terminal:
                task.status = TaskStatus.failed
                task.error_message = "cancelled"
                changed += 1
        if changed:
            Manifest.write(record.manifest_path, tasks)
            self.update_run_from_manifest(run_id)
        return changed

    def analyze_run(self, run_id: str, profile_name: str = "gaussian_opt_freq") -> tuple[list, list]:
        """Run result extraction on downloaded files for a run."""
        from ..core.analyzer import analyze_tasks
        from ..services.analysis_profiles import AnalysisProfileStore
        record = self.load_run(run_id)
        tasks = Manifest.read(record.manifest_path)
        profile = AnalysisProfileStore().get(profile_name)
        if profile is None:
            return [], [{"error": f"profile not found: {profile_name}"}]
        results_dir = self.workspace_dir / "results"
        return analyze_tasks(profile.extract_rules, tasks, results_dir, run_id)

    def delete_run(self, run_id: str) -> None:
        """Delete run directory, results, and analysis profile."""
        import re, shutil
        if not re.fullmatch(r'[A-Za-z0-9_\-]+', run_id):
            raise ValueError(f"Invalid run_id: {run_id}")
        run_dir = (self.runs_dir / run_id).resolve()
        if not run_dir.is_relative_to(self.runs_dir.resolve()):
            raise ValueError(f"run_id escapes runs_dir: {run_id}")
        if run_dir.exists():
            shutil.rmtree(run_dir)
        results_dir = (self.workspace_dir / "results" / run_id).resolve()
        if not results_dir.is_relative_to((self.workspace_dir / "results").resolve()):
            raise ValueError(f"run_id escapes results dir: {run_id}")
        if results_dir.exists():
            shutil.rmtree(results_dir)

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
        )

    def _write_run_json(self, record: RunRecord) -> None:
        data = {
            "run_id": record.run_id,
            "server_id": record.server_id,
            "remote_dir": record.remote_dir,
            "command_template": record.command_template,
            "max_parallel": record.max_parallel,
            "mode": record.mode,
            "created_at": record.created_at,
            "local_dir": record.local_dir,
            "status_summary": record.status_summary,
        }
        record.run_dir.mkdir(parents=True, exist_ok=True)
        (record.run_dir / "run.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def _tasks_from_plan(plan: RunPlan, batch: BatchMeta) -> list[TaskRecord]:
    return [
        TaskRecord(
            task_id=task.task_id,
            batch_id=plan.run_id,
            remote_job_dir=task.remote_job_dir,
            task_files=[],
            remote_task_files=[task.source_name],
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
