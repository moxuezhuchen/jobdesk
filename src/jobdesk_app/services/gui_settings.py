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
    last_server_id: str = ""
    last_remote_dirs: dict[str, str] | None = None  # server_id -> last remote path
    auto_connect: bool = True
    overwrite_policy: str = "skip_same_size"
    command_template: str = "bash {name}"
    max_parallel: int = 4
    batch_size: int = 0
    language: str = "en"
    column_widths: dict[str, list[int]] | None = None
    window_size: list[int] | None = None
    # Runs page state
    auto_refresh_enabled: bool = False
    auto_refresh_interval: int = 30
    auto_download_enabled: bool = False
    notify_enabled: bool = False
    download_patterns: str = "result.log, output.log, .jobdesk_submit.log"
    hide_dotfiles: bool = True
    # Per-software download patterns
    software_download_patterns: dict[str, str] | None = None  # e.g. {"Gaussian": "*.log,*.chk", "ORCA": "*.out,*.gbw"}

    def __post_init__(self):
        if self.column_widths is None:
            object.__setattr__(self, "column_widths", {})
        if self.last_remote_dirs is None:
            object.__setattr__(self, "last_remote_dirs", {})
        if self.software_download_patterns is None:
            object.__setattr__(self, "software_download_patterns", {
                "Gaussian": "*.log,*.chk",
                "ORCA": "*.out,*.gbw",
            })


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
            last_server_id=str(raw.get("last_server_id", "")),
            last_remote_dirs=dict(raw.get("last_remote_dirs", {}) or {}),
            auto_connect=bool(raw.get("auto_connect", True)),
            overwrite_policy=str(raw.get("overwrite_policy", "skip_same_size")),
            command_template=str(raw.get("command_template", "bash {name}") or "bash {name}"),
            max_parallel=max(1, int(raw.get("max_parallel", 4) or 4)),
            batch_size=max(0, int(raw.get("batch_size", 0) or 0)),
            language=str(raw.get("language", "en") or "en"),
            column_widths=dict(raw.get("column_widths", {}) or {}),
            window_size=raw.get("window_size"),
            auto_refresh_enabled=bool(raw.get("auto_refresh_enabled", False)),
            auto_refresh_interval=max(10, int(raw.get("auto_refresh_interval", 30) or 30)),
            auto_download_enabled=bool(raw.get("auto_download_enabled", False)),
            notify_enabled=bool(raw.get("notify_enabled", False)),
            download_patterns=str(raw.get("download_patterns", "result.log, output.log, .jobdesk_submit.log")),
            hide_dotfiles=bool(raw.get("hide_dotfiles", True)),
            software_download_patterns=dict(raw.get("software_download_patterns", {}) or {}),
        )

    def save(self, settings: GuiSettings) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "default_local_folder": settings.default_local_folder,
            "last_local_folder": settings.last_local_folder,
            "default_remote_dir": settings.default_remote_dir,
            "default_server_id": settings.default_server_id,
            "last_server_id": settings.last_server_id,
            "last_remote_dirs": settings.last_remote_dirs or {},
            "auto_connect": settings.auto_connect,
            "overwrite_policy": settings.overwrite_policy,
            "command_template": settings.command_template,
            "max_parallel": settings.max_parallel,
            "batch_size": settings.batch_size,
            "language": settings.language,
            "column_widths": settings.column_widths or {},
            "window_size": settings.window_size,
            "auto_refresh_enabled": settings.auto_refresh_enabled,
            "auto_refresh_interval": settings.auto_refresh_interval,
            "auto_download_enabled": settings.auto_download_enabled,
            "notify_enabled": settings.notify_enabled,
            "download_patterns": settings.download_patterns,
            "hide_dotfiles": settings.hide_dotfiles,
            "software_download_patterns": settings.software_download_patterns or {},
        }
        self.path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return self.path
