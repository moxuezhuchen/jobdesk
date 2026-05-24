"""Tests for Gaussian and ORCA output parsers."""
import tempfile
from pathlib import Path

import pytest

from jobdesk_app.core.parsers.gaussian import diagnose_gaussian, parse_gaussian_log
from jobdesk_app.core.parsers.orca import diagnose_orca, parse_orca_out
from jobdesk_app.services.analysis_profiles import BUILTIN_PROFILES, AnalysisProfileStore

# ---- Gaussian test fixtures ------------------------------------------------

GAUSSIAN_OPT_FREQ_LOG = """\
 Entering Gaussian System, Link 0=g16
 SCF Done:  E(RB3LYP) =  -78.5873456789     A.U. after    9 cycles
 SCF Done:  E(RB3LYP) =  -78.5901234567     A.U. after    8 cycles
 Stationary point found.
 Zero-point correction=                           0.049876 (Hartree/Particle)
 Thermal correction to Energy=                    0.052345
 Thermal correction to Enthalpy=                  0.053289
 Thermal correction to Gibbs Free Energy=         0.021456
 Temperature   298.150 Kelvin.  Pressure   1.00000 Atm.
 Frequencies --    -12.3456   456.7890   789.0123
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1          6           0        0.000000    0.000000    0.000000
      2          1           0        1.089000    0.000000    0.000000
 ---------------------------------------------------------------------
 Mulliken charges:
               1
     1  C   -0.234567
     2  H    0.234567
 Sum of Mulliken charges =   0.00000
 Job cpu time:       0 days  0 hours  5 minutes 23.4 seconds.
 Elapsed time:       0 days  0 hours  5 minutes 30.1 seconds.
 Normal termination of Gaussian 16.
"""

GAUSSIAN_ERROR_LOG = """\
 Entering Gaussian System, Link 0=g16
 SCF Done:  E(RB3LYP) =  -78.5873456789     A.U. after    9 cycles
 Convergence failure -- run terminated.
 Error termination via Lnk1e in /opt/g16/l502.exe
"""

GAUSSIAN_KILLED_LOG = """\
 Entering Gaussian System, Link 0=g16
 SCF Done:  E(RB3LYP) =  -78.5873456789     A.U. after    9 cycles
"""


def _write(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


class TestGaussianParser:
    def test_scf_energies_extracted(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            r = parse_gaussian_log(p)
            assert len(r.scf_energies) == 2
            assert r.final_energy_au == pytest.approx(-78.5901234567)
        finally:
            p.unlink()

    def test_normal_termination(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            r = parse_gaussian_log(p)
            assert r.normal_termination is True
            assert r.error_termination is False
        finally:
            p.unlink()

    def test_converged(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            assert parse_gaussian_log(p).converged is True
        finally:
            p.unlink()

    def test_thermochemistry(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            r = parse_gaussian_log(p)
            assert r.zpe_au == pytest.approx(0.049876)
            assert r.enthalpy_au == pytest.approx(0.053289)
            assert r.gibbs_au == pytest.approx(0.021456)
            assert r.thermo_temperature_k == pytest.approx(298.15)
        finally:
            p.unlink()

    def test_frequencies_and_imaginary(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            r = parse_gaussian_log(p)
            assert -12.3456 in r.frequencies_cm1
            assert r.imaginary_freq_count == 1
        finally:
            p.unlink()

    def test_geometry_extracted(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            r = parse_gaussian_log(p)
            assert r.final_xyz is not None
            assert "C" in r.final_xyz
            assert r.atom_symbols == ["C", "H"]
        finally:
            p.unlink()

    def test_mulliken_charges(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            r = parse_gaussian_log(p)
            assert r.mulliken_charges[1] == pytest.approx(-0.234567)
            assert r.mulliken_charges[2] == pytest.approx(0.234567)
        finally:
            p.unlink()

    def test_timing(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            r = parse_gaussian_log(p)
            assert r.cpu_time_seconds == pytest.approx(5 * 60 + 23.4)
            assert r.walltime_seconds == pytest.approx(5 * 60 + 30.1)
        finally:
            p.unlink()

    def test_error_termination(self):
        p = _write(GAUSSIAN_ERROR_LOG)
        try:
            r = parse_gaussian_log(p)
            assert r.error_termination is True
            assert r.normal_termination is False
            assert "Convergence failure" in r.error_message
        finally:
            p.unlink()

    def test_missing_file_returns_empty(self):
        r = parse_gaussian_log("/nonexistent/path.log")
        assert r.final_energy_au is None
        assert r.normal_termination is False

    def test_diagnose_clean(self):
        p = _write(GAUSSIAN_OPT_FREQ_LOG)
        try:
            assert diagnose_gaussian(p) is None
        finally:
            p.unlink()

    def test_diagnose_error(self):
        p = _write(GAUSSIAN_ERROR_LOG)
        try:
            diag = diagnose_gaussian(p)
            assert diag is not None
            assert "Convergence" in diag
        finally:
            p.unlink()

    def test_diagnose_killed(self):
        p = _write(GAUSSIAN_KILLED_LOG)
        try:
            diag = diagnose_gaussian(p)
            assert diag is not None
            assert "killed" in diag.lower() or "termination" in diag.lower()
        finally:
            p.unlink()

    def test_heavy_atoms_br_and_ti_resolved_correctly(self):
        """B6: Br (35) and Ti (22) must appear as symbols, not X35/X22."""
        log = """\
 Standard orientation:
 ---------------------------------------------------------------------
 Center     Atomic      Atomic             Coordinates (Angstroms)
 Number     Number       Type             X           Y           Z
 ---------------------------------------------------------------------
      1         22           0        0.000000    0.000000    0.000000
      2         35           0        2.300000    0.000000    0.000000
      3          6           0        1.000000    1.000000    0.000000
 ---------------------------------------------------------------------
 Normal termination of Gaussian 16.
"""
        p = _write(log)
        try:
            r = parse_gaussian_log(p)
            assert r.atom_symbols == ["Ti", "Br", "C"]
            assert "Ti" in r.final_xyz
            assert "Br" in r.final_xyz
            assert "X22" not in r.final_xyz
            assert "X35" not in r.final_xyz
        finally:
            p.unlink()


# ---- ORCA test fixtures ----------------------------------------------------

ORCA_OPT_FREQ_OUT = """\
                         -----------------------
                         FINAL SINGLE POINT ENERGY       -78.590123456789
                         -----------------------
                         -----------------------
                         FINAL SINGLE POINT ENERGY       -78.591234567890
                         -----------------------
THE OPTIMIZATION HAS CONVERGED
Zero point energy                ...      0.049876 Eh
Total enthalpy                   ...     -78.541358 Eh
Final Gibbs free energy          ...     -78.569778 Eh
Temperature   ...    298.15 K
   6:     -15.2345 cm**-1
   7:     456.7890 cm**-1
   8:     789.0123 cm**-1
CARTESIAN COORDINATES (ANGSTROEM)
  C      0.000000    0.000000    0.000000
  H      1.089000    0.000000    0.000000

MULLIKEN ATOMIC CHARGES
   0 C :   -0.234567
   1 H :    0.234567
Sum of atomic charges:    0.000000
TOTAL RUN TIME: 0 days 0 hours 5 minutes 30 seconds
ORCA TERMINATED NORMALLY
"""

ORCA_ERROR_OUT = """\
FINAL SINGLE POINT ENERGY       -78.590123456789
SCF NOT CONVERGED after 125 iterations
ORCA finished with error return
"""


class TestOrcaParser:
    def test_scf_energies(self):
        p = _write_orca(ORCA_OPT_FREQ_OUT)
        try:
            r = parse_orca_out(p)
            assert len(r.scf_energies) == 2
            assert r.final_energy_au == pytest.approx(-78.591234567890)
        finally:
            p.unlink()

    def test_normal_termination(self):
        p = _write_orca(ORCA_OPT_FREQ_OUT)
        try:
            r = parse_orca_out(p)
            assert r.normal_termination is True
            assert r.converged is True
        finally:
            p.unlink()

    def test_thermochemistry(self):
        p = _write_orca(ORCA_OPT_FREQ_OUT)
        try:
            r = parse_orca_out(p)
            assert r.zpe_au == pytest.approx(0.049876)
            assert r.gibbs_au == pytest.approx(-78.569778)
            assert r.thermo_temperature_k == pytest.approx(298.15)
        finally:
            p.unlink()

    def test_frequencies_and_imaginary(self):
        p = _write_orca(ORCA_OPT_FREQ_OUT)
        try:
            r = parse_orca_out(p)
            assert -15.2345 in r.frequencies_cm1
            assert r.imaginary_freq_count == 1
        finally:
            p.unlink()

    def test_geometry(self):
        p = _write_orca(ORCA_OPT_FREQ_OUT)
        try:
            r = parse_orca_out(p)
            assert r.final_xyz is not None
            assert r.atom_symbols == ["C", "H"]
        finally:
            p.unlink()

    def test_timing(self):
        p = _write_orca(ORCA_OPT_FREQ_OUT)
        try:
            r = parse_orca_out(p)
            assert r.walltime_seconds == pytest.approx(5 * 60 + 30)
        finally:
            p.unlink()

    def test_error_termination(self):
        p = _write_orca(ORCA_ERROR_OUT)
        try:
            r = parse_orca_out(p)
            assert r.error_termination is True
            assert r.normal_termination is False
        finally:
            p.unlink()

    def test_diagnose_clean(self):
        p = _write_orca(ORCA_OPT_FREQ_OUT)
        try:
            assert diagnose_orca(p) is None
        finally:
            p.unlink()

    def test_diagnose_error(self):
        p = _write_orca(ORCA_ERROR_OUT)
        try:
            diag = diagnose_orca(p)
            assert diag is not None
        finally:
            p.unlink()


def _write_orca(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".out", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)


# ---- AnalysisProfileStore tests --------------------------------------------

class TestAnalysisProfileStore:
    def test_builtin_profiles_available(self):
        store = AnalysisProfileStore()
        profiles = store.list_profiles()
        assert "gaussian_sp" in profiles
        assert "gaussian_opt_freq" in profiles
        assert "orca_sp" in profiles
        assert "orca_opt_freq" in profiles
        assert "orca_dlpno_ccsd_t" in profiles

    def test_gaussian_opt_freq_has_expected_rules(self):
        profile = BUILTIN_PROFILES["gaussian_opt_freq"]
        names = [r.name for r in profile.extract_rules]
        assert "scf_energy" in names
        assert "zpe" in names
        assert "gibbs_correction" in names

    def test_gaussian_opt_freq_no_imaginary_freq_count_field(self):
        """imaginary_freq_count was misleading; replaced by leading_imaginary_frequency_cm1."""
        profile = BUILTIN_PROFILES["gaussian_opt_freq"]
        names = [r.name for r in profile.extract_rules]
        assert "imaginary_freq_count" not in names

    def test_gaussian_opt_freq_has_leading_imaginary_frequency(self):
        """The true imaginary freq count is from parse_gaussian_log().imaginary_freq_count."""
        from jobdesk_app.config.schema import ExtractStrategy, ExtractType
        profile = BUILTIN_PROFILES["gaussian_opt_freq"]
        rules_by_name = {r.name: r for r in profile.extract_rules}
        assert "leading_imaginary_frequency_cm1" in rules_by_name
        rule = rules_by_name["leading_imaginary_frequency_cm1"]
        assert rule.type == ExtractType.float
        assert rule.unit == "cm-1"
        assert rule.strategy == ExtractStrategy.all

    def test_user_profile_save_and_load(self, tmp_path):
        from jobdesk_app.config.schema import ExtractResult, ExtractStrategy, ExtractType
        store = AnalysisProfileStore(tmp_path)
        from jobdesk_app.services.analysis_profiles import AnalysisProfile
        profile = AnalysisProfile(
            name="my_custom",
            description="Custom profile",
            extract_rules=[
                ExtractResult(
                    name="energy",
                    source_glob="*.log",
                    regex=r"Energy=\s*(?P<value>[-\d.]+)",
                    strategy=ExtractStrategy.last,
                    type=ExtractType.float,
                )
            ],
        )
        store.save(profile)
        loaded = store.get("my_custom")
        assert loaded is not None
        assert loaded.extract_rules[0].name == "energy"

    def test_user_profile_overrides_builtin(self, tmp_path):
        from jobdesk_app.services.analysis_profiles import AnalysisProfile
        store = AnalysisProfileStore(tmp_path)
        override = AnalysisProfile(name="gaussian_sp", description="override", extract_rules=[])
        store.save(override)
        loaded = store.get("gaussian_sp")
        assert loaded.description == "override"

    def test_delete_user_profile(self, tmp_path):
        from jobdesk_app.services.analysis_profiles import AnalysisProfile
        store = AnalysisProfileStore(tmp_path)
        store.save(AnalysisProfile(name="temp", description="", extract_rules=[]))
        store.delete("temp")
        assert store.get("temp") is None

    def test_profile_name_cannot_escape_store_directory(self, tmp_path):
        from jobdesk_app.services.analysis_profiles import AnalysisProfile

        store = AnalysisProfileStore(tmp_path)
        with pytest.raises(ValueError, match="invalid profile name"):
            store.save(AnalysisProfile(name="../outside", description="", extract_rules=[]))
        with pytest.raises(ValueError, match="invalid profile name"):
            store.delete("../outside")

    def test_profile_rewrite_is_atomic(self, tmp_path, monkeypatch):
        from jobdesk_app.services.analysis_profiles import AnalysisProfile

        store = AnalysisProfileStore(tmp_path)
        profile = AnalysisProfile(name="stable", description="original", extract_rules=[])
        store.save(profile)
        original = (tmp_path / "stable.json").read_text(encoding="utf-8")

        def fail_replace(self, target):
            raise RuntimeError("replace failed")

        monkeypatch.setattr(Path, "replace", fail_replace)
        with pytest.raises(RuntimeError, match="replace failed"):
            store.save(AnalysisProfile(name="stable", description="new", extract_rules=[]))

        assert (tmp_path / "stable.json").read_text(encoding="utf-8") == original

    def test_invalid_user_profile_is_reported_in_logs(self, tmp_path, caplog):
        (tmp_path / "invalid.json").write_text("{not json", encoding="utf-8")

        with caplog.at_level("WARNING"):
            AnalysisProfileStore(tmp_path).list_profiles()

        assert "invalid.json" in caplog.text
