"""Strict consumer-side parser for ConfFlow's output manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .confflow_contract import OUTPUT_MANIFEST_SCHEMA


class OutputManifestError(ValueError):
    """The producer output manifest is not safe or not versioned."""


@dataclass(frozen=True)
class ConfFlowOutputManifest:
    """Validated terminal-to-relative-output mapping."""

    terminals: dict[str, tuple[str, ...]]
    paths: tuple[str, ...]


def parse_output_manifest(raw: object, *, work_dir: Path | None = None) -> ConfFlowOutputManifest:
    """Parse and validate a manifest without permitting path escape.

    Every output is normalized as a POSIX relative path. Absolute paths,
    backslashes, ``.``/``..`` components, duplicate targets, and symlink
    escapes from ``work_dir`` are rejected before any download is attempted.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw, object_pairs_hook=_unique_object)
        except (json.JSONDecodeError, ValueError) as exc:
            raise OutputManifestError(f"malformed output manifest JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise OutputManifestError("output manifest must be an object")
    if raw.get("content_schema") != OUTPUT_MANIFEST_SCHEMA:
        raise OutputManifestError(f"unsupported output manifest schema: expected {OUTPUT_MANIFEST_SCHEMA!r}")
    terminals = raw.get("terminals")
    if not isinstance(terminals, dict):
        raise OutputManifestError("output manifest terminals must be an object")
    if not terminals:
        raise OutputManifestError("output manifest requires at least one terminal")

    root = work_dir.resolve() if work_dir is not None else None
    parsed: dict[str, tuple[str, ...]] = {}
    all_paths: list[str] = []
    seen: set[str] = set()
    for terminal, values in terminals.items():
        if not isinstance(terminal, str) or not terminal:
            raise OutputManifestError("output manifest terminal names must be non-empty strings")
        if not isinstance(values, list):
            raise OutputManifestError(f"output manifest terminal {terminal!r} must contain a list")
        if not values:
            raise OutputManifestError(f"output manifest terminal {terminal!r} must not be empty")
        terminal_paths: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise OutputManifestError(f"output manifest path in terminal {terminal!r} must be a string")
            path = _safe_relative_path(value)
            normalized = path.as_posix()
            if normalized in seen:
                raise OutputManifestError(f"output manifest contains duplicate target: {normalized}")
            if root is not None:
                candidate = (root / Path(*path.parts)).resolve(strict=False)
                if not candidate.is_relative_to(root):
                    raise OutputManifestError(f"output manifest path escapes work directory: {value}")
            seen.add(normalized)
            terminal_paths.append(normalized)
            all_paths.append(normalized)
        parsed[terminal] = tuple(terminal_paths)
    return ConfFlowOutputManifest(terminals=parsed, paths=tuple(all_paths))


def load_output_manifest(path: Path, *, work_dir: Path | None = None) -> ConfFlowOutputManifest:
    """Load and validate a local manifest file."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise OutputManifestError(f"cannot read output manifest {path}: {exc}") from exc
    return parse_output_manifest(raw, work_dir=work_dir)


def _safe_relative_path(value: str) -> PurePosixPath:
    if not value or "\\" in value or "\x00" in value:
        raise OutputManifestError(f"unsafe output manifest path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or value != path.as_posix()
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise OutputManifestError(f"unsafe output manifest path: {value!r}")
    return path


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value
