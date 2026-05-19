"""SMILES to 3D structure conversion and third-party viewer integration.

SMILES→3D requires rdkit (optional dependency).
Viewer integration opens local files in configured external programs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# ---- SMILES → 3D -----------------------------------------------------------

def smiles_to_xyz(
    smiles: str,
    output_path: Path | str | None = None,
    title: str = "",
    optimize: bool = True,
) -> str:
    """Convert a SMILES string to a 3D XYZ file using RDKit.

    Requires: pip install rdkit-pypi

    Args:
        smiles: SMILES string (e.g. "c1ccccc1" for benzene).
        output_path: If given, write XYZ to this path.
        title: Comment line in XYZ (defaults to SMILES).
        optimize: Run MMFF94 force field optimization.

    Returns:
        XYZ file content as string.

    Raises:
        ImportError: If rdkit is not installed.
        ValueError: If SMILES is invalid or 3D embedding fails.
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError:
        raise ImportError(
            "rdkit is required for SMILES→3D conversion. "
            "Install it with: pip install rdkit-pypi"
        )

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"Invalid SMILES: {smiles!r}")

    mol = Chem.AddHs(mol)
    result = AllChem.EmbedMolecule(mol, AllChem.ETKDGv3())
    if result != 0:
        raise ValueError(f"3D embedding failed for SMILES: {smiles!r}")

    if optimize:
        AllChem.MMFFOptimizeMolecule(mol)

    conf = mol.GetConformer()
    atoms = [(atom.GetSymbol(), *conf.GetAtomPosition(i)) for i, atom in enumerate(mol.GetAtoms())]
    n = len(atoms)
    comment = title or smiles
    lines = [str(n), comment]
    for sym, x, y, z in atoms:
        lines.append(f"{sym:<2s}  {x:12.6f}  {y:12.6f}  {z:12.6f}")
    xyz_content = "\n".join(lines) + "\n"

    if output_path:
        Path(output_path).write_text(xyz_content, encoding="utf-8")

    return xyz_content


def smiles_to_gjf(
    smiles: str,
    output_path: Path | str | None = None,
    preset_name: str = "b3lyp_631gd_opt_freq",
    title: str = "",
) -> str:
    """Convert SMILES directly to a Gaussian .gjf input file.

    Requires rdkit. Combines smiles_to_xyz + build_gjf.
    """
    import tempfile
    from .input_builder import build_from_preset, GAUSSIAN_PRESETS, ORCA_PRESETS

    with tempfile.NamedTemporaryFile(mode="w", suffix=".xyz", delete=False, encoding="utf-8") as f:
        tmp_xyz = Path(f.name)

    try:
        smiles_to_xyz(smiles, tmp_xyz, title=title or smiles)
        return build_from_preset(tmp_xyz, preset_name, output_path)
    finally:
        tmp_xyz.unlink(missing_ok=True)


def is_rdkit_available() -> bool:
    """Return True if rdkit is importable."""
    try:
        import rdkit  # noqa: F401
        return True
    except ImportError:
        return False


# ---- Third-party viewer integration ----------------------------------------

# Default viewer paths on Windows
_DEFAULT_VIEWERS: dict[str, list[str]] = {
    "avogadro": [
        r"C:\Program Files\Avogadro2\avogadro2.exe",
        r"C:\Program Files (x86)\Avogadro\avogadro.exe",
    ],
    "gaussview": [
        r"C:\G16W\gview.exe",
        r"C:\G09W\gview.exe",
        r"C:\Program Files\Gaussian\GaussView 6\gview.exe",
    ],
    "chemcraft": [
        r"C:\Program Files\Chemcraft\Chemcraft.exe",
        r"C:\Program Files (x86)\Chemcraft\Chemcraft.exe",
    ],
    "iboview": [
        r"C:\Program Files\IboView\IboView.exe",
    ],
    "molden": [
        r"C:\Program Files\Molden\molden.exe",
    ],
    "vesta": [
        r"C:\Program Files\VESTA-win64\VESTA.exe",
    ],
}


def find_viewer(name: str, custom_path: str | None = None) -> str | None:
    """Find the executable path for a named viewer.

    Args:
        name: Viewer name (avogadro, gaussview, chemcraft, iboview, molden, vesta).
        custom_path: User-configured path override.

    Returns:
        Path to executable, or None if not found.
    """
    if custom_path and Path(custom_path).exists():
        return custom_path
    for candidate in _DEFAULT_VIEWERS.get(name.lower(), []):
        if Path(candidate).exists():
            return candidate
    return None


def open_in_viewer(
    file_path: Path | str,
    viewer_name: str = "avogadro",
    custom_path: str | None = None,
) -> bool:
    """Open a molecular file in a third-party viewer.

    Args:
        file_path: Path to the file to open (.xyz, .gjf, .log, .out, etc.).
        viewer_name: Name of the viewer to use.
        custom_path: Override path to the viewer executable.

    Returns:
        True if the viewer was launched, False if not found.
    """
    exe = find_viewer(viewer_name, custom_path)
    if exe is None:
        return False
    try:
        subprocess.Popen([exe, str(file_path)], close_fds=True)
        return True
    except Exception:
        return False


def list_available_viewers(custom_paths: dict[str, str] | None = None) -> dict[str, str]:
    """Return a dict of viewer_name → executable_path for all found viewers."""
    found: dict[str, str] = {}
    for name in _DEFAULT_VIEWERS:
        custom = (custom_paths or {}).get(name)
        exe = find_viewer(name, custom)
        if exe:
            found[name] = exe
    return found
