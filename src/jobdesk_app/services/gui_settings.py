from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..app_paths import get_app_data_dir


@dataclass(frozen=True)
class GuiSettings:
    default_local_folder: str = ""
    last_local_folder: str = ""
    default_remote_dir: str = "/tmp"
    default_server_id: str = ""
    auto_connect: bool = True
    overwrite_policy: str = "skip_same_size"
    command_template: str = "bash {name}"
    max_parallel: int = 4
    batch_size: int = 0
    language: str = "en"
    column_widths: dict[str, list[int]] | None = None
    window_size: list[int] | None = None

    def __post_init__(self):
        if self.column_widths is None:
            object.__setattr__(self, "column_widths", {})


class GuiSettingsStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else get_app_data_dir() / "gui_settings.yaml"

    def load(self) -> GuiSettings:
        if not self.path.exists():
            return GuiSettings()
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return GuiSettings(
            default_local_folder=str(raw.get("default_local_folder", "")),
            last_local_folder=str(raw.get("last_local_folder", "")),
            default_remote_dir=str(raw.get("default_remote_dir", "/tmp") or "/tmp"),
            default_server_id=str(raw.get("default_server_id", "")),
            auto_connect=bool(raw.get("auto_connect", True)),
            overwrite_policy=str(raw.get("overwrite_policy", "skip_same_size")),
            command_template=str(raw.get("command_template", "bash {name}") or "bash {name}"),
            max_parallel=max(1, int(raw.get("max_parallel", 4) or 4)),
            batch_size=max(0, int(raw.get("batch_size", 0) or 0)),
            language=str(raw.get("language", "en") or "en"),
            column_widths=dict(raw.get("column_widths", {}) or {}),
            window_size=raw.get("window_size"),
        )

    def save(self, settings: GuiSettings) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "default_local_folder": settings.default_local_folder,
            "last_local_folder": settings.last_local_folder,
            "default_remote_dir": settings.default_remote_dir,
            "default_server_id": settings.default_server_id,
            "auto_connect": settings.auto_connect,
            "overwrite_policy": settings.overwrite_policy,
            "command_template": settings.command_template,
            "max_parallel": settings.max_parallel,
            "batch_size": settings.batch_size,
            "language": settings.language,
            "column_widths": settings.column_widths or {},
            "window_size": settings.window_size,
        }
        self.path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return self.path
