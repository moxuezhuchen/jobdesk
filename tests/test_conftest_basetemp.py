"""Regression tests for conftest.py basetemp logic."""

import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_config(basetemp=None):
    config = MagicMock()
    config.option.basetemp = basetemp
    return config


def _run_configure(config, monkeypatch, env_val=None):
    """Import and run pytest_configure with controlled env."""
    if env_val is not None:
        monkeypatch.setenv("JOBDESK_TEST_BASETEMP", env_val)
    else:
        monkeypatch.delenv("JOBDESK_TEST_BASETEMP", raising=False)
    import importlib  # noqa: E402

    import conftest  # noqa: E402
    importlib.reload(conftest)
    conftest.pytest_configure(config)
    return config.option.basetemp


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only conftest logic")
class TestConftestBasetemp:
    def test_default_in_system_temp(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        assert result.startswith(tempfile.gettempdir())

    def test_default_is_unique_each_call(self, monkeypatch):
        r1 = _run_configure(_make_config(), monkeypatch, env_val="")
        r2 = _run_configure(_make_config(), monkeypatch, env_val="")
        assert r1 != r2

    def test_default_contains_jobdesk_pytest_prefix(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        assert "jobdesk_pytest_" in Path(result).name

    def test_default_is_not_repo_root(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        repo_root = str(Path(__file__).resolve().parent.parent)
        assert not result.startswith(repo_root)

    def test_whitespace_env_uses_default(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="   ")
        assert "jobdesk_pytest_" in result

    def test_custom_env_value_used(self, monkeypatch, tmp_path):
        custom = str(tmp_path / "my_custom")
        (tmp_path / "my_custom").mkdir()
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val=custom)
        assert result == custom

    def test_explicit_basetemp_not_overridden(self, monkeypatch):
        config = _make_config(basetemp="C:\\explicit\\path")
        _run_configure(config, monkeypatch, env_val="")
        assert config.option.basetemp == "C:\\explicit\\path"

    def test_subprocess_no_basetemp_smoke(self):
        """A real pytest subprocess allocates tmp_path outside the repo."""
        repo = Path(__file__).resolve().parent.parent
        test_file = Path(__file__).with_name(
            f"_test_basetemp_smoke_{uuid.uuid4().hex}.py"
        )
        test_file.write_text(
            "import tempfile\n"
            "from pathlib import Path\n\n"
            "def test_tmp_path_uses_system_basetemp(tmp_path):\n"
            "    resolved = tmp_path.resolve()\n"
            "    system_temp = Path(tempfile.gettempdir()).resolve()\n"
            f"    repo_root = Path({str(repo)!r}).resolve()\n"
            "    assert resolved.is_relative_to(system_temp)\n"
            "    assert not resolved.is_relative_to(repo_root)\n"
            "    assert any(p.name.startswith('jobdesk_pytest_') "
            "for p in resolved.parents)\n",
            encoding="utf-8",
        )
        env = dict(__import__("os").environ)
        env.pop("JOBDESK_TEST_BASETEMP", None)
        env["QT_QPA_PLATFORM"] = "offscreen"
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", str(test_file),
                 "-q", "-p", "no:cacheprovider"],
                capture_output=True, text=True, env=env, cwd=repo,
            )
            assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        finally:
            test_file.unlink(missing_ok=True)
