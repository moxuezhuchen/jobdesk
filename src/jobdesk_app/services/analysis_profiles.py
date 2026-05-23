"""Analysis profiles: built-in and user-defined extract rule sets.

Built-in profiles cover common Gaussian/ORCA output patterns.
Users can also define custom profiles stored in %APPDATA%/JobDesk/analysis_profiles/.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..app_paths import get_app_data_dir
from ..config.schema import ExtractResult, ExtractStrategy, ExtractType
from ..core.atomic_write import atomic_write_text

logger = logging.getLogger(__name__)


@dataclass
class AnalysisProfile:
    name: str
    description: str
    extract_rules: list[ExtractResult]


# ---- Built-in profiles -----------------------------------------------------

BUILTIN_PROFILES: dict[str, AnalysisProfile] = {
    "gaussian_sp": AnalysisProfile(
        name="gaussian_sp",
        description="Gaussian single-point energy",
        extract_rules=[
            ExtractResult(
                name="scf_energy",
                source_glob="*.log",
                regex=r"SCF Done:\s+E\(\S+\)\s*=\s*(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
        ],
    ),
    "gaussian_opt_freq": AnalysisProfile(
        name="gaussian_opt_freq",
        description="Gaussian opt+freq: energy, ZPE, H, G, imaginary frequencies",
        extract_rules=[
            ExtractResult(
                name="scf_energy",
                source_glob="*.log",
                regex=r"SCF Done:\s+E\(\S+\)\s*=\s*(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
            ExtractResult(
                name="zpe",
                source_glob="*.log",
                regex=r"Zero-point correction=\s*(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
            ExtractResult(
                name="enthalpy_correction",
                source_glob="*.log",
                regex=r"Thermal correction to Enthalpy=\s*(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
            ExtractResult(
                name="gibbs_correction",
                source_glob="*.log",
                regex=r"Thermal correction to Gibbs Free Energy=\s*(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
            ExtractResult(
                name="imaginary_freq_count",
                source_glob="*.log",
                regex=r"Frequencies --\s+(?P<value>-[\d.]+)",
                strategy=ExtractStrategy.all,
                type=ExtractType.float,
                unit="cm-1",
            ),
        ],
    ),
    "orca_sp": AnalysisProfile(
        name="orca_sp",
        description="ORCA single-point energy",
        extract_rules=[
            ExtractResult(
                name="final_energy",
                source_glob="*.out",
                regex=r"FINAL SINGLE POINT ENERGY\s+(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
        ],
    ),
    "orca_opt_freq": AnalysisProfile(
        name="orca_opt_freq",
        description="ORCA opt+freq: energy, ZPE, H, G",
        extract_rules=[
            ExtractResult(
                name="final_energy",
                source_glob="*.out",
                regex=r"FINAL SINGLE POINT ENERGY\s+(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
            ExtractResult(
                name="zpe",
                source_glob="*.out",
                regex=r"Zero point energy\s*\.\.\.\s*(?P<value>[-\d.]+)\s*Eh",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
            ExtractResult(
                name="gibbs",
                source_glob="*.out",
                regex=r"Final Gibbs free energy\s*\.\.\.\s*(?P<value>[-\d.]+)\s*Eh",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
        ],
    ),
    "orca_dlpno_ccsd_t": AnalysisProfile(
        name="orca_dlpno_ccsd_t",
        description="ORCA DLPNO-CCSD(T) energy",
        extract_rules=[
            ExtractResult(
                name="hf_energy",
                source_glob="*.out",
                regex=r"FINAL SINGLE POINT ENERGY\s+(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.first,
                type=ExtractType.float,
                unit="Hartree",
            ),
            ExtractResult(
                name="ccsd_t_energy",
                source_glob="*.out",
                regex=r"FINAL SINGLE POINT ENERGY\s+(?P<value>[-\d.]+)",
                strategy=ExtractStrategy.last,
                type=ExtractType.float,
                unit="Hartree",
            ),
        ],
    ),
}


# ---- User profile store ----------------------------------------------------

class AnalysisProfileStore:
    """Load/save user-defined analysis profiles from disk."""

    def __init__(self, base_dir: Path | None = None):
        self._base = base_dir or get_app_data_dir() / "analysis_profiles"

    def list_profiles(self) -> dict[str, AnalysisProfile]:
        """Return all profiles: built-ins + user-defined (user overrides built-in)."""
        profiles = dict(BUILTIN_PROFILES)
        if self._base.exists():
            for f in sorted(self._base.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    rules = [ExtractResult(**r) for r in data.get("extract_rules", [])]
                    profiles[f.stem] = AnalysisProfile(
                        name=f.stem,
                        description=data.get("description", ""),
                        extract_rules=rules,
                    )
                except Exception as exc:
                    logger.warning("Skipping invalid analysis profile %s: %s", f, exc)
        return profiles

    def get(self, name: str) -> AnalysisProfile | None:
        return self.list_profiles().get(name)

    def save(self, profile: AnalysisProfile) -> None:
        self._validate_name(profile.name)
        self._base.mkdir(parents=True, exist_ok=True)
        data = {
            "description": profile.description,
            "extract_rules": [r.model_dump() for r in profile.extract_rules],
        }
        atomic_write_text(
            self._base / f"{profile.name}.json",
            json.dumps(data, indent=2, ensure_ascii=False),
        )

    def delete(self, name: str) -> None:
        self._validate_name(name)
        path = self._base / f"{name}.json"
        path.unlink(missing_ok=True)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError(f"invalid profile name: {name}")
