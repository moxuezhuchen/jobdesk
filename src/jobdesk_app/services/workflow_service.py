"""Workflow chain: multi-step computational chemistry workflows.

A WorkflowSpec declares a DAG of steps (e.g. opt → freq → sp).
Each step is a RunSpec with optional dependency on a previous step's output.
The WorkflowRunner executes steps in topological order, passing geometry
from one step to the next when requested.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from ..core.run import RunMode, RunSource, RunSpec


@dataclass(frozen=True)
class WorkflowStep:
    """A single step in a workflow."""
    name: str
    command_template: str
    depends_on: list[str] = field(default_factory=list)
    input_from: str | None = None          # step name to take geometry from
    extract_profile: str | None = None     # analysis profile to run after download
    on_failure: Literal["stop", "continue"] = "stop"
    max_parallel: int = 4
    resources: dict = field(default_factory=dict)  # ResourceSpec overrides


@dataclass(frozen=True)
class WorkflowSpec:
    """A named workflow consisting of ordered steps."""
    name: str
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)

    def step(self, name: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.name == name), None)

    def topological_order(self) -> list[WorkflowStep]:
        """Return steps in dependency order (Kahn's algorithm)."""
        in_degree = {s.name: len(s.depends_on) for s in self.steps}
        ready = [s for s in self.steps if not s.depends_on]
        result: list[WorkflowStep] = []
        step_map = {s.name: s for s in self.steps}
        while ready:
            step = ready.pop(0)
            result.append(step)
            for other in self.steps:
                if step.name in other.depends_on:
                    in_degree[other.name] -= 1
                    if in_degree[other.name] == 0:
                        ready.append(step_map[other.name])
        if len(result) != len(self.steps):
            raise ValueError("Workflow has a cycle")
        return result


@dataclass
class WorkflowRun:
    """Tracks the execution state of a workflow instance."""
    workflow_id: str
    workflow_name: str
    workspace_dir: Path
    server_id: str
    remote_dir: str
    sources: list[str]                     # original source paths
    step_run_ids: dict[str, str] = field(default_factory=dict)   # step_name → run_id
    step_status: dict[str, str] = field(default_factory=dict)    # step_name → status
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def save(self) -> None:
        path = self.workspace_dir / ".jobdesk" / "workflows" / f"{self.workflow_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "server_id": self.server_id,
            "remote_dir": self.remote_dir,
            "sources": self.sources,
            "step_run_ids": self.step_run_ids,
            "step_status": self.step_status,
            "created_at": self.created_at,
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, workspace_dir: Path, workflow_id: str) -> "WorkflowRun":
        path = workspace_dir / ".jobdesk" / "workflows" / f"{workflow_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            workflow_id=data["workflow_id"],
            workflow_name=data["workflow_name"],
            workspace_dir=workspace_dir,
            server_id=data["server_id"],
            remote_dir=data["remote_dir"],
            sources=data["sources"],
            step_run_ids=data.get("step_run_ids", {}),
            step_status=data.get("step_status", {}),
            created_at=data.get("created_at", ""),
        )


class WorkflowRunner:
    """Executes a WorkflowSpec against a set of source files.

    Each step creates a RunService run. Geometry is transferred between
    steps by extracting the final XYZ from the upstream step's results
    and writing it as the input for the downstream step.
    """

    def __init__(self, workspace_dir: Path | str):
        self.workspace_dir = Path(workspace_dir).resolve()

    def start(
        self,
        spec: WorkflowSpec,
        server_id: str,
        remote_dir: str,
        sources: list[str],
        workflow_id: str | None = None,
    ) -> WorkflowRun:
        """Create a WorkflowRun and execute the first ready steps."""
        wf_id = workflow_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        wf_run = WorkflowRun(
            workflow_id=wf_id,
            workflow_name=spec.name,
            workspace_dir=self.workspace_dir,
            server_id=server_id,
            remote_dir=remote_dir,
            sources=sources,
        )
        wf_run.save()
        return wf_run

    def advance(
        self,
        spec: WorkflowSpec,
        wf_run: WorkflowRun,
        ssh_factory=None,
        sftp_factory=None,
    ) -> list[str]:
        """Check which steps are ready to run and create their RunService runs.

        This method only creates runs (no SSH needed). The caller is responsible
        for submitting each created run via RunService.submit_run().

        Returns list of step names that were started.
        """
        from ..services.run_service import RunService
        svc = RunService(self.workspace_dir)
        started: list[str] = []

        for step in spec.topological_order():
            if step.name in wf_run.step_status:
                continue  # already started or done
            # Check dependencies
            deps_ok = all(
                wf_run.step_status.get(dep) == "completed"
                for dep in step.depends_on
            )
            if not deps_ok:
                continue

            # Determine sources for this step
            if step.input_from and step.input_from in wf_run.step_run_ids:
                sources = self._extract_geometry_sources(
                    wf_run, step.input_from, step.command_template
                )
                if sources is None:
                    # Cannot extract geometry — block this step
                    continue
            else:
                sources = [RunSource(path=s) for s in wf_run.sources]

            run_spec = RunSpec(
                server_id=wf_run.server_id,
                remote_dir=wf_run.remote_dir,
                command_template=step.command_template,
                max_parallel=step.max_parallel,
                mode=RunMode.selected_files,
                sources=sources,
            )
            record = svc.create_run(run_spec)
            wf_run.step_run_ids[step.name] = record.run_id
            wf_run.step_status[step.name] = "running"
            wf_run.save()
            started.append(step.name)

        return started

    def sync_status(self, spec: WorkflowSpec, wf_run: WorkflowRun) -> None:
        """Update step_status from the underlying run manifests."""
        from ..services.run_service import RunService
        svc = RunService(self.workspace_dir)
        for step_name, run_id in wf_run.step_run_ids.items():
            try:
                record = svc.load_run(run_id)
            except Exception:
                continue
            summary = record.status_summary
            total = sum(summary.values())
            failed = summary.get("failed", 0)
            downloaded = summary.get("downloaded", 0) + summary.get("analyzed", 0)
            if failed > 0 and wf_run.step_status.get(step_name) == "running":
                step = spec.step(step_name)
                if step and step.on_failure == "stop":
                    wf_run.step_status[step_name] = "failed"
                else:
                    wf_run.step_status[step_name] = "completed"
            elif downloaded == total and total > 0:
                # Only mark completed when all results are downloaded locally
                wf_run.step_status[step_name] = "completed"
        wf_run.save()

    def _extract_geometry_sources(
        self,
        wf_run: WorkflowRun,
        from_step: str,
        command_template: str,
    ) -> list[RunSource] | None:
        """Extract final XYZ from upstream step results.

        Returns None if geometry cannot be extracted (results not downloaded
        or parsing failed). Caller must not proceed with original inputs.
        """
        from ..core.parsers import parse_gaussian_log, parse_orca_out
        run_id = wf_run.step_run_ids.get(from_step)
        if not run_id:
            return None

        results_dir = self.workspace_dir / "results" / run_id
        if not results_dir.exists():
            return None

        sources: list[RunSource] = []
        cmd = command_template.lower().split()[0] if command_template.strip() else ""

        for task_dir in sorted(results_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            # Find the output file
            log_files = list(task_dir.glob("*.log")) + list(task_dir.glob("*.out"))
            if not log_files:
                continue
            log_file = log_files[0]
            # Parse geometry
            if "orca" in cmd:
                result = parse_orca_out(log_file)
            else:
                result = parse_gaussian_log(log_file)
            if result.final_xyz and result.atom_symbols:
                # Write XYZ to a local file for upload
                xyz_path = task_dir / f"{task_dir.name}_opt.xyz"
                n = len(result.atom_symbols)
                xyz_path.write_text(
                    f"{n}\n{task_dir.name} from {from_step}\n{result.final_xyz}\n",
                    encoding="utf-8",
                )
                sources.append(RunSource(path=str(xyz_path)))

        return sources if sources else None


# ---- Built-in workflow templates -------------------------------------------

BUILTIN_WORKFLOWS: dict[str, WorkflowSpec] = {
    "opt_freq": WorkflowSpec(
        name="opt_freq",
        description="Geometry optimization followed by frequency analysis",
        steps=[
            WorkflowStep(
                name="opt",
                command_template="g16 {name}",
                extract_profile="gaussian_opt_freq",
            ),
            WorkflowStep(
                name="freq",
                command_template="g16 {name}",
                depends_on=["opt"],
                input_from="opt",
                extract_profile="gaussian_opt_freq",
            ),
        ],
    ),
    "opt_freq_sp": WorkflowSpec(
        name="opt_freq_sp",
        description="opt → freq → high-level single point",
        steps=[
            WorkflowStep(name="opt", command_template="g16 {name}", extract_profile="gaussian_opt_freq"),
            WorkflowStep(name="freq", command_template="g16 {name}", depends_on=["opt"], input_from="opt", extract_profile="gaussian_opt_freq"),
            WorkflowStep(name="sp", command_template="orca {name}", depends_on=["freq"], input_from="freq", extract_profile="orca_dlpno_ccsd_t"),
        ],
    ),
}
