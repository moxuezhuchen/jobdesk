"""Bounded editor diagnostics and migration policy for workflow authoring."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from .workflow_document import WorkflowDocument


@dataclass(frozen=True)
class WorkflowDiagnostic:
    """A structured authoring diagnostic."""

    severity: Literal["error", "warning", "info"] | str
    code: str
    message: str
    path: str = ""


class WorkflowSemanticValidator(Protocol):
    """Port for producer-owned semantic acceptance."""

    def validate(
        self,
        payload: Mapping[str, Any],
        *,
        allow_legacy_placeholder: bool = False,
    ) -> Sequence[WorkflowDiagnostic | str]:
        ...


class WorkflowSchemaLintPort(Protocol):
    """Backward-compatible document-level lint port."""

    def lint(self, document: WorkflowDocument) -> Sequence[WorkflowDiagnostic]:
        ...


@dataclass(frozen=True)
class MigrationPolicy:
    """Explicit policy required before changing a saved format."""

    allow_format_change: bool = False
    backup_created: bool = False


def _structural_diagnostics(document: WorkflowDocument) -> list[WorkflowDiagnostic]:
    """Check the authoring shape without interpreting producer parameters."""

    diagnostics: list[WorkflowDiagnostic] = []
    raw = document.raw
    if not isinstance(raw, dict):
        diagnostics.append(WorkflowDiagnostic("error", "document.mapping", "workflow must be a mapping"))
        return diagnostics

    if document.source_format == "canonical":
        if "global" not in raw:
            diagnostics.append(
                WorkflowDiagnostic("error", "schema.global.required", "canonical workflow needs a global mapping", "global")
            )
        if "steps" not in raw:
            diagnostics.append(
                WorkflowDiagnostic("error", "schema.steps.required", "canonical workflow needs a steps list", "steps")
            )
    elif document.migration.requires_migration:
        diagnostics.append(
            WorkflowDiagnostic(
                "warning",
                "migration.backup_required",
                "legacy workflow format requires an explicit migration and backup decision",
            )
        )

    if "global" in raw and raw.get("global") is not None and not isinstance(raw.get("global"), dict):
        diagnostics.append(WorkflowDiagnostic("error", "schema.global.mapping", "global must be a mapping", "global"))

    steps = raw.get("steps")
    if steps is not None and not isinstance(steps, list):
        diagnostics.append(WorkflowDiagnostic("error", "schema.steps.list", "workflow steps must be a list", "steps"))
        return diagnostics

    names: set[str] = set()
    for index, step in enumerate(steps or [], start=1):
        path = f"steps[{index - 1}]"
        if isinstance(step, str):
            # Bare tokens are a documented legacy input handled by the
            # compatibility migration adapter.
            continue
        if not isinstance(step, dict):
            diagnostics.append(
                WorkflowDiagnostic("error", "schema.step.mapping", "workflow step must be a mapping", path)
            )
            continue
        name = step.get("name")
        if not isinstance(name, str) or not name.strip():
            diagnostics.append(WorkflowDiagnostic("error", "schema.step.name", "step name is required", f"{path}.name"))
        elif name in names:
            diagnostics.append(
                WorkflowDiagnostic("error", "schema.step.name_unique", "step names must be unique", f"{path}.name")
            )
        else:
            names.add(name)
        if not isinstance(step.get("type"), str) or not step.get("type", "").strip():
            diagnostics.append(WorkflowDiagnostic("error", "schema.step.type", "step type is required", f"{path}.type"))
        params = step.get("params")
        if params is not None and not isinstance(params, dict):
            diagnostics.append(
                WorkflowDiagnostic("error", "schema.step.params", "step params must be a mapping", f"{path}.params")
            )
        for key in ("inputs", "fan_in", "fan_out"):
            value = step.get(key)
            if value is not None and (
                not isinstance(value, list) or not all(isinstance(item, str) for item in value)
            ):
                diagnostics.append(
                    WorkflowDiagnostic("error", f"schema.step.{key}", f"{key} must be a list of step names", f"{path}.{key}")
                )
    return diagnostics


def _coerce_semantic_diagnostic(item: WorkflowDiagnostic | str) -> WorkflowDiagnostic:
    if isinstance(item, WorkflowDiagnostic):
        return item
    return WorkflowDiagnostic("error", "producer.semantic", str(item))


def lint_workflow(
    document: WorkflowDocument,
    *,
    semantic_validator: WorkflowSemanticValidator | WorkflowSchemaLintPort | None = None,
) -> list[WorkflowDiagnostic]:
    """Return structural diagnostics and optional producer diagnostics.

    No producer parameter allowlist lives here.  A producer validator is
    invoked only when the caller explicitly injects one.
    """

    diagnostics = _structural_diagnostics(document)
    if document.migration.requires_migration:
        # The warning is emitted above with the other format diagnostics.
        pass
    if semantic_validator is not None and not any(item.severity == "error" for item in diagnostics):
        canonical = document.canonical_mapping()
        validate = getattr(semantic_validator, "validate", None)
        if callable(validate):
            result = validate(
                canonical,
                allow_legacy_placeholder=document.migration.requires_migration,
            )
            diagnostics.extend(_coerce_semantic_diagnostic(item) for item in result)
        else:
            lint = getattr(semantic_validator, "lint", None)
            if not callable(lint):
                raise TypeError("semantic_validator must provide validate() or lint()")
            diagnostics.extend(_coerce_semantic_diagnostic(item) for item in lint(document))
    return diagnostics


def require_migration_policy(document: WorkflowDocument, policy: MigrationPolicy | None) -> None:
    """Reject silent legacy-to-canonical conversion."""

    if not document.migration.requires_migration:
        return
    if policy is None or not policy.allow_format_change:
        raise ValueError("workflow format migration requires allow_format_change=True")
    if document.migration.backup_required and not policy.backup_created:
        raise ValueError("workflow format migration requires backup_created=True")


__all__ = [
    "MigrationPolicy",
    "WorkflowDiagnostic",
    "WorkflowSemanticValidator",
    "WorkflowSchemaLintPort",
    "lint_workflow",
    "require_migration_policy",
]
