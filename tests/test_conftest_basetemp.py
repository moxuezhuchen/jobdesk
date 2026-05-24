"""Regression tests for conftest.py basetemp logic."""

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
    # Re-import to get fresh module
    import importlib  # noqa: E402

    import conftest  # noqa: E402
    importlib.reload(conftest)
    conftest.pytest_configure(config)
    return config.option.basetemp


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only conftest logic")
class TestConftestBasetemp:
    def test_empty_env_defaults_to_pytest_tmp_local(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        # conftest.py lives at repo root, so .pytest_tmp_local is under repo root
        assert result.endswith(".pytest_tmp_local")
        assert result != str(Path(__file__).resolve().parent)  # not tests/ dir

    def test_whitespace_env_defaults_to_pytest_tmp_local(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="   ")
        assert result.endswith(".pytest_tmp_local")

    def test_unset_env_defaults_to_pytest_tmp_local(self, monkeypatch):
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val=None)
        assert result.endswith(".pytest_tmp_local")

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

    def test_default_does_not_use_repo_root(self, monkeypatch):
        """The bug: empty env produced Path('.') which is repo root."""
        config = _make_config()
        result = _run_configure(config, monkeypatch, env_val="")
        # Must not be the bare repo root (where .git lives)
        assert ".pytest_tmp_local" in result
