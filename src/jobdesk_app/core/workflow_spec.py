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

from dataclasses import dataclass
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


@dataclass(frozen=True)
class WorkflowSpec:
    """A validated ConfFlow workflow YAML document.

    Holds the parsed ``GlobalConfigModel`` instance. ``to_yaml`` serializes it
    back through Pydantic so round-trip is lossless. The wizard constructs one
    of these from form input and passes it to the run service.
    """

    global_config: Any  # GlobalConfigModel when confflow is installed

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
    ) -> "WorkflowSpec":
        """Build a GlobalConfigModel from form-friendly fields.

        ``program`` is "gaussian" / "orca". The remaining fields map directly
        onto the CalcConfigModel sections that 99% of users touch; pass
        ``extra_options`` for advanced keys (e.g. ``solvent``, ``scan``) that
        the form does not expose.
        """
        require_confflow()
        calc_payload: dict[str, Any] = {
            "program": program,
            "method": method,
            "basis": basis,
            "charge": charge,
            "multiplicity": multiplicity,
            "nproc": nproc,
            "memory_mb": memory_mb,
            "steps": list(steps),
        }
        if extra_options:
            calc_payload.update(extra_options)
        # When the user picks ORCA, the policy's input template emits
        # ``! {keyword}`` — we therefore assemble ``keyword`` from the
        # method/basis text fields unless the user explicitly supplied one
        # via ``extra_options["keyword"]``.
        if program == "orca" and not calc_payload.get("keyword"):
            assembled = assemble_orca_keyword(method, basis)
            if assembled:
                calc_payload["keyword"] = assembled
        # GlobalConfigModel in v1.0.10 has shape:
        #   { "work_dir": str, "calc": CalcConfigModel-shaped dict }
        # We pass a dict so validators run; downstream code may later
        # re-parse into the typed CalcConfigModel if needed.
        global_payload: dict[str, Any] = {
            "work_dir": work_dir_name,
            "calc": calc_payload,
        }
        model = GlobalConfigModel.model_validate(global_payload)
        return cls(global_config=model)

    @classmethod
    def from_yaml(cls, yaml_text: str) -> "WorkflowSpec":
        require_confflow()
        import yaml

        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            raise ValueError("workflow YAML must be a mapping at the top level")
        model = GlobalConfigModel.model_validate(data)
        return cls(global_config=model)

    def to_yaml(self) -> str:
        require_confflow()
        import yaml

        # Pydantic v2: ``model_dump(mode="json")`` gives JSON-compatible types
        # which yaml.safe_dump can render without a custom representer.
        data = self.global_config.model_dump(mode="json", exclude_none=True)
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    def to_form(self) -> dict[str, Any]:
        """Inverse of :meth:`from_form` for round-tripping the wizard.

        Falls back to empty strings when a key is missing so the form
        pre-populates cleanly.
        """
        require_confflow()
        # GlobalConfigModel has ``model_dump``; tolerate either nested or flat
        # calc shape across minor schema versions.
        data = self.global_config.model_dump(mode="json", exclude_none=True)
        calc = data.get("calc", {}) if isinstance(data, dict) else {}
        if not isinstance(calc, dict):
            calc = {}
        steps = calc.get("steps") or []
        return {
            "work_dir_name": data.get("work_dir", ""),
            "program": calc.get("program", ""),
            "method": calc.get("method", ""),
            "basis": calc.get("basis", ""),
            "charge": calc.get("charge", 0),
            "multiplicity": calc.get("multiplicity", 1),
            "nproc": calc.get("nproc", 1),
            "memory_mb": calc.get("memory_mb", 1024),
            "steps": list(steps) if isinstance(steps, list) else [],
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
