"""Gaussian/ORCA input file builder.

Generates .gjf (Gaussian) or .inp (ORCA) input files from XYZ coordinates
and a method/basis specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
        nproc=8,
        mem="16GB",
    ),
    "b3lyp_d3_def2tzvp_opt": GaussianInputSpec(
        method_basis="B3LYP/def2-TZVP EmpiricalDispersion=GD3BJ",
        job_keywords=["opt"],
        nproc=8,
        mem="16GB",
    ),
    "m062x_def2tzvp_opt_freq": GaussianInputSpec(
        method_basis="M062X/def2-TZVP",
        job_keywords=["opt", "freq"],
        nproc=8,
        mem="16GB",
    ),
    "wb97xd_def2tzvp_sp": GaussianInputSpec(
        method_basis="wB97X-D/def2-TZVP",
        job_keywords=["SP"],
        nproc=8,
        mem="16GB",
    ),
    "ccsd_t_ccpvtz_sp": GaussianInputSpec(
        method_basis="CCSD(T)/cc-pVTZ",
        job_keywords=["SP"],
        nproc=16,
        mem="32GB",
    ),
}

ORCA_PRESETS: dict[str, OrcaInputSpec] = {
    "b3lyp_def2tzvp_opt_freq": OrcaInputSpec(
        keywords="! B3LYP D3BJ def2-TZVP def2/J RIJCOSX TightSCF opt freq",
        nproc=8,
        mem_per_core_mb=2000,
    ),
    "dlpno_ccsd_t_sp": OrcaInputSpec(
        keywords="! DLPNO-CCSD(T) cc-pVTZ cc-pVTZ/C TightSCF",
        nproc=16,
        mem_per_core_mb=4000,
    ),
    "r2scan3c_opt_freq": OrcaInputSpec(
        keywords="! r2SCAN-3c opt freq",
        nproc=8,
        mem_per_core_mb=2000,
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
    raise ValueError(f"Unknown preset: {preset_name!r}. Available: {sorted(GAUSSIAN_PRESETS) + sorted(ORCA_PRESETS)}")


def list_presets() -> dict[str, str]:
    """Return all preset names with their keyword strings."""
    result = {}
    for name, gspec in GAUSSIAN_PRESETS.items():
        result[name] = f"Gaussian: {gspec.method_basis} {' '.join(gspec.job_keywords)}"
    for name, ospec in ORCA_PRESETS.items():
        result[name] = f"ORCA: {ospec.keywords}"
    return result


def preset_to_confflow_fields(preset_name: str) -> dict[str, Any]:
    """Map a preset name to the wizard's form-friendly fields.

    ConfFlow's YAML model accepts ``method`` + ``basis`` as separate
    strings; the wizard's preset dropdown uses the legacy
    :data:`GAUSSIAN_PRESETS` / :data:`ORCA_PRESETS` keyed by name.  This
    converter splits each preset back into method/basis so the wizard
    can drop the preset selection into the workflow.yaml.

    Returns a dict with keys ``method``, ``basis``, ``nproc``,
    ``memory_mb`` (always present; ``nproc``/``memory_mb`` default to
    the preset's resources, ``method``/``basis`` are empty strings if
    no preset matches so the wizard leaves the text fields alone).
    """
    empty: dict[str, Any] = {
        "method": "",
        "basis": "",
        "nproc": 1,
        "memory_mb": 1024,
    }
    if preset_name in GAUSSIAN_PRESETS:
        spec = GAUSSIAN_PRESETS[preset_name]
        # Gaussian ``method_basis`` is "METHOD/BASIS" or "METHOD/BASIS ExtraDispersion=..."
        mb = spec.method_basis.strip()
        if "/" in mb:
            method, basis = mb.split("/", 1)
        else:
            method, basis = mb, ""
        return {
            "method": method.strip(),
            "basis": basis.strip(),
            "nproc": spec.nproc,
            "memory_mb": _mem_to_mb(spec.mem),
        }
    if preset_name in ORCA_PRESETS:
        orca_spec = ORCA_PRESETS[preset_name]
        # ORCA ``keywords`` starts with ``!``, then tokens like "B3LYP D3BJ def2-TZVP def2/J Opt".
        tokens = orca_spec.keywords.replace("!", "").split()
        method, basis = _split_orca_method_basis(tokens)
        return {
            "method": method,
            "basis": basis,
            "nproc": orca_spec.nproc,
            "memory_mb": int(orca_spec.mem_per_core_mb),
        }
    return empty


def _mem_to_mb(mem_str: str) -> int:
    """Parse a memory string like '16GB', '32GB', '1024MB' into MB."""
    s = mem_str.strip().upper().replace(" ", "")
    if s.endswith("GB"):
        try:
            return int(float(s[:-2]) * 1024)
        except ValueError:
            return 1024
    if s.endswith("MB"):
        try:
            return int(float(s[:-2]))
        except ValueError:
            return 1024
    try:
        return int(s)
    except ValueError:
        return 1024


# Tokens that look like basis-set names or auxiliary basis names — used by
# :func:`_split_orca_method_basis` to split an ORCA keyword line.
_ORCA_BASIS_TOKENS = {
    "def2-SVP",
    "def2-TZVP",
    "def2-QZVP",
    "def2-SV(P)",
    "def2-TZVPP",
    "def2-TZVPD",
    "def2/J",
    "def2-SVP/C",
    "def2-TZVP/C",
    "cc-pVDZ",
    "cc-pVTZ",
    "cc-pVQZ",
    "aug-cc-pVDZ",
    "aug-cc-pVTZ",
    "aug-cc-pVQZ",
    "cc-pVDZ/C",
    "cc-pVTZ/C",
    "cc-pVQZ/C",
    "MINIS",
    "MINAO",
}


def _split_orca_method_basis(tokens: list[str]) -> tuple[str, str]:
    """Best-effort split of ORCA tokens into ``(method, basis)`` for the wizard.

    The ORCA keyword line is a free-form bag of tokens: method, dispersion,
    basis, auxiliary basis, RI flags, SCF tightness, job keywords.  We pick
    the *last* token that looks like a basis set as ``basis``; everything
    before that (until the first job keyword like ``Opt``) is the method.
    Anything else (dispersion, RI flags, …) is included in the method string.
    """
    job_keywords = {
        "Opt",
        "SP",
        "Freq",
        "NumFreq",
        "TS",
        "OptTS",
        "TightSCF",
        "LooseSCF",
        "NormalSCF",
        "MiniPrint",
        "NormalPrint",
        "RIJCOSX",
        "RI",
        "RIJK",
        "RI-MP2",
        # Dispersion flags (D3BJ etc.) are NOT job keywords — they describe
        # the method. Keep them out of this set so they end up in the method
        # string the wizard sends to ConfFlow.
    }
    method_tokens: list[str] = []
    basis_tokens: list[str] = []
    seen_basis = False
    for tok in tokens:
        if tok in job_keywords:
            break
        if tok in _ORCA_BASIS_TOKENS or "/" in tok:
            seen_basis = True
            basis_tokens.append(tok)
            continue
        if seen_basis:
            # Tokens after the basis are still auxiliary basis or job tokens.
            basis_tokens.append(tok)
            continue
        method_tokens.append(tok)
    return " ".join(method_tokens), " ".join(basis_tokens)


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
    if n_atoms <= 0:
        raise ValueError(f"XYZ atom count must be positive: {path}")

    coordinate_lines = lines[2:]
    while coordinate_lines and not coordinate_lines[-1].strip():
        coordinate_lines.pop()
    if len(coordinate_lines) != n_atoms:
        raise ValueError(
            f"XYZ file {path} declares {n_atoms} atoms but contains {len(coordinate_lines)} coordinate rows"
        )

    atoms: list[tuple[str, float, float, float]] = []
    for row_number, line in enumerate(coordinate_lines, start=1):
        parts = line.split()
        if len(parts) != 4:
            raise ValueError(f"XYZ file {path} has invalid coordinate row {row_number}: {line!r}")
        try:
            atoms.append((parts[0], float(parts[1]), float(parts[2]), float(parts[3])))
        except ValueError as exc:
            raise ValueError(f"XYZ file {path} has invalid coordinate row {row_number}: {line!r}") from exc
    return atoms
