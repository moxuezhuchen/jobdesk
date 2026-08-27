"""Pure mappings between saved workflow layouts and JobDesk's canonical view."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .workflow_document import WorkflowDocument

STEP_TOKEN_TO_TYPE: dict[str, tuple[str, dict[str, Any]]] = {
    "confgen": ("confgen", {"chains": ["1-2-3-4"], "angle_step": 120, "bond_multiplier": 1.15}),
    "preopt": ("calc", {"itask": "opt"}),
    "opt": ("calc", {"itask": "opt"}),
    "opt_freq": ("calc", {"itask": "opt_freq"}),
    "sp": ("calc", {"itask": "sp"}),
    "freq": ("calc", {"itask": "freq"}),
    "ts": ("calc", {"itask": "ts"}),
    "refine": ("calc", {"itask": "sp"}),
}


def token_to_step(token: str, *, idx: int | None = None) -> dict[str, Any]:
    tok = str(token or "").strip().lower()
    if not tok:
        return {"name": f"step_{(idx or 1):02d}", "type": "calc", "params": {"itask": "sp"}}
    step_type, base_params = STEP_TOKEN_TO_TYPE.get(tok, ("calc", {"itask": tok}))
    return {"name": tok, "type": step_type, "params": dict(base_params)}


def normalize_steps(raw_steps: Any) -> list[dict[str, Any]]:
    """Produce a canonical view without dropping unfamiliar step members."""
    if raw_steps == []:
        return []
    if not isinstance(raw_steps, list):
        raise ValueError("steps must be a list")
    output: list[dict[str, Any]] = []
    for index, value in enumerate(raw_steps, start=1):
        if isinstance(value, str):
            output.append(token_to_step(value, idx=index))
            continue
        if not isinstance(value, dict):
            # A malformed item must remain visible to the caller/linter rather
            # than being silently discarded.
            output.append({"name": f"step_{index:02d}", "type": "calc", "params": {}, "_jobdesk_raw": deepcopy(value)})
            continue
        step = deepcopy(value)
        params_value = step.get("params")
        if "params" in step and not isinstance(params_value, dict):
            raise ValueError(f"step {index} params must be a mapping")
        params = deepcopy(params_value) if isinstance(params_value, dict) else {}
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
            if key in step and key not in params:
                params[key] = step[key]
        step["name"] = str(step.get("name") or f"step_{index:02d}")
        step["type"] = str(step.get("type") or "calc")
        step["params"] = params
        if step["type"] == "calc":
            params.setdefault("itask", "sp")
        if "inputs" in step and (
            not isinstance(step["inputs"], list) or not all(isinstance(x, str) for x in step["inputs"])
        ):
            raise ValueError("step inputs must be a list of step names")
        output.append(step)
    return output


def _lift_legacy_resources(global_block: dict[str, Any]) -> None:
    if "nproc" in global_block and "cores_per_task" not in global_block:
        try:
            global_block["cores_per_task"] = int(global_block["nproc"])
        except (TypeError, ValueError):
            pass
        global_block.pop("nproc", None)
    if "memory_mb" in global_block and "total_memory" not in global_block:
        value = global_block.pop("memory_mb")
        try:
            number = int(value)
            global_block["total_memory"] = (
                f"{number // 1024}GB" if number >= 1024 and number % 1024 == 0 else f"{number}MB"
            )
        except (TypeError, ValueError):
            global_block["memory_mb"] = value


def canonical_mapping(document: WorkflowDocument | dict[str, Any]) -> dict[str, Any]:
    """Map v0.5/v0.6/flat/token layouts while retaining extensions verbatim."""
    source = document.to_mapping() if isinstance(document, WorkflowDocument) else deepcopy(document)
    if not isinstance(source, dict):
        return {"global": {}, "steps": []}
    if "global" in source:
        global_value = source.get("global")
        global_block = deepcopy(global_value) if isinstance(global_value, dict) else global_value
        if isinstance(global_block, dict):
            _lift_legacy_resources(global_block)
        result = {key: deepcopy(value) for key, value in source.items() if key not in {"global", "steps"}}
        result["global"] = global_block if global_block is not None else {}
        result["steps"] = normalize_steps(source["steps"] if "steps" in source else [])
        _move_legacy_calc_fields(result)
        return result
    if "steps" in source:
        preserved = source.get("__jobdesk_document_extensions__")
        global_block = {
            key: deepcopy(value)
            for key, value in source.items()
            if key not in {"steps", "__jobdesk_document_extensions__"}
        }
        top_level: dict[str, Any] = {}
        if isinstance(preserved, dict):
            saved_global = preserved.get("state")
            if isinstance(saved_global, dict):
                global_block = {**deepcopy(saved_global), **global_block}
            saved_top = preserved.get("top_level")
            if isinstance(saved_top, dict):
                top_level = deepcopy(saved_top)
        _lift_legacy_resources(global_block)
        result = {"global": global_block, "steps": normalize_steps(source["steps"] if "steps" in source else [])}
        result = {**top_level, **result}
        _move_legacy_calc_fields(result)
        return result
    legacy_value = source.get("calc")
    legacy_calc: dict[str, Any] = deepcopy(legacy_value) if isinstance(legacy_value, dict) else {}
    global_block = {key: deepcopy(value) for key, value in source.items() if key not in {"calc", "steps"}}
    for key, value in legacy_calc.items():
        if key != "steps":
            global_block.setdefault(key, deepcopy(value))
    _lift_legacy_resources(global_block)
    result = {"global": global_block, "steps": normalize_steps(legacy_calc.get("steps") or source.get("steps") or [])}
    _move_legacy_calc_fields(result)
    return result


def _move_legacy_calc_fields(payload: dict[str, Any]) -> None:
    """Attach old flat calc aliases to the first calc step without losing them."""
    global_block = payload.get("global")
    steps = payload.get("steps")
    if not isinstance(global_block, dict) or not isinstance(steps, list):
        return
    first_calc = next((step for step in steps if isinstance(step, dict) and step.get("type") == "calc"), None)
    if first_calc is None:
        return
    params = first_calc.get("params")
    if not isinstance(params, dict):
        return
    program = global_block.get("program", global_block.get("iprog"))
    if program is not None and "iprog" not in params:
        params["iprog"] = program
    if "keyword" not in params:
        parts = [str(global_block[key]).strip() for key in ("method", "basis") if global_block.get(key)]
        if parts:
            params["keyword"] = " ".join(parts)
    for key in ("keyword", "iprog", "blocks"):
        if key in global_block and key not in params:
            params[key] = deepcopy(global_block[key])


def document_from_canonical(
    payload: dict[str, Any], *, wizard_metadata: dict[str, Any] | None = None
) -> WorkflowDocument:
    """Make a canonical document preserving the supplied workflow extensions."""
    document = WorkflowDocument.from_mapping(payload)
    if wizard_metadata:
        return WorkflowDocument(
            payload=document.to_mapping(),
            version=document.version,
            source_layout=document.source_layout,
            wizard_metadata=deepcopy(wizard_metadata),
        )
    return document
