"""ORCA output parser.

Extracts energies, thermochemistry, frequencies, geometry, and error info
from ORCA .out files without any external dependencies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OrcaResult:
    """Parsed data from an ORCA output file."""

    # Convergence
    converged: bool = False
    normal_termination: bool = False

    # Energies (Hartree)
    scf_energies: list[float] = field(default_factory=list)
    final_energy_au: float | None = None
    # DLPNO-CCSD(T) / MP2 / etc.
    correlation_energy_au: float | None = None
    total_energy_au: float | None = None        # final total energy (may differ from SCF)

    # Thermochemistry
    zpe_au: float | None = None
    enthalpy_au: float | None = None
    gibbs_au: float | None = None
    thermo_temperature_k: float | None = None

    # Frequencies
    frequencies_cm1: list[float] = field(default_factory=list)
    imaginary_freq_count: int = 0

    # Geometry (last geometry block)
    final_xyz: str | None = None
    atom_symbols: list[str] = field(default_factory=list)

    # Charges
    mulliken_charges: dict[int, float] = field(default_factory=dict)

    # Error info
    error_termination: bool = False
    error_message: str | None = None

    # Timing
    walltime_seconds: float | None = None


_RE_SCF = re.compile(r"Total Energy\s*:\s*([-\d.]+)\s*Eh")
_RE_SCF_ALT = re.compile(r"FINAL SINGLE POINT ENERGY\s+([-\d.]+)")
_RE_DLPNO = re.compile(r"FINAL CORRELATION ENERGY\s+([-\d.]+)")
_RE_TOTAL = re.compile(r"Total Energy\s*=\s*([-\d.]+)")
_RE_CONVERGED = re.compile(r"OPTIMIZATION RUN DONE|THE OPTIMIZATION HAS CONVERGED")
_RE_NORMAL = re.compile(r"ORCA TERMINATED NORMALLY")
_RE_ERROR = re.compile(r"ORCA finished with error|Error in")
_RE_ZPE = re.compile(r"Zero point energy\s*\.\.\.\s*([-\d.]+)\s*Eh")
_RE_ENTHALPY = re.compile(r"Total enthalpy\s*\.\.\.\s*([-\d.]+)\s*Eh")
_RE_GIBBS = re.compile(r"Final Gibbs free energy\s*\.\.\.\s*([-\d.]+)\s*Eh")
_RE_TEMP = re.compile(r"Temperature\s*\.\.\.\s*([\d.]+)\s*K")
_RE_FREQ = re.compile(r"(\d+):\s+([-\d.]+)\s+cm\*\*-1")
_RE_WALL = re.compile(r"TOTAL RUN TIME:\s+(\d+)\s+days\s+(\d+)\s+hours\s+(\d+)\s+minutes\s+(\d+)\s+seconds")
_RE_COORD_HEADER = re.compile(r"CARTESIAN COORDINATES \(ANGSTROEM\)")
_RE_COORD_ROW = re.compile(r"^\s+(\w+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)")
_RE_MULLIKEN_HEADER = re.compile(r"MULLIKEN ATOMIC CHARGES")
_RE_MULLIKEN_ROW = re.compile(r"^\s+(\d+)\s+\w+\s*:\s*([-\d.]+)")

_KNOWN_ERRORS = [
    "SCF NOT CONVERGED",
    "ORCA finished with error",
    "Error in",
    "DIIS failed",
    "Geometry optimization did not converge",
    "No space left on device",
    "Out of memory",
]


def parse_orca_out(path: Path | str) -> OrcaResult:
    """Parse an ORCA output file and return an OrcaResult."""
    result = OrcaResult()
    path = Path(path)
    if not path.exists():
        return result

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # SCF energies
    for m in _RE_SCF_ALT.finditer(text):
        result.scf_energies.append(float(m.group(1)))
    if result.scf_energies:
        result.final_energy_au = result.scf_energies[-1]

    # Correlation / total energy
    m = _RE_DLPNO.search(text)
    if m:
        result.correlation_energy_au = float(m.group(1))

    # Convergence / termination
    result.converged = bool(_RE_CONVERGED.search(text))
    result.normal_termination = bool(_RE_NORMAL.search(text))
    result.error_termination = bool(_RE_ERROR.search(text))

    for err in _KNOWN_ERRORS:
        if err in text:
            result.error_message = err
            break

    # Thermochemistry
    m = _RE_ZPE.search(text)
    if m:
        result.zpe_au = float(m.group(1))
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
        try:
            result.frequencies_cm1.append(float(m.group(2)))
        except ValueError:
            pass
    result.imaginary_freq_count = sum(1 for f in result.frequencies_cm1 if f < 0)

    # Geometry: last CARTESIAN COORDINATES block
    coord_positions = [m.start() for m in _RE_COORD_HEADER.finditer(text)]
    if coord_positions:
        block_text = text[coord_positions[-1]:]
        atoms: list[tuple[str, float, float, float]] = []
        in_table = False
        for line in block_text.splitlines()[1:]:  # skip the header line itself
            if not line.strip():
                if in_table:
                    break
                continue
            m = _RE_COORD_ROW.match(line)
            if m:
                in_table = True
                atoms.append((m.group(1), float(m.group(2)), float(m.group(3)), float(m.group(4))))
            elif in_table:
                break
        if atoms:
            result.atom_symbols = [a[0] for a in atoms]
            result.final_xyz = "\n".join(
                f"{a[0]:2s}  {a[1]:12.6f}  {a[2]:12.6f}  {a[3]:12.6f}" for a in atoms
            )

    # Mulliken charges
    in_mulliken = False
    for line in lines:
        if _RE_MULLIKEN_HEADER.search(line):
            in_mulliken = True
            continue
        if in_mulliken:
            m = _RE_MULLIKEN_ROW.match(line)
            if m:
                result.mulliken_charges[int(m.group(1)) + 1] = float(m.group(2))
            elif line.strip() and "Sum" not in line and "---" not in line:
                in_mulliken = False

    # Timing
    m = _RE_WALL.search(text)
    if m:
        d, h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        result.walltime_seconds = d * 86400 + h * 3600 + mi * 60 + s

    return result


def diagnose_orca_result(result: OrcaResult) -> str | None:
    """Return a human-readable error diagnosis from an already-parsed result, or None if clean."""
    if result.normal_termination:
        return None
    if result.error_message:
        return result.error_message
    if result.error_termination:
        return "ORCA error termination"
    if result.scf_energies and not result.normal_termination:
        return "Abnormal termination (job may have been killed or timed out)"
    return None


def diagnose_orca(path: Path | str) -> str | None:
    """Return a human-readable error diagnosis for an ORCA output, or None if clean."""
    return diagnose_orca_result(parse_orca_out(path))
