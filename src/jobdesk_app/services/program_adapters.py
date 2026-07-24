"""Program-specific adapters that translate a JobDesk ``RunSpec`` into the
remote command template and download-pattern set.

Currently:

* :class:`ConfFlowAdapter` builds a multi-molecule batch whose remote program
  is ``confflow``. Submission goes through the existing nohup pipeline
  (``_submit_nohup``) — no scheduler change is needed, because the command
  template invokes ``confflow`` with the staged input, YAML configuration,
  and a per-input work directory.  Callers can opt into ``--resume`` when
  they want ConfFlow to continue from its checkpoint directory.
"""

from __future__ import annotations

import posixpath
import shlex

from ..core.confflow_contract import (
    RUN_SUMMARY_FILE,
    WORK_DIR_SUFFIX,
    WORKFLOW_STATE_FILE,
    WORKFLOW_STATS_FILE,
)
from ..core.run import RunMode, RunSource, RunSpec, WorkflowKind


class ConfFlowAdapter:
    """Build a single JobDesk run whose remote program owns the workflow."""

    @staticmethod
    def build_spec(
        server_id: str,
        remote_dir: str,
        xyz_paths: list[str] | str,
        config_path: str,
        max_parallel: int = 1,
        resume: bool = False,
    ) -> RunSpec:
        if isinstance(xyz_paths, str):
            xyz_paths = [xyz_paths]
        work_dir_token = f"{{basename}}{WORK_DIR_SUFFIX}"
        command = (
            f"workspace={shlex.quote(remote_dir)} && source={{path}} && "
            'staged="$workspace/"{artifact_name} && cd "$workspace" && '
            'if [ "$source" != "$staged" ]; then cp -- "$source" "$staged"; fi && '
            f'confflow "$staged" -c {shlex.quote(config_path)} '
            f'-w "$workspace/"{work_dir_token}'
        )
        if resume:
            command += " --resume"
        return RunSpec(
            server_id=server_id,
            remote_dir=remote_dir,
            command_template=command,
            max_parallel=max_parallel,
            mode=RunMode.selected_files,
            sources=_workflow_sources(xyz_paths),
            supporting_sources=[RunSource(config_path)],
            result_templates=[
                "{basename}.txt",
                "{basename}min.xyz",
                f"{work_dir_token}/{RUN_SUMMARY_FILE}",
                f"{work_dir_token}/{WORKFLOW_STATS_FILE}",
                f"{work_dir_token}/{WORKFLOW_STATE_FILE}",
            ],
            workflow_kind=WorkflowKind.confflow,
        )

    @staticmethod
    def build_dag_spec(
        server_id: str,
        remote_dir: str,
        xyz_paths: list[str] | str,
        config_path: str,
        max_parallel: int = 1,
        resume: bool = False,
    ) -> RunSpec:
        """Build a DAG-flavoured ConfFlow run.

        The remote command template and ``result_templates`` are
        identical to :meth:`build_spec`; the engine's DAG walk lives
        entirely in the YAML payload (``StepConfig.inputs``) and is
        parsed by the ConfFlow DAG engine.  We
        flip ``workflow_kind`` to ``WorkflowKind.dag`` so the runs
        / results page can distinguish a DAG run from a linear one.
        """
        if isinstance(xyz_paths, str):
            xyz_paths = [xyz_paths]
        work_dir_token = f"{{basename}}{WORK_DIR_SUFFIX}"
        command = (
            f"workspace={shlex.quote(remote_dir)} && source={{path}} && "
            'staged="$workspace/"{artifact_name} && cd "$workspace" && '
            'if [ "$source" != "$staged" ]; then cp -- "$source" "$staged"; fi && '
            f'confflow "$staged" -c {shlex.quote(config_path)} '
            f'-w "$workspace/"{work_dir_token}'
        )
        if resume:
            command += " --resume"
        return RunSpec(
            server_id=server_id,
            remote_dir=remote_dir,
            command_template=command,
            max_parallel=max_parallel,
            mode=RunMode.selected_files,
            sources=_workflow_sources(xyz_paths),
            supporting_sources=[RunSource(config_path)],
            result_templates=[
                "{basename}.txt",
                "{basename}min.xyz",
                f"{work_dir_token}/{RUN_SUMMARY_FILE}",
                f"{work_dir_token}/{WORKFLOW_STATS_FILE}",
                f"{work_dir_token}/{WORKFLOW_STATE_FILE}",
            ],
            workflow_kind=WorkflowKind.dag,
        )


def _workflow_sources(paths: list[str]) -> list[RunSource]:
    """Assign collision-free staged names while preserving unique basenames."""
    used_stems: set[str] = set()
    used_names: set[str] = set()
    sources: list[RunSource] = []
    for index, path in enumerate(paths, start=1):
        name = posixpath.basename(path.rstrip("/")) or f"input_{index}"
        stem, extension = posixpath.splitext(name)
        stem = stem or f"input_{index}"
        candidate = stem
        candidate_name = f"{candidate}{extension}"
        suffix = 2
        while candidate in used_stems or candidate_name in used_names:
            candidate = f"{stem}_{suffix}"
            candidate_name = f"{candidate}{extension}"
            suffix += 1
        used_stems.add(candidate)
        used_names.add(candidate_name)
        sources.append(
            RunSource(
                path,
                artifact_stem=candidate,
                artifact_name=candidate_name,
            )
        )
    return sources
