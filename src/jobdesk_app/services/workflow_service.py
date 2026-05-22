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


def append_event(
    workspace_dir: Path,
    workflow_id: str,
    event_type: str,
    step_name: str = "",
    message: str = "",
    details: dict | None = None,
) -> None:
    """Append a diagnostic event to the workflow's .events.jsonl file."""
    path = Path(workspace_dir) / ".jobdesk" / "workflows" / f"{workflow_id}.events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "workflow_id": workflow_id,
        "step_name": step_name,
        "event_type": event_type,
        "message": message,
        "created_at": datetime.now().isoformat(),
        "details": details or {},
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def read_events(workspace_dir: Path, workflow_id: str) -> list[dict]:
    """Read all events for a workflow."""
    path = Path(workspace_dir) / ".jobdesk" / "workflows" / f"{workflow_id}.events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


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
        append_event(
            self.workspace_dir,
            wf_id,
            "workflow_started",
            message=f"Workflow {spec.name} started",
            details={"sources": sources},
        )
        return wf_run

    def advance(
        self,
        spec: WorkflowSpec,
        wf_run: WorkflowRun,
        ssh_factory=None,
        sftp_factory=None,
    ) -> tuple[list[str], dict[str, str]]:
        """Check which steps are ready to run and create their RunService runs.

        Returns (started_step_names, pending_uploads) where pending_uploads
        maps local file paths to remote destination paths that must be
        uploaded before submission.
        """
        from ..services.run_service import RunService
        svc = RunService(self.workspace_dir)
        started: list[str] = []
        pending_uploads: dict[str, str] = {}

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
                result = self._prepare_downstream_inputs(
                    wf_run, step, spec,
                )
                if result is None:
                    # Cannot extract geometry — block this step
                    append_event(
                        self.workspace_dir,
                        wf_run.workflow_id,
                        "geometry_extraction_failed",
                        step_name=step.name,
                        message=f"Cannot extract geometry from {step.input_from} for {step.name}",
                    )
                    continue
                sources, uploads = result
                pending_uploads.update(uploads)
                append_event(
                    self.workspace_dir,
                    wf_run.workflow_id,
                    "downstream_input_generated",
                    step_name=step.name,
                    message=f"Generated {len(uploads)} input(s) for {step.name}",
                    details={"files": list(uploads.values())},
                )
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
            append_event(
                self.workspace_dir,
                wf_run.workflow_id,
                "step_started",
                step_name=step.name,
                message=f"Step {step.name} started",
                details={"run_id": record.run_id},
            )

        return started, pending_uploads

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

    def _prepare_downstream_inputs(
        self,
        wf_run: WorkflowRun,
        step: "WorkflowStep",
        spec: "WorkflowSpec",
    ) -> tuple[list[RunSource], dict[str, str]] | None:
        """Generate proper input files from upstream geometry for a downstream step.

        Returns (sources_with_remote_paths, {local_path: remote_path}) or None
        if geometry cannot be extracted.
        """
        from ..core.input_builder import GaussianInputSpec, OrcaInputSpec, build_gjf, build_inp
        from ..core.parsers import parse_gaussian_log, parse_orca_out

        from_step = step.input_from
        run_id = wf_run.step_run_ids.get(from_step)
        if not run_id:
            return None

        results_dir = self.workspace_dir / "results" / run_id
        if not results_dir.exists():
            return None

        # Determine upstream parser from the upstream step's command
        upstream_step = spec.step(from_step)
        upstream_cmd = (upstream_step.command_template if upstream_step else "").lower()

        # Determine downstream input format from this step's command
        cmd_base = step.command_template.lower().split()[0] if step.command_template.strip() else ""
        is_orca_downstream = "orca" in cmd_base

        # Infer job type from step name
        step_keywords = _infer_job_keywords(step.name)

        sources: list[RunSource] = []
        uploads: dict[str, str] = {}
        staging_dir = self.workspace_dir / ".jobdesk" / "workflow_inputs" / wf_run.workflow_id / step.name
        staging_dir.mkdir(parents=True, exist_ok=True)

        for task_dir in sorted(results_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            log_files = list(task_dir.glob("*.log")) + list(task_dir.glob("*.out"))
            if not log_files:
                continue
            log_file = log_files[0]

            # Parse geometry from upstream output
            if "orca" in upstream_cmd:
                result = parse_orca_out(log_file)
            else:
                result = parse_gaussian_log(log_file)

            if not result.final_xyz or not result.atom_symbols:
                continue

            # Write intermediate XYZ
            xyz_path = staging_dir / f"{task_dir.name}.xyz"
            n = len(result.atom_symbols)
            xyz_path.write_text(
                f"{n}\n{task_dir.name} from {from_step}\n{result.final_xyz}\n",
                encoding="utf-8",
            )

            # Generate proper input file
            if is_orca_downstream:
                inp_name = f"{task_dir.name}_{step.name}.inp"
                inp_path = staging_dir / inp_name
                build_inp(xyz_path, OrcaInputSpec(keywords=f"! {step_keywords}"), output_path=inp_path)
            else:
                inp_name = f"{task_dir.name}_{step.name}.gjf"
                inp_path = staging_dir / inp_name
                build_gjf(xyz_path, GaussianInputSpec(job_keywords=step_keywords.split()), output_path=inp_path)

            # Remote target path
            remote_path = f"{wf_run.remote_dir.rstrip('/')}/{inp_name}"
            sources.append(RunSource(path=remote_path))
            uploads[str(inp_path)] = remote_path

        return (sources, uploads) if sources else None


def _infer_job_keywords(step_name: str) -> str:
    """Infer Gaussian/ORCA job keywords from step name."""
    name = step_name.lower()
    if "opt" in name and "freq" in name:
        return "opt freq"
    if "freq" in name:
        return "freq"
    if "opt" in name:
        return "opt"
    if "sp" in name or "single" in name:
        return "SP"
    return "SP"


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
