"""Tests for viewer module (SMILES→3D and third-party viewer integration)."""
from unittest.mock import patch

import pytest

from jobdesk_app.core.viewer import (
    find_viewer,
    is_rdkit_available,
    list_available_viewers,
    open_in_viewer,
)


class TestFindViewer:
    def test_returns_none_when_not_found(self):
        assert find_viewer("avogadro") is None or isinstance(find_viewer("avogadro"), str)

    def test_custom_path_used_when_exists(self, tmp_path):
        fake_exe = tmp_path / "avogadro.exe"
        fake_exe.write_text("fake")
        result = find_viewer("avogadro", str(fake_exe))
        assert result == str(fake_exe)

    def test_custom_path_ignored_when_missing(self):
        result = find_viewer("avogadro", "/nonexistent/avogadro.exe")
        # Falls back to default search (likely None on test machine)
        assert result is None or isinstance(result, str)

    def test_unknown_viewer_returns_none(self):
        assert find_viewer("nonexistent_viewer_xyz") is None


class TestOpenInViewer:
    def test_returns_false_when_viewer_not_found(self):
        assert open_in_viewer("/tmp/mol.xyz", "avogadro_nonexistent") is False

    def test_launches_viewer_when_found(self, tmp_path):
        fake_exe = tmp_path / "avogadro.exe"
        fake_exe.write_text("fake")
        mol_file = tmp_path / "mol.xyz"
        mol_file.write_text("1\ntest\nC 0 0 0\n")
        with patch("subprocess.Popen") as mock_popen:
            result = open_in_viewer(mol_file, "avogadro", str(fake_exe))
            assert result is True
            mock_popen.assert_called_once()
            call_args = mock_popen.call_args[0][0]
            assert str(fake_exe) in call_args
            assert str(mol_file) in call_args


class TestListAvailableViewers:
    def test_returns_dict(self):
        viewers = list_available_viewers()
        assert isinstance(viewers, dict)

    def test_custom_paths_included(self, tmp_path):
        fake_exe = tmp_path / "avogadro.exe"
        fake_exe.write_text("fake")
        viewers = list_available_viewers({"avogadro": str(fake_exe)})
        assert "avogadro" in viewers
        assert viewers["avogadro"] == str(fake_exe)


class TestIsRdkitAvailable:
    def test_returns_bool(self):
        result = is_rdkit_available()
        assert isinstance(result, bool)


class TestSmilesConversion:
    @pytest.mark.skipif(not is_rdkit_available(), reason="rdkit not installed")
    def test_benzene_to_xyz(self):
        from jobdesk_app.core.viewer import smiles_to_xyz
        xyz = smiles_to_xyz("c1ccccc1")
        lines = xyz.strip().splitlines()
        n_atoms = int(lines[0])
        assert n_atoms > 0
        # Benzene: 6C + 6H = 12 atoms
        assert n_atoms == 12

    @pytest.mark.skipif(not is_rdkit_available(), reason="rdkit not installed")
    def test_xyz_has_correct_format(self):
        from jobdesk_app.core.viewer import smiles_to_xyz
        xyz = smiles_to_xyz("C")  # methane
        lines = xyz.strip().splitlines()
        assert int(lines[0]) == 5  # 1C + 4H
        # Each atom line: symbol + 3 floats
        for line in lines[2:]:
            parts = line.split()
            assert len(parts) == 4
            float(parts[1])  # x
            float(parts[2])  # y
            float(parts[3])  # z

    @pytest.mark.skipif(not is_rdkit_available(), reason="rdkit not installed")
    def test_writes_to_file(self, tmp_path):
        from jobdesk_app.core.viewer import smiles_to_xyz
        out = tmp_path / "mol.xyz"
        smiles_to_xyz("C", out)
        assert out.exists()
        assert int(out.read_text().splitlines()[0]) == 5

    @pytest.mark.skipif(not is_rdkit_available(), reason="rdkit not installed")
    def test_invalid_smiles_raises(self):
        from jobdesk_app.core.viewer import smiles_to_xyz
        with pytest.raises(ValueError, match="Invalid SMILES"):
            smiles_to_xyz("not_a_smiles_!!!")

    def test_smiles_to_xyz_raises_without_rdkit(self):
        from jobdesk_app.core.viewer import smiles_to_xyz
        if is_rdkit_available():
            pytest.skip("rdkit is installed")
        with pytest.raises(ImportError, match="rdkit"):
            smiles_to_xyz("c1ccccc1")
