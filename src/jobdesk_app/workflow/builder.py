"""Form state ↔ YAML converter for the ConfFlow workflow wizard.

The converter is intentionally pure: ``form_state_to_yaml(state)`` returns the
serialized ``str``, and ``yaml_to_form_state(text)`` rebuilds the same flat dict
that the GUI operates on. We avoid maintaining a ``schema_snapshot.json`` —
the structure is derived directly from the dataclass definitions and the field
catalog in :mod:`jobdesk_app.workflow.schema`.

The GUI form has three layers of validation per the plan (§6):

1. **Form layer** (immediate feedback). ``validate_state`` raises a
   :class:`ValidationError` listing every offending field, so the GUI can light
   up red borders in one pass.
2. **Local Pydantic/Pydantic-style** — :func:`validate_state` builds the same
   mapping that ``WorkflowConfig.from_mapping`` consumes and delegates final
   structural checks to the runtime model.
3. **Agent dry-run** is performed by the daemon after submission; this module
   only handles layers 1 and 2.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from .config.models import (
    GlobalOptions,
    WorkflowConfig,
    _validate_memory,
)
from .schema import (
    FieldSpec,
    GLOBAL_FIELDS,
    STEP_FIELDS,
    get_field,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class BuilderError(ValueError):
    """Base class for builder errors."""


class ValidationError(BuilderError):
    """Raised when form state fails validation."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors) if errors else "validation failed")


# ---------------------------------------------------------------------------
# State containers
# ---------------------------------------------------------------------------


@dataclass
class StepState:
    type: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        params = dict(self.params)
        name = params.pop("name", self.type)
        return {"name": name, "type": self.type, "enabled": self.enabled, "params": params}


@dataclass
class FormState:
    """Top-level state held by both the GUI and the CLI.

    The structure mirrors the on-disk YAML: a global section plus an ordered
    list of steps. The form renderer exposes the same dict shape, so the GUI
    never needs to translate between an internal model and a wire model.
    """

    global_options: dict[str, Any]
    steps: list[StepState] = field(default_factory=list)

    # -- serialization helpers -------------------------------------------------

    def to_mapping(self) -> dict[str, Any]:
        global_block = {key: value for key, value in self.global_options.items() if _keep_global(key, value)}
        return {"global": global_block, "steps": [step.to_dict() for step in self.steps]}

    def to_yaml(self) -> str:
        text = yaml.safe_dump(
            self.to_mapping(),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        return text

    # -- cloning --------------------------------------------------------------

    def clone(self) -> FormState:
        return FormState(
            global_options=dict(self.global_options),
            steps=[StepState(type=s.type, enabled=s.enabled, params=dict(s.params)) for s in self.steps],
        )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


_GLOBAL_KEYS: frozenset[str] = frozenset(spec.key for spec in GLOBAL_FIELDS)


def _keep_global(key: str, value: Any) -> bool:
    if value in (None, "", (), []):
        return False
    return key in _GLOBAL_KEYS


def default_form_state() -> FormState:
    from .schema import default_global_state

    return FormState(global_options=default_global_state(), steps=[])


# ---------------------------------------------------------------------------
# Form-state parsing & validation
# ---------------------------------------------------------------------------


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return True
    return False


def _coerce_scalar(spec: FieldSpec, raw: Any) -> Any:
    """Convert a raw widget value to a YAML-ready value.

    Returns the sentinel ``_UNSET`` when the user-provided value is empty and a
    default exists, signalling the caller to drop the key entirely.
    """

    if spec.kind == "int":
        if _is_blank(raw):
            return spec.default
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValidationError([f"{spec.key}: expected integer, got {raw!r}"]) from exc
        if spec.min_value is not None and value < spec.min_value:
            raise ValidationError([f"{spec.key}: must be >= {spec.min_value}"])
        if spec.max_value is not None and value > spec.max_value:
            raise ValidationError([f"{spec.key}: must be <= {spec.max_value}"])
        return value
    if spec.kind == "float":
        if _is_blank(raw):
            return spec.default
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValidationError([f"{spec.key}: expected number, got {raw!r}"]) from exc
        if math.isnan(value) or math.isinf(value):
            raise ValidationError([f"{spec.key}: invalid number {raw!r}"])
        if spec.min_value is not None and value < spec.min_value:
            raise ValidationError([f"{spec.key}: must be >= {spec.min_value}"])
        if spec.max_value is not None and value > spec.max_value:
            raise ValidationError([f"{spec.key}: must be <= {spec.max_value}"])
        return value
    if spec.kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}
    if spec.kind == "str":
        if _is_blank(raw):
            return spec.default
        return str(raw).strip()
    if spec.kind == "choice":
        if _is_blank(raw):
            return spec.default
        text = str(raw).strip()
        if spec.choices and text not in spec.choices:
            raise ValidationError([f"{spec.key}: invalid choice {text!r}; allowed: {', '.join(spec.choices)}"])
        return text
    if spec.kind == "list_str":
        return _coerce_list(raw, lambda v: str(v).strip())
    if spec.kind == "list_int":
        return _coerce_list(raw, lambda v: int(str(v).strip()))
    if spec.kind == "list_pair":
        return _coerce_pair_list(raw)
    if spec.kind == "str_or_dict":
        if _is_blank(raw):
            return spec.default
        text = str(raw)
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                loaded = yaml.safe_load(stripped)
            except yaml.YAMLError as exc:
                raise ValidationError([f"{spec.key}: invalid YAML body: {exc}"]) from exc
            if isinstance(loaded, (dict, list)):
                return loaded
        return text
    raise ValidationError([f"{spec.key}: unknown field kind {spec.kind}"])


def _coerce_list(raw: Any, item_fn) -> list:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        # Accept Python literal/empty-bracket forms as well as user-friendly
        # comma-separated input. This keeps the round-trip stable when the
        # GUI default is the empty tuple ``()`` or list ``[]``.
        stripped = raw.strip()
        if stripped in {"", "()", "[]"}:
            return []
        text = stripped.replace("\n", ",").replace(";", ",")
        items = [chunk.strip() for chunk in text.split(",") if chunk.strip()]
    else:
        raise ValidationError([f"expected list, got {type(raw).__name__}"])
    coerced = []
    for chunk in items:
        try:
            coerced.append(item_fn(chunk))
        except (TypeError, ValueError) as exc:
            raise ValidationError([f"invalid list item {chunk!r}: {exc}"]) from exc
    return coerced


def _coerce_pair_list(raw: Any) -> list[tuple[int, int]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, str):
        chunks = raw.replace("\n", ",").replace(";", ",").split(",")
        items = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            for sub in chunk.split():
                items.append(sub)
    else:
        raise ValidationError([f"expected list of pairs, got {type(raw).__name__}"])
    pairs: list[tuple[int, int]] = []
    pending: list[int] = []
    for token in items:
        token = str(token).strip()
        if not token:
            continue
        try:
            pending.append(int(token))
        except ValueError as exc:
            raise ValidationError([f"invalid atom index {token!r}: {exc}"]) from exc
        if len(pending) == 2:
            pairs.append((pending[0], pending[1]))
            pending = []
    if pending:
        raise ValidationError(["bond pair list has dangling atom index, need pairs of two"])
    return pairs


def validate_state(state: FormState) -> None:
    """Run field-level validation on the entire form state.

    Raises :class:`ValidationError` if any field fails. Empty form (no steps,
    all defaults) is accepted.
    """

    errors: list[str] = []
    for spec in GLOBAL_FIELDS:
        try:
            _coerce_scalar(spec, state.global_options.get(spec.key, spec.default))
        except ValidationError as exc:
            errors.extend(exc.errors)

    for index, step in enumerate(state.steps, start=1):
        fields = STEP_FIELDS.get(step.type)
        if fields is None:
            errors.append(f"step {index}: unsupported type {step.type!r}")
            continue
        for spec in fields:
            try:
                _coerce_scalar(spec, step.params.get(spec.key, spec.default))
            except ValidationError as exc:
                errors.extend(f"step {index} ({step.type}) {e}" for e in exc.errors)

    if errors:
        raise ValidationError(errors)


# ---------------------------------------------------------------------------
# Mapping <-> form state
# ---------------------------------------------------------------------------


def build_mapping(state: FormState) -> dict[str, Any]:
    """Convert a :class:`FormState` into the raw mapping consumed by the runtime."""

    validate_state(state)
    global_block = _clean_global(state.global_options)
    steps: list[dict[str, Any]] = []
    for step in state.steps:
        params = _clean_step_params(step)
        params = _coerce_step_runtime(step.type, params)
        if not params:
            params = {}
        steps.append({"name": params.pop("name", step.type), "type": step.type, "enabled": step.enabled, "params": params})
    return {"global": global_block, "steps": steps}


def _clean_global(raw: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for spec in GLOBAL_FIELDS:
        if not spec.is_visible(dict(raw)):
            continue
        value = _coerce_scalar(spec, raw.get(spec.key, spec.default))
        if _is_blank(value) and _is_blank(spec.default):
            continue
        out[spec.key] = value
    return out


def _clean_step_params(step: StepState) -> dict[str, Any]:
    fields = STEP_FIELDS.get(step.type, ())
    out: dict[str, Any] = {}
    for spec in fields:
        if not spec.is_visible(step.params):
            continue
        if spec.key == "name":
            # Persisted on the step dict, not in params.
            value = step.params.get(spec.key, spec.default)
            if value not in (None, ""):
                out["name"] = str(value).strip() or spec.default
            continue
        value = _coerce_scalar(spec, step.params.get(spec.key, spec.default))
        if _is_blank(value) and _is_blank(spec.default):
            continue
        out[spec.key] = value
    return out


def _coerce_step_runtime(step_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """Match runtime field names.

    The form uses ``iprog``/``itask`` everywhere, but some fields live in
    nested groups on :class:`CalcStepParams`. We only need to rename the top-
    level keys; deeper coercion happens when the runtime model loads.
    """

    if step_type == "calc":
        # ``blocks`` may already be a dict or a multi-line ORCA block string.
        blocks = params.get("blocks")
        if isinstance(blocks, str) and blocks.strip():
            params["blocks"] = blocks
        else:
            params.pop("blocks", None)
    return params


# ---------------------------------------------------------------------------
# YAML <-> state
# ---------------------------------------------------------------------------


def form_state_to_yaml(state: FormState) -> str:
    """Serialize form state to YAML."""

    mapping = build_mapping(state)
    return yaml.safe_dump(mapping, default_flow_style=False, sort_keys=False, allow_unicode=True)


def yaml_to_form_state(text: str) -> FormState:
    """Parse a YAML config back into a :class:`FormState`.

    Unknown keys are preserved as-is so user edits survive round-trips, but
    missing-but-defaulted keys are filled in so the GUI has a stable shape.
    """

    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise BuilderError("workflow YAML root must be a mapping")
    global_raw = raw.get("global") or {}
    global_options = _global_state_from_mapping(global_raw)
    steps: list[StepState] = []
    for step_raw in raw.get("steps") or []:
        if not isinstance(step_raw, dict):
            continue
        step_type = str(step_raw.get("type", "")).strip().lower()
        if step_type == "gen":
            step_type = "confgen"
        if step_type == "task":
            step_type = "calc"
        if step_type not in STEP_FIELDS:
            raise BuilderError(f"unknown step type: {step_type!r}")
        params = dict(step_raw.get("params") or {})
        params.setdefault("name", step_raw.get("name", step_type))
        # Lift the most common global defaults onto the step form so it can be
        # edited locally without losing context.
        for lift_key in (
            "charge",
            "multiplicity",
            "iprog",
            "itask",
            "keyword",
            "cores_per_task",
            "total_memory",
            "rmsd_threshold",
            "energy_window",
            "energy_tolerance",
            "noH",
            "auto_clean",
        ):
            params.setdefault(lift_key, global_options.get(lift_key))
        steps.append(StepState(type=step_type, enabled=bool(step_raw.get("enabled", True)), params=params))
    return FormState(global_options=global_options, steps=steps)


def _global_state_from_mapping(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Fill the global form state from a YAML block, defaulting missing keys."""

    base = default_form_state().global_options
    for spec in GLOBAL_FIELDS:
        if spec.key in raw and raw[spec.key] is not None:
            base[spec.key] = raw[spec.key]
    # Memory is normalized through the runtime validator, but the form keeps
    # raw strings. We don't strip "GB" suffixes here — that happens at runtime.
    return base


# ---------------------------------------------------------------------------
# Runtime validation
# ---------------------------------------------------------------------------


def validate_runtime(state: FormState) -> WorkflowConfig:
    """Layer-2 validation: hand the mapping to :class:`WorkflowConfig`.

    Raises :class:`jobdesk_app.workflow.core.exceptions.ConfigurationError` on
    schema errors and :class:`ValidationError` on form-level ones.
    """

    validate_state(state)
    mapping = build_mapping(state)
    return WorkflowConfig.from_mapping(mapping)


def required_minimum() -> Iterable[str]:
    """Return keys that must always appear in the serialized output.

    Useful for the CLI's ``--dry-run`` mode.
    """

    return ("global", "steps")


__all__ = [
    "FormState",
    "StepState",
    "BuilderError",
    "ValidationError",
    "default_form_state",
    "form_state_to_yaml",
    "yaml_to_form_state",
    "validate_state",
    "validate_runtime",
    "build_mapping",
    "GLOBAL_FIELDS",
    "STEP_FIELDS",
]


# Re-export for callers who only know about this module.
_VALIDATE_MEMORY = _validate_memory  # noqa: F401 — kept for legacy imports.