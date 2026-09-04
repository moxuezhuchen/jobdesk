"""Load and validate the user-level ``servers.yaml`` file."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ...core.configuration import ServersConfig


def get_default_servers_path() -> Path:
    """Return ``%APPDATA%/JobDesk/servers.yaml`` with the legacy fallback."""

    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return Path(appdata) / "JobDesk" / "servers.yaml"


def load_servers(path: Path | str | None = None) -> ServersConfig:
    """Load the existing YAML shape and validate it as :class:`ServersConfig`."""

    resolved_path = get_default_servers_path() if path is None else Path(path)
    if not resolved_path.exists():
        raise FileNotFoundError(f"servers.yaml 不存在: {resolved_path}")

    raw = yaml.safe_load(resolved_path.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"servers.yaml 为空: {resolved_path}")
    return ServersConfig(**raw)


__all__ = ["get_default_servers_path", "load_servers"]
