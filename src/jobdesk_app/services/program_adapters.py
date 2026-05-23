from __future__ import annotations

import posixpath
import shlex

from ..core.run import RunMode, RunSource, RunSpec


class ConfFlowAdapter:
    """Build a single JobDesk run whose remote program owns the workflow."""

    @staticmethod
    def build_spec(
        server_id: str,
        remote_dir: str,
        xyz_path: str,
        config_path: str,
        max_parallel: int = 1,
        resume: bool = False,
    ) -> RunSpec:
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
            sources=[RunSource(xyz_path)],
            supporting_sources=[RunSource(config_path)],
            result_templates=[
                "{basename}.txt",
                "{basename}min.xyz",
                "{basename}_confflow_work/run_summary.json",
                "{basename}_confflow_work/workflow_stats.json",
            ],
        )
