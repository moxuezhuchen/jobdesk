"""YAML codec for :mod:`workflow_document`, intentionally ConfFlow-free."""

from __future__ import annotations

from typing import Any

import yaml

from .workflow_document import WorkflowDocument, WorkflowDocumentError


def decode_workflow_yaml(text: str) -> WorkflowDocument:
    value: Any = yaml.safe_load(text)
    if not isinstance(value, dict):
        raise WorkflowDocumentError("workflow YAML must be a mapping at the top level")
    return WorkflowDocument.from_mapping(value)


def encode_workflow_yaml(document: WorkflowDocument) -> str:
    """Encode without sorting, retaining all unfamiliar mapping members."""
    return yaml.safe_dump(document.to_mapping(), sort_keys=False, allow_unicode=True, default_flow_style=False)
