"""Disk-backed saved-workflow and reusable-step libraries.

Only user-saved *workflows* live in ``<app_data_dir>/method_presets``.
Bundled YAML is deliberately limited to reusable *steps* under
``jobdesk_app.resources.step_presets``: a workflow is created only after
the user composes steps and saves that composition.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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


@dataclass(frozen=True)
class StepPreset:
    """A reusable YAML fragment for one workflow step.

    Unlike :class:`MethodPreset`, a step preset intentionally contains
    no workflow-global resources and no ``inputs``.  The graph owns
    dependencies; applying a preset must therefore never rewire a
    workflow by surprise.
    """

    name: str
    source: PresetSource
    path: Path
    step: dict[str, Any]


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
    """Store user-saved workflow compositions.

    The historical name remains for compatibility, but this store never
    exposes bundled entries.  Those are step presets and belong to
    :class:`StepPresetStore` instead.
    """

    @property
    def user_dir(self) -> Path:
        d = get_app_data_dir() / "method_presets"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _iter_user(self) -> list[Path]:
        if not self.user_dir.exists():
            return []
        return sorted(p for p in self.user_dir.rglob("*.yaml") if p.is_file())

    def list_presets(self) -> list[MethodPreset]:
        presets: list[MethodPreset] = []
        for path in self._iter_user():
            presets.append(MethodPreset(name=path.stem, source="user", path=path, spec=_read_spec_from_path(path)))
        return presets

    def load(self, name: str, *, source: PresetSource | None = None) -> WorkflowSpec:
        return WorkflowSpec.from_yaml(self.load_yaml(name, source=source))

    def load_yaml(self, name: str, *, source: PresetSource | None = None) -> str:
        """Return the persisted YAML without normalising its step fragments."""
        if source == "user" or (source is None and (self.user_dir / f"{name}.yaml").exists()):
            user_path = self.user_dir / f"{name}.yaml"
            if user_path.exists():
                return user_path.read_text(encoding="utf-8")
        raise KeyError(f"Method preset {name!r} not found")

    def save_user(self, name: str, spec: WorkflowSpec) -> Path:
        """Persist ``spec`` to ``<user_dir>/<name>.yaml`` atomically."""
        target = self.user_dir / _safe_preset_filename(name)
        atomic_write_text(target, spec.to_yaml())
        return target

    def save_user_yaml(self, name: str, yaml_text: str) -> Path:
        """Persist a validated workflow YAML snapshot without normalising steps.

        The visual workflow editor owns per-step YAML. Validate first, then
        preserve the exact authored content as the saved workflow snapshot so
        its step fragments and graph ordering round-trip unchanged.
        """
        WorkflowSpec.from_yaml(yaml_text)
        target = self.user_dir / _safe_preset_filename(name)
        atomic_write_text(target, yaml_text)
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


class StepPresetStore:
    """Disk-backed library of ``{type, params}`` step YAML fragments."""

    def __init__(self) -> None:
        self._builtin_pkg = "jobdesk_app.resources.step_presets"

    @property
    def user_dir(self) -> Path:
        directory = get_app_data_dir() / "step_presets"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _builtin_root(self) -> Path:
        return Path(str(importlib.resources.files(self._builtin_pkg)))

    def _iter_builtin(self) -> list[Path]:
        root = self._builtin_root()
        return sorted(path for path in root.rglob("*.yaml") if path.is_file()) if root.exists() else []

    def _iter_user(self) -> list[Path]:
        # Listing built-ins must not fail merely because a sandboxed or
        # read-only installation cannot create the optional user folder.
        directory = get_app_data_dir() / "step_presets"
        if not directory.exists():
            return []
        return sorted(path for path in directory.rglob("*.yaml") if path.is_file())

    @staticmethod
    def _read_step(path: Path) -> dict[str, Any]:
        import yaml

        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict):
            raise ValueError(f"step preset {path.name!r} must be a YAML mapping")
        forbidden = {"name", "inputs", "global", "steps"}.intersection(value)
        if forbidden:
            raise ValueError(f"step preset {path.name!r} contains workflow-owned keys: " + ", ".join(sorted(forbidden)))
        step_type = value.get("type")
        params = value.get("params", {})
        if not isinstance(step_type, str) or not step_type.strip() or not isinstance(params, dict):
            raise ValueError(f"step preset {path.name!r} must contain type and params mapping")
        return {"type": step_type.strip(), "params": dict(params)}

    def list_presets(self) -> list[StepPreset]:
        seen: dict[str, StepPreset] = {}
        for path in self._iter_user():
            seen[path.stem] = StepPreset(path.stem, "user", path, self._read_step(path))
        for path in self._iter_builtin():
            if path.stem not in seen:
                seen[path.stem] = StepPreset(path.stem, "builtin", path, self._read_step(path))
        return list(seen.values())

    def load(self, name: str, *, source: PresetSource | None = None) -> dict[str, Any]:
        for preset in self.list_presets():
            if preset.name == name and (source is None or preset.source == source):
                return dict(preset.step)
        raise KeyError(f"Step preset {name!r} not found")

    def save_user(self, name: str, step: dict[str, Any]) -> Path:
        import yaml

        cleaned = self._read_step_from_value(step)
        target = self.user_dir / _safe_preset_filename(name)
        atomic_write_text(target, yaml.safe_dump(cleaned, sort_keys=False, allow_unicode=True))
        return target

    @staticmethod
    def _read_step_from_value(value: dict[str, Any]) -> dict[str, Any]:
        forbidden = {"name", "inputs", "global", "steps"}.intersection(value)
        if forbidden:
            raise ValueError("step presets cannot contain " + ", ".join(sorted(forbidden)))
        step_type = value.get("type")
        params = value.get("params", {})
        if not isinstance(step_type, str) or not step_type.strip() or not isinstance(params, dict):
            raise ValueError("step preset must contain a non-empty type and params mapping")
        return {"type": step_type.strip(), "params": dict(params)}


__all__ = [
    "MethodPreset",
    "MethodPresetStore",
    "PresetSource",
    "StepPreset",
    "StepPresetStore",
]
