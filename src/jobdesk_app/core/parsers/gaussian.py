"""Gaussian output log parser.

Extracts energies, thermochemistry, frequencies, geometry, and error info
from Gaussian 09/16 .log files without any external dependencies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GaussianResult:
    """Parsed data from a Gaussian log file."""

    # Convergence
    converged: bool = False                     # Stationary point found
    normal_termination: bool = False            # Normal termination of Gaussian

    # Energies (Hartree)
    scf_energies: list[float] = field(default_factory=list)   # all SCF Done values
    final_energy_au: float | None = None        # last SCF Done

    # Thermochemistry (Hartree, at printed temperature)
    zpe_au: float | None = None
    thermal_energy_au: float | None = None      # E (thermal)
    enthalpy_au: float | None = None            # H
    gibbs_au: float | None = None               # G
    thermo_temperature_k: float | None = None

    # Frequencies
    frequencies_cm1: list[float] = field(default_factory=list)
    imaginary_freq_count: int = 0

    # Geometry (last Standard orientation block)
    final_xyz: str | None = None                # XYZ format string (no header)
    atom_symbols: list[str] = field(default_factory=list)

    # Charges
    mulliken_charges: dict[int, float] = field(default_factory=dict)  # 1-indexed

    # Error info
    error_termination: bool = False
    error_message: str | None = None            # first recognized error line

    # Timing
    cpu_time_seconds: float | None = None
    walltime_seconds: float | None = None


# ---- Regex patterns --------------------------------------------------------

_RE_SCF = re.compile(r"SCF Done:\s+E\(\S+\)\s*=\s*([-\d.]+)")
_RE_CONVERGED = re.compile(r"Stationary point found")
_RE_NORMAL = re.compile(r"Normal termination of Gaussian")
_RE_ERROR = re.compile(r"Error termination")
_RE_ZPE = re.compile(r"Zero-point correction=\s*([-\d.]+)")
_RE_THERMAL = re.compile(r"Thermal correction to Energy=\s*([-\d.]+)")
_RE_ENTHALPY = re.compile(r"Thermal correction to Enthalpy=\s*([-\d.]+)")
_RE_GIBBS = re.compile(r"Thermal correction to Gibbs Free Energy=\s*([-\d.]+)")
_RE_TEMP = re.compile(r"Temperature\s+([\d.]+)\s+Kelvin")
_RE_FREQ = re.compile(r"Frequencies --\s+([\d.\s-]+)")
_RE_MULLIKEN_HEADER = re.compile(r"Mulliken charges( and spin densities)?:")
_RE_MULLIKEN_ROW = re.compile(r"^\s+(\d+)\s+\w+\s+([-\d.]+)")
_RE_CPU = re.compile(r"Job cpu time:\s+(\d+)\s+days\s+(\d+)\s+hours\s+(\d+)\s+minutes\s+([\d.]+)\s+seconds")
_RE_WALL = re.compile(r"Elapsed time:\s+(\d+)\s+days\s+(\d+)\s+hours\s+(\d+)\s+minutes\s+([\d.]+)\s+seconds")
_RE_STD_ORI_HEADER = re.compile(r"Standard orientation:")
_RE_ATOM_ROW = re.compile(r"^\s+\d+\s+(\d+)\s+\d+\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")

_ATOMIC_SYMBOLS = {
    1: "H", 2: "He", 3: "Li", 4: "Be", 5: "B", 6: "C", 7: "N", 8: "O",
    9: "F", 10: "Ne", 11: "Na", 12: "Mg", 13: "Al", 14: "Si", 15: "P",
    16: "S", 17: "Cl", 18: "Ar", 19: "K", 20: "Ca", 21: "Sc", 22: "Ti",
    23: "V", 24: "Cr", 25: "Mn", 26: "Fe", 27: "Co", 28: "Ni", 29: "Cu",
    30: "Zn", 31: "Ga", 32: "Ge", 33: "As", 34: "Se", 35: "Br", 36: "Kr",
    37: "Rb", 38: "Sr", 39: "Y", 40: "Zr", 41: "Nb", 42: "Mo", 43: "Tc",
    44: "Ru", 45: "Rh", 46: "Pd", 47: "Ag", 48: "Cd", 49: "In", 50: "Sn",
    51: "Sb", 52: "Te", 53: "I", 54: "Xe", 55: "Cs", 56: "Ba", 57: "La",
    58: "Ce", 59: "Pr", 60: "Nd", 61: "Pm", 62: "Sm", 63: "Eu", 64: "Gd",
    65: "Tb", 66: "Dy", 67: "Ho", 68: "Er", 69: "Tm", 70: "Yb", 71: "Lu",
    72: "Hf", 73: "Ta", 74: "W", 75: "Re", 76: "Os", 77: "Ir", 78: "Pt",
    79: "Au", 80: "Hg", 81: "Tl", 82: "Pb", 83: "Bi", 84: "Po", 85: "At",
    86: "Rn", 87: "Fr", 88: "Ra", 89: "Ac", 90: "Th", 91: "Pa", 92: "U",
    93: "Np", 94: "Pu", 95: "Am", 96: "Cm", 97: "Bk", 98: "Cf", 99: "Es",
    100: "Fm", 101: "Md", 102: "No", 103: "Lr", 104: "Rf", 105: "Db",
    106: "Sg", 107: "Bh", 108: "Hs", 109: "Mt", 110: "Ds", 111: "Rg",
    112: "Cn", 113: "Nh", 114: "Fl", 115: "Mc", 116: "Lv", 117: "Ts", 118: "Og",
}

_KNOWN_ERRORS = [
    "Convergence failure",
    "Convergence criterion not met",
    "Negative curvature in",
    "l9999.exe",
    "Erroneous write",
    "No space left on device",
    "Out of memory",
    "galloc:",
    "Z-matrix not found",
    "Atomic number out of range",
    "FormBX had a problem",
    "Inaccurate quadrature",
    "Singular matrix",
]


def parse_gaussian_log(path: Path | str) -> GaussianResult:
    """Parse a Gaussian log file and return a GaussianResult."""
    result = GaussianResult()
    path = Path(path)
    if not path.exists():
        return result

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # SCF energies
    for m in _RE_SCF.finditer(text):
        result.scf_energies.append(float(m.group(1)))
    if result.scf_energies:
        result.final_energy_au = result.scf_energies[-1]

    # Fallback: extract from archive HF= field (archive wraps lines)
    if result.final_energy_au is None:
        # Remove newlines within archive section for robust parsing
        archive_text = re.sub(r"\n ", "", text)
        hf_match = re.search(r"HF=([-\d.]+)", archive_text)
        if hf_match:
            result.final_energy_au = float(hf_match.group(1))

    # Convergence / termination
    result.converged = bool(_RE_CONVERGED.search(text))
    result.normal_termination = bool(_RE_NORMAL.search(text))
    result.error_termination = bool(_RE_ERROR.search(text))

    # Error message
    for err in _KNOWN_ERRORS:
        if err in text:
            result.error_message = err
            break

    # Thermochemistry
    m = _RE_ZPE.search(text)
    if m:
        result.zpe_au = float(m.group(1))
    m = _RE_THERMAL.search(text)
    if m:
        result.thermal_energy_au = float(m.group(1))
    m = _RE_ENTHALPY.search(text)
    if m:
        result.enthalpy_au = float(m.group(1))
    m = _RE_GIBBS.search(text)
    if m:
        result.gibbs_au = float(m.group(1))
    m = _RE_TEMP.search(text)
    if m:
        result.thermo_temperature_k = float(m.group(1))

    # Frequencies
    for m in _RE_FREQ.finditer(text):
        for val in m.group(1).split():
            try:
                result.frequencies_cm1.append(float(val))
            except ValueError:
                pass
    result.imaginary_freq_count = sum(1 for f in result.frequencies_cm1 if f < 0)

    # Geometry: last Standard orientation block
    std_ori_positions = [m.start() for m in _RE_STD_ORI_HEADER.finditer(text)]
    if std_ori_positions:
        last_block_start = std_ori_positions[-1]
        block_text = text[last_block_start:]
        atoms: list[tuple[str, float, float, float]] = []
        dash_count = 0
        for line in block_text.splitlines():
            if "---" in line:
                dash_count += 1
                if dash_count == 3:
                    break
                continue
            if dash_count == 2:
                m = _RE_ATOM_ROW.match(line)
                if m:
                    atomic_num = int(m.group(1))
                    sym = _ATOMIC_SYMBOLS.get(atomic_num, f"X{atomic_num}")
                    atoms.append((sym, float(m.group(2)), float(m.group(3)), float(m.group(4))))
        if atoms:
            result.atom_symbols = [a[0] for a in atoms]
            xyz_lines = [f"{a[0]:2s}  {a[1]:12.6f}  {a[2]:12.6f}  {a[3]:12.6f}" for a in atoms]
            result.final_xyz = "\n".join(xyz_lines)

    # Mulliken charges
    in_mulliken = False
    skip_next = False
    for line in lines:
        if _RE_MULLIKEN_HEADER.search(line):
            in_mulliken = True
            skip_next = True  # next line is column numbers
            continue
        if skip_next:
            skip_next = False
            continue
        if in_mulliken:
            m = _RE_MULLIKEN_ROW.match(line)
            if m:
                result.mulliken_charges[int(m.group(1))] = float(m.group(2))
            elif line.strip() and not line.strip().startswith("Sum"):
                in_mulliken = False

    # Timing
    m = _RE_CPU.search(text)
    if m:
        d, h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
        result.cpu_time_seconds = d * 86400 + h * 3600 + mi * 60 + s
    m = _RE_WALL.search(text)
    if m:
        d, h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4))
        result.walltime_seconds = d * 86400 + h * 3600 + mi * 60 + s

    return result


def diagnose_gaussian_result(result: GaussianResult) -> str | None:
    """Return a human-readable error diagnosis from an already-parsed result, or None if clean."""
    if result.normal_termination and result.converged:
        return None
    if result.error_message:
        return result.error_message
    if result.error_termination:
        return "Error termination (unknown cause)"
    if not result.normal_termination and result.scf_energies:
        return "Abnormal termination (job may have been killed or timed out)"
    return None


def diagnose_gaussian(path: Path | str) -> str | None:
    """Return a human-readable error diagnosis for a Gaussian log, or None if clean."""
    return diagnose_gaussian_result(parse_gaussian_log(path))
