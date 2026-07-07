#!/usr/bin/env python3
"""Mock Gaussian 16 front-end for ConfFlow Phase 6 smoke testing.

ConFlow invokes: g16 <basename>   (in the task work_dir, cwd=work_dir).
Reads <basename>.gjf, extracts coordinates, and produces a Gaussian-style
<basename>.log that contains:
  - SCF Done energy line
  - Standard orientation geometry block  (confflow geometry.py reads this)
  - Archive \\HF=... entry  (parse_output fallback)
  - Normal termination marker

This lets the full ConfFlow pipeline run without a real Gaussian license.
"""
from __future__ import annotations

import os
import sys
import time

MOCK_ENERGY = -40.478123  # methane SP energy (Hartree)


def atomic_number(symbol: str) -> int:
   _MAP = {"H": 1, "C": 6, "N": 7, "O": 8, "F": 9, "P": 15,
            "S": 16, "Cl": 17, "Br": 35, "I": 53}
    return _MAP.get(symbol.capitalize(), 99)


def parse_gjf(basename: str) -> tuple[list[tuple[str, float, float, float]], int]:
    """Return list of (symbol, x, y, z) and atom count."""
    path = f"{basename}.gjf"
    if not os.path.exists(path):
        # Fallback methane geometry
        return [
            ("C", 0.0, 0.0, 0.0),
            ("H", 0.629118, 0.629118, 0.629118),
            ("H", -0.629118, -0.629118, 0.629118),
            ("H", -0.629118, 0.629118, -0.629118),
            ("H", 0.629118, -0.629118, -0.629118),
        ], 5

    coords: list[tuple[str, float, float, float]] = []
    with open(path) as f:
        lines = f.readlines()

    # Skip route card, title, charge/multiplicity line
    idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            idx = i + 1
            break
    else:
        idx = len(lines)

    # Next non-empty line is charge/multiplicity — skip it
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    idx += 1  # skip charge/mult line

    for i in range(idx, len(lines)):
        line = lines[i].strip()
        if not line:
            break
        parts = line.split()
        if len(parts) >= 4:
            try:
                sym = parts[0]
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                coords.append((sym, x, y, z))
            except ValueError:
                pass

    if not coords:
        coords = [("C", 0.0, 0.0, 0.0), ("H", 0.629, 0.629, 0.629),
                  ("H", -0.629, -0.629, 0.629),
                  ("H", -0.629, 0.629, -0.629),
                  ("H", 0.629, -0.629, -0.629)]
    return coords, len(coords)


def write_log(basename: str, coords: list[tuple[str, float, float, float]]) -> None:
    log_path = f"{basename}.log"
    n = len(coords)

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f" Entering Gaussian 16 (MOCK for ConfFlow Phase 6 smoke test)\n")
        f.write(f" Initial command: {basename}\n\n")

        # SCF Done line
        f.write(f"\n SCF Done:  E(RB3LYP) = {MOCK_ENERGY:+.10f}   A.U. after   1 cycles\n\n")

        # Standard orientation block
        f.write(" Standard orientation:\n")
        f.write(" ---------------------------------------------------------------------\n")
        f.write(" Center     Atomic     Atomic              Coordinates (Angstroms)\n")
        f.write(" Number     Number      Type              X           Y           Z\n")
        f.write(" ---------------------------------------------------------------------\n")
        for i, (sym, x, y, z) in enumerate(coords, 1):
            an = atomic_number(sym)
            f.write(
                f" {i:>5d} {an:>8d}             0      {x:>12.6f}      {y:>12.6f}      {z:>12.6f}\n"
            )
        f.write(" ---------------------------------------------------------------------\n")

        # Archive entry (fallback energy source)
        f.write(f"\n \\HF={MOCK_ENERGY:.10f}\\\\\n")

        # Normal termination
        f.write("\n Normal termination.\n")


def main() -> int:
    delay = float(os.environ.get("JOBDESK_MOCK_G16_DELAY", "0.3"))
    time.sleep(delay)

    if len(sys.argv) < 2:
        print("Usage: g16 <basename>", file=sys.stderr)
        return 1

    basename = sys.argv[1].replace(".gjf", "")
    coords, n = parse_gjf(basename)
    write_log(basename, coords)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
