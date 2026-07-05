"""Declarative field catalog for the ConfFlow workflow form.

This module powers Stage 4's YAML wizard. Instead of maintaining a hand-written
``schema_snapshot.json``, we derive the form structure directly from the
runtime model definitions in :mod:`jobdesk_app.workflow.config.models` and the
constants module. Field metadata is encoded once here, consumed by both:

* the PySide6 form renderer (``gui/pages/workflow_builder_page.py``)
* the CLI workflow builder (``cli/workflow_cmd.py``)

Each :class:`FieldSpec` entry describes one user-editable input. The catalog is
intentionally **minimal**: only fields the user actually needs to see in the
GUI are exposed. Defaults come from the same constants module so they stay in
sync with the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence

from .config.models import (
    CleanupOptions,
    ExecutionOptions,
    GlobalOptions,
    ResourceOptions,
    TSOptions,
)
from .shared.defaults import (
    DEFAULT_CHARGE,
    DEFAULT_CORES_PER_TASK,
    DEFAULT_DELETE_WORK_DIR,
    DEFAULT_ENABLE_DYNAMIC_RESOURCES,
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
    DEFAULT_WORKFLOW_AUTO_CLEAN,
)


FieldKind = Literal[
    "int",
    "float",
    "str",
    "bool",
    "choice",
    "list_str",
    "list_int",
    "list_pair",
    "str_or_dict",
]


@dataclass(frozen=True)
class FieldSpec:
    """Single user-editable form field.

    Attributes mirror what the GUI form renderer needs to instantiate widgets
    and the validator needs to convert raw widget values into YAML-ready
    scalars.
    """

    key: str
    kind: FieldKind
    label_key: str  # i18n key in gui.i18n.schema_hints
    default: Any = None
    choices: Sequence[Any] = ()
    placeholder: str = ""
    min_value: float | None = None
    max_value: float | None = None
    help_key: str | None = None  # optional i18n key for tooltip / description
    section: str = "general"  # logical group inside a step form
    visible_when: Callable[[dict[str, Any]], bool] | None = None

    def is_visible(self, form_state: dict[str, Any]) -> bool:
        if self.visible_when is None:
            return True
        try:
            return bool(self.visible_when(form_state))
        except Exception:
            return True


def _task_visible(form_state: dict[str, Any]) -> bool:
    """Bond-atoms and TS sub-options only show when itask == ts."""

    return form_state.get("itask") == "ts"


def _cleanup_visible(form_state: dict[str, Any]) -> bool:
    return form_state.get("auto_clean", DEFAULT_WORKFLOW_AUTO_CLEAN)


# ---------------------------------------------------------------------------
# Global options
# ---------------------------------------------------------------------------


GLOBAL_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("charge", "int", "field.charge", default=DEFAULT_CHARGE),
    FieldSpec("multiplicity", "int", "field.multiplicity", default=DEFAULT_MULTIPLICITY, min_value=1),
    FieldSpec(
        "cores_per_task",
        "int",
        "field.cores_per_task",
        default=DEFAULT_CORES_PER_TASK,
        min_value=1,
    ),
    FieldSpec(
        "total_memory",
        "str",
        "field.total_memory",
        default=DEFAULT_TOTAL_MEMORY,
        placeholder="8GB",
    ),
    FieldSpec(
        "max_parallel_jobs",
        "int",
        "field.max_parallel_jobs",
        default=DEFAULT_MAX_PARALLEL_JOBS,
        min_value=1,
    ),
    FieldSpec("iprog", "choice", "field.iprog", default="orca", choices=("orca", "g16")),
    FieldSpec(
        "itask",
        "choice",
        "field.itask",
        default="opt_freq",
        choices=("opt", "sp", "freq", "opt_freq", "ts"),
    ),
    FieldSpec("keyword", "str", "field.keyword", default="", placeholder="B3LYP def2-SVP"),
    FieldSpec("freeze", "list_int", "field.freeze", default=(), placeholder="0"),
    FieldSpec(
        "rmsd_threshold",
        "float",
        "field.rmsd_threshold",
        default=DEFAULT_RMSD_THRESHOLD,
        section="cleanup",
    ),
    FieldSpec("energy_window", "float", "field.energy_window", default=None, section="cleanup"),
    FieldSpec(
        "energy_tolerance",
        "float",
        "field.energy_tolerance",
        default=0.05,
        section="cleanup",
    ),
    FieldSpec("noH", "bool", "field.noH", default=False, section="cleanup"),
    FieldSpec(
        "auto_clean",
        "bool",
        "field.auto_clean",
        default=DEFAULT_WORKFLOW_AUTO_CLEAN,
        section="cleanup",
    ),
    FieldSpec(
        "ts_bond_atoms",
        "list_pair",
        "field.ts_bond_atoms",
        default=None,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "ts_rescue_scan",
        "bool",
        "field.ts_rescue_scan",
        default=DEFAULT_TS_RESCUE_SCAN,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "scan_coarse_step",
        "float",
        "field.scan_coarse_step",
        default=DEFAULT_SCAN_COARSE_STEP,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "scan_fine_step",
        "float",
        "field.scan_fine_step",
        default=DEFAULT_SCAN_FINE_STEP,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "scan_uphill_limit",
        "int",
        "field.scan_uphill_limit",
        default=DEFAULT_SCAN_UPHILL_LIMIT,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "sandbox_root",
        "str",
        "field.sandbox_root",
        default="",
        placeholder="/scratch/confflow",
    ),
    FieldSpec("allowed_executables", "list_str", "field.allowed_executables", default=()),
    FieldSpec(
        "enable_dynamic_resources",
        "bool",
        "field.enable_dynamic_resources",
        default=DEFAULT_ENABLE_DYNAMIC_RESOURCES,
    ),
    FieldSpec(
        "resume_from_backups",
        "bool",
        "field.resume_from_backups",
        default=DEFAULT_RESUME_FROM_BACKUPS,
    ),
    FieldSpec(
        "delete_work_dir",
        "bool",
        "field.delete_work_dir",
        default=DEFAULT_DELETE_WORK_DIR,
    ),
)


# ---------------------------------------------------------------------------
# Calc step fields
# ---------------------------------------------------------------------------


CALC_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("name", "str", "field.step_name", default="calc"),
    FieldSpec("iprog", "choice", "field.iprog", default="g16", choices=("g16", "orca")),
    FieldSpec(
        "itask",
        "choice",
        "field.itask",
        default="opt_freq",
        choices=("opt", "sp", "freq", "opt_freq", "ts"),
    ),
    FieldSpec(
        "keyword",
        "str",
        "field.keyword",
        default="",
        placeholder="B3LYP/6-31G* opt freq",
    ),
    FieldSpec(
        "gaussian_path",
        "str",
        "field.gaussian_path",
        default="g16",
        visible_when=lambda s: s.get("iprog") == "g16",
    ),
    FieldSpec(
        "orca_path",
        "str",
        "field.orca_path",
        default="orca",
        visible_when=lambda s: s.get("iprog") == "orca",
    ),
    FieldSpec(
        "cores_per_task",
        "int",
        "field.cores_per_task",
        default=DEFAULT_CORES_PER_TASK,
        min_value=1,
    ),
    FieldSpec(
        "total_memory",
        "str",
        "field.total_memory",
        default=DEFAULT_TOTAL_MEMORY,
        placeholder="8GB",
    ),
    FieldSpec("charge", "int", "field.charge", default=DEFAULT_CHARGE),
    FieldSpec("multiplicity", "int", "field.multiplicity", default=DEFAULT_MULTIPLICITY, min_value=1),
    FieldSpec("freeze", "list_int", "field.freeze", default=(), placeholder="0"),
    FieldSpec(
        "blocks",
        "str_or_dict",
        "field.blocks",
        default=None,
        placeholder='%pal nprocs 8 end\n%maxcore 4000',
        visible_when=lambda s: s.get("iprog") == "orca",
    ),
    FieldSpec(
        "orca_maxcore",
        "int",
        "field.orca_maxcore",
        default=None,
        placeholder="4000",
        visible_when=lambda s: s.get("iprog") == "orca",
    ),
    FieldSpec(
        "gaussian_modredundant",
        "str",
        "field.gaussian_modredundant",
        default="",
        placeholder="B 1 2 S 10 0.1",
        visible_when=lambda s: s.get("iprog") == "g16",
    ),
    FieldSpec(
        "gaussian_link0",
        "str",
        "field.gaussian_link0",
        default="",
        placeholder="%nproc=8\n%mem=8GB",
        visible_when=lambda s: s.get("iprog") == "g16",
    ),
    FieldSpec("auto_clean", "bool", "field.auto_clean", default=DEFAULT_WORKFLOW_AUTO_CLEAN),
    FieldSpec(
        "rmsd_threshold",
        "float",
        "field.rmsd_threshold",
        default=DEFAULT_RMSD_THRESHOLD,
        section="cleanup",
    ),
    FieldSpec(
        "energy_window",
        "float",
        "field.energy_window",
        default=None,
        section="cleanup",
    ),
    FieldSpec(
        "energy_tolerance",
        "float",
        "field.energy_tolerance",
        default=0.05,
        section="cleanup",
    ),
    FieldSpec("noH", "bool", "field.noH", default=False, section="cleanup"),
    FieldSpec(
        "dedup_only",
        "bool",
        "field.dedup_only",
        default=False,
        section="cleanup",
        visible_when=_cleanup_visible,
    ),
    FieldSpec(
        "keep_all_topos",
        "bool",
        "field.keep_all_topos",
        default=False,
        section="cleanup",
        visible_when=_cleanup_visible,
    ),
    FieldSpec(
        "imag",
        "int",
        "field.imag",
        default=None,
        min_value=0,
        section="cleanup",
    ),
    FieldSpec(
        "max_conformers",
        "int",
        "field.max_conformers",
        default=None,
        min_value=1,
        section="cleanup",
    ),
    FieldSpec(
        "ts_bond_atoms",
        "list_pair",
        "field.ts_bond_atoms",
        default=None,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "ts_rescue_scan",
        "bool",
        "field.ts_rescue_scan",
        default=DEFAULT_TS_RESCUE_SCAN,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "ts_bond_drift_threshold",
        "float",
        "field.ts_bond_drift_threshold",
        default=DEFAULT_TS_BOND_DRIFT_THRESHOLD,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "ts_rmsd_threshold",
        "float",
        "field.ts_rmsd_threshold",
        default=DEFAULT_TS_RMSD_THRESHOLD,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "scan_coarse_step",
        "float",
        "field.scan_coarse_step",
        default=DEFAULT_SCAN_COARSE_STEP,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "scan_fine_step",
        "float",
        "field.scan_fine_step",
        default=DEFAULT_SCAN_FINE_STEP,
        section="ts",
        visible_when=_task_visible,
    ),
    FieldSpec(
        "scan_uphill_limit",
        "int",
        "field.scan_uphill_limit",
        default=DEFAULT_SCAN_UPHILL_LIMIT,
        section="ts",
        visible_when=_task_visible,
    ),
)


# ---------------------------------------------------------------------------
# Confgen step fields
# ---------------------------------------------------------------------------


CONFGEN_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("name", "str", "field.step_name", default="confgen"),
    FieldSpec(
        "engine",
        "choice",
        "field.engine",
        default="rdkit",
        choices=("rdkit", "rdkit-mmff", "rdkit-uff"),
    ),
    FieldSpec(
        "angle_step",
        "float",
        "field.angle_step",
        default=30.0,
        min_value=1.0,
        max_value=180.0,
    ),
    FieldSpec("chain_steps", "list_int", "field.chain_steps", default=()),
    FieldSpec("bond_pairs", "list_pair", "field.bond_pairs", default=()),
    FieldSpec(
        "rmsd_threshold",
        "float",
        "field.rmsd_threshold",
        default=DEFAULT_RMSD_THRESHOLD,
    ),
    FieldSpec("noH", "bool", "field.noH", default=False),
    FieldSpec("energy_window", "float", "field.energy_window", default=None),
    FieldSpec("max_conformers", "int", "field.max_conformers", default=None, min_value=1),
)


# ---------------------------------------------------------------------------
# Field lookup
# ---------------------------------------------------------------------------


STEP_FIELDS: dict[str, tuple[FieldSpec, ...]] = {
    "calc": CALC_FIELDS,
    "confgen": CONFGEN_FIELDS,
}


@dataclass(frozen=True)
class StepKindSpec:
    """User-facing metadata for a step type."""

    name: str
    label_key: str
    fields: tuple[FieldSpec, ...]


STEP_KINDS: tuple[StepKindSpec, ...] = (
    StepKindSpec(name="calc", label_key="step.calc", fields=CALC_FIELDS),
    StepKindSpec(name="confgen", label_key="step.confgen", fields=CONFGEN_FIELDS),
)


def get_step_fields(step_type: str) -> tuple[FieldSpec, ...]:
    return STEP_FIELDS.get(step_type, ())


def get_field(field_key: str, fields: tuple[FieldSpec, ...]) -> FieldSpec | None:
    for spec in fields:
        if spec.key == field_key:
            return spec
    return None


def default_global_state() -> dict[str, Any]:
    """Provide the GUI form's initial state from dataclass defaults."""

    options = GlobalOptions()
    return _global_state_from(options)


def _global_state_from(options: GlobalOptions) -> dict[str, Any]:
    return {
        "charge": options.charge,
        "multiplicity": options.multiplicity,
        "cores_per_task": options.cores_per_task,
        "total_memory": options.total_memory,
        "max_parallel_jobs": options.max_parallel_jobs,
        "iprog": options.iprog,
        "itask": options.itask,
        "keyword": options.keyword or "",
        "freeze": list(options.freeze),
        "rmsd_threshold": options.rmsd_threshold,
        "energy_window": options.energy_window,
        "energy_tolerance": options.energy_tolerance,
        "noH": options.noH,
        "auto_clean": options.auto_clean,
        "ts_bond_atoms": list(options.ts_bond_atoms) if options.ts_bond_atoms else None,
        "ts_rescue_scan": options.ts_rescue_scan,
        "scan_coarse_step": options.scan_coarse_step,
        "scan_fine_step": options.scan_fine_step,
        "scan_uphill_limit": options.scan_uphill_limit,
        "sandbox_root": options.sandbox_root or "",
        "allowed_executables": list(options.allowed_executables),
        "enable_dynamic_resources": options.enable_dynamic_resources,
        "resume_from_backups": options.resume_from_backups,
        "delete_work_dir": options.delete_work_dir,
    }


def default_step_state(step_type: str) -> dict[str, Any]:
    if step_type == "calc":
        # Build a sane default state for the calc form without going through
        # ``CalcStepParams.from_params`` (which raises on missing keyword).
        # The wizard user is expected to fill the keyword in; ``validate_state``
        # accepts the empty default and the runtime catches it later.
        return {
            "name": "calc",
            "iprog": "g16",
            "itask": "opt_freq",
            "keyword": "",
            "gaussian_path": "g16",
            "orca_path": "orca",
            "cores_per_task": DEFAULT_CORES_PER_TASK,
            "total_memory": DEFAULT_TOTAL_MEMORY,
            "charge": DEFAULT_CHARGE,
            "multiplicity": DEFAULT_MULTIPLICITY,
            "freeze": [],
            "blocks": None,
            "orca_maxcore": None,
            "gaussian_modredundant": "",
            "gaussian_link0": "",
            "auto_clean": DEFAULT_WORKFLOW_AUTO_CLEAN,
            "rmsd_threshold": DEFAULT_RMSD_THRESHOLD,
            "energy_window": None,
            "energy_tolerance": 0.05,
            "noH": False,
            "dedup_only": False,
            "keep_all_topos": False,
            "imag": None,
            "max_conformers": None,
            "ts_bond_atoms": None,
            "ts_rescue_scan": DEFAULT_TS_RESCUE_SCAN,
            "ts_bond_drift_threshold": DEFAULT_TS_BOND_DRIFT_THRESHOLD,
            "ts_rmsd_threshold": DEFAULT_TS_RMSD_THRESHOLD,
            "scan_coarse_step": DEFAULT_SCAN_COARSE_STEP,
            "scan_fine_step": DEFAULT_SCAN_FINE_STEP,
            "scan_uphill_limit": DEFAULT_SCAN_UPHILL_LIMIT,
        }
    state: dict[str, Any] = {}
    for spec in STEP_FIELDS.get(step_type, ()):
        state[spec.key] = spec.default
    return state


def _calc_state_from_params(params) -> dict[str, Any]:
    """Best-effort projection from a parsed :class:`CalcStepParams` instance
    back to form-state keys. Retained for legacy callers; the wizard's
    :func:`default_step_state` no longer goes through this path because
    ``CalcStepParams.from_params`` raises on missing keyword, which would
    make the empty form unstartable.
    """
    return {
        "name": "calc",
        "iprog": params.program,
        "itask": params.task,
        "keyword": params.keyword,
        "gaussian_path": params.gaussian_path,
        "orca_path": params.orca_path,
        "cores_per_task": params.resources.cores_per_task,
        "total_memory": params.resources.total_memory,
        "charge": params.charge,
        "multiplicity": params.multiplicity,
        "freeze": list(params.freeze),
        "blocks": params.blocks,
        "orca_maxcore": params.orca_maxcore,
        "gaussian_modredundant": params.gaussian_modredundant or "",
        "gaussian_link0": params.gaussian_link0 or "",
        "auto_clean": params.cleanup.enabled,
        "rmsd_threshold": params.cleanup.rmsd_threshold,
        "energy_window": params.cleanup.energy_window,
        "energy_tolerance": params.cleanup.energy_tolerance,
        "noH": params.cleanup.no_h,
        "dedup_only": params.cleanup.dedup_only,
        "keep_all_topos": params.cleanup.keep_all_topos,
        "imag": params.cleanup.imag,
        "max_conformers": params.cleanup.max_conformers,
        "ts_bond_atoms": list(params.ts.bond_atoms) if params.ts.bond_atoms else None,
        "ts_rescue_scan": params.ts.rescue_scan,
        "ts_bond_drift_threshold": params.ts.bond_drift_threshold,
        "ts_rmsd_threshold": params.ts.rmsd_threshold,
        "scan_coarse_step": params.ts.scan_coarse_step,
        "scan_fine_step": params.ts.scan_fine_step,
        "scan_uphill_limit": params.ts.scan_uphill_limit,
    }


# ResourceOptions / CleanupOptions / TSOptions are exported here so callers can
# re-use them when building runtime dicts. The fields above reference the same
# constants as these dataclasses, so they cannot drift.
__all__ = [
    "FieldSpec",
    "FieldKind",
    "GLOBAL_FIELDS",
    "STEP_FIELDS",
    "STEP_KINDS",
    "CALC_FIELDS",
    "CONFGEN_FIELDS",
    "default_global_state",
    "default_step_state",
    "get_step_fields",
    "get_field",
    "ResourceOptions",
    "CleanupOptions",
    "TSOptions",
    "ExecutionOptions",
]