#!/usr/bin/env python3

"""
ConfFlow YAML configuration validation.

This module provides standalone validation functions for workflow YAML documents
that are compatible with the ConfFlow schema. These functions are maintained
locally so editor validation remains independent of the external package.
"""

from __future__ import annotations

import re
from typing import Any


def validate_yaml_config(
    config: dict[str, Any], required_sections: list[str] | None = None
) -> list[str]:
    """Validate the structure of a YAML configuration file.

    Parameters
    ----------
    config : dict
        Parsed configuration dictionary.
    required_sections : list of str or None
        List of required configuration sections.

    Returns
    -------
    list of str
        List of error messages (empty list means validation passed).
    """
    errors: list[str] = []

    if required_sections is None:
        required_sections = ["global", "steps"]

    for section in required_sections:
        if section not in config:
            errors.append(f"missing required section: '{section}'")

    if "global" in config:
        global_config = config["global"]

        if not isinstance(global_config, dict):
            errors.append("'global' must be a mapping")
        else:
            cores = global_config.get("cores_per_task", 1)
            if not isinstance(cores, int) or cores <= 0:
                errors.append(f"invalid cores_per_task: {cores}")

            max_jobs = global_config.get("max_parallel_jobs", 1)
            if not isinstance(max_jobs, int) or max_jobs <= 0:
                errors.append(f"invalid max_parallel_jobs: {max_jobs}")

    if "steps" in config:
        steps = config["steps"]

        if not isinstance(steps, list):
            errors.append("'steps' must be a list")
        else:
            for i, step in enumerate(steps):
                step_errors = _validate_step_config(step, i)
                errors.extend(step_errors)

    return errors


def _validate_step_config(step: dict[str, Any], index: int) -> list[str]:
    """Validate a single step's configuration."""
    errors: list[str] = []
    step_id = f"step {index + 1}"

    def _pair_list_ok(val: Any) -> bool:
        if val is None:
            return True
        if isinstance(val, str):
            nums = re.findall(r"\d+", val)
            return len(nums) >= 2
        if isinstance(val, (list, tuple)):
            if len(val) == 0:
                return True
            if len(val) == 2 and all(isinstance(x, int) for x in val):
                return True
            if all(isinstance(x, (list, tuple)) and len(x) == 2 for x in val):
                return True
            if all(isinstance(x, str) for x in val):
                return all(len(re.findall(r"\d+", x)) >= 2 for x in val)
        return False

    if "name" not in step:
        errors.append(f"{step_id}: missing 'name' field")
    else:
        step_id = f"step '{step['name']}'"

    if "type" not in step:
        errors.append(f"{step_id}: missing 'type' field")
    else:
        step_type = step["type"]
        valid_types = ["confgen", "calc", "gen", "task"]
        if step_type not in valid_types:
            errors.append(
                f"{step_id}: invalid type '{step_type}', must be 'confgen', 'calc', 'gen' or 'task'"
            )

    if "params" in step:
        params = step["params"]
        step_type = step.get("type", "")

        if step_type in ["calc", "task"]:
            itask = params.get("itask")
            valid_itasks = ["opt", "sp", "freq", "opt_freq", "ts", 0, 1, 2, 3, 4]
            if itask is not None and itask not in valid_itasks:
                errors.append(f"{step_id}: invalid itask value '{itask}'")

            iprog = params.get("iprog")
            valid_iprogs = ["gaussian", "g16", "orca", 1, 2]
            if iprog is not None and iprog not in valid_iprogs:
                errors.append(f"{step_id}: invalid iprog value '{iprog}'")

            if "keyword" not in params and iprog in ["orca", 2]:
                errors.append(f"{step_id}: ORCA task missing 'keyword' parameter")

        elif step_type in ["confgen", "gen"]:
            chains = params.get("chains", None)
            if chains is None:
                chains = params.get("chain", None)
            if not chains:
                errors.append(
                    f"{step_id}: confgen step requires 'chains' (or 'chain'), e.g. chains: ['81-79-78-86-92']"
                )

            for key in ("add_bond", "del_bond", "no_rotate", "force_rotate"):
                if key in params and not _pair_list_ok(params.get(key)):
                    errors.append(
                        f"{step_id}: confgen parameter '{key}' format error; expected [[a,b], ...] / [a,b] / ['a b', ...] / 'a b' (1-based indices)"
                    )

            angle_step = params.get("angle_step")
            if angle_step is not None:
                if not isinstance(angle_step, (int, float)) or angle_step <= 0:
                    errors.append(f"{step_id}: invalid angle_step value '{angle_step}'")

    return errors


__all__ = ["validate_yaml_config"]
