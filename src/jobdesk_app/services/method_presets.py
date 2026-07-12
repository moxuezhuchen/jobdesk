"""Disk-backed method preset library for the workflow builder.

Built-in presets ship under ``jobdesk_app.resources.method_presets`` as
confflow YAML files; user-saved presets land in
``<app_data_dir>/method_presets/<name>.yaml``. Both shapes round-trip
through :class:`jobdesk_app.core.workflow_spec.WorkflowSpec` so the
editor, the wizard, and the run service see the same data model.
"""
from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..app_paths import get_app_data_dir
from ..core.atomic_write import atomic_write_text
from ..core.workflow_spec import WorkflowSpec

PresetSource = Literal["builtin", "user"]


@dataclass(frozen=True)
class MethodPreset:
    name: str
    source: PresetSource
    path: Path
    spec: WorkflowSpec


def _read_spec_from_path(path: Path) -> WorkflowSpec:
    text = path.read_text(encoding="utf-8")
    return WorkflowSpec.from_yaml(text)


def _safe_preset_filename(name: str) -> str:
    # Strip path separators to keep this strictly inside user_dir.
    cleaned = name.strip().replace("/", "_").replace("\\", "_")
    if not cleaned:
        raise ValueError("preset name must be non-empty")
    return f"{cleaned}.yaml"


class MethodPresetStore:
    """Resolve confflow YAML presets from built-in and user directories.

    Lookup precedence (when name collides):

    1. User directory wins (``<app_data_dir>/method_presets``).
    2. Built-in directory (``jobdesk_app.resources.method_presets``).

    Both directories hold confflow YAML files; the file stem is the
    preset name (``b3lyp_631gd_opt_freq.yaml``).
    """

    def __init__(self) -> None:
        self._builtin_pkg = "jobdesk_app.resources.method_presets"

    @property
    def user_dir(self) -> Path:
        d = get_app_data_dir() / "method_presets"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _builtin_root(self) -> Path:
        # ``importlib.resources.files`` returns a ``Traversable``; turn
        # the relevant ones into ``Path`` so ``iterdir`` is uniform.
        traversable = importlib.resources.files(self._builtin_pkg)
        return Path(str(traversable))

    def _iter_builtin(self) -> list[Path]:
        root = self._builtin_root()
        if not root.exists():
            return []
        # Walk subdirectories (gaussian/, orca/, conflow/).
        return sorted(p for p in root.rglob("*.yaml") if p.is_file())

    def _iter_user(self) -> list[Path]:
        if not self.user_dir.exists():
            return []
        return sorted(p for p in self.user_dir.rglob("*.yaml") if p.is_file())

    def list_presets(self) -> list[MethodPreset]:
        seen: dict[str, MethodPreset] = {}
        # User first so user overrides built-in on collision.
        for path in self._iter_user():
            seen[path.stem] = MethodPreset(
                name=path.stem, source="user", path=path, spec=_read_spec_from_path(path)
            )
        for path in self._iter_builtin():
            if path.stem in seen:
                continue
            seen[path.stem] = MethodPreset(
                name=path.stem, source="builtin", path=path, spec=_read_spec_from_path(path)
            )
        return list(seen.values())

    def load(self, name: str, *, source: PresetSource | None = None) -> WorkflowSpec:
        if source == "user" or (source is None and (self.user_dir / f"{name}.yaml").exists()):
            user_path = self.user_dir / f"{name}.yaml"
            if user_path.exists():
                return _read_spec_from_path(user_path)
        if source == "builtin" or source is None:
            for path in self._iter_builtin():
                if path.stem == name:
                    return _read_spec_from_path(path)
        raise KeyError(f"Method preset {name!r} not found")

    def save_user(self, name: str, spec: WorkflowSpec) -> Path:
        """Persist ``spec`` to ``<user_dir>/<name>.yaml`` atomically."""
        target = self.user_dir / _safe_preset_filename(name)
        atomic_write_text(target, spec.to_yaml())
        return target

    def delete_user(self, name: str) -> None:
        target = self.user_dir / _safe_preset_filename(name)
        if target.exists():
            target.unlink()

    def rename_user(self, old_name: str, new_name: str) -> Path:
        src = self.user_dir / _safe_preset_filename(old_name)
        dst = self.user_dir / _safe_preset_filename(new_name)
        atomic_write_text(dst, src.read_text(encoding="utf-8"))
        src.unlink()
        return dst


__all__ = ["MethodPreset", "MethodPresetStore", "PresetSource"]
