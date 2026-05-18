from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..app_paths import get_app_data_dir


@dataclass
class AnalysisProfile:
    extract_rules: list[dict]


class AnalysisProfileStore:
    def __init__(self, base_dir: Path | None = None):
        self._base = base_dir or get_app_data_dir() / "analysis_profiles"

    def load(self, run_id: str) -> AnalysisProfile | None:
        path = self._base / f"{run_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return AnalysisProfile(extract_rules=data.get("extract_rules", []))

    def save(self, run_id: str, profile: AnalysisProfile) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        path = self._base / f"{run_id}.json"
        path.write_text(json.dumps({"extract_rules": profile.extract_rules}, indent=2, ensure_ascii=False), encoding="utf-8")
