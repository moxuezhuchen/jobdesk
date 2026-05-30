"""Scheduler adapters for job submission.

Supports nohup (default), Slurm, and PBS/Torque.
Each adapter translates JobDesk's internal submit/poll/cancel into
the scheduler-specific commands.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class JobState(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    unknown = "unknown"


@dataclass
class ResourceSpec:
    """Resource requirements for a single task."""
    cpus: int = 1
    memory_mb: int = 2048
    walltime_minutes: int = 1440  # 24h default
    partition: str = ""
    account: str = ""
    gpus: int = 0
    extra_directives: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ResourceSpec":
        return cls(
            cpus=int(d.get("cpus", 1)),
            memory_mb=int(d.get("memory_mb", 2048)),
            walltime_minutes=int(d.get("walltime_minutes", 1440)),
            partition=str(d.get("partition", "")),
            account=str(d.get("account", "")),
            gpus=int(d.get("gpus", 0)),
            extra_directives=list(d.get("extra_directives", [])),
        )

    def walltime_hms(self) -> str:
        h, rem = divmod(self.walltime_minutes, 60)
        return f"{h:02d}:{rem:02d}:00"


class SchedulerAdapter(Protocol):
    """Protocol for scheduler adapters."""

    def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
        """Submit job script, return scheduler job_id."""
        ...

    def poll(self, ssh, job_id: str) -> JobState:
        """Poll job state."""
        ...

    def cancel(self, ssh, job_id: str) -> None:
        """Cancel a running/pending job."""
        ...


class NohupAdapter:
    """Default adapter: nohup bash in background.

    job_id is the remote PID (best-effort; not reliable for polling).
    Status is tracked via .jobdesk_status file written by the runner script.
    """

    def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
        dir_q = shlex.quote(str(script_path).rsplit("/", 1)[0])
        script_q = shlex.quote(script_path)
        r = ssh.run(
            f"cd {dir_q} && nohup setsid bash {script_q} > .jobdesk_submit.log 2>&1 & echo $!",
            timeout=30,
        )
        return r.stdout.strip() or "0"

    def poll(self, ssh, job_id: str) -> JobState:
        # nohup: check if PID still alive
        if not job_id or job_id == "0":
            return JobState.unknown
        r = ssh.run(f"kill -0 {shlex.quote(job_id)} 2>/dev/null && echo alive || echo dead")
        return JobState.running if "alive" in r.stdout else JobState.completed

    def cancel(self, ssh, job_id: str) -> None:
        if job_id and job_id != "0":
            pid_q = shlex.quote(job_id)
            # Group-priority check command (reused after TERM and KILL)
            check_cmd = (
                f"kill -0 -- -{pid_q} 2>/dev/null || kill -0 {pid_q} 2>/dev/null"
            )
            # TERM with process-group preference
            ssh.run(
                f"kill -TERM -- -{pid_q} 2>/dev/null || kill -TERM {pid_q} 2>/dev/null",
                timeout=15,
            )
            # Brief grace period then group-priority check
            r = ssh.run(f"sleep 2; {check_cmd} && echo alive || echo dead", timeout=20)
            if "alive" in r.stdout:
                # Escalate to SIGKILL
                ssh.run(
                    f"kill -KILL -- -{pid_q} 2>/dev/null || kill -KILL {pid_q} 2>/dev/null",
                    timeout=15,
                )
                r = ssh.run(f"sleep 1; {check_cmd} && echo alive || echo dead", timeout=15)
                if "alive" in r.stdout:
                    raise RuntimeError(f"process {job_id} still alive after SIGKILL")


class SlurmAdapter:
    """Slurm scheduler adapter (sbatch / squeue / scancel)."""

    def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
        r = ssh.run(f"sbatch {shlex.quote(script_path)}", timeout=30)
        # sbatch output: "Submitted batch job 12345"
        for word in r.stdout.split():
            if word.isdigit():
                return word
        raise RuntimeError(f"sbatch failed: {r.stdout} {r.stderr}")

    def poll(self, ssh, job_id: str) -> JobState:
        try:
            r = ssh.run(
                f"squeue -j {shlex.quote(job_id)} -h -o '%T' 2>/dev/null || echo DONE",
                timeout=15,
            )
        except Exception:
            return JobState.unknown
        state = r.stdout.strip().upper()
        _MAP = {
            "PENDING": JobState.pending,
            "PD": JobState.pending,
            "RUNNING": JobState.running,
            "R": JobState.running,
            "COMPLETING": JobState.running,
            "CG": JobState.running,
            "COMPLETED": JobState.completed,
            "FAILED": JobState.failed,
            "CANCELLED": JobState.cancelled,
            "TIMEOUT": JobState.failed,
            "OUT_OF_MEMORY": JobState.failed,
            "NODE_FAIL": JobState.failed,
            "DONE": JobState.completed,  # squeue returns nothing when job is done
        }
        return _MAP.get(state, JobState.unknown)

    def cancel(self, ssh, job_id: str) -> None:
        r = ssh.run(f"scancel {shlex.quote(job_id)}", timeout=15)
        if r.exit_code != 0:
            raise RuntimeError(f"scancel failed (exit {r.exit_code}): {r.stderr or r.stdout}")

    @staticmethod
    def build_header(resources: ResourceSpec, job_name: str = "jobdesk") -> list[str]:
        lines = [
            "#!/usr/bin/env bash",
            f"#SBATCH --job-name={job_name}",
            "#SBATCH --ntasks=1",
            f"#SBATCH --cpus-per-task={resources.cpus}",
            f"#SBATCH --mem={resources.memory_mb}M",
            f"#SBATCH --time={resources.walltime_hms()}",
        ]
        if resources.partition:
            lines.append(f"#SBATCH --partition={resources.partition}")
        if resources.account:
            lines.append(f"#SBATCH --account={resources.account}")
        if resources.gpus > 0:
            lines.append(f"#SBATCH --gres=gpu:{resources.gpus}")
        for directive in resources.extra_directives:
            lines.append(f"#SBATCH {directive}")
        return lines


class PBSAdapter:
    """PBS/Torque scheduler adapter (qsub / qstat / qdel)."""

    def submit(self, ssh, script_path: str, resources: ResourceSpec) -> str:
        r = ssh.run(f"qsub {shlex.quote(script_path)}", timeout=30)
        # qsub output: "12345.hostname"
        job_id = r.stdout.strip().split(".")[0]
        if not job_id.isdigit():
            raise RuntimeError(f"qsub failed: {r.stdout} {r.stderr}")
        return job_id

    def poll(self, ssh, job_id: str) -> JobState:
        try:
            r = ssh.run(
                f"qstat -f {shlex.quote(job_id)} 2>/dev/null | grep 'job_state' || echo DONE",
                timeout=15,
            )
        except Exception:
            return JobState.unknown
        text = r.stdout.upper()
        if "DONE" in text or "job_state" not in text.lower():
            return JobState.completed
        if "JOB_STATE = Q" in text:
            return JobState.pending
        if "JOB_STATE = R" in text:
            return JobState.running
        if "JOB_STATE = E" in text:
            return JobState.running
        if "JOB_STATE = C" in text:
            return JobState.completed
        return JobState.unknown

    def cancel(self, ssh, job_id: str) -> None:
        r = ssh.run(f"qdel {shlex.quote(job_id)}", timeout=15)
        if r.exit_code != 0:
            raise RuntimeError(f"qdel failed (exit {r.exit_code}): {r.stderr or r.stdout}")

    @staticmethod
    def build_header(resources: ResourceSpec, job_name: str = "jobdesk") -> list[str]:
        lines = [
            "#!/usr/bin/env bash",
            f"#PBS -N {job_name}",
            f"#PBS -l nodes=1:ppn={resources.cpus}",
            f"#PBS -l mem={resources.memory_mb}mb",
            f"#PBS -l walltime={resources.walltime_hms()}",
        ]
        if resources.partition:
            lines.append(f"#PBS -q {resources.partition}")
        if resources.account:
            lines.append(f"#PBS -A {resources.account}")
        for directive in resources.extra_directives:
            lines.append(f"#PBS {directive}")
        return lines


def make_adapter(scheduler_type: str) -> SchedulerAdapter:
    """Factory: return the right adapter for the given scheduler type."""
    t = (scheduler_type or "nohup").lower()
    if t == "nohup":
        return NohupAdapter()
    if t in ("slurm", "sbatch"):
        return SlurmAdapter()
    if t in ("pbs", "torque", "qsub"):
        return PBSAdapter()
    raise ValueError(f"Unknown scheduler type: {scheduler_type}")
