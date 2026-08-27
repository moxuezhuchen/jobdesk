"""Dependency-free, lossless representation of a workflow document.

This is deliberately a document model rather than a ConfFlow model.  JobDesk
must be able to open, preserve and edit a saved workflow on a base install;
the remote producer remains the authority for admission.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping


class WorkflowDocumentError(ValueError):
    """Raised when a workflow document cannot be represented safely."""


@dataclass(frozen=True)
class WorkflowDocument:
    """A versioned workflow payload plus non-engine wizard metadata.

    ``payload`` is intentionally untyped JSON/YAML data.  Keeping a deep copy
    of it is what prevents an unfamiliar producer extension from disappearing
    merely because JobDesk does not yet have a form control for it.
    """

    payload: dict[str, Any]
    version: str = "unversioned"
    source_layout: str = "canonical"
    wizard_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise WorkflowDocumentError("workflow document must be a mapping at the top level")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "WorkflowDocument":
        payload = deepcopy(dict(value))
        layout = "canonical" if "global" in payload and "steps" in payload else "legacy"
        version_value = payload.get("version", payload.get("schema_version", "unversioned"))
        return cls(payload=payload, version=str(version_value), source_layout=layout)

    def to_mapping(self) -> dict[str, Any]:
        """Return an isolated copy suitable for a YAML writer or mapper."""
        return deepcopy(self.payload)

    def with_payload(self, payload: Mapping[str, Any]) -> "WorkflowDocument":
        return WorkflowDocument(
            payload=deepcopy(dict(payload)),
            version=self.version,
            source_layout=self.source_layout,
            wizard_metadata=deepcopy(self.wizard_metadata),
        )
