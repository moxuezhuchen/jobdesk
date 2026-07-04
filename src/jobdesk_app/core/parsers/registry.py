"""Parser registry for software-agnostic result parsing."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

_parsers: dict[str, Callable[[Path], Any]] = {}


class ParserRegistry:
    """Software → parse function registry, auto-discovered via side-effect imports."""

    @classmethod
    def register(cls, software: str, parse_fn: Callable[[Path], Any]) -> None:
        _parsers[software.lower()] = parse_fn

    @classmethod
    def get(cls, software: str) -> Callable[[Path], Any]:
        software = software.lower()
        if software not in _parsers:
            raise KeyError(f"no parser registered for software: {software}")
        return _parsers[software]
