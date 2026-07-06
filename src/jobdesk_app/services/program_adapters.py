"""Program-specific adapters that translate a JobDesk ``RunSpec`` into the
remote command template and download-pattern set.

Currently:

* :class:`ConfFlowAdapter` builds a multi-molecule batch whose remote program
  is ``confflow``. Submission goes through the existing nohup pipeline
  (``_submit_nohup``) — no scheduler change is needed, because the command
  template already encodes ``confflow {name} -c yaml -w work --resume`` and
  ``--resume`` lets a disconnected SSH session pick up where it left off via
  ConfFlow's checkpoint directory.
"""
from __future__ import annotations

import posixpath
import shlex

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
        config_name = posixpath.basename(config_path)
        command = (
            f"confflow {{name}} -c {shlex.quote(config_name)} "
            "-w {basename}_confflow_work"
        )
        if resume:
            command += " --resume"
        return RunSpec(
            server_id=server_id,
            remote_dir=remote_dir,
            command_template=command,
            max_parallel=max_parallel,
            mode=RunMode.selected_files,
            sources=[RunSource(p) for p in xyz_paths],
            supporting_sources=[RunSource(config_path)],
            result_templates=[
                "{basename}.txt",
                "{basename}min.xyz",
                "{basename}_confflow_work/run_summary.json",
                "{basename}_confflow_work/workflow_stats.json",
            ],
            workflow_kind=WorkflowKind.confflow,
        )
