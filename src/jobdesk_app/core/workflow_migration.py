"""Compatibility migration for pre-canonical workflow documents.

The authoring document deliberately keeps this policy out of its data model.
This adapter is the one bounded place where JobDesk's historical token-list
and flat workflow shapes are projected into the producer-facing shape.  It is
not a producer validator: acceptance of ``itask``/``iprog`` values and
``confgen`` parameters remains owned by the producer validator port.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _format_memory(value: Any) -> str:
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return "4GB"
    if amount >= 1024 and amount % 1024 == 0:
        return f"{amount // 1024}GB"
    if amount >= 1024:
        return f"{amount / 1024:.1f}GB"
    return f"{amount}MB"


class LegacyWorkflowMigrationAdapter:
    """Project supported historical shapes without discarding extensions."""

    @staticmethod
    def step_from_token(token: str, *, index: int | None = None) -> dict[str, Any]:
        """Convert a historical wizard token into one producer step.

        This is migration compatibility, not semantic acceptance.  The
        producer validator still decides whether the resulting fields are
        legal for the installed ConfFlow version.
        """

        value = str(token or "").strip().lower()
        if not value:
            return {
                "name": f"step_{(index or 1):02d}",
                "type": "calc",
                "params": {"itask": "sp"},
            }
        if value == "confgen":
            return {
                "name": value,
                "type": "confgen",
                "params": {
                    "chains": ["1-2-3-4"],
                    "angle_step": 120,
                    "bond_multiplier": 1.15,
                },
            }
        task = {"preopt": "opt", "refine": "sp"}.get(value, value)
        return {"name": value, "type": "calc", "params": {"itask": task}}

    @classmethod
    def normalise_steps(cls, raw_steps: Any, *, add_legacy_defaults: bool = True) -> list[dict[str, Any]]:
        """Convert legacy step containers while preserving unknown fields."""

        if raw_steps is None:
            return []
        if not isinstance(raw_steps, list):
            raise ValueError("workflow steps must be a list")

        result: list[dict[str, Any]] = []
        for index, step in enumerate(raw_steps, start=1):
            if isinstance(step, str):
                result.append(cls.step_from_token(step, index=index))
                continue
            if not isinstance(step, dict):
                raise ValueError(f"workflow step {index} must be a mapping")

            item = deepcopy(step)
            item.setdefault("name", f"step_{index:02d}")
            item.setdefault("type", "calc")
            params = item.get("params")
            if params is None:
                params = {}
                item["params"] = params
            elif not isinstance(params, dict):
                raise ValueError(f"workflow step {index} params must be a mapping")

            # Older files put these fields beside ``params``.  Copy them into
            # the producer view, but retain the original fields for a
            # lossless authoring round-trip.
            for key in (
                "iprog",
                "itask",
                "keyword",
                "energy_window",
                "cores_per_task",
                "total_memory",
                "max_parallel_jobs",
                "blocks",
            ):
                if key in item and key not in params:
                    params[key] = deepcopy(item[key])

            if add_legacy_defaults and item.get("type") == "calc":
                params.setdefault("itask", "sp")
            if "inputs" in item:
                inputs = item["inputs"]
                if not isinstance(inputs, list) or not all(isinstance(value, str) for value in inputs):
                    raise ValueError(f"workflow step {index} inputs must be a list of step names")
            result.append(item)
        return result

    @staticmethod
    def _lift_legacy_resource_keys(global_block: dict[str, Any]) -> None:
        if "nproc" in global_block and "cores_per_task" not in global_block:
            try:
                global_block["cores_per_task"] = int(global_block["nproc"])
            except (TypeError, ValueError):
                pass
            global_block.pop("nproc", None)
        if "memory_mb" in global_block and "total_memory" not in global_block:
            global_block["total_memory"] = _format_memory(global_block["memory_mb"])
            global_block.pop("memory_mb", None)

    @staticmethod
    def _attach_flat_step_fields(global_block: dict[str, Any], steps: list[dict[str, Any]]) -> None:
        first_calc = next((step for step in steps if step.get("type") == "calc"), None)
        if first_calc is None:
            return
        for key in ("keyword", "iprog", "blocks"):
            if key in global_block and key not in first_calc.get("params", {}):
                first_calc.setdefault("params", {})[key] = deepcopy(global_block[key])
                global_block.pop(key)

    def canonicalize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Return the historical compatibility projection of ``data``."""

        if not isinstance(data, dict):
            raise ValueError("workflow YAML must be a mapping at the top level")
        source = deepcopy(data)

        if "global" in source:
            global_block = source.get("global")
            if global_block is None:
                global_block = {}
            if not isinstance(global_block, dict):
                raise ValueError("workflow global must be a mapping")
            # Canonical documents are preserved as authored.  Only nested
            # token-list steps need the compatibility projection.
            out = source
            out["global"] = global_block
            out["steps"] = self.normalise_steps(
                source.get("steps") or [],
                add_legacy_defaults=False,
            )
            self._lift_legacy_resource_keys(global_block)
            return out

        if "steps" in source:
            global_block = {key: value for key, value in source.items() if key != "steps"}
            self._lift_legacy_resource_keys(global_block)
            steps = self.normalise_steps(source.get("steps") or [])
            self._attach_flat_step_fields(global_block, steps)
            return {"global": global_block, "steps": steps}

        legacy_calc_value = source.get("calc")
        legacy_calc: dict[str, Any]
        if isinstance(legacy_calc_value, dict):
            legacy_calc = legacy_calc_value
        else:
            legacy_calc = {}
        global_block = {key: value for key, value in source.items() if key not in {"calc", "steps"}}
        for key, value in legacy_calc.items():
            if key != "steps":
                global_block.setdefault(key, deepcopy(value))
        self._lift_legacy_resource_keys(global_block)
        steps = self.normalise_steps(legacy_calc.get("steps") or source.get("steps") or [])
        self._attach_flat_step_fields(global_block, steps)
        return {"global": global_block, "steps": steps}


__all__ = ["LegacyWorkflowMigrationAdapter"]
