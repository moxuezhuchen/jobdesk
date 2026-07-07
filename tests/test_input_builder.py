"""Tests for Gaussian/ORCA input file builder."""
import tempfile
from pathlib import Path

import pytest

from jobdesk_app.core.input_builder import (
    GAUSSIAN_PRESETS,
    ORCA_PRESETS,
    GaussianInputSpec,
    OrcaInputSpec,
    build_from_preset,
    build_gjf,
    build_inp,
    list_presets,
    preset_to_confflow_fields,
)

ETHANE_XYZ = """\
8
ethane
C   0.000000   0.000000   0.000000
C   1.540000   0.000000   0.000000
H  -0.390000   1.027000   0.000000
H  -0.390000  -0.513000  -0.889000
H  -0.390000  -0.513000   0.889000
H   1.930000   1.027000   0.000000
H   1.930000  -0.513000  -0.889000
H   1.930000  -0.513000   0.889000
"""


def _xyz_file(content: str = ETHANE_XYZ) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".xyz", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


class TestBuildGjf:
    def test_basic_gjf_structure(self):
        p = _xyz_file()
        try:
            content = build_gjf(p)
            assert "%nproc=8" in content
            assert "%mem=16GB" in content
            assert "# B3LYP/6-31G(d) opt freq" in content
            assert "0 1" in content
            assert " C " in content
            assert " H " in content
        finally:
            p.unlink()

    def test_custom_spec(self):
        p = _xyz_file()
        try:
            spec = GaussianInputSpec(
                method_basis="M062X/def2-TZVP",
                job_keywords=["SP"],
                charge=-1,
                multiplicity=2,
                nproc=16,
                mem="32GB",
            )
            content = build_gjf(p, spec)
            assert "# M062X/def2-TZVP SP" in content
            assert "-1 2" in content
            assert "%nproc=16" in content
            assert "%mem=32GB" in content
        finally:
            p.unlink()

    def test_writes_to_file(self, tmp_path):
        p = _xyz_file()
        out = tmp_path / "mol.gjf"
        try:
            build_gjf(p, output_path=out)
            assert out.exists()
            assert "B3LYP" in out.read_text()
        finally:
            p.unlink()

    def test_atom_count_matches_xyz(self):
        p = _xyz_file()
        try:
            content = build_gjf(p)
            # Count atom lines (lines with element symbol + 3 floats)
            atom_lines = [
                line
                for line in content.splitlines()
                if line.strip() and line.strip()[0].isalpha() and len(line.split()) == 4
            ]
            assert len(atom_lines) == 8  # ethane has 8 atoms
        finally:
            p.unlink()

    def test_invalid_xyz_raises(self, tmp_path):
        bad = tmp_path / "bad.xyz"
        bad.write_text("not a number\n", encoding="utf-8")
        with pytest.raises(ValueError):
            build_gjf(bad)

    def test_rejects_missing_coordinate_rows(self, tmp_path):
        bad = tmp_path / "truncated.xyz"
        bad.write_text(
            "3\nwater\nO 0 0 0\nH 0 0.7 0.5\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="declares 3 atoms but contains 2 coordinate rows"):
            build_gjf(bad)

    def test_rejects_extra_coordinate_rows(self, tmp_path):
        bad = tmp_path / "extra.xyz"
        bad.write_text(
            "2\nfragment\nH 0 0 0\nH 0 0 1\nO 0 0 2\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="declares 2 atoms but contains 3 coordinate rows"):
            build_gjf(bad)

    def test_rejects_malformed_coordinate_rows(self, tmp_path):
        bad = tmp_path / "malformed.xyz"
        bad.write_text(
            "2\nfragment\nH 0 0 0\nO x 0 2\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="invalid coordinate row 2"):
            build_inp(bad)


class TestBuildInp:
    def test_basic_orca_structure(self):
        p = _xyz_file()
        try:
            content = build_inp(p)
            assert "! B3LYP" in content
            assert "%pal nprocs 8 end" in content
            assert "* xyz 0 1" in content
            assert "*" in content
        finally:
            p.unlink()

    def test_custom_orca_spec(self):
        p = _xyz_file()
        try:
            spec = OrcaInputSpec(
                keywords="! DLPNO-CCSD(T) cc-pVTZ",
                charge=1,
                multiplicity=1,
                nproc=32,
            )
            content = build_inp(p, spec)
            assert "! DLPNO-CCSD(T)" in content
            assert "* xyz 1 1" in content
            assert "%pal nprocs 32 end" in content
        finally:
            p.unlink()


class TestPresets:
    def test_gaussian_presets_exist(self):
        assert "b3lyp_631gd_opt_freq" in GAUSSIAN_PRESETS
        assert "m062x_def2tzvp_opt_freq" in GAUSSIAN_PRESETS
        assert "ccsd_t_ccpvtz_sp" in GAUSSIAN_PRESETS

    def test_orca_presets_exist(self):
        assert "b3lyp_def2tzvp_opt_freq" in ORCA_PRESETS
        assert "dlpno_ccsd_t_sp" in ORCA_PRESETS

    def test_build_from_gaussian_preset(self):
        p = _xyz_file()
        try:
            content = build_from_preset(p, "b3lyp_631gd_opt_freq")
            assert "B3LYP/6-31G(d)" in content
            assert "opt" in content
        finally:
            p.unlink()

    def test_build_from_orca_preset(self):
        p = _xyz_file()
        try:
            content = build_from_preset(p, "dlpno_ccsd_t_sp")
            assert "DLPNO-CCSD(T)" in content
            assert "* xyz" in content
        finally:
            p.unlink()

    def test_unknown_preset_raises(self):
        p = _xyz_file()
        try:
            with pytest.raises(ValueError, match="Unknown preset"):
                build_from_preset(p, "nonexistent_preset")
        finally:
            p.unlink()

    def test_list_presets_returns_all(self):
        presets = list_presets()
        assert len(presets) == len(GAUSSIAN_PRESETS) + len(ORCA_PRESETS)
        for name in GAUSSIAN_PRESETS:
            assert name in presets
        for name in ORCA_PRESETS:
            assert name in presets


class TestPresetToConfflowFields:
    """Phase 8A: converter from legacy preset name to wizard-friendly fields."""

    def test_gaussian_preset_splits_method_and_basis(self):
        fields = preset_to_confflow_fields("b3lyp_631gd_opt_freq")
        assert fields["method"] == "B3LYP"
        assert fields["basis"] == "6-31G(d)"
        assert fields["nproc"] == 8
        assert fields["memory_mb"] == 16 * 1024

    def test_gaussian_preset_with_empirical_dispersion(self):
        fields = preset_to_confflow_fields("b3lyp_d3_def2tzvp_opt")
        # The Gaussian preset stores the dispersion on the basis side, so the
        # wizard sees "def2-TZVP EmpiricalDispersion=GD3BJ" as basis.
        assert fields["method"] == "B3LYP"
        assert fields["basis"].startswith("def2-TZVP")

    def test_orca_preset_strips_bang_and_splits_basis(self):
        fields = preset_to_confflow_fields("b3lyp_def2tzvp_opt_freq")
        # ORCA preset is "! B3LYP D3BJ def2-TZVP def2/J RIJCOSX TightSCF opt freq"
        assert fields["method"] == "B3LYP D3BJ"
        assert "def2-TZVP" in fields["basis"]
        assert "def2/J" in fields["basis"]

    def test_orca_preset_dlpno(self):
        fields = preset_to_confflow_fields("dlpno_ccsd_t_sp")
        assert "DLPNO-CCSD(T)" in fields["method"]
        assert "cc-pVTZ" in fields["basis"]

    def test_unknown_preset_returns_empty(self):
        fields = preset_to_confflow_fields("does_not_exist")
        assert fields["method"] == ""
        assert fields["basis"] == ""
        # Defaults still non-zero so the wizard doesn't show "1MB" / "1 core".
        assert fields["nproc"] >= 1
        assert fields["memory_mb"] >= 1024

    def test_orca_preset_memory_mb_matches_mem_per_core(self):
        # ORCA presets store memory *per core*, which is exactly what the
        # wizard's memory field wants.
        fields = preset_to_confflow_fields("r2scan3c_opt_freq")
        assert fields["memory_mb"] == ORCA_PRESETS["r2scan3c_opt_freq"].mem_per_core_mb
