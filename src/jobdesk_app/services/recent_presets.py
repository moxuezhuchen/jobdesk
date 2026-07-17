"""Persistence for the calculation-widget "recent presets" MRU (Phase 9E-1).

Phase 9D-4 introduced an in-memory most-recently-used preset strip in the
calculator widget. Phase 9E-1 promotes it to a YAML-on-disk list so the
user's favourite picks survive restarts.

Mirrors :class:`RunProfileStore.save_command_history` for the storage
shape — a single YAML key holding the ordered list, written atomically
through :func:`atomic_write_text`.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from pathlib import Path

import yaml

from ..app_paths import get_app_data_dir
from ..core.atomic_write import atomic_write_text

_LOG = logging.getLogger(__name__)

_KEY = "recent_presets"


class PresetFavouriteStore:
    """Disk-backed store for the most-recently-used calculation presets.

    Each wizard / calculator instance reads its MRU from disk on
    construction and writes back after each pick. Reads never block or
    raise (a missing / corrupted file yields an empty list, which the
    widget treats as "first run"). Writes silently skip on permission
    errors so a misconfigured home directory never crashes the wizard.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else get_app_data_dir() / "recent_presets.yaml"

    def load(self) -> list[str]:
        """Return the saved MRU, most-recent-first. Never raises."""
        if not self.path.exists():
            return []
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            _LOG.warning("recent_presets: failed to read %s: %s", self.path, exc)
            return []
        values = raw.get(_KEY, []) or []
        if not isinstance(values, list):
            return []
        return [str(v) for v in values if v]

    def save(self, presets: "OrderedDict[str, None] | list[str]") -> None:
        """Persist ``presets`` atomically. Never raises.

        Accepts either an ``OrderedDict`` (the widget's in-memory shape)
        or a plain list. Order is preserved most-recent-first.
        """
        if isinstance(presets, OrderedDict):
            values = list(presets.keys())
        else:
            values = list(presets)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self.path,
                yaml.safe_dump({_KEY: values}, sort_keys=True, allow_unicode=False),
            )
        except OSError as exc:
            _LOG.warning("recent_presets: failed to write %s: %s", self.path, exc)

    def clear(self) -> None:
        """Remove the on-disk MRU. Best-effort; never raises."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            _LOG.warning("recent_presets: failed to clear %s: %s", self.path, exc)
