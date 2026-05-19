"""Gaussian/ORCA input file builder.

Generates .gjf (Gaussian) or .inp (ORCA) input files from XYZ coordinates
and a method/basis specification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GaussianInputSpec:
    """Parameters for a Gaussian input file."""
    method_basis: str = "B3LYP/6-31G(d)"
    job_keywords: list[str] = field(default_factory=lambda: ["opt", "freq"])
    charge: int = 0
    multiplicity: int = 1
    nproc: int = 8
    mem: str = "16GB"
    title: str = ""
    extra_route: str = ""


@dataclass
class OrcaInputSpec:
    """Parameters for an ORCA input file."""
    keywords: str = "! B3LYP def2-TZVP opt freq"
    charge: int = 0
    multiplicity: int = 1
    nproc: int = 8
    mem_per_core_mb: int = 2000
    extra_blocks: str = ""


# ---- Method presets --------------------------------------------------------

GAUSSIAN_PRESETS: dict[str, GaussianInputSpec] = {
    "b3lyp_631gd_opt_freq": GaussianInputSpec(
        method_basis="B3LYP/6-31G(d)",
        job_keywords=["opt", "freq"],
        nproc=8, mem="16GB",
    ),
    "b3lyp_d3_def2tzvp_opt": GaussianInputSpec(
        method_basis="B3LYP/def2-TZVP EmpiricalDispersion=GD3BJ",
        job_keywords=["opt"],
        nproc=8, mem="16GB",
    ),
    "m062x_def2tzvp_opt_freq": GaussianInputSpec(
        method_basis="M062X/def2-TZVP",
        job_keywords=["opt", "freq"],
        nproc=8, mem="16GB",
    ),
    "wb97xd_def2tzvp_sp": GaussianInputSpec(
        method_basis="wB97X-D/def2-TZVP",
        job_keywords=["SP"],
        nproc=8, mem="16GB",
    ),
    "ccsd_t_ccpvtz_sp": GaussianInputSpec(
        method_basis="CCSD(T)/cc-pVTZ",
        job_keywords=["SP"],
        nproc=16, mem="32GB",
    ),
}

ORCA_PRESETS: dict[str, OrcaInputSpec] = {
    "b3lyp_def2tzvp_opt_freq": OrcaInputSpec(
        keywords="! B3LYP D3BJ def2-TZVP def2/J RIJCOSX TightSCF opt freq",
        nproc=8, mem_per_core_mb=2000,
    ),
    "dlpno_ccsd_t_sp": OrcaInputSpec(
        keywords="! DLPNO-CCSD(T) cc-pVTZ cc-pVTZ/C TightSCF",
        nproc=16, mem_per_core_mb=4000,
    ),
    "r2scan3c_opt_freq": OrcaInputSpec(
        keywords="! r2SCAN-3c opt freq",
        nproc=8, mem_per_core_mb=2000,
    ),
}


# ---- Builders --------------------------------------------------------------

def build_gjf(
    xyz_path: Path | str,
    spec: GaussianInputSpec | None = None,
    output_path: Path | str | None = None,
) -> str:
    """Build a Gaussian .gjf input file from an XYZ file.

    Args:
        xyz_path: Path to the XYZ file (first line = atom count, second = title, rest = coords).
        spec: GaussianInputSpec. Defaults to B3LYP/6-31G(d) opt freq.
        output_path: If given, write the gjf to this path.

    Returns:
        The gjf file content as a string.
    """
    if spec is None:
        spec = GaussianInputSpec()
    xyz_path = Path(xyz_path)
    atoms = _read_xyz(xyz_path)
    title = spec.title or xyz_path.stem

    route = f"# {spec.method_basis} {' '.join(spec.job_keywords)}"
    if spec.extra_route:
        route += f" {spec.extra_route}"

    lines = [
        f"%nproc={spec.nproc}",
        f"%mem={spec.mem}",
        "",
        route,
        "",
        title,
        "",
        f"{spec.charge} {spec.multiplicity}",
    ]
    for sym, x, y, z in atoms:
        lines.append(f" {sym:<2s}  {x:12.6f}  {y:12.6f}  {z:12.6f}")
    lines.append("")
    lines.append("")

    content = "\n".join(lines)
    if output_path:
        Path(output_path).write_text(content, encoding="utf-8")
    return content


def build_inp(
    xyz_path: Path | str,
    spec: OrcaInputSpec | None = None,
    output_path: Path | str | None = None,
) -> str:
    """Build an ORCA .inp input file from an XYZ file.

    Args:
        xyz_path: Path to the XYZ file.
        spec: OrcaInputSpec. Defaults to B3LYP/def2-TZVP opt freq.
        output_path: If given, write the inp to this path.

    Returns:
        The inp file content as a string.
    """
    if spec is None:
        spec = OrcaInputSpec()
    xyz_path = Path(xyz_path)
    atoms = _read_xyz(xyz_path)

    lines = [
        spec.keywords,
        "",
        f"%pal nprocs {spec.nproc} end",
        f"%maxcore {spec.mem_per_core_mb}",
        "",
    ]
    if spec.extra_blocks:
        lines.append(spec.extra_blocks)
        lines.append("")

    lines.append(f"* xyz {spec.charge} {spec.multiplicity}")
    for sym, x, y, z in atoms:
        lines.append(f"  {sym:<2s}  {x:12.6f}  {y:12.6f}  {z:12.6f}")
    lines.append("*")
    lines.append("")

    content = "\n".join(lines)
    if output_path:
        Path(output_path).write_text(content, encoding="utf-8")
    return content


def build_from_preset(
    xyz_path: Path | str,
    preset_name: str,
    output_path: Path | str | None = None,
) -> str:
    """Build an input file using a named preset.

    Automatically detects Gaussian vs ORCA from the preset name.
    """
    if preset_name in GAUSSIAN_PRESETS:
        return build_gjf(xyz_path, GAUSSIAN_PRESETS[preset_name], output_path)
    if preset_name in ORCA_PRESETS:
        return build_inp(xyz_path, ORCA_PRESETS[preset_name], output_path)
    raise ValueError(
        f"Unknown preset: {preset_name!r}. "
        f"Available: {sorted(GAUSSIAN_PRESETS) + sorted(ORCA_PRESETS)}"
    )


def list_presets() -> dict[str, str]:
    """Return all preset names with their keyword strings."""
    result = {}
    for name, spec in GAUSSIAN_PRESETS.items():
        result[name] = f"Gaussian: {spec.method_basis} {' '.join(spec.job_keywords)}"
    for name, spec in ORCA_PRESETS.items():
        result[name] = f"ORCA: {spec.keywords}"
    return result


# ---- Internal helpers ------------------------------------------------------

def _read_xyz(path: Path) -> list[tuple[str, float, float, float]]:
    """Parse an XYZ file, return list of (symbol, x, y, z)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 3:
        raise ValueError(f"XYZ file too short: {path}")
    try:
        n_atoms = int(lines[0].strip())
    except ValueError:
        raise ValueError(f"First line of XYZ must be atom count: {path}")
    atoms: list[tuple[str, float, float, float]] = []
    for line in lines[2:2 + n_atoms]:
        parts = line.split()
        if len(parts) < 4:
            continue
        atoms.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
    return atoms
