from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..app_paths import get_app_data_dir


@dataclass(frozen=True)
class RunProfile:
    server_id: str
    remote_dir: str
    command_template: str
    max_parallel: int
    download_patterns: list[str]


class RunProfileStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else get_app_data_dir() / "run_profiles.yaml"

    def save_last(
        self,
        server_id: str,
        remote_dir: str,
        command_template: str,
        max_parallel: int,
        download_patterns: list[str] | None = None,
    ) -> None:
        data = self._read()
        key = _key(server_id, remote_dir)
        data[key] = {
            "server_id": server_id,
            "remote_dir": remote_dir,
            "command_template": command_template,
            "max_parallel": max_parallel,
            "download_patterns": download_patterns or [],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump({"profiles": data}, sort_keys=True), encoding="utf-8")

    def load_last(self, server_id: str, remote_dir: str) -> RunProfile | None:
        data = self._read().get(_key(server_id, remote_dir))
        if not data:
            return None
        return RunProfile(
            server_id=data["server_id"],
            remote_dir=data["remote_dir"],
            command_template=data["command_template"],
            max_parallel=int(data.get("max_parallel", 1)),
            download_patterns=list(data.get("download_patterns", [])),
        )

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        return raw.get("profiles", {}) or {}


def _key(server_id: str, remote_dir: str) -> str:
    return f"{server_id}|{remote_dir.rstrip('/') or '/'}"
