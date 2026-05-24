"""Regression tests for conftest.py basetemp logic."""

import subprocess
import sys
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
    def test_default_creates_unique_session_dir(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        assert ".pytest_tmp_session_" in result
        assert len(result) > 0

    def test_default_is_unique_each_call(self, monkeypatch):
        config1 = _make_config()
        result1 = _run_configure(config1, monkeypatch, env_val="")
        config2 = _make_config()
        result2 = _run_configure(config2, monkeypatch, env_val="")
        assert result1 != result2

    def test_default_is_under_repo_root(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        repo_root = str(Path(__file__).resolve().parent.parent)
        assert result.startswith(repo_root)

    def test_default_matches_gitignore_pattern(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        dirname = Path(result).name
        assert dirname.startswith(".pytest_tmp_")

    def test_default_is_not_repo_root(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        repo_root = str(Path(__file__).resolve().parent.parent)
        assert result != repo_root

    def test_whitespace_env_uses_default(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="   ")
        assert ".pytest_tmp_session_" in result

    def test_custom_env_value_used(self, monkeypatch, tmp_path):
        config = _make_config()
        custom = str(tmp_path / "my_custom_base")
        (tmp_path / "my_custom_base").mkdir()
        result = _run_configure(config, monkeypatch, env_val=custom)
        assert result == custom

    def test_explicit_basetemp_not_overridden(self, monkeypatch):
        config = _make_config(basetemp="C:\\explicit\\path")
        _run_configure(config, monkeypatch, env_val="")
        assert config.option.basetemp == "C:\\explicit\\path"

    def test_subprocess_no_basetemp_smoke(self, monkeypatch, tmp_path):
        """A real pytest subprocess without --basetemp must succeed using tmp_path."""
        test_file = tmp_path / "test_smoke.py"
        test_file.write_text(
            "def test_tmp_path_works(tmp_path):\n"
            "    assert tmp_path.exists()\n"
            "    assert '.pytest_tmp_session_' in str(tmp_path)\n",
            encoding="utf-8",
        )
        env = dict(__import__("os").environ)
        env.pop("JOBDESK_TEST_BASETEMP", None)
        env["QT_QPA_PLATFORM"] = "offscreen"
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(test_file), "-q"],
            capture_output=True, text=True, env=env, cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
