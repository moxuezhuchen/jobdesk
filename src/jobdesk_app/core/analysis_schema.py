"""Domain types for result extraction rules."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ExtractStrategy(str, Enum):
    first = "first"
    last = "last"
    all = "all"


class ExtractType(str, Enum):
    float = "float"
    int = "int"
    str = "str"


class ExtractResult(BaseModel):
    """One domain-level result extraction rule."""

    name: str = Field(..., description="Result field name")
    source_glob: str = Field(..., description="Source file glob")
    regex: str = Field(..., description="Regular expression containing a named value group")
    strategy: ExtractStrategy = Field(default=ExtractStrategy.last)
    type: ExtractType = Field(default=ExtractType.float)
    unit: str | None = Field(default=None)


__all__ = ["ExtractResult", "ExtractStrategy", "ExtractType"]
