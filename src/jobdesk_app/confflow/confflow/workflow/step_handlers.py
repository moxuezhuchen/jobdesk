#!/usr/bin/env python3

"""Workflow step handler functions.

Phase 3 (DAG execution): ``run_confgen_step`` and ``run_calc_step`` now
accept an ``inputs: list[str]`` of predecessor output paths instead of a
single ``current_input`` value. The first entry is treated as the primary
predecessor (fed to ``ChemTaskManager.run``); additional entries are
recorded as a WARNING because real fan-in merging is partially supported
in this release.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import Any

from .. import calc
from ..blocks import confgen
from ..config.schema import ConfigSchema
from ..core.exceptions import ConfFlowError
from ..core.pairs import normalize_pair_list
from .config_builder import build_task_config
from .helpers import as_list, is_multi_frame_any, pushd, resolve_step_output
from .stats import FailureTracker

__all__ = [
    "StepContext",
    "run_confgen_step",
    "run_calc_step",
]


logger = logging.getLogger("confflow.workflow.step_handlers")


@dataclass
class StepContext:
    """Encapsulates common parameters shared between step handler functions.

    Reduces the parameter count of ``run_calc_step`` from 8 positional
    arguments to a single context object, improving readability and
    making it easier to add new context fields in the future.
    """

    step_dir: str
    inputs: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    global_config: dict[str, Any] = field(default_factory=dict)
    root_dir: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    failure_tracker: FailureTracker | None = None
    step_name: str = ""


def _primary_input(inputs: list[str]) -> str:
    """Return the first non-empty input path; raise if the list is empty."""
    if not inputs:
        raise ConfFlowError("step invoked with empty inputs list")
    primary = inputs[0]
    if not isinstance(primary, str) or not primary:
        raise ConfFlowError(f"step invoked with invalid primary input: {primary!r}")
    return primary


def run_confgen_step(
    step_dir: str,
    inputs: list[str],
    params: dict[str, Any],
    input_files: list[str],
) -> str:
    """Execute a conformer generation step (execution adapter layer).

    Parameters
    ----------
    step_dir : str
        Per-step working directory (e.g. ``<root>/step_01``).
    inputs : list[str]
        List of predecessor output paths. The first entry is the primary
        predecessor; additional entries indicate fan-in (not yet supported
        beyond passing the primary through).
    params : dict
        Conformer-generation parameters (``chains``, ``angle_step``, etc.).
    input_files : list[str]
        The original workflow input files; used only for the multi-frame
        detection heuristic.
    """
    if len(inputs) > 1:
        logger.warning(
            "step %s received %d inputs from predecessors; fan-in is partially "
            "supported in this release. Using the primary (inputs[0]) only.",
            os.path.basename(step_dir),
            len(inputs),
        )

    primary = _primary_input(inputs)
    expected_output = os.path.join(step_dir, "search.xyz")
    multi_frame = len(input_files) == 1 and is_multi_frame_any(primary)

    if multi_frame:
        shutil.copy2(primary, expected_output)
    elif not os.path.exists(expected_output):
        with pushd(step_dir):
            confgen.run_generation(
                input_files=primary,
                angle_step=params.get("angle_step", 120),
                bond_threshold=params.get("bond_multiplier", 1.15),
                clash_threshold=0.65,
                add_bond=normalize_pair_list(params.get("add_bond")),
                del_bond=normalize_pair_list(params.get("del_bond")),
                no_rotate=normalize_pair_list(params.get("no_rotate")),
                force_rotate=normalize_pair_list(params.get("force_rotate")),
                optimize=params.get("optimize", False),
                confirm=False,
                chains=as_list(params.get("chains", params.get("chain"))),
                chain_steps=as_list(params.get("chain_steps", params.get("steps"))),
                chain_angles=as_list(params.get("chain_angles", params.get("angles"))),
                rotate_side=params.get("rotate_side", "left"),
            )
        if not os.path.exists(expected_output):
            raise ConfFlowError("confgen did not generate search.xyz")
    return expected_output


def run_calc_step(
    step_dir: str,
    inputs: list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str,
) -> str:
    """Execute a calculation step (execution adapter layer).

    Parameters
    ----------
    step_dir : str
        Per-step working directory.
    inputs : list[str]
        List of predecessor output paths. The first entry is the primary
        predecessor that ``ChemTaskManager.run`` will consume; any extra
        entries trigger a fan-in warning.
    params : dict
        Step-specific calc parameters (``iprog``, ``itask``, ``keyword``,
        ...).
    global_config, root_dir, steps : ...
        Forwarded to ``build_task_config``.
    failure_tracker : FailureTracker
        Where to record any ``failed.xyz`` produced by the calc.
    step_name : str
        Display name of the step (used for failure bookkeeping).
    """
    if len(inputs) > 1:
        logger.warning(
            "step %s received %d inputs from predecessors; fan-in is partially "
            "supported in this release. Using the primary (inputs[0]) only.",
            step_name,
            len(inputs),
        )

    primary = _primary_input(inputs)
    task_config = build_task_config(params, global_config, root_dir, steps)
    ConfigSchema.validate_calc_config(task_config)

    existing_output = resolve_step_output(step_dir, "calc")
    if existing_output is not None:
        final_input = existing_output
        step_failed = os.path.join(step_dir, "failed.xyz")
        if os.path.exists(step_failed):
            failure_tracker.append(step_failed, step_name)
        return final_input

    manager = calc.ChemTaskManager(task_config)
    manager.work_dir = step_dir
    manager.run(input_xyz_file=primary)

    work_failed = os.path.join(step_dir, "failed.xyz")

    final_input = resolve_step_output(step_dir, "calc")
    if final_input is None:
        raise ConfFlowError("Calculation task did not produce expected output")

    if os.path.exists(work_failed):
        failure_tracker.append(work_failed, step_name)

    return final_input
