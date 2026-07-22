from __future__ import annotations

import posixpath
import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class RunMode(str, Enum):
    selected_files = "selected_files"
    selected_directories = "selected_directories"
    current_directory = "current_directory"


class WorkflowKind(str, Enum):
    """How the remote program interprets a RunSpec's command_template.

    ``gaussian`` / ``orca`` invoke a single-shot quantum-chemistry binary
    (.gjf / .inp execution). ``confflow`` invokes the ConfFlow workflow engine
    over one or more XYZ inputs against a workflow YAML; the wizard emits the
    YAML and ``program_adapters.ConfFlowAdapter`` renders the command.
    ``dag`` is a multi-step variant of ``confflow``: the same YAML schema is
    used, but the per-step ``inputs`` lists (Phase 10.1-10.4) declare a DAG
    topology that the engine resolves via ``graphlib.TopologicalSorter``.
    The remote command is identical; the difference is purely data-shape.
    This field lives on RunSpec only — not RunRecord — so introducing it needs no
    schema migration on existing rows.
    """

    gaussian = "gaussian"
    orca = "orca"
    confflow = "confflow"
    dag = "dag"


@dataclass(frozen=True)
class RunSource:
    path: str
    is_dir: bool = False
    artifact_stem: str | None = None
    artifact_name: str | None = None

    @property
    def name(self) -> str:
        return posixpath.basename(self.path.rstrip("/"))

    @property
    def stem(self) -> str:
        name = self.name
        return name.rsplit(".", 1)[0] if "." in name else name

    @property
    def parent(self) -> str:
        return posixpath.dirname(self.path.rstrip("/")) or "/"

    @property
    def rendered_stem(self) -> str:
        return self.artifact_stem or self.stem

    @property
    def rendered_name(self) -> str:
        return self.artifact_name or self.name


@dataclass(frozen=True)
class RunSpec:
    server_id: str
    remote_dir: str
    command_template: str
    max_parallel: int
    mode: RunMode
    sources: list[RunSource] = field(default_factory=list)
    supporting_sources: list[RunSource] = field(default_factory=list)
    result_templates: list[str] = field(default_factory=list)
    batch_size: int | None = None
    # How the remote program interprets command_template; default keeps
    # backwards compatibility with rows that predate WorkflowKind.
    workflow_kind: WorkflowKind = WorkflowKind.gaussian


@dataclass(frozen=True)
class RunTaskPlan:
    task_id: str
    source_path: str
    source_name: str
    remote_job_dir: str
    command: str
    supporting_paths: list[str] = field(default_factory=list)
    remote_result_files: list[str] = field(default_factory=list)
    workflow_kind: WorkflowKind = WorkflowKind.gaussian
    remote_config_path: str = ""
    remote_workflow_dir: str = ""
    remote_state_path: str = ""
    remote_stats_path: str = ""
    remote_log_path: str = ""
    remote_result_paths: list[str] = field(default_factory=list)
    dry_run_command: str = ""
    resume_command: str = ""
    resume_dry_run_command: str = ""
    resume_requested: bool = False


@dataclass(frozen=True)
class RunPlan:
    run_id: str
    created_at: datetime
    spec: RunSpec
    tasks: list[RunTaskPlan]


def remote_run_dir(remote_dir: str, run_id: str) -> str:
    base = (remote_dir or "/").rstrip("/") or "/"
    return posixpath.join(base, ".jobdesk_runs", run_id)


def build_run_plan(spec: RunSpec, run_id: str | None = None) -> RunPlan:
    rid = run_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_remote_dir = remote_run_dir(spec.remote_dir, rid)
    tasks: list[RunTaskPlan] = []
    used_task_ids: set[str] = set()
    sources = _sources_for_mode(spec)
    for index, source in enumerate(sources, start=1):
        raw_task_id = (
            "current_directory"
            if spec.mode == RunMode.current_directory
            else (source.rendered_stem or source.rendered_name or f"task_{index}")
        )
        task_id = _unique_task_id(_safe_task_id(raw_task_id, index), used_task_ids)
        used_task_ids.add(task_id)
        work_dir = source.path if source.is_dir else source.parent
        command = _render_command(spec.command_template, source)
        remote_job_dir = posixpath.join(run_remote_dir, task_id)
        remote_result_files = [_render_text_template(item, source) for item in spec.result_templates]
        is_workflow = spec.workflow_kind in {WorkflowKind.confflow, WorkflowKind.dag}
        rendered_command = f"cd {shlex.quote(work_dir)} && {command}"
        resume_command = _append_command_flag(rendered_command, "--resume") if is_workflow else ""
        remote_workflow_dir = (
            posixpath.join(spec.remote_dir, f"{source.rendered_stem}_confflow_work") if is_workflow else ""
        )
        tasks.append(
            RunTaskPlan(
                task_id=task_id,
                source_path=source.path,
                source_name=source.name,
                remote_job_dir=remote_job_dir,
                command=rendered_command,
                supporting_paths=[item.path for item in spec.supporting_sources],
                remote_result_files=remote_result_files,
                workflow_kind=spec.workflow_kind,
                remote_config_path=_workflow_config_path(spec) if is_workflow else "",
                remote_workflow_dir=remote_workflow_dir,
                remote_state_path=(posixpath.join(remote_workflow_dir, ".workflow_state.json") if is_workflow else ""),
                remote_stats_path=(posixpath.join(remote_workflow_dir, "workflow_stats.json") if is_workflow else ""),
                remote_log_path=posixpath.join(remote_job_dir, ".jobdesk_submit.log"),
                remote_result_paths=[posixpath.join(spec.remote_dir, item) for item in remote_result_files],
                dry_run_command=(_append_command_flag(rendered_command, "--dry-run") if is_workflow else ""),
                resume_command=resume_command,
                resume_dry_run_command=(_append_command_flag(resume_command, "--dry-run") if is_workflow else ""),
                resume_requested=is_workflow and _has_command_flag(rendered_command, "--resume"),
            )
        )
    return RunPlan(run_id=rid, created_at=datetime.now(), spec=spec, tasks=tasks)


def _workflow_config_path(spec: RunSpec) -> str:
    """Return the exact staged workflow configuration used by ConfFlow."""
    for source in spec.supporting_sources:
        if source.path.lower().endswith((".yaml", ".yml")):
            return source.path
    return spec.supporting_sources[0].path if spec.supporting_sources else ""


def _has_command_flag(command: str, flag: str) -> bool:
    return re.search(rf"(?<!\S){re.escape(flag)}(?!\S)", command) is not None


def _append_command_flag(command: str, flag: str) -> str:
    return command if _has_command_flag(command, flag) else f"{command} {flag}"


def _sources_for_mode(spec: RunSpec) -> list[RunSource]:
    if spec.mode == RunMode.current_directory:
        return [RunSource(path=spec.remote_dir, is_dir=True)]
    if spec.mode == RunMode.selected_directories:
        return [source for source in spec.sources if source.is_dir]
    return [source for source in spec.sources if not source.is_dir]


def _render_command(template: str, source: RunSource) -> str:
    import shlex

    values = {
        "path": shlex.quote(source.path),
        "name": shlex.quote(source.name),
        "stem": shlex.quote(source.rendered_stem),
        "basename": shlex.quote(source.rendered_stem),
        "artifact_name": shlex.quote(source.rendered_name),
        "dir": shlex.quote(source.parent),
    }
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result


def _render_text_template(template: str, source: RunSource) -> str:
    values = {
        "path": source.path,
        "name": source.name,
        "stem": source.rendered_stem,
        "basename": source.rendered_stem,
        "artifact_name": source.rendered_name,
        "dir": source.parent,
    }
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result


def _safe_task_id(value: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return cleaned or f"task_{index}"


def _unique_task_id(candidate: str, used_task_ids: set[str]) -> str:
    if candidate not in used_task_ids:
        return candidate
    suffix = 2
    while f"{candidate}_{suffix}" in used_task_ids:
        suffix += 1
    return f"{candidate}_{suffix}"


def chunk_sources(sources: list[RunSource], batch_size: int | None) -> list[list[RunSource]]:
    if not batch_size or batch_size <= 0 or batch_size >= len(sources):
        return [list(sources)]
    return [sources[i : i + batch_size] for i in range(0, len(sources), batch_size)]
