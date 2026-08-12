"""Qt-free YAML codec and atomic persistence for workflow documents."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .workflow_document import WorkflowDocument


class WorkflowCodec:
    """Parse and serialize :class:`WorkflowDocument` without producer imports."""

    @staticmethod
    def loads(text: str) -> WorkflowDocument:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("workflow YAML must be a mapping at the top level")
        return WorkflowDocument.from_mapping(data)

    @staticmethod
    def dumps(document: WorkflowDocument, *, canonical: bool = False) -> str:
        data = document.canonical_mapping() if canonical else document.mapping()
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)

    @staticmethod
    def dumps_mapping(data: dict[str, Any]) -> str:
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)

    @staticmethod
    def write_atomic(path: str | Path, text: str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
        return target


__all__ = ["WorkflowCodec"]
