"""Pydantic wrapper around the optional ConfFlow workflow schema.

ConfFlow exposes ``core.models.GlobalConfigModel`` and ``CalcConfigModel`` as
its top-level YAML schema. We import them lazily so that:

* ``import jobdesk_app.core.workflow_spec`` always succeeds (even when the
  ``chem`` extra is not installed — GUI still loads).
* Only methods that actually validate YAML (``from_yaml``, ``to_yaml``,
  ``dry_run``) need the package.

The wizard and ``program_adapters.ConfFlowAdapter`` use this module to convert
between form input and the on-disk ``workflow.yaml`` that the remote
``confflow`` process consumes.
"""
from __future__ import annotations

import functools
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from jobdesk_app.confflow.confflow.core.models import CalcConfigModel, GlobalConfigModel
    _CONFFLOW_AVAILABLE = True
except ImportError:  # vendored confflow not present (developer forgot to subtree pull)
    CalcConfigModel = None  # type: ignore[misc,assignment]
    GlobalConfigModel = None  # type: ignore[misc,assignment]
    _CONFFLOW_AVAILABLE = False


@dataclass(frozen=True)
class DryRunReport:
    """Summary returned by :meth:`WorkflowSpec.dry_run`.

    ConfFlow's ``--dry-run`` prints the resolved workflow to stdout. We capture
    the first ~200 lines plus a boolean ``ok`` flag so the wizard can show a
    short preview without parsing ConfFlow's full output format.
    """

    ok: bool
    preview_lines: tuple[str, ...]
    error: str = ""


def _strip_bang(s: str) -> str:
    """Remove a single leading ``!`` from a user-typed keyword line.

    ConfFlow's ORCA policy template (``BUILTIN_TEMPLATES['orca']``) already
    emits ``! {keyword}``.  If a user pastes a raw ORCA keyword line that
    starts with ``!``, we get ``!! method basis`` and ORCA rejects it.
    Sanitize once at the wizard boundary so downstream templates are
    authoritative.
    """
    stripped = s.strip()
    while stripped.startswith("!"):
        stripped = stripped[1:].lstrip()
    return stripped


def assemble_orca_keyword(method: str, basis: str, extra: str = "") -> str:
    """Assemble an ORCA keyword line from wizard form fields.

    ConfFlow's ORCA policy expects a single ``keyword`` string of the form
    ``"<method> <basis> [extra]"``.  The wizard collects ``method`` and
    ``basis`` as separate text fields so the form stays compact; here we
    splice them together.  The leading ``!`` is *omitted* — the policy
    template adds it.  Empty components are dropped.
    """
    parts = [_strip_bang(p) for p in (method, basis) if p and p.strip()]
    extra = _strip_bang(extra)
    if extra:
        parts.append(extra)
    return " ".join(parts)


class ConfFlowUnavailableError(RuntimeError):
    """Raised when a workflow_spec method needs the confflow package but it
    is not installed (the ``chem`` extra was skipped).
    """


def require_confflow() -> None:
    if not _CONFFLOW_AVAILABLE:
        raise ConfFlowUnavailableError(
            "confflow package is not installed. "
            "Reinstall with `pip install -e .[chem]` on the same Python that "
            "runs JobDesk, and on the Linux server as well."
        )


def _validate_confflow_semantics(
    payload: dict[str, Any], *, allow_legacy_confgen_placeholder: bool = False
) -> None:
    """Apply ConfFlow's step validation without probing remote executables.

    A workflow can legitimately contain an executable path that only exists
    on the selected remote server.  The editor must validate task types,
    programs and conformer settings locally, but must not reject such a
    remote path merely because it is absent on this Windows machine.
    """
    from jobdesk_app.confflow.confflow.config.schema import validate_yaml_config

    validation_payload = deepcopy(payload)
    global_config = validation_payload.get("global")
    if isinstance(global_config, dict):
        global_config.pop("gaussian_path", None)
        global_config.pop("orca_path", None)
    errors = validate_yaml_config(validation_payload)
    # A pre-schema token list could contain a bare ``confgen`` planning
    # placeholder.  Keep only that *source format* readable.  Canonical
    # ``global``/``steps`` workflow YAML is user-authored and must retain the
    # ConfFlow requirement for a real torsion chain.
    if allow_legacy_confgen_placeholder:
        errors = [
            error for error in errors
            if "confgen step requires 'chains'" not in error
        ]
    if errors:
        raise ValueError("Invalid workflow YAML: " + "; ".join(errors))


@functools.lru_cache(maxsize=1)
def _kb_to_mb(n: float) -> int:
    return max(1, int(n / 1024))


def _parse_mem_mb_local(value: Any) -> int:
    """Parse a memory value into MB.

    ``GlobalConfigModel`` accepts ``"4GB"``/``"500MB"`` strings, but our
    legacy form / YAML editor stored an integer in MB.  This helper
    handles both shapes so the YAML editor can pre-populate the
    ``memory_mb`` field cleanly.
    """
    if value is None or value == "":
        return 1024
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().upper()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(GB|MB|KB|B)?$", s)
    if not m:
        return 1024
    n = float(m.group(1))
    unit = m.group(2) or "MB"
    if unit == "GB":
        return int(n * 1024)
    if unit == "KB":
        return _kb_to_mb(n)
    if unit == "B":
        return 1
    return int(n)


def _split_keyword_into_form(
    keyword: str | None, *, has_method: bool, has_basis: bool
) -> tuple[str, str, str]:
    """Best-effort: split a ``keyword`` string into ``(method, basis, extra)``.

    Used by :meth:`WorkflowSpec.to_form` so the YAML editor can be
    round-tripped after the wizard splits things for editing. The first
    whitespace-delimited token is treated as the method; if a subsequent
    token looks like a Gaussian/ORCA basis set (starts with an alphanumeric
    or letter cluster not a flag keyword), we use it as ``basis``. The rest
    is returned as ``extra_keyword``.

    This is heuristic — ``keyword`` is a free-form program line so we only
    need an "approximate" split for the editor preview.
    """
    if not keyword:
        return ("", "", "")
    parts = keyword.strip().split()
    if not parts:
        return ("", "", "")
    if not has_method and not has_basis:
        # Pure keyword line: all goes into method slot so the YAML
        # editor preview shows it.
        return (parts[0], "", " ".join(parts[1:]))
    method = parts[0] if has_method else ""
    basis = ""
    extras: list[str] = []
    started_basis = False
    for tok in parts[1:]:
        if not started_basis and has_basis and basis == "" and not tok.startswith("(") and "=" not in tok:
            basis = tok
            started_basis = True
        else:
            extras.append(tok)
    return (method, basis, " ".join(extras))


def _format_mem_mb(memory_mb: int) -> str:
    """Render an int MB count as confflow's ``"4GB"``/``"500MB"`` string.

    Picks GB when divisible / round, else MB. The wizard supplies MB
    because that's the form-friendly unit; confflow's
    ``GlobalConfigModel`` insists on a unit-bearing string.
    """
    if memory_mb is None:
        return "4GB"
    try:
        n = int(memory_mb)
    except (TypeError, ValueError):
        return "4GB"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024}GB"
    if n >= 1024:
        # Round to one decimal GB for clarity.
        return f"{n / 1024:.1f}GB"
    return f"{n}MB"


def _iprog_token(program: str) -> str:
    """Map wizard ``program`` (``"gaussian"``/``"orca"``) → confflow ``iprog``.

    confflow's policies accept ``"gaussian"``, ``"g16"``, ``"g09"``,
    ``"orca"`` interchangeably. We pick the explicit full name so the
    YAML reads cleanly.
    """
    p = str(program or "").strip().lower()
    if p in {"gaussian", "g16", "g09", "g03"}:
        return "gaussian"
    if p == "orca":
        return "orca"
    return p or "gaussian"


def _itask_token(value: str) -> str:
    """Normalise confflow's ``itask`` to a wizard token.

    The wizard uses ``opt_freq``, ``opt``, ``sp``, ``ts``, ``freq``
    (matching confflow's high-level names).  Anything else passes
    through verbatim.
    """
    s = str(value or "").strip().lower()
    if s in {"opt_freq", "optfreq"}:
        return "opt_freq"
    return s


_STEP_TOKEN_TO_TYPE = {
    "confgen": (
        "confgen",
        {"chains": ["1-2-3-4"], "angle_step": 120, "bond_multiplier": 1.15},
    ),
    "preopt": ("calc", {"itask": "opt"}),
    "opt": ("calc", {"itask": "opt"}),
    "opt_freq": ("calc", {"itask": "opt_freq"}),
    "sp": ("calc", {"itask": "sp"}),
    "freq": ("calc", {"itask": "freq"}),
    "ts": ("calc", {"itask": "ts"}),
    "refine": ("calc", {"itask": "sp"}),
}


def _token_to_step(token: str, *, idx: int | None = None) -> dict[str, Any]:
    """Convert a wizard token (``opt_freq``/``sp``/...) into a step dict.

    The output is ``{name, type, params}``. ``iprog``/``keyword`` are
    injected later by :meth:`WorkflowSpec.from_form` because they are
    the same across steps.
    """
    tok = str(token or "").strip().lower()
    if not tok:
        return {
            "name": f"step_{(idx or 1):02d}",
            "type": "calc",
            "params": {"itask": "sp"},
        }
    step_type, base_params = _STEP_TOKEN_TO_TYPE.get(tok, ("calc", {"itask": tok}))
    # Name: prefer the token; fall back to a deterministic step_xx.
    name = tok if tok in {"confgen", "preopt", "opt_freq", "refine"} else tok
    return {
        "name": name,
        "type": step_type,
        "params": dict(base_params),
    }


def _normalise_yaml_to_schema(data: dict[str, Any]) -> dict[str, Any]:
    """Lift legacy flat / ``calc:`` / token-list shapes into the
    canonical ``{global, steps}`` confflow schema.

    Three legacy shapes are recognised:

    1. **Already canonical** — ``{"global": {...}, "steps": [...]}``
       passes through unchanged.
    2. **v5 flat** — ``{work_dir, program, keyword, nproc, memory_mb, steps: [...]}``
       is folded into ``global`` + ``steps``.
    3. **v1..4 nested** — ``{work_dir, calc: {program, method, basis, ...,
       keyword, steps: [...]}}`` is similarly folded.

    After normalising, ``steps`` always follows the
    ``{name, type, params}`` confflow contract.  Bare string tokens
    (``"opt_freq"``) are converted; already-dict steps pass through.
    """
    if not isinstance(data, dict):
        return {"global": {}, "steps": []}
    has_global = "global" in data
    has_steps = "steps" in data
    if has_global and has_steps:
        # Fully canonical — pass through.
        global_dict = dict(data.get("global") or {})
        steps_list = _normalise_steps_list(data.get("steps") or [])
        _lift_legacy_resource_keys(global_dict)
        return {"global": global_dict, "steps": steps_list}
    if has_global:
        # Has ``global`` but ``steps`` may be missing or a list of
        # bare tokens.
        global_dict = dict(data.get("global") or {})
        steps_list = _normalise_steps_list(data.get("steps") or [])
        _lift_legacy_resource_keys(global_dict)
        return {"global": global_dict, "steps": steps_list}
    if has_steps:
        # v5-flat shape: ``{work_dir, program, charge, ..., steps: [...]}``
        # — everything except ``steps`` is treated as global, and
        # ``keyword``/``iprog`` are attached to the first calc step.
        global_dict = {k: v for k, v in data.items() if k != "steps"}
        _lift_legacy_resource_keys(global_dict)
        steps_list = _normalise_steps_list(data.get("steps") or [])
        if steps_list:
            first_calc = next(
                (s for s in steps_list
                 if isinstance(s, dict) and s.get("type") == "calc"),
                None,
            )
            if first_calc is not None:
                for k in ("keyword", "iprog", "blocks"):
                    if k in global_dict and k not in first_calc.get("params", {}):
                        first_calc.setdefault("params", {})[k] = global_dict.pop(k)
        return {"global": global_dict, "steps": steps_list}
    # Neither ``global`` nor ``steps`` — fully flat legacy / ``calc:`` shape.
    legacy_calc = data.get("calc") if isinstance(data.get("calc"), dict) else None
    global_dict = {k: v for k, v in data.items() if k != "calc" and k != "steps"}
    if legacy_calc:
        for k, v in legacy_calc.items():
            if k == "steps":
                continue
            global_dict.setdefault(k, v)
    _lift_legacy_resource_keys(global_dict)
    raw_steps = (
        (legacy_calc or {}).get("steps")
        or data.get("steps")
        or []
    )
    steps_list = _normalise_steps_list(raw_steps)
    if steps_list:
        first_calc = next(
            (s for s in steps_list
             if isinstance(s, dict) and s.get("type") == "calc"),
            None,
        )
        if first_calc is not None:
            for k in ("keyword", "iprog", "blocks"):
                if k in global_dict and k not in first_calc.get("params", {}):
                    first_calc.setdefault("params", {})[k] = global_dict.pop(k)
    return {"global": global_dict, "steps": steps_list}


def _lift_legacy_resource_keys(global_dict: dict[str, Any]) -> None:
    """Translate wizard-only resource names into confflow-native ones."""
    if "nproc" in global_dict and "cores_per_task" not in global_dict:
        try:
            global_dict["cores_per_task"] = int(global_dict["nproc"])
        except (TypeError, ValueError):
            pass
        global_dict.pop("nproc", None)
    if "memory_mb" in global_dict and "total_memory" not in global_dict:
        global_dict["total_memory"] = _format_mem_mb(global_dict["memory_mb"])
        global_dict.pop("memory_mb", None)


def _normalise_steps_list(raw_steps: Any) -> list[dict[str, Any]]:
    """Coerce whatever ``steps:`` looks like into ``[{name, type, params}]``."""
    if not raw_steps:
        return []
    if not isinstance(raw_steps, list):
        return []
    out: list[dict[str, Any]] = []
    for idx, step in enumerate(raw_steps, start=1):
        if isinstance(step, str):
            out.append(_token_to_step(step, idx=idx))
            continue
        if not isinstance(step, dict):
            continue
        # Already a step dict — pass through, filling in defaults.
        name = str(step.get("name") or f"step_{idx:02d}")
        step_type = str(step.get("type") or "calc")
        params = dict(step.get("params") or {})
        # If the legacy ``keyword`` / ``iprog`` ended up at the step's
        # top level instead of inside ``params``, hoist them.
        for k in ("iprog", "itask", "keyword", "energy_window", "cores_per_task",
                 "total_memory", "max_parallel_jobs", "blocks"):
            if k in step and k not in params:
                params[k] = step[k]
        if step_type == "calc":
            params.setdefault("itask", "sp")
        normalised = {"name": name, "type": step_type, "params": params}
        # Dependencies are workflow-owned, but they are still part of the
        # final document.  Preserve them when loading a saved workflow so a
        # graph projection or submit preview cannot silently flatten a DAG.
        if "inputs" in step:
            inputs = step["inputs"]
            if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
                raise ValueError("step inputs must be a list of step names")
            normalised["inputs"] = list(inputs)
        out.append(normalised)
    return out


def _validate_via_global_model(raw: dict[str, Any]) -> None:
    """Best-effort validation using ``GlobalConfigModel`` for the
    ``global`` section.  Raises on garbage input.  The typed
    :class:`GlobalConfigModel` only covers the ``global`` half of the
    schema — it knows nothing about ``steps``.
    """
    from jobdesk_app.confflow.confflow.core.models import GlobalConfigModel

    GlobalConfigModel.model_validate(raw.get("global") or {})


@dataclass(frozen=True)
class WorkflowSpec:
    """A validated ConfFlow workflow YAML document.

    Holds the parsed ``GlobalConfigModel`` instance. ``to_yaml`` serializes it
    back through Pydantic so round-trip is lossless. The wizard constructs one
    of these from form input and passes it to the run service.
    """

    global_config: Any  # GlobalConfigModel when confflow is installed
    # The full canonical schema (``{global, steps}``) ready for the
    # confflow engine to consume.  Kept alongside the typed model so we
    # can round-trip without losing the steps list or any non-typed
    # fields the engine understands but ``GlobalConfigModel`` ignores.
    _raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_form(
        cls,
        *,
        work_dir_name: str,
        program: str,
        method: str,
        basis: str,
        charge: int,
        multiplicity: int,
        nproc: int,
        memory_mb: int,
        steps: tuple[str, ...] = ("confgen", "preopt", "opt", "refine", "sp"),
        extra_options: dict[str, Any] | None = None,
        extra_keyword: str = "",
        freeze: list[int] | tuple[int, ...] | None = None,
        max_parallel_jobs: int | None = None,
    ) -> "WorkflowSpec":
        """Build a :class:`WorkflowSpec` from wizard form fields.

        The output mirrors the real confflow schema::

            global:
              cores_per_task / total_memory / charge / multiplicity / freeze
              max_parallel_jobs
            steps:
              - name: step_01
                type: confgen
                params: {...}
              - name: step_02
                type: calc
                params:
                  iprog: gaussian   # or orca
                  itask: opt        # sp / opt / opt_freq / ts / freq
                  keyword: M06-2X def2-TZVP SMD(solvent=toluene)

        ``method``/``basis``/``extra_keyword`` are spliced into the
        step's ``keyword`` string because that is how confflow's
        Gaussian/ORCA policies consume them. Resources live in
        ``global`` (so they apply to every step); ``freeze`` and
        ``max_parallel_jobs`` are also global because they describe the
        whole conformer search.
        """
        require_confflow()
        keyword = assemble_orca_keyword(method, basis, extra_keyword)
        if program in ("gaussian", "g16") and not keyword.strip():
            keyword = f"{method} {basis}".strip()
        # Map our legacy ``nproc``/``memory_mb`` int form to the
        # confflow-expected ``cores_per_task`` + ``total_memory``
        # (string with units).
        global_payload: dict[str, Any] = {
            "cores_per_task": int(nproc) if nproc else None,
            "total_memory": _format_mem_mb(memory_mb),
            "charge": int(charge),
            "multiplicity": int(multiplicity),
        }
        if freeze:
            global_payload["freeze"] = [int(x) for x in freeze]
        if max_parallel_jobs is not None and max_parallel_jobs > 1:
            global_payload["max_parallel_jobs"] = int(max_parallel_jobs)
        # Drop None entries so they don't trip exclude_none=False dumps.
        global_payload = {k: v for k, v in global_payload.items() if v is not None}
        # Wizard-supplied work directory.  confflow treats this as a
        # CLI-level concern (``--work-dir``), but the wizard persists
        # it inside ``global`` so a saved preset can be reloaded and
        # round-tripped back to the same name.
        if work_dir_name:
            global_payload["work_dir"] = work_dir_name

        # Build the steps list. Each user-visible step ("opt_freq",
        # "opt", "sp", "ts", "freq", "confgen", "preopt", "refine")
        # maps to a single confflow step with a sensible name + itask.
        step_dicts = [_token_to_step(tok) for tok in steps]

        # Stitch the assembled keyword onto the first calc step. If there
        # are multiple calc steps and only one keyword, apply it to the
        # first one (the wizard doesn't model per-step keywords yet).
        first_calc = next((s for s in step_dicts if s["type"] == "calc"), None)
        if first_calc is not None and keyword:
            first_calc["params"]["keyword"] = keyword
            first_calc["params"]["iprog"] = _iprog_token(program)

        # Optional passthrough of free-form advanced options (solvent,
        # TS parameters, etc.) — they land in ``global`` since they're
        # workflow-wide settings.
        if extra_options:
            global_payload.update(extra_options)

        raw = {
            "global": global_payload,
            "steps": step_dicts,
        }
        # We keep the model_validate step so we get pydantic's nice
        # error reporting on garbage input, but the canonical
        # serialization is the ``raw`` dict (which IS the schema
        # confflow actually consumes).
        try:
            _validate_via_global_model(raw)
        except Exception:
            # Fall back to a permissive model_validate of just the
            # global part so users still get typed defaults.
            GlobalConfigModel.model_validate(global_payload or {})
        return cls(global_config=GlobalConfigModel.model_validate(global_payload or {}), _raw=raw)

    @classmethod
    def from_yaml(cls, yaml_text: str) -> "WorkflowSpec":
        """Parse the canonical confflow YAML shape.

        Accepts::

            global: {...}
            steps:  [...]

        Older flat / ``calc:`` / token-list layouts still parse — we
        normalise them on the way in so the rest of the pipeline can
        rely on one shape.
        """
        require_confflow()
        import yaml

        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            raise ValueError("workflow YAML must be a mapping at the top level")
        is_legacy_token_layout = not ("global" in data and "steps" in data)
        normalised = _normalise_yaml_to_schema(data)
        _validate_confflow_semantics(
            normalised,
            allow_legacy_confgen_placeholder=is_legacy_token_layout,
        )
        # Populate the typed global model for callers that want it
        # (engine integration, default-supplementation).
        global_dict = normalised.get("global", {}) or {}
        model = GlobalConfigModel.model_validate(global_dict)
        return cls(global_config=model, _raw=normalised)

    def to_yaml(self) -> str:
        """Serialise to the canonical confflow YAML the engine consumes.

        Output shape::

            global:
              cores_per_task: 16
              total_memory: 32GB
              charge: 0
              multiplicity: 1
              freeze: [...]
            steps:
              - name: step_01
                type: confgen
                params: {...}
              - name: step_02
                type: calc
                params:
                  iprog: gaussian
                  keyword: "M06-2X def2-TZVP SMD(solvent=toluene)"
                  itask: opt

        We suppress confflow-engine defaults so the user only sees what
        they actually picked. The full model state is still kept in
        memory in case the engine ever needs the resolved defaults.
        """
        require_confflow()
        import yaml

        raw = getattr(self, "_raw", None)
        if not raw:
            # Legacy flat-shape models built before _raw existed —
            # rebuild a minimal raw payload from the model + step list.
            raw = self._reconstruct_raw()
        # Engine-facing YAML is an exact workflow snapshot.  Do not hide
        # defaults or inferred task fields here: e.g. dropping
        # ``itask: opt_freq`` silently changes a frequency workflow to an
        # optimisation when ConfFlow applies its own default.
        out = deepcopy(raw)
        return yaml.safe_dump(out, sort_keys=False, allow_unicode=True, default_flow_style=False)

    def to_user_yaml(self) -> str:
        """Wizard-side YAML rendering — leaner than :meth:`to_yaml`.

        v6 phase-6 split: the wizard owns three independent views of
        the same ``WorkflowSpec``:

        1. **Calculation card** (YAML editor) — for editing per-step
           keyword / iprog / itask lines.
        2. **Steps card** — for adding, reordering, deleting steps.
        3. **Global settings card** — for work_dir / cores / memory /
           charge / multiplicity / freeze.

        ``to_yaml()`` is the engine-facing payload and must include
        every required field (notably ``global`` and per-step
        ``type``). Showing *that* payload in the wizard's editor
        duplicates info the user just edited in cards 2 and 3 and
        floods the view with boilerplate.

        ``to_user_yaml()`` returns a wizard-oriented rendering:

        * No ``global:`` block at all (those values are exposed by
          card 3).
        * No ``type: calc`` since every step in the wizard except
          ``confgen`` is a calc.
        * No ``itask`` / ``iprog`` when they match the wizard token
          or the engine default, exactly as :meth:`to_yaml` does.
        * A header comment tells the user where to edit globally
          and where to edit per-step.

        The serialized form is still valid YAML — when ``Apply`` is
        clicked, the wizard ``merge``s any changes back into the spec
        via :meth:`from_yaml` (which can recover the missing ``type``
        and ``global`` from the source ``WorkflowSpec``).
        """
        require_confflow()
        import yaml

        raw = getattr(self, "_raw", None)
        if not raw:
            raw = self._reconstruct_raw()
        # Note: deliberately does NOT call ``_filter_user_facing_global``
        # — the user-facing representation has no ``global`` block at
        # all. The engine-facing ``to_yaml()`` does the filtering.
        steps_list = self._filter_user_facing_steps(
            raw.get("steps") or [],
            global_dict={},  # no global in user view, so don't pre-hide iprog-based on it
            omit_type_calc=True,
        )
        out: dict[str, Any] = {"steps": steps_list} if steps_list else {}
        return yaml.safe_dump(
            out,
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )

    @staticmethod
    def _filter_user_facing_global(global_dict: dict[str, Any]) -> dict[str, Any]:
        """Pick the subset of ``global`` keys the user actually picked.

        Defaults like ``charge: 0`` / ``multiplicity: 1`` /
        ``rmsd_threshold: 0.25`` are hidden — they match confflow's
        defaults and the user didn't pick them. ``freeze`` is shown
        when non-empty (a non-trivial constraint).
        """
        from jobdesk_app.confflow.confflow.core.models import GlobalConfigModel

        defaults = {
            fname: field.default
            for fname, field in GlobalConfigModel.model_fields.items()
            if field.default is not None
        }
        ordered_keys = [
            "gaussian_path",
            "orca_path",
            "cores_per_task",
            "total_memory",
            "max_parallel_jobs",
            "charge",
            "multiplicity",
            "freeze",
            "rmsd_threshold",
            "energy_window",
        ]
        keep: dict[str, Any] = {}
        for key in ordered_keys:
            if key not in global_dict:
                continue
            value = global_dict[key]
            if value in (None, "", [], {}):
                continue
            if key in defaults and value == defaults[key]:
                continue
            keep[key] = value
        # Anything else (advanced options, ts_*, scan_*, etc.) goes
        # through only if the user explicitly set it.
        engine_internal = {
            "energy_tolerance", "noH",
            "ts_bond_atoms", "ts_rescue_scan", "scan_coarse_step",
            "scan_fine_step", "scan_uphill_limit", "ts_bond_drift_threshold",
            "ts_rmsd_threshold", "enable_dynamic_resources",
            "resume_from_backups", "stop_check_interval_seconds",
            "force_consistency",
        }
        for key, value in global_dict.items():
            if key in ordered_keys or key in engine_internal:
                continue
            # ``work_dir`` is technically a CLI parameter to
            # confflow (``--work-dir``), but the wizard persists it in
            # the YAML anyway so reloading a saved user preset can
            # recover the wizard's intended work directory.  confflow
            # itself just ignores unknown ``global.*`` keys.
            if value in (None, "", [], {}):
                continue
            keep[key] = value
        return keep

    @staticmethod
    def _filter_user_facing_steps(
        steps: list[dict[str, Any]],
        *,
        global_dict: dict[str, Any] | None = None,
        omit_type_calc: bool = False,
    ) -> list[dict[str, Any]]:
        """Emit only the user-meaningful fields per step.

        Two callers share this filter:

        * ``to_yaml()`` — the canonical confflow shape for the engine
          loader.  The confflow loader rejects step dicts that omit
          ``type``, so we never elide ``type``.
        * ``to_user_yaml()`` — the wizard-side rendering.  ``type``
          is hidden when it matches the wizard default ``calc``
          (every step in the wizard except ``confgen`` is a calc).
        """
        global_dict = global_dict or {}
        global_iprog = str(
            global_dict.get("iprog_default")
            or global_dict.get("iprog")
            or "gaussian"
        ).lower()
        # Wizard-internal step names — when ``from_form`` synthesised
        # the step from a wizard token, the step's *name* already
        # tells you the task (or that it's a wizard-only concept like
        # ``confgen``/``preopt``/``refine``).  In those cases the
        # auto-generated ``itask`` param is redundant noise.
        wizard_token_names = {"confgen", "preopt", "refine", "opt_freq"}

        def _should_skip_param(name: str, params: dict[str, Any], value: Any) -> bool:
            if value in (None, "", [], []):
                return True
            # ``itask`` defaults to ``sp`` and is always redundant for
            # wizard-token-named steps (``preopt`` already implies
            # ``opt``, ``opt_freq`` already implies ``opt_freq``, etc.).
            if name == "itask":
                if params.get("__step_name__") in wizard_token_names:
                    return True
                if str(value) == "sp" and not params.get("__itask_explicit__"):
                    return True
            # ``iprog`` matches the global default → hide per-step.
            if name == "iprog" and str(value).lower() == global_iprog:
                return True
            # Hide resource overrides that match the global.
            for resource_key in (
                "cores_per_task", "total_memory", "max_parallel_jobs",
                "energy_window",
            ):
                if name == resource_key and resource_key in global_dict:
                    if value == global_dict[resource_key]:
                        return True
            return False

        clean: list[dict[str, Any]] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            entry: dict[str, Any] = {}
            step_name = str(step.get("name") or "")
            step_type = str(step.get("type") or "calc")
            if step_name:
                entry["name"] = step_name
            if step_type:
                if omit_type_calc and step_type == "calc":
                    pass  # default; hide for the wizard view
                else:
                    entry["type"] = step_type
            params = step.get("params")
            if isinstance(params, dict) and params:
                kept_params: dict[str, Any] = {}
                hint = dict(params)
                hint["__step_name__"] = step_name
                for k, v in params.items():
                    if _should_skip_param(k, hint, v):
                        continue
                    kept_params[k] = v
                if kept_params:
                    entry["params"] = kept_params
            # ``inputs`` describes the execution graph, not a cosmetic
            # default.  Preserve both empty roots and named dependencies in
            # engine-facing YAML so reopening a workflow cannot flatten it.
            if "inputs" in step:
                entry["inputs"] = list(step["inputs"])
            clean.append(entry)
        return clean

    def _reconstruct_raw(self) -> dict[str, Any]:
        """Best-effort recovery of the ``{global, steps}`` payload from
        the typed model.  Used when ``_raw`` wasn't populated (e.g. a
        legacy ``GlobalConfigModel``-only instance from before v6).
        """
        data = self.global_config.model_dump(mode="json", exclude_none=True)
        return {
            "global": data,
            "steps": data.pop("steps", []) or [],
        }

    def to_form(self) -> dict[str, Any]:
        """Round-trip the wizard form from a :class:`WorkflowSpec`.

        Reads the canonical ``{global, steps}`` payload, extracts the
        first calc step's ``keyword`` for editor preview, and infers
        ``program`` from the first calc step's ``iprog``.  Resources
        (``cores_per_task``, ``total_memory``, ``charge``,
        ``multiplicity``, ``freeze``, ``max_parallel_jobs``) come
        from ``global``.
        """
        require_confflow()
        raw = getattr(self, "_raw", None) or self._reconstruct_raw()
        global_dict = raw.get("global") or {}
        steps_list = raw.get("steps") or []
        # Pick the first calc step to drive the editor preview.
        calc_step = next(
            (s for s in steps_list if isinstance(s, dict) and s.get("type") == "calc"),
            None,
        )
        calc_params = (calc_step or {}).get("params") or {}
        keyword = str(calc_params.get("keyword") or "")
        iprog = str(calc_params.get("iprog") or "")
        itask = str(calc_params.get("itask") or "")
        method, basis, extra_kw = _split_keyword_into_form(
            keyword, has_method=True, has_basis=True
        )
        program = {
            "gaussian": "gaussian", "g16": "gaussian", "g09": "gaussian",
            "orca": "orca",
        }.get(iprog.lower(), "gaussian" if not iprog else iprog)
        # Tokenise the step list back to the wizard's compact form:
        # "opt_freq", "opt", "sp", "freq", "ts", "confgen", "preopt",
        # "refine". A step with extra params (e.g. confgen) is kept
        # as the bare token; the user can re-edit per-step params
        # directly in the YAML.
        tokens: list[str] = []
        # Wizard-token shortcuts preserved by ``from_form``: if the user
        # wrote ``steps=("confgen", "preopt", "refine")`` we want those
        # exact names back on round-trip, not the canonical
        # ``(opt, opt, sp)`` we generated under the hood.
        wizard_tokens = {"confgen", "preopt", "opt_freq", "refine"}
        for step in steps_list:
            if not isinstance(step, dict):
                continue
            name = str(step.get("name") or "")
            kind = str(step.get("type") or "")
            if kind == "confgen":
                tokens.append("confgen")
                continue
            if kind == "calc":
                if name in wizard_tokens:
                    tokens.append(name)
                else:
                    t = _itask_token(str(
                        step.get("params", {}).get("itask", "")
                    ))
                    if t:
                        tokens.append(t)
                continue
            tokens.append(name or kind)
        nproc = int(global_dict.get("cores_per_task", 1) or 1)
        memory_mb = _parse_mem_mb_local(global_dict.get("total_memory"))
        return {
            "work_dir_name": global_dict.get("work_dir", ""),
            "program": program,
            "method": method,
            "basis": basis,
            "extra_keyword": extra_kw,
            "charge": int(global_dict.get("charge", 0) or 0),
            "multiplicity": int(global_dict.get("multiplicity", 1) or 1),
            "nproc": nproc,
            "memory_mb": memory_mb,
            "steps": tokens,
            "keyword": keyword,
            "itask": itask,
            "freeze": list(global_dict.get("freeze") or []),
            "max_parallel_jobs": int(global_dict.get("max_parallel_jobs", 1) or 1),
        }

    def dry_run(self) -> DryRunReport:
        """Best-effort local dry-run.

        ConfFlow's ``--dry-run`` needs an XYZ file and a work dir; here we
        just validate the YAML and report whether ``GlobalConfigModel``
        parsed cleanly. The wizard shows this as a green/red indicator
        before the user clicks Submit. A full remote dry-run happens on the
        server when the user clicks Submit (``confflow --dry-run`` is
        called there).
        """
        try:
            require_confflow()
            # Round-trip serialize/parse: if this works the document is valid.
            text = self.to_yaml()
            WorkflowSpec.from_yaml(text)
            return DryRunReport(ok=True, preview_lines=tuple(text.splitlines()[:200]))
        except ConfFlowUnavailableError as exc:
            return DryRunReport(ok=False, preview_lines=(), error=str(exc))
        except Exception as exc:
            return DryRunReport(ok=False, preview_lines=(), error=f"{type(exc).__name__}: {exc}")


def write_workflow_yaml(spec: WorkflowSpec, path: str | Path) -> Path:
    """Convenience: serialize ``spec`` and write atomically to ``path``."""
    require_confflow()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = spec.to_yaml()
    # Atomic write so a half-written workflow.yaml never reaches the remote.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(target)
    return target


__all__ = [
    "ConfFlowUnavailableError",
    "DryRunReport",
    "WorkflowSpec",
    "assemble_orca_keyword",
    "require_confflow",
    "write_workflow_yaml",
]
