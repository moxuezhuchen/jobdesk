"""Tests for the AppConfig unified configuration module."""

import threading
from pathlib import Path

from jobdesk_app.services.app_config import AppConfig, get_config


class TestAppConfig:
    """Test AppConfig initialization and path properties."""

    def test_singleton_pattern(self):
        """Test that from_default() returns the same instance."""
        config1 = AppConfig.from_default()
        config2 = AppConfig.from_default()
        assert config1 is config2

    def test_singleton_thread_safety(self):
        """Test that singleton creation is thread-safe."""
        results: list[AppConfig] = []

        def get_instance():
            results.append(AppConfig.from_default())

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same instance
        assert all(r is results[0] for r in results)

    def test_paths_use_appdata(self, tmp_path, monkeypatch):
        """Test that paths use APPDATA when set."""
        # Reset singleton for clean test
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path))
        config = AppConfig.from_default()

        assert config.app_data_dir == tmp_path / "JobDesk"
        assert config.runs_dir == tmp_path / "JobDesk" / "runs"
        assert config.logs_dir == tmp_path / "JobDesk" / "logs"

        # Reset singleton after test
        AppConfig._instance = None

    def test_paths_use_joobdesk_appdata_env(self, tmp_path, monkeypatch):
        """Test that JOBDESK_APPDATA takes precedence over APPDATA."""
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path / "fallback"))
        monkeypatch.setenv("JOBDESK_APPDATA", str(tmp_path / "preferred"))
        config = AppConfig.from_default()

        assert config.app_data_dir == tmp_path / "preferred" / "JobDesk"

        AppConfig._instance = None

    def test_config_files_in_appdata(self, tmp_path, monkeypatch):
        """Test that config file paths are in app data directory."""
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path))
        config = AppConfig.from_default()

        assert config.servers_path == tmp_path / "JobDesk" / "servers.yaml"
        assert config.settings_path == tmp_path / "JobDesk" / "settings.json"
        assert config.presets_path == tmp_path / "JobDesk" / "method_presets.json"

        AppConfig._instance = None

    def test_resources_dir_exists_or_fallback(self):
        """Test that resources directory is detected."""
        config = AppConfig.from_default()
        # resources_dir should be set to a valid path
        assert config.resources_dir is not None
        assert isinstance(config.resources_dir, Path)

    def test_subdirectories_point_to_resources(self):
        """Test that subdirectory paths are correctly derived."""
        config = AppConfig.from_default()

        assert config.method_presets_dir == config.resources_dir / "method_presets"
        assert config.step_presets_dir == config.resources_dir / "step_presets"
        assert config.workflow_examples_dir == config.resources_dir / "workflow_examples"


class TestEnsureDirs:
    """Test ensure_dirs() method."""

    def test_ensure_dirs_creates_directories(self, tmp_path, monkeypatch):
        """Test that ensure_dirs creates all required directories."""
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path))
        config = AppConfig.from_default()

        # Verify directories don't exist yet
        assert not config.app_data_dir.exists()
        assert not config.runs_dir.exists()
        assert not config.logs_dir.exists()

        # Create directories
        config.ensure_dirs()

        # Verify directories exist
        assert config.app_data_dir.is_dir()
        assert config.runs_dir.is_dir()
        assert config.logs_dir.is_dir()

        AppConfig._instance = None

    def test_ensure_dirs_idempotent(self, tmp_path, monkeypatch):
        """Test that ensure_dirs can be called multiple times safely."""
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path))
        config = AppConfig.from_default()

        # Call multiple times - should not raise
        config.ensure_dirs()
        config.ensure_dirs()
        config.ensure_dirs()

        # All directories should exist
        assert config.app_data_dir.is_dir()
        assert config.runs_dir.is_dir()
        assert config.logs_dir.is_dir()

        AppConfig._instance = None


class TestBackwardCompatibility:
    """Test backward compatibility helpers."""

    def test_get_default_servers_path(self, tmp_path, monkeypatch):
        """Test servers_path helper matches config.servers.get_default_servers_path()."""
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path))
        config = AppConfig.from_default()

        from jobdesk_app.config.servers import get_default_servers_path

        # Should return the same path
        assert config.get_default_servers_path() == get_default_servers_path()

        AppConfig._instance = None

    def test_get_default_runs_db_path(self, tmp_path, monkeypatch):
        """Test get_default_runs_db_path() returns correct path."""
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path))
        config = AppConfig.from_default()

        expected_db_path = tmp_path / "JobDesk" / "runs" / "jobdesk.db"
        assert config.get_default_runs_db_path() == expected_db_path

        AppConfig._instance = None

    def test_get_logs_dir_path(self, tmp_path, monkeypatch):
        """Test get_logs_dir_path() returns correct path."""
        AppConfig._instance = None

        monkeypatch.setenv("APPDATA", str(tmp_path))
        config = AppConfig.from_default()

        expected_logs_path = tmp_path / "JobDesk" / "logs"
        assert config.get_logs_dir_path() == expected_logs_path

        AppConfig._instance = None


class TestGetConfig:
    """Test the convenience get_config() function."""

    def test_get_config_returns_singleton(self):
        """Test that get_config() returns the singleton."""
        # Reset singleton
        original = AppConfig._instance
        AppConfig._instance = None

        config = get_config()
        assert config is AppConfig.from_default()

        # Restore
        AppConfig._instance = original


class TestRepr:
    """Test __repr__ method."""

    def test_repr_contains_key_paths(self):
        """Test that repr includes key configuration paths."""
        config = AppConfig.from_default()
        repr_str = repr(config)

        # Should contain key paths
        assert "app_data_dir" in repr_str
        assert "runs_dir" in repr_str
        assert "logs_dir" in repr_str
        assert "servers_path" in repr_str
