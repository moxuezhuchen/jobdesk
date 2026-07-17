#!/usr/bin/env python3

"""Workflow execution engine (split from confflow.main).

Design goals:
- Pure business logic: no sys.exit calls.
- Testable: core entry ``run_workflow()`` accepts explicit parameters.

Phase 3 (DAG execution): the inner dispatch loop now walks the workflow
graph in topological order via :class:`graphlib.TopologicalSorter`. Each
step's handler receives the **list** of predecessor output paths instead of
a single path. The ``run_workflow`` public API is unchanged: legacy linear
workflows (no ``inputs`` declared on any step) keep working exactly as
before.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime
from typing import Any

from .. import calc
from ..blocks import confgen, viz
from ..config.schema import ConfigSchema
from ..core import io as io_xyz
from ..core.exceptions import ConfFlowError
from ..core.pairs import normalize_pair_list
from ..core.types import TaskStatus
from ..core.utils import (
    get_logger,
    index_to_letter_prefix,
)
from .config_builder import _itask_label as _itask_label  # re-export for test compatibility
from .config_builder import _normalize_iprog_label as _normalize_iprog_label  # re-export
from .config_builder import (
    build_step_dir_name_map,
    build_task_config,
    load_workflow_config,
)
from .dag import build_step_graph, topo_order
from .helpers import as_list, count_conformers_any, is_multi_frame_any, pushd
from .presenter import print_step_footer_block, print_step_header_block, print_workflow_start
from .stats import (
    CheckpointManager,
    FailureTracker,
    TaskStatsCollector,
    Tracer,
    WorkflowStatsTracker,
)
from .validation import validate_inputs_compatible

__all__ = [
    "run_workflow",
    "DagCycleError",
]

logger = get_logger()


class DagCycleError(ConfFlowError):
    """Raised when the workflow graph contains a dependency cycle.

    Inherits from :class:`ConfFlowError` so existing exception handlers
    that catch the base class still work.
    """


def _run_confgen_step(
    step_dir: str, inputs: list[str], params: dict[str, Any], input_files: list[str]
) -> str:
    """Execute a conformer generation step (DAG-aware).

    Primary predecessor is ``inputs[0]``. When more than one input is
    supplied (fan-in, or a multi-input workflow root) ``run_generation``
    receives the full ``inputs`` list so it can handle the conformer
    ensemble. The ``multi_frame`` shortcut only fires for the single-input
    case where ``inputs[0]`` itself contains multiple frames.
    """
    expected_output = os.path.join(step_dir, "search.xyz")
    primary = inputs[0] if inputs else (input_files[0] if input_files else "")

    if len(inputs) > 1:
        # Multi-input workflow root or fan-in: hand the full list to
        # ``run_generation``. Fan-in warnings are the engine's job; the
        # confgen call itself still wants the full list.
        with pushd(step_dir):
            confgen.run_generation(
                input_files=list(inputs),
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
            raise RuntimeError("confgen did not generate search.xyz")
        return expected_output

    # Single-input path. Use the multi-frame shortcut when the file
    # already contains multiple conformers.
    multi_frame = len(input_files) == 1 and is_multi_frame_any(primary)
    if multi_frame and isinstance(primary, str):
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
            raise RuntimeError("confgen did not generate search.xyz")
    return expected_output


def _run_calc_step(
    step_dir: str,
    inputs: list[str],
    params: dict[str, Any],
    global_config: dict[str, Any],
    root_dir: str,
    steps: list[dict[str, Any]],
    failure_tracker: FailureTracker,
    step_name: str,
) -> str:
    """Execute a calculation task step (DAG-aware)."""
    primary = inputs[0] if inputs else ""
    if len(inputs) > 1:
        logger.warning(
            "fan-in partially supported in this release: step %s received %d inputs, "
            "only the first (%s) is consumed",
            step_name,
            len(inputs),
            primary,
        )

    task_config = build_task_config(params, global_config, root_dir, steps)
    ConfigSchema.validate_calc_config(task_config)

    expected_clean = os.path.join(step_dir, "output.xyz")
    expected_raw = os.path.join(step_dir, "result.xyz")

    if os.path.exists(expected_clean) or os.path.exists(expected_raw):
        final_input = expected_clean if os.path.exists(expected_clean) else expected_raw
        step_failed = os.path.join(step_dir, "failed.xyz")
        if os.path.exists(step_failed):
            failure_tracker.append(step_failed, step_name)
        return final_input

    manager = calc.ChemTaskManager(task_config)
    manager.work_dir = step_dir
    manager.run(input_xyz_file=primary)

    work_cleaned = os.path.join(step_dir, "output.xyz")
    work_raw = os.path.join(step_dir, "result.xyz")
    work_failed = os.path.join(step_dir, "failed.xyz")

    if os.path.exists(work_cleaned):
        final_input = work_cleaned
    elif os.path.exists(work_raw):
        final_input = work_raw
    else:
        raise RuntimeError("Calculation task did not produce expected output")

    if os.path.exists(work_failed):
        failure_tracker.append(work_failed, step_name)

    return final_input


def run_workflow(
    input_xyz: list[str],
    config_file: str,
    work_dir: str,
    original_input_files: list[str] | None = None,
    resume: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    if verbose and hasattr(logger, "set_level"):
        logger.set_level(10)

    input_files = [os.path.abspath(x) for x in input_xyz]
    original_inputs = (
        [os.path.abspath(x) for x in original_input_files] if original_input_files else input_files
    )
    for fp in input_files:
        if not os.path.exists(fp):
            raise FileNotFoundError(f"Input file does not exist: {fp}")

    cfg = load_workflow_config(config_file)
    global_config = cfg["global"]
    steps = cfg["steps"]
    step_dirnames, _ = build_step_dir_name_map(steps)

    # Pre-load confgen params for multi-input flexible chain consistency check
    confgen_params = None
    if len(input_files) > 1:
        for step in steps:
            if step.get("type", "").lower() == "confgen":
                confgen_params = step.get("params", {})
                break
        validate_inputs_compatible(
            input_files,
            confgen_params,
            force_consistency=global_config.get("force_consistency", False),
        )

    root_dir = os.path.abspath(work_dir)
    os.makedirs(root_dir, exist_ok=True)
    failed_dir = os.path.join(root_dir, "failed")
    os.makedirs(failed_dir, exist_ok=True)

    try:
        shutil.copy2(config_file, os.path.join(failed_dir, os.path.basename(config_file)))
    except Exception:
        pass

    if hasattr(logger, "add_file_handler"):
        logger.add_file_handler(os.path.join(root_dir, "confflow.log"))

    checkpoint = CheckpointManager(root_dir)
    stats_tracker = WorkflowStatsTracker(input_files, original_inputs)
    failure_tracker = FailureTracker(failed_dir)

    if not resume:
        failure_tracker.clear_previous()

    resume_from_step = checkpoint.load() if resume else -1
    current_input: str | list[str] = input_files[0] if len(input_files) == 1 else input_files

    # === Print workflow start header ===
    print_workflow_start(input_files, current_input)

    # ------------------------------------------------------------------
    # Phase 3: DAG dispatch.
    # ------------------------------------------------------------------
    # When at least one step declares an ``inputs`` field we honour those
    # explicit edges. When none do we synthesise the legacy linear chain
    # (step i's predecessor is step i-1) so existing YAML workflows run
    # exactly as before.
    raw_predecessors, by_step_name, declared_inputs = build_step_graph(steps)
    if any(declared_inputs.values()):
        predecessors = raw_predecessors
    else:
        ordered_names = list(by_step_name.keys())
        predecessors = {
            name: ([ordered_names[i - 1]] if i > 0 else []) for i, name in enumerate(ordered_names)
        }
    waves = topo_order(predecessors)

    # Linear-order index for the checkpoint bookkeeping that the existing
    # resume semantics expect.
    position_by_name: dict[str, int] = {
        name: idx for idx, (name, _) in enumerate(zip(by_step_name.keys(), steps))
    }
    name_to_dirname: dict[str, str] = dict(zip(by_step_name.keys(), step_dirnames))

    # step_name -> output xyz path recorded for downstream consumers.
    step_outputs: dict[str, str] = {}

    def _seed_for_root(predecessor_name: str) -> str:
        """Return the upstream input a root step should consume."""
        if isinstance(current_input, str):
            return current_input
        # Multi-input workflow: feed the first global input as the
        # predecessor seed (matches legacy linear behaviour).
        return current_input[0]

    def _resolve_inputs_for_step(step_name: str) -> list[str]:
        deps = predecessors.get(step_name, [])
        if not deps:
            # Root step. If the workflow started with multiple inputs,
            # feed them all; otherwise pass the single path.
            if isinstance(current_input, list):
                return list(current_input)
            return [current_input]
        result: list[str] = []
        for dep in deps:
            out = step_outputs.get(dep)
            if out is None:
                raise ConfFlowError(
                    f"step {step_name!r} depends on {dep!r} but that step produced no output"
                )
            result.append(out)
        return result

    finished_count = 0
    total_steps = len(steps)
    for wave in waves:
        for step_name in sorted(wave, key=lambda n: str(n)):
            step = by_step_name[step_name]
            step_type = step.get("type", "")

            if resume_from_step >= position_by_name.get(step_name, -1):
                existing_dir = os.path.join(root_dir, name_to_dirname[step_name])
                seeded: str | None = None
                for candidate in ("output.xyz", "result.xyz", "search.xyz"):
                    cand_path = os.path.join(existing_dir, candidate)
                    if os.path.exists(cand_path):
                        seeded = cand_path
                        break
                # When the resumed step has no surviving output on disk
                # (e.g. the original run wrote somewhere we no longer see)
                # fall back to the workflow input so the chain keeps
                # advancing. This matches the legacy linear resume path.
                step_outputs[step_name] = seeded or _seed_for_root(step_name)
                finished_count += 1
                continue

            if not step.get("enabled", True):
                finished_count += 1
                continue

            step_dir = os.path.join(root_dir, name_to_dirname[step_name])
            os.makedirs(step_dir, exist_ok=True)
            params = step.get("params", {}) or {}
            inputs_for_step = _resolve_inputs_for_step(step_name)

            step_start = time.time()
            in_n = count_conformers_any(inputs_for_step)
            step_stats: dict[str, Any] = {
                "name": step_name,
                "type": step_type,
                "index": finished_count + 1,
                "input_conformers": in_n,
                "start_time": datetime.now().isoformat(),
            }

            current_input_str: str | None = None
            try:
                print_step_header_block(
                    step_index=finished_count + 1,
                    total_steps=total_steps,
                    step_name=step_name,
                    step_type=step_type,
                    global_config=global_config,
                    params=params,
                    in_count=in_n,
                )

                if step_type in ["confgen", "gen"]:
                    expected_output = os.path.join(step_dir, "search.xyz")
                    primary = inputs_for_step[0]
                    multi_frame = len(input_files) == 1 and is_multi_frame_any(primary)
                    if multi_frame:
                        step_stats["status"] = TaskStatus.SKIPPED_MULTI
                    elif os.path.exists(expected_output):
                        step_stats["status"] = TaskStatus.SKIPPED

                    current_input_str = _run_confgen_step(
                        step_dir, inputs_for_step, params, input_files
                    )
                    io_xyz.ensure_xyz_cids(current_input_str, prefix=index_to_letter_prefix(0))
                    if step_stats.get("status") not in [
                        TaskStatus.SKIPPED_MULTI,
                        TaskStatus.SKIPPED,
                    ]:
                        step_stats["status"] = TaskStatus.COMPLETED

                elif step_type in ["calc", "task"]:
                    expected_clean = os.path.join(step_dir, "output.xyz")
                    expected_raw = os.path.join(step_dir, "result.xyz")
                    if os.path.exists(expected_clean) or os.path.exists(expected_raw):
                        step_stats["status"] = TaskStatus.SKIPPED

                    current_input_str = _run_calc_step(
                        step_dir,
                        inputs_for_step,
                        params,
                        global_config,
                        root_dir,
                        steps,
                        failure_tracker,
                        step_name,
                    )
                    io_xyz.ensure_xyz_cids(current_input_str, prefix=index_to_letter_prefix(0))
                    if step_stats.get("status") != TaskStatus.SKIPPED:
                        step_stats["status"] = TaskStatus.COMPLETED

                else:
                    raise ConfFlowError(f"unknown step type: {step_type!r}")

                step_outputs[step_name] = current_input_str
                step_stats["output_xyz"] = os.path.abspath(current_input_str)

            except Exception as e:
                step_stats["status"] = TaskStatus.FAILED
                step_stats["error"] = str(e)
                checkpoint.save(finished_count - 1, stats_tracker.get_stats())
                raise
            finally:
                step_stats["end_time"] = datetime.now().isoformat()
                step_stats["duration_seconds"] = round(time.time() - step_start, 2)
                step_stats["output_conformers"] = (
                    count_conformers_any(current_input_str) if current_input_str else 0
                )

                failed_count = 0
                if step_type in ["calc", "task"]:
                    db_path = os.path.join(step_dir, "results.db")
                    failed_count = TaskStatsCollector.count_failed(db_path) or 0
                    step_stats["failed_conformers"] = failed_count

                print_step_footer_block(
                    step_stats=step_stats,
                    in_count=in_n,
                    failed_count=failed_count,
                )

                stats_tracker.add_step(step_stats)
                if step_stats["status"] in [
                    TaskStatus.COMPLETED,
                    TaskStatus.SKIPPED,
                    TaskStatus.SKIPPED_MULTI,
                ]:
                    checkpoint.save(finished_count, stats_tracker.get_stats())

            finished_count += 1

    # The final output is the last recorded step output (by YAML order).
    if step_outputs:
        last_name: str | None = None
        for step in steps:
            n = step.get("name") or step.get("key")
            if n in step_outputs:
                last_name = n
        if last_name is None:
            last_name = next(iter(step_outputs))
        final_input: str | list[str] = step_outputs[last_name]
    else:
        final_input = current_input

    final_stats = stats_tracker.finalize(final_input)

    # Tracing
    try:
        Tracer.trace_low_energy(final_stats)
    except Exception as e:
        logger.debug(f"Trace failed: {e}")

    # Report and lowest energy output
    if isinstance(final_input, str) and os.path.exists(final_input):
        confs = viz.parse_xyz_file(final_input)
        report_text = viz.generate_text_report(confs, stats=final_stats)
        if report_text:
            # CLI redirects stdout to <input>.txt (isatty=False). When used as a library from a TTY,
            # keep silent by default.
            if not sys.stdout.isatty():
                print(report_text)

        best_conf, best_energy, _ = viz.get_lowest_energy_conformer(confs)
        if best_conf:
            input_dir = os.path.dirname(os.path.abspath(original_inputs[0]))
            input_base = os.path.splitext(os.path.basename(original_inputs[0]))[0]
            lowest_path = os.path.join(input_dir, f"{input_base}min.xyz")
            io_xyz.write_xyz_file(lowest_path, [best_conf], atomic=True)

            best_meta = best_conf.get("metadata") or {}
            final_stats["lowest_conformer"] = {
                "cid": best_meta.get("CID"),
                "energy": best_energy,
                "xyz_path": lowest_path,
            }
            logger.info(f"Lowest-energy conformer written: {lowest_path}")

    # Write final statistics
    stats_file = os.path.join(root_dir, "workflow_stats.json")
    with open(stats_file, "w", encoding="utf-8") as f:
        import json

        json.dump(final_stats, f, indent=2, ensure_ascii=False)

    return final_stats
