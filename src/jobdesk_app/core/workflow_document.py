"""Lossless workflow authoring documents.

This module deliberately knows nothing about Qt or ConfFlow's Python models.
It owns the YAML author's view of a workflow, including fields that JobDesk
does not understand yet.  The compatibility facade may project the document
into the producer's canonical shape, but this object never drops authoring
data while doing so.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .workflow_editor import WorkflowDiagnostic, WorkflowSchemaLintPort, WorkflowSemanticValidator

DocumentFormat = Literal["canonical", "flat", "nested-calc", "legacy"]


@dataclass(frozen=True)
class MigrationDecision:
    """The explicit decision associated with a document format change."""

    source_format: DocumentFormat
    target_format: DocumentFormat
    action: Literal["preserve", "migrate"]
    backup_required: bool
    reasons: tuple[str, ...] = ()

    @property
    def requires_migration(self) -> bool:
        return self.action == "migrate"


def _format_of(data: dict[str, Any]) -> DocumentFormat:
    if "global" in data:
        return "canonical"
    if "steps" in data:
        return "flat"
    if isinstance(data.get("calc"), dict):
        return "nested-calc"
    return "legacy"


def normalise_workflow_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Project a saved mapping through the explicit compatibility adapter."""

    from .workflow_migration import LegacyWorkflowMigrationAdapter

    return LegacyWorkflowMigrationAdapter().canonicalize(data)


class WorkflowMigrationPort(Protocol):
    """Narrow port for an application-owned legacy-to-canonical projection."""

    def canonicalize(self, data: dict[str, Any]) -> dict[str, Any]:
        ...


@dataclass(frozen=True)
class WorkflowDocument:
    """A lossless authoring document plus its canonical projection."""

    raw: dict[str, Any]
    source_format: DocumentFormat = "canonical"
    migration: MigrationDecision = field(
        default_factory=lambda: MigrationDecision("canonical", "canonical", "preserve", False)
    )

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "WorkflowDocument":
        if not isinstance(data, dict):
            raise ValueError("workflow YAML must be a mapping at the top level")
        raw = deepcopy(data)
        source_format = _format_of(raw)
        if source_format == "canonical":
            decision = MigrationDecision("canonical", "canonical", "preserve", False)
        else:
            decision = MigrationDecision(
                source_format,
                "canonical",
                "migrate",
                True,
                ("saved workflow uses a legacy shape; preserve a backup before canonical migration",),
            )
        return cls(raw=raw, source_format=source_format, migration=decision)

    @property
    def steps(self) -> list[dict[str, Any]]:
        steps = self.raw.get("steps", [])
        return deepcopy(steps) if isinstance(steps, list) else []

    @property
    def global_config(self) -> dict[str, Any]:
        value = self.raw.get("global", {})
        return deepcopy(value) if isinstance(value, dict) else {}

    def mapping(self) -> dict[str, Any]:
        """Return the exact authoring mapping, copied for caller safety."""
        return deepcopy(self.raw)

    def canonical_mapping(self, migration: WorkflowMigrationPort | None = None) -> dict[str, Any]:
        """Return a canonical projection through an explicit migration port."""
        if migration is None:
            return normalise_workflow_mapping(self.raw)
        return migration.canonicalize(self.raw)

    def lint(
        self,
        validator: WorkflowSemanticValidator | WorkflowSchemaLintPort | None = None,
    ) -> list[WorkflowDiagnostic]:
        """Return structural/editor diagnostics without importing Qt or ConfFlow."""
        from .workflow_editor import lint_workflow

        return lint_workflow(self, semantic_validator=validator)


__all__ = [
    "DocumentFormat",
    "MigrationDecision",
    "WorkflowMigrationPort",
    "WorkflowDocument",
    "normalise_workflow_mapping",
]
