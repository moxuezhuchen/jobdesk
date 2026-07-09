#!/usr/bin/env python3

"""ConfFlow Pydantic data models.

Complementary to the TypedDict definitions in ``core.types``:

- TypedDict: lightweight static type annotations (no runtime overhead).
- Pydantic models: data containers requiring runtime validation and serialisation.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_ENABLE_DYNAMIC_RESOURCES,
    DEFAULT_FORCE_CONSISTENCY,
    DEFAULT_MAX_PARALLEL_JOBS,
    DEFAULT_MULTIPLICITY,
    DEFAULT_RESUME_FROM_BACKUPS,
    DEFAULT_RMSD_THRESHOLD,
    DEFAULT_SCAN_COARSE_STEP,
    DEFAULT_SCAN_FINE_STEP,
    DEFAULT_SCAN_UPHILL_LIMIT,
    DEFAULT_STOP_CHECK_INTERVAL_SECONDS,
    DEFAULT_TOTAL_MEMORY,
    DEFAULT_TS_BOND_DRIFT_THRESHOLD,
    DEFAULT_TS_RESCUE_SCAN,
    DEFAULT_TS_RMSD_THRESHOLD,
)

__all__ = [
    "TaskContext",
    "GlobalConfigModel",
    "CalcConfigModel",
    "StepConfig",
]


class StepConfig(BaseModel):
    """Per-step workflow configuration used by the DAG dispatcher (Phase 3).

    The schema is intentionally permissive (``extra="allow"``) so the engine
    can read step dicts that contain extra keys not modelled here (e.g.
    ``kind``, ``enabled``, ``params``, runtime-only metadata).

    The two new fields are:

    - ``inputs``: names of upstream step names that this step depends on.
      Empty list means "root step" (consumes the global workflow input).
    - ``outputs``: paths this step will write. Optional; used by the
      dispatcher for documentation / cross-checks only.

    Backward compatibility: existing linear workflows do not set ``inputs``
    on any step. The engine detects this case and synthesises an implicit
    linear chain ``step[i-1].name -> step[i].name`` so legacy YAML
    continues to work unchanged.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)

    @field_validator("inputs", "outputs", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: Any) -> list[str]:
        """Accept a single string, comma-separated string, or list of strings."""
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.replace(",", " ").split() if s.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x) for x in v]
        return []


class TaskContext(BaseModel):
    """Context information for a computation task."""

    model_config = ConfigDict(extra="allow")

    job_name: str
    work_dir: str
    coords: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class GlobalConfigModel(BaseModel):
    """Pydantic model for global configuration validation.

    Provides runtime type coercion and validation for all global parameters.
    Used as a validation layer alongside the existing ConfigSchema.
    """

    model_config = ConfigDict(extra="allow")

    # Program paths
    gaussian_path: str = ""
    orca_path: str = ""

    # Resource configuration
    cores_per_task: int = DEFAULT_CORES_PER_TASK
    total_memory: str = DEFAULT_TOTAL_MEMORY
    max_parallel_jobs: int = DEFAULT_MAX_PARALLEL_JOBS

    # Molecular properties
    charge: int = DEFAULT_CHARGE
    multiplicity: int = DEFAULT_MULTIPLICITY

    # Refine parameters
    rmsd_threshold: float = DEFAULT_RMSD_THRESHOLD
    energy_window: float | None = None
    energy_tolerance: float = 0.05
    noH: bool = False

    # Freeze & TS
    freeze: list[int] = Field(default_factory=list)
    ts_bond_atoms: list[int] | None = None

    # TS rescue
    ts_rescue_scan: bool = DEFAULT_TS_RESCUE_SCAN
    scan_coarse_step: float = DEFAULT_SCAN_COARSE_STEP
    scan_fine_step: float = DEFAULT_SCAN_FINE_STEP
    scan_uphill_limit: int = DEFAULT_SCAN_UPHILL_LIMIT
    ts_bond_drift_threshold: float = DEFAULT_TS_BOND_DRIFT_THRESHOLD
    ts_rmsd_threshold: float = DEFAULT_TS_RMSD_THRESHOLD

    # Workflow control
    enable_dynamic_resources: bool = DEFAULT_ENABLE_DYNAMIC_RESOURCES
    resume_from_backups: bool = DEFAULT_RESUME_FROM_BACKUPS
    stop_check_interval_seconds: int = DEFAULT_STOP_CHECK_INTERVAL_SECONDS
    force_consistency: bool = DEFAULT_FORCE_CONSISTENCY

    @field_validator("cores_per_task")
    @classmethod
    def validate_cores(cls, v: int) -> int:
        """Ensure cores_per_task >= 1."""
        if v < 1:
            raise ValueError(f"cores_per_task must be >= 1, got {v}")
        return v

    @field_validator("max_parallel_jobs")
    @classmethod
    def validate_max_jobs(cls, v: int) -> int:
        """Ensure max_parallel_jobs >= 1."""
        if v < 1:
            raise ValueError(f"max_parallel_jobs must be >= 1, got {v}")
        return v

    @field_validator("multiplicity")
    @classmethod
    def validate_multiplicity(cls, v: int) -> int:
        """Ensure multiplicity >= 1."""
        if v < 1:
            raise ValueError(f"multiplicity must be >= 1, got {v}")
        return v

    @field_validator("total_memory")
    @classmethod
    def validate_memory_format(cls, v: str) -> str:
        """Validate memory format like '4GB' or '500MB'."""
        v_upper = str(v).strip().upper()
        if not re.match(r"^\d+(?:\.\d+)?\s*(?:GB|MB|KB|B)$", v_upper):
            raise ValueError(f"total_memory format error: '{v}', expected '4GB' or '500MB'")
        return v

    @field_validator("freeze", mode="before")
    @classmethod
    def coerce_freeze(cls, v: Any) -> list[int]:
        """Accept list or comma-separated string for freeze indices."""
        if v is None:
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.replace(",", " ").split() if x.strip()]
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v]
        return []

    @field_validator("ts_bond_atoms", mode="before")
    @classmethod
    def coerce_ts_bond_atoms(cls, v: Any) -> list[int] | None:
        """Accept list or comma/space-separated string for ts_bond_atoms."""
        if v is None:
            return None
        if isinstance(v, str):
            parts = v.replace(",", " ").split()
            if len(parts) == 2:
                return [int(parts[0]), int(parts[1])]
            return None
        if isinstance(v, (list, tuple)):
            if len(v) == 2:
                return [int(v[0]), int(v[1])]
            return None
        return None


class CalcConfigModel(BaseModel):
    """Pydantic model for calc step configuration validation.

    Validates required fields and program/task type constraints.
    """

    model_config = ConfigDict(extra="allow")

    iprog: str | int
    itask: str | int
    keyword: str

    @field_validator("keyword")
    @classmethod
    def validate_keyword(cls, v: str) -> str:
        """Ensure keyword is non-empty."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("keyword must be a non-empty string")
        return v

    @field_validator("iprog")
    @classmethod
    def validate_iprog(cls, v: str | int) -> str | int:
        """Validate program identifier."""
        valid = {"gaussian", "g16", "orca", "1", "2", 1, 2}
        if v not in valid:
            raise ValueError(f"invalid iprog: {v}, valid: gaussian, g16, orca, 1, 2")
        return v

    @field_validator("itask")
    @classmethod
    def validate_itask(cls, v: str | int) -> str | int:
        """Validate task type."""
        valid = {"opt", "sp", "freq", "opt_freq", "ts", "0", "1", "2", "3", "4", 0, 1, 2, 3, 4}
        if v not in valid:
            raise ValueError(f"invalid itask: {v}, valid: opt, sp, freq, opt_freq, ts, 0-4")
        return v


class StepConfig(BaseModel):
    """Pydantic model for a workflow step (Phase 3 - DAG support).

    Each step in a workflow YAML is loaded into a ``StepConfig``. The two
    fields that turn the workflow from a linear chain into a DAG are
    ``inputs`` (the list of upstream step names whose outputs are fed into
    this step) and ``outputs`` (the list of paths the step is expected to
    write; the engine uses the first existing one as the successor's
    input).

    Backward compatibility: steps with empty ``inputs`` fall back to the
    legacy "previous step's output becomes this step's input" behaviour.
    Legacy workflows that never declare ``inputs`` anywhere therefore
    continue to run as a strict linear chain.
    """

    model_config = ConfigDict(extra="allow")

    name: str
    type: str
    enabled: bool = True
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Ensure name is non-empty."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("step name must be a non-empty string")
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Normalize step type to lowercase string."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("step type must be a non-empty string")
        return str(v).strip().lower()

    @field_validator("inputs", mode="before")
    @classmethod
    def coerce_inputs(cls, v: Any) -> list[str]:
        """Coerce inputs into a list of strings.

        Accepts a single string, a list/tuple of strings, or None. The
        single-string form is the common YAML shorthand for "one
        predecessor".
        """
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, (list, tuple)):
            out: list[str] = []
            for item in v:
                if item is None:
                    continue
                s = str(item).strip()
                if s:
                    out.append(s)
            return out
        return [str(v)]

    @field_validator("outputs", mode="before")
    @classmethod
    def coerce_outputs(cls, v: Any) -> list[str]:
        """Coerce outputs into a list of strings (forward-compat).

        The engine currently does not require this field, but having it
        typed here lets a future release use it to drive IO without a
        second YAML pass.
        """
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        if isinstance(v, (list, tuple)):
            out2: list[str] = []
            for item in v:
                if item is None:
                    continue
                s = str(item).strip()
                if s:
                    out2.append(s)
            return out2
        return [str(v)]
